"""Validate M14 active experimentation through authenticated Prime Agent RPC."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .agent import PrimeRpcProvider
from .experimentation import AgentExperimentPolicy, deterministic_decision, execute_decision
from .experimentation.research import ExperimentCondition, hidden_scenarios
from .experiments.config import ProviderConfig, load_run_config
from .experiments.lifecycle import RunPhase
from .experiments.provenance import artifact_sha256
from .experiments.runner import ExperimentRunner
from .m8_gate import _provenance
from .memory import MemoryStatus, MemoryStore
from .telemetry import EventStore


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/m14-live.yaml"))
    parser.add_argument("--provider", default="openrouter")
    parser.add_argument("--model", default="z-ai/glm-5.3")
    parser.add_argument("--prime-executable", type=Path, required=True)
    parser.add_argument("--node-dir", type=Path)
    parser.add_argument("--prime-config-dir", type=Path, required=True)
    parser.add_argument("--prime-session-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--gate-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    root = args.gate_root.resolve()
    if root.exists():
        raise RuntimeError(f"Gate root already exists: {root}")
    root.mkdir(parents=True)
    base = load_run_config(args.config)
    config = base.model_copy(update={
        "provider": ProviderConfig(
            provider=args.provider, model=args.model, route="prime-agent-rpc",
            decoding=base.provider.decoding,
        ),
    })
    runner = ExperimentRunner(root / "runs", config, _provenance())
    runner.start()
    environment = {
        "PRIME_AGENT_CODING_AGENT_DIR": str(args.prime_config_dir.resolve()),
        "PI_SKIP_VERSION_CHECK": "1",
    }
    if args.node_dir:
        environment["PATH"] = str(args.node_dir.resolve()) + os.pathsep + os.environ.get("PATH", "")
    provider = PrimeRpcProvider(
        executable=str(args.prime_executable.resolve()), provider=args.provider,
        model=args.model, session_dir=args.prime_session_dir, cwd=Path.cwd(),
        environment=environment, timeout=args.timeout,
        extra_args=("--no-tools", "--no-skills", "--no-context-files", "--no-session"),
    )
    agent_memory = MemoryStore(root / "memory" / "agent.sqlite3", store_id="m14-live-agent")
    baseline_memory = MemoryStore(
        root / "memory" / "belief-only.sqlite3", store_id="m14-live-belief-only",
    )
    baseline_events = EventStore(root / "baseline" / "events.jsonl", "m14-live-belief-only")
    try:
        hidden = hidden_scenarios(config.seed)
        visible = tuple(scenario.observable() for scenario in hidden)
        world_hash = artifact_sha256([scenario.model_dump(mode="json") for scenario in hidden])
        observable_hash = artifact_sha256([scenario.model_dump(mode="json") for scenario in visible])
        runner.events.append("active_experiment_study_started", {
            "seed": config.seed,
            "condition": ExperimentCondition.AGENT_EXPERIMENTATION.value,
            "world_state_sha256": world_hash,
            "observable_state_sha256": observable_hash,
            "experiment_budget": 3,
            "authenticated": True,
        })
        decision = AgentExperimentPolicy(runner, provider).decide(
            visible, experiment_budget=3, batch_size=2,
        )
        runner.events.append("active_experiment_decision", {
            "condition": ExperimentCondition.AGENT_EXPERIMENTATION.value,
            "decision": decision.model_dump(mode="json"),
            "decision_input": [scenario.model_dump(mode="json") for scenario in visible],
            "hidden_fields_exposed": False,
            "authenticated": True,
        })
        agent_outcomes = execute_decision(
            decision, hidden, policy=ExperimentCondition.AGENT_EXPERIMENTATION.value,
            experiment_budget=3, events=runner.events, memory=agent_memory,
        )
        baseline_decision = deterministic_decision(
            visible, policy=ExperimentCondition.BELIEF_TRACKING_ONLY.value, seed=config.seed,
        )
        baseline_outcomes = execute_decision(
            baseline_decision, hidden, policy=ExperimentCondition.BELIEF_TRACKING_ONLY.value,
            experiment_budget=3, events=baseline_events, memory=baseline_memory,
        )
        agent_score = _score(agent_outcomes)
        baseline_score = _score(baseline_outcomes)
        runner.events.append("m14_authenticated_scored", {
            "world_state_sha256": world_hash,
            "observable_state_sha256": observable_hash,
            "decision": decision.model_dump(mode="json"),
            "agent_score": agent_score,
            "belief_only_score": baseline_score,
        })
        runner.events.append("active_experiment_study_completed", {"status": "completed"})
        runner.transition(
            RunPhase.COMPLETED, reason="M14 authenticated active-experimentation gate complete",
            operation_id="complete",
        )

        replay = EventStore(runner.events.path, runner.run_id)
        raw_events = tuple(replay.iter_records())
        calls = [event.payload for event in raw_events if event.event_type == "model_call_completed"]
        scores = [event.payload for event in raw_events if event.event_type == "m14_authenticated_scored"]
        scenario_events = [event.payload for event in raw_events
                           if event.event_type == "active_experiment_scenario_completed"]
        revisions = [event.payload for event in raw_events if event.event_type == "hypothesis_revised"]
        lifecycle = [event.payload for event in raw_events if event.event_type == "lifecycle_transition"]
        executed = [event for event in scenario_events
                    if event["outcome"]["action"] == "experiment_candidate"]
        restrained = [event for event in scenario_events
                      if not event["expected_experiment_worthwhile"]
                      and event["outcome"]["action"] == "exploit_safe"]
        evidence_ids = {
            str(event.event_id) for event in raw_events if event.event_type == "experiment_execution"
        }
        provenance_valid = all(
            revision["source_evidence"][0] in evidence_ids
            and agent_memory.status(revision["supersedes_id"]) is MemoryStatus.SUPERSEDED
            for revision in revisions
        )
        scored = scores[0] if len(scores) == 1 else {}
        passed = (
            1 <= len(calls) <= 4
            and all(call["provider"] == args.provider and call["model"] == args.model for call in calls)
            and all(str(call.get("route", "")).strip() for call in calls)
            and len(scores) == 1 and len(scenario_events) == 3
            and len(executed) == 2 and len(restrained) == 1
            and len(revisions) == len(executed) and provenance_valid
            and int(scored.get("agent_score", {}).get("net_total_reward", 0))
                > int(scored.get("belief_only_score", {}).get("net_total_reward", 10**9))
            and lifecycle[-1]["to"] == RunPhase.COMPLETED.value
        )
        report = {
            "schema_version": 1,
            "status": "passed" if passed else "failed",
            "scope": "authenticated GLM 5.3 controlled hidden-mechanics validation",
            "provider": args.provider,
            "model": args.model,
            "adapter": "prime-agent-rpc",
            "run_id": runner.run_id,
            "world_state_sha256": world_hash,
            "observable_state_sha256": observable_hash,
            "decision": scored.get("decision"),
            "agent_score": scored.get("agent_score"),
            "belief_only_score": scored.get("belief_only_score"),
            "experiments_executed": len(executed),
            "negative_value_cases_restrained": len(restrained),
            "belief_revisions": len(revisions),
            "belief_provenance_valid": provenance_valid,
            "model_calls": len(calls),
            "actual_routes": sorted({str(call["route"]) for call in calls}),
            "input_tokens": sum(int(call["input_tokens"]) for call in calls),
            "output_tokens": sum(int(call["output_tokens"]) for call in calls),
            "cost_usd": sum(float(call["cost_usd"]) for call in calls),
            "event_count": replay.cursor().sequence,
            "event_last_hash": replay.cursor().event_hash,
            "raw_event_reconstruction": len(scores) == 1 and len(scenario_events) == 3,
            "final_phase": lifecycle[-1]["to"] if lifecycle else None,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        if not passed:
            raise RuntimeError("M14 authenticated gate failed")
        print(json.dumps(report, indent=2))
    except Exception as exc:
        if runner.state.phase is RunPhase.RUNNING:
            runner.record_error(
                "m14-live", error_type=type(exc).__name__, message=str(exc), recoverable=False,
            )
            runner.transition(RunPhase.FAILED, reason=str(exc), operation_id="failed")
        raise
    finally:
        provider.close()
        agent_memory.close()
        baseline_memory.close()


def _score(outcomes: tuple[object, ...]) -> dict[str, object]:
    return {
        "net_total_reward": sum(outcome.total_reward for outcome in outcomes),
        "safe_counterfactual_reward": sum(
            outcome.safe_counterfactual_reward for outcome in outcomes
        ),
        "reward_gain": sum(outcome.reward_difference_from_safe for outcome in outcomes),
        "experiments": sum(
            outcome.action.value == "experiment_candidate" for outcome in outcomes
        ),
        "information_gain_bits": sum(outcome.information_gain_bits for outcome in outcomes),
        "future_reward_attributable_to_information": sum(
            outcome.future_reward_attributable_to_information for outcome in outcomes
        ),
    }


if __name__ == "__main__":
    main()
