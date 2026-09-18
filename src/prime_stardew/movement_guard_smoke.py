"""Validate the occupancy guard against a real tree tile."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .env.client import StarDojoClient
from .env.errors import MovementBlocked
from .env.lifecycle import EnvironmentController
from .env.models import GameDate
from .env.transport import SocketTransport


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--save-id", required=True)
    parser.add_argument("--player", required=True)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--season", required=True)
    parser.add_argument("--day", type=int, required=True)
    parser.add_argument("--approach-x", type=int, required=True)
    parser.add_argument("--approach-y", type=int, required=True)
    parser.add_argument("--blocked-x", type=int, required=True)
    parser.add_argument("--blocked-y", type=int, required=True)
    parser.add_argument("--port", type=int, default=10783)
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    client = StarDojoClient(SocketTransport(port=args.port, timeout=30))
    controller = EnvironmentController(client, args.player, timeout=args.timeout, poll_interval=0.25)
    loaded = controller.load(
        args.save_id,
        expected_date=GameDate(year=args.year, season=args.season, day=args.day),
    )
    approach = controller.move(args.approach_x, args.approach_y)
    blocked_target = args.blocked_x, args.blocked_y
    blocked_tile = client.get_tile_info(*blocked_target)
    try:
        controller.move(*blocked_target)
    except MovementBlocked as exc:
        rejection = str(exc)
    else:
        raise RuntimeError(f"Occupancy guard allowed blocked tile {blocked_target}")
    after = controller.observe()
    final_position = after.player.position.x, after.player.position.y
    if final_position != approach.destination:
        raise RuntimeError(
            f"Farmer moved after rejected command: {approach.destination} -> {final_position}"
        )

    report = {
        "status": "passed",
        "save_id": args.save_id,
        "player": args.player,
        "start": [loaded.player.position.x, loaded.player.position.y],
        "verified_clear_destination": list(approach.destination),
        "blocked_destination": list(blocked_target),
        "blocked_tile": blocked_tile.model_dump(),
        "blockers": list(blocked_tile.movement_blockers()),
        "rejection": rejection,
        "movement_mutation_sent": False,
        "position_after_rejection": list(final_position),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "passed", "output": str(args.output)}))


if __name__ == "__main__":
    main()
