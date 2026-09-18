"""Run the two phases of the live M2 interruption and exact-once recovery probe."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Iterable

from .checkpoint_cli import default_saves_root
from .env.checkpoints import CheckpointManager
from .env.client import StarDojoClient
from .env.lifecycle import EnvironmentController
from .env.models import GameDate
from .env.transport import SocketTransport
from .experiments.checkpoints import CheckpointKind, RunCheckpointManager
from .telemetry.events import EventRecord, EventStore


ACTION_ID = "day-7-move-to-62-17"
TARGET = (62, 17)


def unfinished_action_ids(records: Iterable[EventRecord]) -> set[str]:
    started: set[str] = set()
    completed: set[str] = set()
    for record in records:
        action_id = record.payload.get("action_id")
        if not isinstance(action_id, str):
            continue
        if record.event_type == "action_started":
            started.add(action_id)
        elif record.event_type == "action_completed":
            completed.add(action_id)
    return started - completed


def _controller(port: int, player: str, timeout: float) -> EnvironmentController:
    client = StarDojoClient(SocketTransport(port=port, timeout=30))
    return EnvironmentController(client, player, timeout=timeout, poll_interval=0.25)


def _checkpoint_manager(saves_root: Path) -> RunCheckpointManager:
    return RunCheckpointManager(CheckpointManager(saves_root))


def phase_one(args: argparse.Namespace) -> dict[str, Any]:
    store = EventStore(args.event_log, args.run_id)
    if list(store.iter_records()):
        raise RuntimeError(f"Recovery event log is not empty: {args.event_log}")
    store.append("run_started", {"phase": 1, "save_id": args.save_id})
    controller = _controller(args.port, args.player, args.timeout)
    day_six = controller.load(
        args.save_id,
        expected_date=GameDate(year=1, season="spring", day=6),
    )
    store.append("day_started", {"year": 1, "season": "spring", "day": 6})
    transition = controller.finish_day()
    store.append(
        "day_completed",
        {"completed_day": 6, "next_day": transition.after.model_dump()},
    )
    store.append("checkpoint_requested", {"boundary": "spring-7"})
    manifest = _checkpoint_manager(args.saves_root).create(
        run_id=args.run_id,
        destination=args.checkpoint,
        save_id=args.save_id,
        player=args.player,
        game_date=GameDate(year=1, season="spring", day=7),
        agent_state={
            "schema_version": 1,
            "lifecycle": "day_complete",
            "completed_through_day": 6,
            "next_action_id": ACTION_ID,
            "expected_position": [64, 15],
        },
        configuration={
            "schema_version": 1,
            "probe": "m2-exact-once-recovery",
            "action_target": list(TARGET),
        },
        event_store=store,
        kind=CheckpointKind.DAY,
        labels=("live-recovery", "spring-7"),
    )
    store.append("checkpoint_published", {"checkpoint_id": manifest.checkpoint_id})
    store.append("action_started", {"action_id": ACTION_ID, "attempt": 1})
    moved = controller.move(*TARGET)
    # Deliberately omit action_completed. The caller now terminates the owned game
    # process to inject the interruption before Stardew can save this movement.
    return {
        "status": "ready_for_injected_crash",
        "run_id": args.run_id,
        "checkpoint_id": manifest.checkpoint_id,
        "checkpoint_cursor": manifest.event_cursor.model_dump(),
        "day_before": day_six.game_state.date.model_dump(),
        "checkpoint_day": transition.after.model_dump(),
        "action_id": ACTION_ID,
        "attempt_1_position": list(moved.destination),
        "action_completed_written": False,
    }


def phase_two(args: argparse.Namespace) -> dict[str, Any]:
    store = EventStore(args.event_log, args.run_id)
    manager = _checkpoint_manager(args.saves_root)
    manifest = manager.verify(args.checkpoint, event_store=store)
    trailing_before = list(store.events_after(manifest.event_cursor))
    unfinished = unfinished_action_ids(trailing_before)
    if unfinished != {ACTION_ID}:
        raise RuntimeError(f"Unexpected unfinished actions after checkpoint: {unfinished}")

    store.append(
        "recovery_started",
        {
            "checkpoint_id": manifest.checkpoint_id,
            "unfinished_action_ids": sorted(unfinished),
        },
    )
    controller = _controller(args.port, args.player, args.timeout)
    restored = controller.load(
        args.save_id,
        expected_date=GameDate(year=1, season="spring", day=7),
    )
    restored_position = restored.player.position.x, restored.player.position.y
    expected_position = tuple(
        json.loads((args.checkpoint / "agent" / "state.json").read_text(encoding="utf-8"))[
            "expected_position"
        ]
    )
    if restored_position != expected_position:
        raise RuntimeError(
            f"Interrupted action was not rolled back: {restored_position} != {expected_position}"
        )

    store.append("action_started", {"action_id": ACTION_ID, "attempt": 2, "recovery": True})
    moved = controller.move(*TARGET)
    store.append(
        "action_completed",
        {
            "action_id": ACTION_ID,
            "attempt": 2,
            "position": list(moved.destination),
        },
    )
    completed = [
        record
        for record in store.iter_records()
        if record.event_type == "action_completed" and record.payload.get("action_id") == ACTION_ID
    ]
    if len(completed) != 1:
        raise RuntimeError(f"Logical action has {len(completed)} completion events")

    transition = controller.finish_day()
    store.append("day_completed", {"completed_day": 7, "next_day": transition.after.model_dump()})
    store.append("checkpoint_requested", {"boundary": "spring-8"})
    final_manifest = manager.create(
        run_id=args.run_id,
        destination=args.final_checkpoint,
        save_id=args.save_id,
        player=args.player,
        game_date=GameDate(year=1, season="spring", day=8),
        agent_state={
            "schema_version": 1,
            "lifecycle": "completed",
            "completed_through_day": 7,
            "completed_action_ids": [ACTION_ID],
        },
        configuration=json.loads(
            (args.checkpoint / "config" / "config.json").read_text(encoding="utf-8")
        ),
        event_store=store,
        kind=CheckpointKind.MILESTONE,
        labels=("m2-gate", "exact-once-recovery", "spring-8"),
    )
    store.append("run_completed", {"checkpoint_id": final_manifest.checkpoint_id})
    return {
        "status": "passed",
        "run_id": args.run_id,
        "restored_checkpoint_id": manifest.checkpoint_id,
        "final_checkpoint_id": final_manifest.checkpoint_id,
        "events_after_restore_cursor": [record.event_type for record in trailing_before],
        "unfinished_action_ids": sorted(unfinished),
        "position_after_attempt_1_before_crash": list(TARGET),
        "position_after_restore": list(restored_position),
        "position_after_reissue": list(moved.destination),
        "action_started_attempts": 2,
        "action_completion_events": len(completed),
        "unique_committed_action_ids": 1,
        "final_game_date": transition.after.model_dump(),
        "final_checkpoint_kind": final_manifest.kind,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("phase1", "phase2"))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--event-log", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--final-checkpoint", type=Path)
    parser.add_argument("--save-id", required=True)
    parser.add_argument("--player", required=True)
    parser.add_argument("--saves-root", type=Path, default=default_saves_root())
    parser.add_argument("--port", type=int, default=10783)
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.phase == "phase2" and args.final_checkpoint is None:
        parser.error("phase2 requires --final-checkpoint")

    report = phase_one(args) if args.phase == "phase1" else phase_two(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + os.linesep, encoding="utf-8")
    print(json.dumps({"status": report["status"], "output": str(args.output)}))


if __name__ == "__main__":
    main()
