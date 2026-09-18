"""Validate M15 contextual adaptation and A recovery through authenticated Prime Agent RPC."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .agent import PrimeRpcProvider
from .experiments.config import ProviderConfig, load_run_config
from .experiments.lifecycle import RunPhase
from .experiments.runner import ExperimentRunner
from .m8_gate import _provenance
from .memory import MemoryKind, MemoryRecord, MemoryStore
from .shifting import AgentShiftPolicy, ShiftResponse, shift_world_family
from .telemetry import EventStore


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/m15-live.yaml"))
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
    memory = MemoryStore(root / "memory" / "beliefs.sqlite3", store_id="m15-live")
    family = shift_world_family(config.seed)
    mechanics = [
        {"mechanic_id": item.mechanic_id, "choices": list(item.choices)}
        for item in family.mechanics
    ]
    try:
        a_memories = []
        b_observations = []
        observation_events: dict[str, str] = {}
        for mechanic in family.mechanics:
            a_action = mechanic.optimal_action("A")
            a_memories.append({
                "mechanic_id": mechanic.mechanic_id,
                "context_cue": family.visible_context_cues["A"],
                "preferred_action": a_action,
                "expected_reward": mechanic.high_reward,
                "evidence": "six successful World A trials",
            })
            reward = mechanic.reward("B", a_action)
            event = runner.events.append("shift_probe_observed", {
                "phase": "B", "visible_context_cue": family.visible_context_cues["B"],
                "mechanic_id": mechanic.mechanic_id, "tried_action": a_action,
                "observed_reward": reward, "prior_expected_reward": mechanic.high_reward,
                "hidden_optimal_in_decision_input": False,
            })
            observation_events[mechanic.mechanic_id] = str(event.event_id)
            b_observations.append({
                "mechanic_id": mechanic.mechanic_id, "tried_action": a_action,
                "observed_reward": reward, "prior_expected_reward": mechanic.high_reward,
            })
        policy = AgentShiftPolicy(runner, provider)
        b_decision = policy.decide(
            phase="world-b-adaptation", context_cue=family.visible_context_cues["B"],
            mechanics=mechanics, observations=b_observations, memories=a_memories,
        )
        b_choices = {item.mechanic_id: item for item in b_decision.choices}
        b_memory_rows = []
        for mechanic in family.mechanics:
            choice = b_choices[mechanic.mechanic_id]
            memory_id = f"belief:m15-live:{mechanic.mechanic_id}:B:v1"
            memory.add(MemoryRecord(
                memory_id=memory_id, kind=MemoryKind.BELIEF,
                text=(f"In context {family.visible_context_cues['B']}, prefer "
                      f"{choice.preferred_action} for {mechanic.mechanic_id}."),
                confidence=0.75,
                source_event_ids=(observation_events[mechanic.mechanic_id],),
                task_domain="stardew-shift", tags=("m15", "B", mechanic.mechanic_id),
                payload={"response": choice.response.value, "preferred_action": choice.preferred_action},
            ))
            b_memory_rows.append({
                "mechanic_id": mechanic.mechanic_id,
                "context_cue": family.visible_context_cues["B"],
                "preferred_action": choice.preferred_action,
                "response": choice.response.value,
                "source_evidence": [observation_events[mechanic.mechanic_id]],
            })
        runner.events.append("shift_b_decision_recorded", {
            "decision": b_decision.model_dump(mode="json"), "hidden_fields_exposed": False,
        })
        return_decision = policy.decide(
            phase="world-a-return", context_cue=family.visible_context_cues["A"],
            mechanics=mechanics, observations=[], memories=a_memories + b_memory_rows,
        )
        return_choices = {item.mechanic_id: item for item in return_decision.choices}
        runner.events.append("shift_return_decision_recorded", {
            "decision": return_decision.model_dump(mode="json"), "hidden_fields_exposed": False,
        })
        shifted = [item for item in family.mechanics if item.shifted]
        b_correct = sum(
            b_choices[item.mechanic_id].preferred_action == item.optimal_action("B")
            for item in family.mechanics
        )
        return_correct = sum(
            return_choices[item.mechanic_id].preferred_action == item.optimal_action("A")
            for item in family.mechanics
        )
        contextualized = sum(
            b_choices[item.mechanic_id].response is ShiftResponse.CONTEXTUALIZE
            for item in shifted
        )
        stable = next(item for item in family.mechanics if not item.shifted)
        stable_retained = (
            b_choices[stable.mechanic_id].response is ShiftResponse.RETAIN
            and b_choices[stable.mechanic_id].preferred_action == stable.optimal_action("B")
        )
        global_overwrite_return_correct = sum(
            b_choices[item.mechanic_id].preferred_action == item.optimal_action("A")
            for item in family.mechanics
        )
        runner.events.append("m15_authenticated_scored", {
            "b_correct": b_correct, "return_a_correct": return_correct,
            "contextualized_shifted": contextualized,
            "stable_retained": stable_retained,
            "global_overwrite_return_correct": global_overwrite_return_correct,
            "hidden_fields_exposed": False,
        })
        runner.transition(
            RunPhase.COMPLETED, reason="M15 authenticated contextual recovery complete",
            operation_id="complete",
        )
        replay = EventStore(runner.events.path, runner.run_id)
        records = tuple(replay.iter_records())
        calls = [item.payload for item in records if item.event_type == "model_call_completed"]
        scored = [item.payload for item in records if item.event_type == "m15_authenticated_scored"]
        passed = (
            len(calls) in {2, 3, 4} and len(scored) == 1
            and b_correct == 3 and return_correct == 3 and contextualized == 2
            and stable_retained and global_overwrite_return_correct == 1
            and all(call["provider"] == args.provider and call["model"] == args.model for call in calls)
            and all(len(record.source_event_ids) == 1 for record in memory.records())
        )
        report = {
            "schema_version": 1,
            "status": "passed" if passed else "failed",
            "scope": "authenticated controlled M15 contextual adaptation and recovery gate",
            "provider": args.provider, "model": args.model, "adapter": "prime-agent-rpc",
            "seed": config.seed,
            "world_state_sha256": family.world_state_sha256,
            "observable_state_sha256": family.observable_state_sha256,
            "b_correct": b_correct, "b_total": 3,
            "return_a_correct": return_correct, "return_a_total": 3,
            "contextualized_shifted": contextualized, "shifted_total": 2,
            "stable_retained": stable_retained,
            "global_overwrite_return_correct": global_overwrite_return_correct,
            "belief_provenance_valid": all(len(record.source_event_ids) == 1 for record in memory.records()),
            "hidden_fields_exposed": False,
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
            raise RuntimeError("M15 authenticated gate failed")
        print(json.dumps(report, indent=2))
    except Exception as exc:
        if runner.state.phase is RunPhase.RUNNING:
            runner.record_error(
                "m15-live", error_type=type(exc).__name__, message=str(exc), recoverable=False,
            )
            runner.transition(RunPhase.FAILED, reason=str(exc), operation_id="failed")
        raise
    finally:
        provider.close()
        memory.close()


if __name__ == "__main__":
    main()
