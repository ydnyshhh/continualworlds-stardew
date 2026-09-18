"""Build and restore the disposable Spring 6 combined checkpoint fixture."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from prime_stardew.env.checkpoints import CheckpointManager
from prime_stardew.env.models import GameDate
from prime_stardew.experiments.checkpoints import RunCheckpointManager
from prime_stardew.telemetry.events import EventStore


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--saves-root", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--source-save-id", default="PrimeStardewSmoke_406041616")
    parser.add_argument("--restore-save-id", default="PrimeStardewCombined_406041616")
    args = parser.parse_args()

    run_id = "combined-checkpoint-spring06"
    run_root = args.workspace / "runtime" / "runs" / run_id
    checkpoint = args.workspace / "runtime" / "checkpoints" / "combined-spring06"
    event_store = EventStore(run_root / "events.jsonl", run_id)
    if list(event_store.iter_records()):
        raise RuntimeError(f"Validation run already exists: {run_root}")
    event_store.append("run_initialized", {"fixture": args.source_save_id})
    event_store.append(
        "fixture_observed",
        {"player": "PrimeStardewSmoke", "year": 1, "season": "spring", "day": 6},
    )
    event_store.append("day_boundary_committed", {"completed_through_day": 5})

    manager = RunCheckpointManager(CheckpointManager(args.saves_root))
    manifest = manager.create(
        run_id=run_id,
        destination=checkpoint,
        save_id=args.source_save_id,
        player="PrimeStardewSmoke",
        game_date=GameDate(year=1, season="spring", day=6),
        agent_state={
            "schema_version": 1,
            "lifecycle": "day_complete",
            "completed_through_day": 5,
            "decision_step": 0,
            "goals": [],
            "memory": [],
            "rng_state": {"seed": 406041616},
        },
        configuration={
            "schema_version": 1,
            "agent": "scripted-checkpoint-validator",
            "fixture_save_id": args.source_save_id,
            "seed": 406041616,
            "observation_mode": "structured_local",
            "checkpoint_boundary": "completed_day",
        },
        event_store=event_store,
        environment={
            "game": "Stardew Valley",
            "game_version": "1.6.15.24356",
            "smapi_version": "4.5.2",
            "stardojo_version": "1.0.0-patched",
            "stardojo_dll_sha256": "f9fe38e0a35676d48b6f676268097576e010d5671b470eba5ceca097e2f0ef20",
        },
    )
    event_store.append("interruption_injected", {"phase": "after_checkpoint_publication"})
    manager.verify(checkpoint, event_store=event_store)
    restored = manager.restore(
        checkpoint,
        args.restore_save_id,
        event_store=event_store,
    )
    trailing = list(event_store.events_after(restored.event_cursor))
    report = {
        "status": "checkpoint_restored",
        "checkpoint": str(checkpoint.resolve()),
        "checkpoint_id": manifest.checkpoint_id,
        "run_id": run_id,
        "source_save_id": args.source_save_id,
        "restore_save_id": args.restore_save_id,
        "game_date": manifest.game_checkpoint.game_date.model_dump(),
        "event_cursor": restored.event_cursor.model_dump(),
        "events_after_cursor": [item.event_type for item in trailing],
        "agent_state_restored": restored.agent_state,
        "configuration_restored": restored.configuration,
        "bundle_file_count": len(manifest.files),
    }
    output = args.workspace / "runtime" / "smoke" / "m2-combined-restore.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
