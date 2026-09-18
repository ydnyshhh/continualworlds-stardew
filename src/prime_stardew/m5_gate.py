"""Exercise the M5 agent contract over all five proven M3 trajectories."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .agent import ContextInputs, PrimeSession, ScriptedProvider
from .experiments.config import load_run_config
from .experiments.lifecycle import RunPhase
from .experiments.provenance import RunProvenance, RuntimeProvenance, capture_code_provenance
from .experiments.runner import ExperimentRunner
from .tasks import TaskScore, TaskTrajectory


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/m5-prime.yaml"))
    parser.add_argument("--m3-report", type=Path, required=True)
    parser.add_argument("--gate-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.gate_root.resolve()
    if root.exists():
        raise RuntimeError(f"Gate root already exists: {root}")
    root.mkdir(parents=True)

    source = json.loads(args.m3_report.read_text(encoding="utf-8"))
    first_by_fixture: dict[str, dict] = {}
    for result in source["results"]:
        first_by_fixture.setdefault(result["fixture_id"], result)
    config = load_run_config(args.config)
    provenance = RunProvenance(
        code=capture_code_provenance(Path.cwd()),
        runtime=RuntimeProvenance(
            game_version="1.6.15.24356", smapi_version="4.5.2",
            stardojo_version="1.0.0-patched",
            stardojo_dll_sha256="6fffa01cdba2b8db5d3c1e008969f05bad9a2730bc2e93c1f8cc2c403d87506b",
        ),
    )
    trajectories = [
        TaskTrajectory.model_validate(first_by_fixture[fixture_id]["trajectory"])
        for fixture_id in config.tasks
    ]
    scores = [TaskScore.model_validate(first_by_fixture[fixture_id]["score"])
              for fixture_id in config.tasks]
    responses = [_decision_json(trajectory, index) for index, trajectory in enumerate(trajectories, 1)]
    runner = ExperimentRunner(root / "runs", config, provenance)
    runner.start()

    provider_one = ScriptedProvider(responses[:4], provider="prime-rpc-contract", model="fixture-model")
    session = PrimeSession(runner, provider_one)
    decisions = []
    for index, (trajectory, score) in enumerate(zip(trajectories[:4], scores[:4]), 1):
        decision = session.decide(_inputs(trajectory, index))
        _assert_actions(decision, trajectory)
        decisions.append(decision)
        runner.record_task(f"atomic-{index}", trajectory, score)

    persisted = session.checkpoint_state()
    provider_two = ScriptedProvider(responses[4:], provider="prime-rpc-contract", model="fixture-model")
    resumed = PrimeSession(runner, provider_two)
    resumed.restore_state(persisted)
    fifth = resumed.decide(_inputs(trajectories[4], 5))
    _assert_actions(fifth, trajectories[4])
    decisions.append(fifth)
    runner.record_task("atomic-5", trajectories[4], scores[4])
    runner.transition(RunPhase.COMPLETED, reason="M5 contract gate complete", operation_id="complete")

    fifth_prompt = provider_two.requests[0].prompt
    calls = [record for record in runner.events.iter_records()
             if record.event_type == "model_call_completed"]
    attributable = all(
        all(record.payload.get(field) for field in ("request_id", "provider", "model", "route"))
        and "decoding" in record.payload for record in calls
    )
    report = {
        "status": "passed",
        "scope": "offline Prime RPC contract; live Prime runtime/model call remains required",
        "run_id": runner.run_id,
        "atomic_tasks": len(decisions),
        "all_task_scores_successful": all(score.success for score in scores),
        "model_calls": len(calls),
        "all_model_calls_attributable": attributable,
        "provider_route": "prime-rpc-contract/deterministic-replay",
        "resume": {
            "goals_before": persisted["active_goals"],
            "goals_present_in_resumed_context": all(
                goal in fifth_prompt for goal in persisted["active_goals"]
            ),
            "transcript_stored_in_checkpoint": False,
            "decision_count": resumed.state.decision_count,
        },
        "final_phase": runner.state.phase.value,
        "source_m3_report": str(args.m3_report.resolve()),
    }
    if len(decisions) != 5 or not attributable or not report["resume"]["goals_present_in_resumed_context"]:
        raise RuntimeError("M5 contract gate failed")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


def _inputs(trajectory: TaskTrajectory, index: int) -> ContextInputs:
    fixture = trajectory.fixture
    return ContextInputs(
        objective=fixture.description,
        observation=json.dumps(trajectory.before.model_dump(mode="json"), sort_keys=True),
        allowed_actions=fixture.allowed_actions,
        recent_events=(),
    )


def _decision_json(trajectory: TaskTrajectory, index: int) -> str:
    return json.dumps({
        "schema_version": 1,
        "actions": [
            {"name": action.name, "arguments": list(action.arguments)}
            for action in trajectory.actions
        ],
        "goal_updates": [f"complete atomic fixture {index + 1}"],
        "memory_notes": [],
        "rationale": "Use the proven allowlisted primitive sequence.",
    }, separators=(",", ":"))


def _assert_actions(decision, trajectory: TaskTrajectory) -> None:
    actual = [(action.name, action.arguments) for action in decision.actions]
    expected = [(action.name, action.arguments) for action in trajectory.actions]
    if actual != expected:
        raise RuntimeError(f"Decision actions differ from trajectory: {actual} != {expected}")


if __name__ == "__main__":
    main()
