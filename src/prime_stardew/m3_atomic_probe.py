"""Run all five M3 atomic fixtures against a disposable live save."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from .env.client import StarDojoClient
from .env.lifecycle import EnvironmentController
from .env.models import Observation
from .env.transport import SocketTransport
from .tasks import (
    ActionCommand,
    LiveTaskHarness,
    TaskFixture,
    TaskKind,
    load_atomic_fixtures,
    score_trajectory,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--player", default="PrimeStardewSmoke")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--port", type=int, default=10783)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.repetitions <= 0:
        parser.error("--repetitions must be positive")

    client = StarDojoClient(SocketTransport(port=args.port, timeout=30))
    controller = EnvironmentController(client, args.player, timeout=45, poll_interval=0.2)
    fixtures = load_atomic_fixtures()
    results = []
    try:
        for fixture in fixtures:
            if fixture.expected_player != args.player:
                raise RuntimeError(f"Unexpected fixture player in {fixture.fixture_id}")
            for repetition in range(1, args.repetitions + 1):
                commands = _setup(controller, fixture)
                harness = LiveTaskHarness(controller, fixture, settle_seconds=0.5)
                trajectory = harness.run(commands)
                score = score_trajectory(trajectory)
                results.append({
                    "fixture_id": fixture.fixture_id,
                    "kind": fixture.kind.value,
                    "repetition": repetition,
                    "score": score.model_dump(mode="json"),
                    "trajectory": trajectory.model_dump(mode="json"),
                })
                if not score.success:
                    raise RuntimeError(
                        f"{fixture.fixture_id} repetition {repetition} failed: "
                        f"{', '.join(score.failure_reasons)}"
                    )
    finally:
        # Ensure a setup-opened menu cannot remain active after a failed probe.
        try:
            client.raw("exit_menu", idempotent=False)
        except Exception:
            pass

    report = {
        "status": "passed",
        "fixture_count": len(fixtures),
        "repetitions_per_fixture": args.repetitions,
        "scored_runs": len(results),
        "all_successful": all(item["score"]["success"] for item in results),
        "privileged_actions_in_scored_runs": sum(
            action["privileged"]
            for item in results
            for action in item["trajectory"]["actions"]
        ),
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in (
        "status", "fixture_count", "repetitions_per_fixture", "scored_runs",
        "privileged_actions_in_scored_runs",
    )}))


def _setup(
    controller: EnvironmentController,
    fixture: TaskFixture,
) -> tuple[ActionCommand, ...]:
    client = controller.client
    client.raw("exit_menu", idempotent=False)
    client.raw("warp", fixture.location, *fixture.origin, idempotent=False)
    controller.wait_until(
        lambda observation: _position(observation) == fixture.origin
        and observation.player.location == fixture.location,
        description=f"reset for {fixture.fixture_id}",
    )

    if fixture.kind is TaskKind.TURN_MOVE:
        return (
            ActionCommand(name="turn", arguments=(fixture.expected_facing,)),
            ActionCommand(name="move_step", arguments=(2,)),
        )

    # Clear only the reusable target tile. These are setup mutations and all
    # finish before LiveTaskHarness.begin captures the scored baseline.
    client.raw("remove_item", *fixture.target, idempotent=False)
    client.raw("world_clear", "crops", "current", idempotent=False)

    if fixture.kind is TaskKind.WATER_CROP:
        client.raw("place_crop", "472", *fixture.target, idempotent=False)
        _wait_crop(controller, fixture.target, watered=False)
        slot = _slot(controller.observe(), "Watering Can")
        return (
            ActionCommand(name="choose_item", arguments=(slot,)),
            ActionCommand(name="turn", arguments=(2,)),
            ActionCommand(name="use"),
        )

    if fixture.kind is TaskKind.CLEAR_DEBRIS:
        client.raw("place_item", "(O)294", "object", *fixture.target, idempotent=False)
        controller.wait_until(
            lambda _: bool(controller.client.get_tile_info(*fixture.target).object_at_tile),
            description="fixture twig",
        )
        slot = _slot(controller.observe(), "Axe")
        return (
            ActionCommand(name="choose_item", arguments=(slot,)),
            ActionCommand(name="turn", arguments=(2,)),
            ActionCommand(name="use"),
            ActionCommand(name="move_step", arguments=(3,)),
        )

    if fixture.kind is TaskKind.HARVEST_CROP:
        client.raw("place_crop", "472", *fixture.target, idempotent=False)
        client.raw("grow_crop", 20, *fixture.target, idempotent=False)
        _wait_crop(controller, fixture.target)
        return (
            ActionCommand(name="turn", arguments=(2,)),
            ActionCommand(name="interact"),
        )

    if fixture.kind is TaskKind.CHEST_TRANSFER:
        client.raw("place_chest", *fixture.target, idempotent=False)
        client.raw("add_item_by_name", fixture.item_name, fixture.quantity, 0, idempotent=False)
        observation = controller.wait_until(
            lambda state: _quantity(state, fixture.item_name or "") >= fixture.quantity,
            description="fixture transfer item",
        )
        slot = _slot(observation, fixture.item_name or "")
        client.turn(2)
        client.interact()
        controller.wait_until(
            lambda state: state.current_menu.type == "Chest",
            description="fixture chest menu",
        )
        return (ActionCommand(name="put_to_chest", arguments=(slot, fixture.quantity)),)

    raise AssertionError(f"Unsupported task kind: {fixture.kind}")


def _wait_crop(
    controller: EnvironmentController,
    target: tuple[int, int],
    *,
    watered: bool | None = None,
) -> None:
    def ready(observation: Observation) -> bool:
        crop = next(
            (item for item in observation.crops if item.position is not None
             and (item.position.x, item.position.y) == target),
            None,
        )
        return crop is not None and (watered is None or crop.is_watered is watered)
    controller.wait_until(ready, description=f"crop at {target}")


def _slot(observation: Observation, name: str) -> int:
    for index, item in enumerate(observation.player.inventory):
        if item.name == name:
            return index
    raise RuntimeError(f"Inventory item not found: {name}")


def _quantity(observation: Observation, name: str) -> int:
    return sum(item.quantity or 0 for item in observation.player.inventory if item.name == name)


def _position(observation: Observation) -> tuple[int, int]:
    return observation.player.position.x, observation.player.position.y


if __name__ == "__main__":
    main()
