"""Run the first live M3 gate: three reset repetitions of turn-and-move."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from .env.client import StarDojoClient
from .env.lifecycle import EnvironmentController
from .env.transport import SocketTransport
from .tasks import ActionCommand, LiveTaskHarness, TaskKind, load_atomic_fixtures, score_trajectory


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--player", default="PrimeStardewSmoke")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--port", type=int, default=10783)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.repetitions <= 0:
        parser.error("--repetitions must be positive")

    fixture = next(
        item for item in load_atomic_fixtures() if item.kind is TaskKind.TURN_MOVE
    )
    if args.player != fixture.expected_player:
        parser.error(f"fixture expects --player {fixture.expected_player!r}")
    client = StarDojoClient(SocketTransport(port=args.port, timeout=30))
    controller = EnvironmentController(client, args.player, timeout=30, poll_interval=0.2)
    results = []
    for repetition in range(1, args.repetitions + 1):
        # Privileged reset is completed before begin(); it cannot enter the scored trace.
        client.raw("warp", fixture.location, *fixture.origin, idempotent=False)
        controller.wait_until(
            lambda observation: (
                observation.player.location == fixture.location
                and (observation.player.position.x, observation.player.position.y) == fixture.origin
            ),
            description=f"M3 reset {repetition}",
        )
        time.sleep(0.25)
        harness = LiveTaskHarness(controller, fixture, settle_seconds=0.4)
        trajectory = harness.run((
            ActionCommand(name="turn", arguments=(fixture.expected_facing,)),
            ActionCommand(name="move_step", arguments=(2,)),
        ))
        score = score_trajectory(trajectory)
        results.append({
            "repetition": repetition,
            "score": score.model_dump(mode="json"),
            "trajectory": trajectory.model_dump(mode="json"),
        })
        if not score.success:
            raise RuntimeError(
                f"Repetition {repetition} failed: {', '.join(score.failure_reasons)}"
            )

    report = {
        "status": "passed",
        "fixture_id": fixture.fixture_id,
        "repetitions": args.repetitions,
        "all_successful": all(item["score"]["success"] for item in results),
        "setup_boundary": "warp completed before each scored trajectory",
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "fixture_id": fixture.fixture_id,
        "repetitions": args.repetitions,
        "output": str(args.output),
    }))


if __name__ == "__main__":
    main()
