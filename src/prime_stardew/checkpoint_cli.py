"""Create, verify, and restore hash-verified Stardew save checkpoints."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .env.checkpoints import CheckpointManager
from .env.models import GameDate


def default_saves_root() -> Path:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA is required unless --saves-root is provided")
    return Path(appdata) / "StardewValley" / "Saves"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--saves-root", type=Path, default=default_saves_root())
    subparsers = parser.add_subparsers(dest="operation", required=True)

    create = subparsers.add_parser("create")
    create.add_argument("--save-id", required=True)
    create.add_argument("--player", required=True)
    create.add_argument("--day", type=int, required=True)
    create.add_argument("--season", required=True)
    create.add_argument("--year", type=int, required=True)
    create.add_argument("--destination", type=Path, required=True)
    create.add_argument("--environment-json", default="{}")

    verify = subparsers.add_parser("verify")
    verify.add_argument("checkpoint", type=Path)

    restore = subparsers.add_parser("restore")
    restore.add_argument("checkpoint", type=Path)
    restore.add_argument("--destination-save-id", required=True)

    args = parser.parse_args()
    manager = CheckpointManager(args.saves_root)
    if args.operation == "create":
        environment = json.loads(args.environment_json)
        if not isinstance(environment, dict):
            parser.error("--environment-json must contain a JSON object")
        result = manager.create(
            args.save_id,
            args.destination,
            player=args.player,
            game_date=GameDate(year=args.year, season=args.season, day=args.day),
            environment=environment,
        )
        print(result.model_dump_json(indent=2))
    elif args.operation == "verify":
        print(manager.verify(args.checkpoint).model_dump_json(indent=2))
    else:
        destination = manager.restore(args.checkpoint, args.destination_save_id)
        print(json.dumps({"status": "restored", "destination": str(destination)}))


if __name__ == "__main__":
    main()
