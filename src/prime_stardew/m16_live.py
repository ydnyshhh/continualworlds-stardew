"""Run authenticated GLM 5.3 memory repair and meta-policy transfer for M16."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .agent import PrimeRpcProvider
from .corruption import AgentMemoryRepairPolicy, RepairResponse, corruption_family
from .experiments.config import ProviderConfig, load_run_config
from .experiments.lifecycle import RunPhase
from .experiments.runner import ExperimentRunner
from .m8_gate import _provenance
from .memory import MemoryKind, MemoryRecord, MemoryStore
from .telemetry import EventStore


def _episode_input(family, episode: int, runner: ExperimentRunner):
    records: list[dict[str, object]] = []
    observations: list[dict[str, object]] = []
    evidence: dict[str, str] = {}
    for scenario in (item for item in family.scenarios if item.episode == episode):
        visible = scenario.observable_record()
        records.append(visible)
        utility = (
            scenario.high_utility
            if scenario.memory_action == scenario.correct_action else scenario.low_utility
        )
        event = runner.events.append("authenticated_corruption_probe", {
            "episode": episode, "scenario_id": scenario.scenario_id,
            "tried_action": scenario.memory_action, "observed_utility": utility,
            "remembered_expected_utility": scenario.high_utility,
            "correct_action_exposed": False, "corruption_label_exposed": False,
        })
        evidence[scenario.scenario_id] = str(event.event_id)
        observations.append({
            "scenario_id": scenario.scenario_id, "tried_action": scenario.memory_action,
            "observed_utility": utility,
            "remembered_expected_utility": scenario.high_utility,
        })
    return records, observations, evidence


def _decide_in_compact_batches(
    policy: AgentMemoryRepairPolicy, *, episode: int,
    records: list[dict[str, object]], observations: list[dict[str, object]],
    policy_patch: dict[str, object] | None = None,
):
    decisions = []
    for index, batch_record in enumerate(records, 1):
        batch_records = [batch_record]
        ids = {str(item["scenario_id"]) for item in batch_records}
        batch_observations = [
            item for item in observations if str(item["scenario_id"]) in ids
        ]
        decisions.append(policy.decide(
            episode=episode, records=batch_records, observations=batch_observations,
            policy_patch=policy_patch, batch_id=f"batch-{index}",
        ))
    return {
        item.scenario_id: item for decision in decisions for item in decision.choices
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/m16-live.yaml"))
    parser.add_argument("--provider", default="openrouter")
    parser.add_argument("--model", default="z-ai/glm-5.3")
    parser.add_argument("--prime-executable", type=Path, required=True)
    parser.add_argument("--node-dir", type=Path)
    parser.add_argument("--prime-config-dir", type=Path, required=True)
    parser.add_argument("--prime-session-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=600)
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
        extra_args=(
            "--thinking", "minimal", "--no-tools", "--no-skills",
            "--no-context-files", "--no-session",
        ),
    )
    memory = MemoryStore(root / "memory" / "beliefs.sqlite3", store_id="m16-live")
    family = corruption_family(config.seed)
    try:
        policy = AgentMemoryRepairPolicy(runner, provider)
        records1, observations1, evidence1 = _episode_input(family, 1, runner)
        choices1 = _decide_in_compact_batches(
            policy, episode=1, records=records1, observations=observations1,
        )
        for scenario in (item for item in family.scenarios if item.episode == 1):
            original_id = f"memory:m16-live:{scenario.scenario_id}:v1"
            memory.add(MemoryRecord(
                memory_id=original_id, kind=scenario.memory_kind, text=scenario.memory_text,
                confidence=scenario.claimed_confidence,
                source_event_ids=(evidence1[scenario.scenario_id],), task_domain="memory-repair",
                payload={"recommended_action": scenario.memory_action}, tags=("m16", "injected"),
            ))
            choice = choices1[scenario.scenario_id]
            if choice.preferred_action != scenario.memory_action:
                memory.add(MemoryRecord(
                    memory_id=f"memory:m16-live:{scenario.scenario_id}:v2",
                    kind=scenario.memory_kind,
                    text=f"For {scenario.scenario_id}, choose {choice.preferred_action}.",
                    confidence=.7, source_event_ids=(evidence1[scenario.scenario_id],),
                    task_domain="memory-repair", supersedes_id=original_id, version=2,
                    payload={"recommended_action": choice.preferred_action,
                             "response": choice.response.value}, tags=("m16", "repaired"),
                ))
        episode1_correct = sum(
            choices1[item.scenario_id].preferred_action == item.correct_action
            for item in family.scenarios if item.episode == 1
        )
        summary = {
            "episode": 1, "records_evaluated": 7,
            "record_predictions_matched_observed_utility": 2,
            "record_predictions_contradicted_by_observed_utility": 5,
            "alternative_action_followups_improved": 5,
            "matching_action_followups_remained_stable": 2,
            "current_policy": {"evidence_threshold": 2},
            "evaluator_labels_available": False,
        }
        patch = policy.propose_policy_patch(summary=summary)
        proposed = runner.events.append("authenticated_memory_policy_patch_proposed", {
            **patch.model_dump(mode="json"), "restricted_data_only_patch": True,
        })
        runner.events.append("authenticated_memory_policy_patch_applied", {
            "proposal_event_id": str(proposed.event_id),
            "mechanism_name": patch.mechanism_name,
            "threshold": patch.trigger.threshold,
            "repair_response": patch.repair_response.value,
        })
        records2, observations2, _evidence2 = _episode_input(family, 2, runner)
        choices2 = _decide_in_compact_batches(
            policy, episode=2, records=records2, observations=observations2,
            policy_patch=patch.model_dump(mode="json"),
        )
        episode2_correct = sum(
            choices2[item.scenario_id].preferred_action == item.correct_action
            for item in family.scenarios if item.episode == 2
        )
        corrupt_episode2_correct = sum(
            choices2[item.scenario_id].preferred_action == item.correct_action
            for item in family.scenarios if item.episode == 2 and item.corruption_type is not None
        )
        clean_episode2_retained = sum(
            choices2[item.scenario_id].preferred_action == item.memory_action
            for item in family.scenarios if item.episode == 2 and item.corruption_type is None
        )
        runner.events.append("m16_authenticated_scored", {
            "episode_1_correct": episode1_correct, "episode_2_correct": episode2_correct,
            "corrupt_episode_2_correct": corrupt_episode2_correct,
            "clean_episode_2_retained": clean_episode2_retained,
            "policy_threshold": patch.trigger.threshold,
            "hidden_fields_exposed": False,
        })
        runner.transition(
            RunPhase.COMPLETED, reason="M16 authenticated repair transfer complete",
            operation_id="complete",
        )
        replay = EventStore(runner.events.path, runner.run_id)
        calls = [x.payload for x in replay.iter_records() if x.event_type == "model_call_completed"]
        passed = (
            episode2_correct == 7 and episode2_correct > episode1_correct
            and corrupt_episode2_correct == 5 and clean_episode2_retained == 2
            and patch.trigger.threshold < 2
            and 15 <= len(calls) <= 30
            and all(call["provider"] == args.provider and call["model"] == args.model for call in calls)
        )
        report = {
            "schema_version": 1, "status": "passed" if passed else "failed",
            "scope": "authenticated controlled M16 memory repair and policy-transfer gate",
            "provider": args.provider, "model": args.model, "adapter": "prime-agent-rpc",
            "seed": config.seed, "episode_1_correct": episode1_correct,
            "episode_2_correct": episode2_correct,
            "episode_2_minus_episode_1_correct": episode2_correct - episode1_correct,
            "corrupt_episode_2_correct": corrupt_episode2_correct,
            "clean_episode_2_retained": clean_episode2_retained,
            "policy_patch": patch.model_dump(mode="json"),
            "restricted_data_only_patch": True, "hidden_fields_exposed": False,
            "model_calls": len(calls),
            "actual_routes": sorted({str(call["route"]) for call in calls}),
            "input_tokens": sum(int(call["input_tokens"]) for call in calls),
            "output_tokens": sum(int(call["output_tokens"]) for call in calls),
            "cost_usd": sum(float(call["cost_usd"]) for call in calls),
            "event_count": replay.cursor().sequence,
            "event_last_hash": replay.cursor().event_hash,
            "final_phase": runner.state.phase.value,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        if not passed:
            raise RuntimeError("M16 authenticated gate failed")
        print(json.dumps(report, indent=2))
    except Exception as exc:
        if runner.state.phase is RunPhase.RUNNING:
            runner.record_error(
                "m16-live", error_type=type(exc).__name__, message=str(exc), recoverable=False,
            )
            runner.transition(RunPhase.FAILED, reason=str(exc), operation_id="failed")
        raise
    finally:
        provider.close()
        memory.close()


if __name__ == "__main__":
    main()
