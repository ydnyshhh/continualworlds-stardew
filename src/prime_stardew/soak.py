"""Run repeated live observation/action checks against an isolated farmer."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path
from typing import Any

from .env.client import StarDojoClient
from .env.lifecycle import EnvironmentController
from .env.models import GameDate
from .env.transport import SocketTransport


def run_soak(
    controller: EnvironmentController,
    save_id: str,
    expected_date: GameDate,
    *,
    cycles: int = 50,
) -> dict[str, Any]:
    if cycles < 1:
        raise ValueError("Cycles must be positive")
    initial = controller.load(save_id, expected_date=expected_date)
    initial_direction = initial.player.facing_direction
    current_direction = initial_direction
    latencies: list[float] = []
    started = time.monotonic()
    try:
        for _ in range(cycles):
            target = (current_direction + 1) % 4
            action = controller.client.turn(target)
            observed = controller.wait_until(
                lambda observation, expected=target: observation.player.facing_direction == expected,
                description=f"soak facing direction {target}",
            )
            if observed.game_state.date != expected_date:
                raise RuntimeError(
                    f"Date changed during soak: {observed.game_state.date}; expected {expected_date}"
                )
            current_direction = observed.player.facing_direction
            latencies.append(action.elapsed_ms)
    finally:
        controller.client.turn(initial_direction)
        controller.wait_until(
            lambda observation: observation.player.facing_direction == initial_direction,
            description=f"restored facing direction {initial_direction}",
        )

    return {
        "status": "passed",
        "save_id": save_id,
        "player": initial.player.name,
        "date": expected_date.model_dump(),
        "cycles": cycles,
        "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
        "action_latency_ms": {
            "min": round(min(latencies), 3),
            "median": round(statistics.median(latencies), 3),
            "max": round(max(latencies), 3),
        },
        "initial_direction": initial_direction,
        "restored_direction": initial_direction,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--save-id", required=True)
    parser.add_argument("--player", required=True)
    parser.add_argument("--day", type=int, required=True)
    parser.add_argument("--season", required=True)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--cycles", type=int, default=50)
    parser.add_argument("--port", type=int, default=10783)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    controller = EnvironmentController(
        StarDojoClient(SocketTransport(port=args.port, timeout=min(args.timeout, 30))),
        args.player,
        timeout=args.timeout,
    )
    report = run_soak(
        controller,
        args.save_id,
        GameDate(year=args.year, season=args.season, day=args.day),
        cycles=args.cycles,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "passed", "cycles": args.cycles, "output": str(args.output)}))


if __name__ == "__main__":
    main()
