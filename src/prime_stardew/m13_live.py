"""Validate M13 replay prioritization through authenticated Prime Agent RPC."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .agent import PrimeRpcProvider
from .experience_replay import AgentReplayPolicy, ReplayCondition, execute_replay
from .experience_replay.engine import deterministic_replay_decision
from .experience_replay.research import replay_fixture
from .experiments.config import ProviderConfig, load_run_config
from .experiments.lifecycle import RunPhase
from .experiments.provenance import artifact_sha256
from .experiments.runner import ExperimentRunner
from .m8_gate import _provenance
from .telemetry import EventStore


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/m13-live.yaml"))
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
    replay_budget = 3
    try:
        experiences, tasks = replay_fixture(config.seed)
        candidate_hash = artifact_sha256([item.model_dump(mode="json") for item in experiences])
        observable_hash = artifact_sha256([task.observable() for task in tasks])
        hidden_hash = artifact_sha256([task.model_dump(mode="json") for task in tasks])
        runner.events.append("experience_replay_study_started", {
            "seed": config.seed, "condition": ReplayCondition.AGENT_PRIORITY.value,
            "replay_budget": replay_budget, "candidate_state_sha256": candidate_hash,
            "observable_evaluation_sha256": observable_hash,
            "hidden_evaluation_sha256": hidden_hash, "authenticated": True,
        })
        for experience in experiences:
            runner.events.append("replay_experience_observed", {
                "experience": experience.model_dump(mode="json"),
            })
        runner.events.append("replay_decision_requested", {
            "candidate_ids": [item.experience_id for item in experiences],
            "replay_budget": replay_budget,
            "evaluation_tasks": [task.observable() for task in tasks],
        })
        decision = AgentReplayPolicy(runner, provider).decide(
            experiences, tasks, replay_budget=replay_budget,
        )
        runner.events.append("replay_decision_made", {
            "condition": ReplayCondition.AGENT_PRIORITY.value,
            "decision": decision.model_dump(mode="json"),
            "decision_input": {
                "experiences": [item.model_dump(mode="json") for item in experiences],
                "evaluation_tasks": [task.observable() for task in tasks],
            },
            "hidden_fields_exposed": False, "authenticated": True,
        })
        agent_execution = execute_replay(
            decision, experiences, replay_budget=replay_budget, events=runner.events,
        )
        baseline_decision = deterministic_replay_decision(
            experiences, tasks, condition=ReplayCondition.ERROR_PRIORITY,
            replay_budget=replay_budget, seed=config.seed,
        )
        agent_score = _score(agent_execution, tasks)
        learned = set(agent_execution["learned_capabilities"])
        for task in tasks:
            runner.events.append("replay_heldout_task_completed", {
                "task_id": task.task_id, "capability": task.capability,
                "success": task.capability in learned,
                "supporting_replay_event_ids": list(agent_execution["replay_event_ids"]),
            })
        baseline_selected = {
            action.experience_id for action in baseline_decision.actions if action.replay
        }
        capability_by_id = {item.experience_id: item.capability for item in experiences}
        baseline_execution = {
            "selected_ids": tuple(baseline_selected),
            "learned_capabilities": tuple(capability_by_id[item] for item in baseline_selected),
        }
        baseline_score = _score(baseline_execution, tasks)
        runner.events.append("m13_authenticated_scored", {
            "candidate_state_sha256": candidate_hash,
            "observable_evaluation_sha256": observable_hash,
            "hidden_evaluation_sha256": hidden_hash,
            "decision": decision.model_dump(mode="json"),
            "agent_score": agent_score,
            "error_priority_score": baseline_score,
            "replay_event_ids": list(agent_execution["replay_event_ids"]),
        })
        runner.events.append("experience_replay_study_completed", {"status": "completed"})
        runner.transition(
            RunPhase.COMPLETED, reason="M13 authenticated experience-replay gate complete",
            operation_id="complete",
        )

        replay = EventStore(runner.events.path, runner.run_id)
        records = tuple(replay.iter_records())
        calls = [item.payload for item in records if item.event_type == "model_call_completed"]
        scores = [item.payload for item in records if item.event_type == "m13_authenticated_scored"]
        replay_events = [item for item in records if item.event_type == "experience_replayed"]
        updates = [item.payload for item in records if item.event_type == "replay_learning_updated"]
        heldout = [item.payload for item in records
                   if item.event_type == "replay_heldout_task_completed"]
        lifecycle = [item.payload for item in records if item.event_type == "lifecycle_transition"]
        replay_ids = {str(item.event_id) for item in replay_events}
        provenance_valid = (
            {str(item["source_replay_event_id"]) for item in updates} == replay_ids
        )
        scored = scores[0] if len(scores) == 1 else {}
        passed = (
            1 <= len(calls) <= 2
            and all(call["provider"] == args.provider and call["model"] == args.model for call in calls)
            and all(str(call.get("route", "")).strip() for call in calls)
            and len(scores) == 1 and len(replay_events) <= replay_budget
            and provenance_valid
            and len(heldout) == len(tasks)
            and sum(bool(item["success"]) for item in heldout)
                == int(scored.get("agent_score", {}).get("held_out_correct", -1))
            and float(scored.get("agent_score", {}).get("held_out_accuracy", 0))
                > float(scored.get("error_priority_score", {}).get("held_out_accuracy", 1))
            and lifecycle[-1]["to"] == RunPhase.COMPLETED.value
        )
        report = {
            "schema_version": 1,
            "status": "passed" if passed else "failed",
            "scope": "authenticated GLM 5.3 experience-replay prioritization validation",
            "provider": args.provider, "model": args.model, "adapter": "prime-agent-rpc",
            "run_id": runner.run_id,
            "candidate_state_sha256": candidate_hash,
            "observable_evaluation_sha256": observable_hash,
            "hidden_evaluation_sha256": hidden_hash,
            "decision": scored.get("decision"),
            "agent_score": scored.get("agent_score"),
            "error_priority_score": scored.get("error_priority_score"),
            "replay_provenance_valid": provenance_valid,
            "model_calls": len(calls),
            "actual_routes": sorted({str(call["route"]) for call in calls}),
            "input_tokens": sum(int(call["input_tokens"]) for call in calls),
            "output_tokens": sum(int(call["output_tokens"]) for call in calls),
            "cost_usd": sum(float(call["cost_usd"]) for call in calls),
            "event_count": replay.cursor().sequence,
            "event_last_hash": replay.cursor().event_hash,
            "raw_event_reconstruction": (
                len(scores) == 1 and provenance_valid and len(heldout) == len(tasks)
            ),
            "final_phase": lifecycle[-1]["to"] if lifecycle else None,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        if not passed:
            raise RuntimeError("M13 authenticated gate failed")
        print(json.dumps(report, indent=2))
    except Exception as exc:
        if runner.state.phase is RunPhase.RUNNING:
            runner.record_error(
                "m13-live", error_type=type(exc).__name__, message=str(exc), recoverable=False,
            )
            runner.transition(RunPhase.FAILED, reason=str(exc), operation_id="failed")
        raise
    finally:
        provider.close()


def _score(execution: dict[str, tuple[str, ...]], tasks: tuple[object, ...]) -> dict[str, object]:
    learned = set(execution["learned_capabilities"])
    correct = sum(task.capability in learned for task in tasks)
    selected = len(execution["selected_ids"])
    return {
        "held_out_correct": correct,
        "held_out_total": len(tasks),
        "held_out_accuracy": correct / len(tasks),
        "replay_count": selected,
        "useful_replay_precision": correct / max(1, selected),
        "selected_ids": list(execution["selected_ids"]),
    }


if __name__ == "__main__":
    main()
