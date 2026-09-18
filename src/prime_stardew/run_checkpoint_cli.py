"""Create, verify, and restore complete PrimeStardew run checkpoints."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .checkpoint_cli import default_saves_root
from .env.checkpoints import CheckpointManager
from .env.models import GameDate
from .experiments.checkpoints import RunCheckpointManager
from .telemetry.events import EventStore


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--saves-root", type=Path, default=default_saves_root())
    subparsers = parser.add_subparsers(dest="operation", required=True)

    create = subparsers.add_parser("create")
    create.add_argument("--run-id", required=True)
    create.add_argument("--event-log", type=Path, required=True)
    create.add_argument("--save-id", required=True)
    create.add_argument("--player", required=True)
    create.add_argument("--day", type=int, required=True)
    create.add_argument("--season", required=True)
    create.add_argument("--year", type=int, required=True)
    create.add_argument("--agent-state", type=Path, required=True)
    create.add_argument("--configuration", type=Path, required=True)
    create.add_argument("--memory-database", type=Path)
    create.add_argument("--destination", type=Path, required=True)
    create.add_argument("--environment-json", default="{}")

    verify = subparsers.add_parser("verify")
    verify.add_argument("checkpoint", type=Path)
    verify.add_argument("--event-log", type=Path)
    verify.add_argument("--run-id")

    restore = subparsers.add_parser("restore")
    restore.add_argument("checkpoint", type=Path)
    restore.add_argument("--destination-save-id", required=True)
    restore.add_argument("--event-log", type=Path)
    restore.add_argument("--run-id")
    restore.add_argument("--destination-memory-path", type=Path)

    args = parser.parse_args()
    manager = RunCheckpointManager(CheckpointManager(args.saves_root))
    if args.operation == "create":
        event_store = EventStore(args.event_log, args.run_id)
        result = manager.create(
            run_id=args.run_id,
            destination=args.destination,
            save_id=args.save_id,
            player=args.player,
            game_date=GameDate(year=args.year, season=args.season, day=args.day),
            agent_state=_read_object(args.agent_state),
            configuration=_read_object(args.configuration),
            event_store=event_store,
            memory_database=args.memory_database,
            environment=_parse_object(args.environment_json, "--environment-json"),
        )
        print(result.model_dump_json(indent=2))
        return

    event_store = _optional_store(args, parser)
    if args.operation == "verify":
        print(manager.verify(args.checkpoint, event_store=event_store).model_dump_json(indent=2))
    else:
        restored = manager.restore(
            args.checkpoint,
            args.destination_save_id,
            event_store=event_store,
            destination_memory_path=args.destination_memory_path,
        )
        print(restored.model_dump_json(indent=2))


def _optional_store(args: argparse.Namespace, parser: argparse.ArgumentParser) -> EventStore | None:
    if bool(args.event_log) != bool(args.run_id):
        parser.error("--event-log and --run-id must be provided together")
    return EventStore(args.event_log, args.run_id) if args.event_log else None


def _read_object(path: Path) -> dict[str, Any]:
    return _parse_object(path.read_text(encoding="utf-8"), str(path))


def _parse_object(encoded: str, label: str) -> dict[str, Any]:
    value = json.loads(encoded)
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return value


if __name__ == "__main__":
    main()
