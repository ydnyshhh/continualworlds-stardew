"""Exercise the M4 equivalence and exact-once resume gates without launching the game."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .env.checkpoints import CheckpointManager
from .experiments.checkpoints import RunCheckpointManager
from .experiments.config import load_run_config
from .experiments.lifecycle import RunPhase
from .experiments.provenance import (
    RunProvenance,
    RuntimeProvenance,
    capture_code_provenance,
)
from .experiments.runner import ExperimentRunner
from .tasks.models import PrimitiveAction, TaskScore, TaskTrajectory


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/m4-scripted.yaml"))
    parser.add_argument("--m3-report", type=Path, required=True)
    parser.add_argument("--gate-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    gate_root = args.gate_root.resolve()
    if gate_root.exists():
        raise RuntimeError(f"Gate root already exists: {gate_root}")
    gate_root.mkdir(parents=True)

    source = json.loads(args.m3_report.read_text(encoding="utf-8"))
    selected = next(item for item in source["results"] if item["kind"] == "turn_move")
    trajectory = TaskTrajectory.model_validate(selected["trajectory"])
    score = TaskScore.model_validate(selected["score"])
    base = load_run_config(args.config)
    synthetic_save_id = "M4GateFixture_1"
    base = base.model_copy(update={
        "fixture": base.fixture.model_copy(update={"save_id": synthetic_save_id}),
        "tasks": (trajectory.fixture.fixture_id,),
    })
    provenance = RunProvenance(
        code=capture_code_provenance(Path.cwd()),
        runtime=RuntimeProvenance(
            game_version="1.6.15.24356",
            smapi_version="4.5.2",
            stardojo_version="1.0.0-patched",
            stardojo_dll_sha256=(
                "6fffa01cdba2b8db5d3c1e008969f05bad9a2730bc2e93c1f8cc2c403d87506b"
            ),
        ),
    )

    runs_root = gate_root / "runs"
    first = ExperimentRunner(runs_root, base.model_copy(update={"repetition": 1}), provenance)
    second = ExperimentRunner(runs_root, base.model_copy(update={"repetition": 2}), provenance)
    first.start()
    second.start()
    first.record_task("turn-move", _with_request_suffix(trajectory, "run-1"), score)
    second.record_task("turn-move", _with_request_suffix(trajectory, "run-2"), score)
    first.transition(RunPhase.COMPLETED, reason="gate workload complete", operation_id="complete")
    second.transition(RunPhase.COMPLETED, reason="gate workload complete", operation_id="complete")
    digest_one = first.equivalence_digest()
    digest_two = second.equivalence_digest()
    if digest_one != digest_two:
        raise RuntimeError("Identical scripted runs produced different normalized trajectories")

    saves = gate_root / "saves"
    _make_save(saves, synthetic_save_id)
    manager = RunCheckpointManager(
        CheckpointManager(saves, stable_checks=2, stable_interval=0, stable_timeout=2)
    )
    interrupted = ExperimentRunner(
        runs_root, base.model_copy(update={"repetition": 3}), provenance
    )
    interrupted.start()
    interrupted.transition(
        RunPhase.DAY_COMPLETE, reason="checkpoint boundary", operation_id="day-complete"
    )
    checkpoint = gate_root / "checkpoint"
    manifest = interrupted.publish_checkpoint(manager, destination=checkpoint)
    interrupted.transition(
        RunPhase.RUNNING, reason="continue after checkpoint", operation_id="continue"
    )
    interrupted.events.append_idempotent(
        "task_started",
        {
            "task_id": "interrupted-task",
            "fixture_id": trajectory.fixture.fixture_id,
            "kind": trajectory.fixture.kind.value,
        },
        idempotency_key="task:interrupted-task:started",
    )
    interrupted.transition(
        RunPhase.INTERRUPTED, reason="injected interruption", operation_id="interrupt"
    )
    trailing_before = len(list(interrupted.events.events_after(manifest.event_cursor)))
    interrupted.resume_from_checkpoint(manager, checkpoint, "M4GateRestored_1")
    interrupted.record_task("interrupted-task", trajectory, score)
    event_count = len(list(interrupted.events.iter_records()))
    interrupted.record_task("interrupted-task", trajectory, score)
    event_count_after_replay = len(list(interrupted.events.iter_records()))
    interrupted.transition(
        RunPhase.COMPLETED, reason="recovered workload complete", operation_id="complete"
    )
    records = tuple(interrupted.events.iter_records())
    keys = [record.payload.get("idempotency_key") for record in records]
    if len(keys) != len(set(keys)):
        raise RuntimeError("Duplicate idempotency keys found after resume")

    report = {
        "status": "passed",
        "equivalent_runs": {
            "run_ids": [first.run_id, second.run_id],
            "digest": digest_one,
            "equal": digest_one == digest_two,
            "action_counts": [first.state.action_count, second.state.action_count],
        },
        "interrupted_resume": {
            "run_id": interrupted.run_id,
            "checkpoint_id": manifest.checkpoint_id,
            "trailing_events_before_resume": trailing_before,
            "completed_task_ids": list(interrupted.state.completed_task_ids),
            "action_count": interrupted.state.action_count,
            "event_count_before_idempotent_replay": event_count,
            "event_count_after_idempotent_replay": event_count_after_replay,
            "duplicate_events_added": event_count_after_replay - event_count,
            "unique_idempotency_keys": len(keys) == len(set(keys)),
            "final_phase": interrupted.state.phase.value,
        },
        "source_m3_report": str(args.m3_report.resolve()),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


def _with_request_suffix(trajectory: TaskTrajectory, suffix: str) -> TaskTrajectory:
    actions = tuple(
        PrimitiveAction(
            **action.model_dump(exclude={"request_id"}),
            request_id=f"{suffix}-{action.sequence}",
        )
        for action in trajectory.actions
    )
    return trajectory.model_copy(update={"actions": actions})


def _make_save(root: Path, save_id: str) -> None:
    path = root / save_id
    path.mkdir(parents=True)
    (path / save_id).write_text("m4-gate-current", encoding="utf-8")
    (path / f"{save_id}_old").write_text("m4-gate-old", encoding="utf-8")


if __name__ == "__main__":
    main()
