"""Run a reversible live SDK smoke test against an already-running StarDojo."""

from __future__ import annotations

import argparse
import json
from importlib.metadata import version
from pathlib import Path
from typing import Any

from .env.client import StarDojoClient
from .env.lifecycle import EnvironmentController
from .env.models import GameDate
from .env.transport import SocketTransport


def run_smoke(
    controller: EnvironmentController,
    save_id: str,
    expected_date: GameDate,
    *,
    screenshot_width: int = 1920,
    screenshot_height: int = 1080,
) -> dict[str, Any]:
    loaded = controller.load(save_id, expected_date=expected_date)
    loaded.validate_screenshot_size(screenshot_width, screenshot_height)
    initial_direction = loaded.player.facing_direction
    test_direction = (initial_direction + 1) % 4

    pause = controller.client.pause()
    resume = controller.client.resume()
    turn = controller.client.turn(test_direction)
    changed = controller.wait_until(
        lambda observation: observation.player.facing_direction == test_direction,
        description=f"facing direction {test_direction}",
    )
    restore = controller.client.turn(initial_direction)
    restored = controller.wait_until(
        lambda observation: observation.player.facing_direction == initial_direction,
        description=f"restored facing direction {initial_direction}",
    )

    screenshot = loaded.screenshot_bytes()
    return {
        "status": "passed",
        "sdk_version": version("prime-stardew"),
        "save_id": save_id,
        "player": loaded.player.name,
        "date": expected_date.model_dump(),
        "time": restored.game_state.time,
        "location": restored.player.location,
        "position": restored.player.position.model_dump(),
        "screenshot": {
            "width": screenshot_width,
            "height": screenshot_height,
            "channels": 4,
            "bytes": len(screenshot or b""),
            "sha256": loaded.raw_screenshot_sha256(),
        },
        "facing_direction": {
            "before": initial_direction,
            "changed": changed.player.facing_direction,
            "restored": restored.player.facing_direction,
        },
        "requests": {
            "pause": pause.request_id,
            "resume": resume.request_id,
            "turn": turn.request_id,
            "restore_turn": restore.request_id,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--save-id", required=True)
    parser.add_argument("--player", required=True)
    parser.add_argument("--day", type=int, required=True)
    parser.add_argument("--season", required=True)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--port", type=int, default=10783)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--poll-interval", type=float, default=0.5)
    parser.add_argument("--screenshot-width", type=int, default=1920)
    parser.add_argument("--screenshot-height", type=int, default=1080)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    client = StarDojoClient(SocketTransport(port=args.port, timeout=min(args.timeout, 30)))
    controller = EnvironmentController(
        client,
        args.player,
        timeout=args.timeout,
        poll_interval=args.poll_interval,
    )
    report = run_smoke(
        controller,
        args.save_id,
        GameDate(year=args.year, season=args.season, day=args.day),
        screenshot_width=args.screenshot_width,
        screenshot_height=args.screenshot_height,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "passed", "output": str(args.output)}))


if __name__ == "__main__":
    main()
