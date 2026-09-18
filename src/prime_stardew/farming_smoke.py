"""Exercise movement variants plus hoe, water, plant, and harvest controls."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Callable

from .env.client import StarDojoClient
from .env.lifecycle import EnvironmentController
from .env.models import GameDate, Observation
from .env.transport import SocketTransport


def run_farming_smoke(
    controller: EnvironmentController,
    *,
    save_id: str,
    player: str,
    expected_date: GameDate,
    origin: tuple[int, int],
    work_origin: tuple[int, int],
    target: tuple[int, int],
    step_target: tuple[int, int],
    blocked_target: tuple[int, int],
) -> dict[str, Any]:
    initial = controller.load(save_id, expected_date=expected_date)
    if _position(initial) != origin:
        raise RuntimeError(f"Expected origin {origin}; found {_position(initial)}")
    target_before = _tile(controller.client, target)
    if target_before.get("object_at_tile") or target_before.get("terrain_at_tile"):
        raise RuntimeError(f"Farming target is occupied: {target_before}")

    approach = controller.client.move(*work_origin)
    if approach.payload != "True":
        raise RuntimeError(f"Could not reach farming origin {work_origin}: {approach.payload}")
    _wait_position(controller, work_origin)
    relative = controller.client.move_relative(
        target[0] - work_origin[0], target[1] - work_origin[1]
    )
    time.sleep(0.5)
    relative_position = controller.client.observe(expected_player=player)
    relative_succeeded = _position(relative_position) == target
    if relative_succeeded:
        _return_to(controller, work_origin)

    step_direction = _step_direction(work_origin, step_target)
    step = controller.client.move_step(step_direction)
    time.sleep(0.5)
    step_position = controller.client.observe(expected_player=player)
    step_succeeded = _position(step_position) == step_target
    if step_succeeded:
        _return_to(controller, work_origin)
    blocked = controller.client.move(*blocked_target)
    blocked_observation = controller.client.observe(expected_player=player)
    blocked_was_respected = _position(blocked_observation) != blocked_target
    if not blocked_was_respected:
        recovery = controller.client.move(*work_origin)
        if recovery.payload != "True":
            raise RuntimeError("Could not recover from occupied-tile movement")
        _wait_position(controller, work_origin)

    direction = _facing_direction(work_origin, target)
    controller.client.choose_item(1)
    _wait_item(controller, "Hoe")
    controller.client.turn(direction)
    _wait_facing(controller, direction)
    before_hoe = controller.client.observe(expected_player=player)
    controller.client.use()
    hoed = _wait_tile(controller.client, target, lambda tile: "HoeDirt" in str(tile.get("terrain_at_tile")))
    after_hoe = controller.client.observe(expected_player=player)

    controller.client.choose_item(2)
    _wait_item(controller, "Watering Can")
    before_water = controller.client.observe(expected_player=player)
    controller.client.use()
    time.sleep(0.5)
    after_water = controller.client.observe(expected_player=player)
    if after_water.player.stamina >= before_water.player.stamina:
        raise RuntimeError("Watering can use did not consume stamina")

    # Seed provisioning is fixture setup; planting and harvesting still use normal controls.
    controller.client.raw("add_item_by_name", "Parsnip Seeds", 2, 0, idempotent=False)
    with_seeds = controller.wait_until(
        lambda observation: _quantity(observation, "Parsnip Seeds") >= 2,
        description="fixture parsnip seeds",
    )
    seed_slot = _slot(with_seeds, "Parsnip Seeds")
    controller.client.choose_item(seed_slot)
    _wait_item(controller, "Parsnip Seeds")
    controller.client.interact()
    planted = _wait_tile(controller.client, target, lambda tile: bool(tile.get("crop_at_tile")))
    after_plant = controller.client.observe(expected_player=player)

    controller.client.raw("grow_crop", 20, *target, idempotent=False)
    time.sleep(0.5)
    parsnips_before = _quantity(after_plant, "Parsnip")
    controller.client.interact()
    harvested_observation = controller.wait_until(
        lambda observation: _quantity(observation, "Parsnip") > parsnips_before,
        description="harvested parsnip in inventory",
    )
    harvested_tile = _tile(controller.client, target)
    if harvested_tile.get("crop_at_tile"):
        raise RuntimeError("Harvested crop remains on its tile")

    return {
        "status": (
            "passed"
            if blocked_was_respected and relative_succeeded and step_succeeded
            else "farming_passed_with_movement_issues"
        ),
        "save_id": save_id,
        "player": player,
        "date": expected_date.model_dump(),
        "movement": {
            "origin": list(origin),
            "work_origin": list(work_origin),
            "relative_destination": list(_position(relative_position)),
            "relative_succeeded": relative_succeeded,
            "step_target": list(step_target),
            "step_destination": list(_position(step_position)),
            "step_succeeded": step_succeeded,
            "relative_response": relative.payload,
            "step_response": step.payload,
            "blocked_target": list(blocked_target),
            "blocked_response": blocked.payload,
            "blocked_finish": list(_position(blocked_observation)),
            "collision_respected": blocked_was_respected,
        },
        "farming": {
            "target": list(target),
            "target_before": target_before,
            "hoed_tile": hoed,
            "planted_tile": planted,
            "harvested_tile": harvested_tile,
            "hoe_stamina_used": before_hoe.player.stamina - after_hoe.player.stamina,
            "watering_stamina_used": before_water.player.stamina - after_water.player.stamina,
            "seed_slot": seed_slot,
            "seeds_before_plant": _quantity(with_seeds, "Parsnip Seeds"),
            "seeds_after_plant": _quantity(after_plant, "Parsnip Seeds"),
            "parsnips_harvested": _quantity(harvested_observation, "Parsnip") - parsnips_before,
        },
    }


def _position(observation: Observation) -> tuple[int, int]:
    return observation.player.position.x, observation.player.position.y


def _quantity(observation: Observation, name: str) -> int:
    return sum(item.quantity or 0 for item in observation.player.inventory if item.name == name)


def _slot(observation: Observation, name: str) -> int:
    for index, item in enumerate(observation.player.inventory):
        if item.name == name:
            return index
    raise RuntimeError(f"Inventory item not found: {name}")


def _current_item(observation: Observation) -> str | None:
    current = observation.player.model_extra.get("CurrentInventory", {})
    return current.get("CurrentItem") if isinstance(current, dict) else None


def _tile(client: StarDojoClient, point: tuple[int, int]) -> dict[str, Any]:
    value = json.loads(client.raw("get_tile_info", *point, idempotent=True).payload)
    if not isinstance(value, dict):
        raise RuntimeError(f"Unexpected tile response for {point}")
    return value


def _wait_tile(
    client: StarDojoClient,
    point: tuple[int, int],
    predicate: Callable[[dict[str, Any]], bool],
    timeout: float = 10,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        tile = _tile(client, point)
        if predicate(tile):
            return tile
        time.sleep(0.25)
    raise RuntimeError(f"Tile {point} did not reach expected state")


def _wait_position(controller: EnvironmentController, point: tuple[int, int]) -> Observation:
    return controller.wait_until(
        lambda observation: _position(observation) == point,
        description=f"position {point}",
    )


def _return_to(controller: EnvironmentController, point: tuple[int, int]) -> None:
    result = controller.client.move(*point)
    if result.payload != "True":
        raise RuntimeError(f"Could not return to working position {point}")
    _wait_position(controller, point)


def _wait_item(controller: EnvironmentController, name: str) -> Observation:
    return controller.wait_until(
        lambda observation: _current_item(observation) == name,
        description=f"selected item {name}",
    )


def _wait_facing(controller: EnvironmentController, direction: int) -> Observation:
    return controller.wait_until(
        lambda observation: observation.player.facing_direction == direction,
        description=f"facing direction {direction}",
    )


def _facing_direction(origin: tuple[int, int], target: tuple[int, int]) -> int:
    delta = target[0] - origin[0], target[1] - origin[1]
    directions = {(0, -1): 0, (1, 0): 1, (0, 1): 2, (-1, 0): 3}
    if delta not in directions:
        raise ValueError("Farming target must be adjacent to origin")
    return directions[delta]


def _step_direction(origin: tuple[int, int], target: tuple[int, int]) -> int:
    delta = target[0] - origin[0], target[1] - origin[1]
    directions = {(0, -1): 1, (1, 0): 2, (0, 1): 3, (-1, 0): 4}
    if delta not in directions:
        raise ValueError("Step target must be adjacent to origin")
    return directions[delta]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--save-id", required=True)
    parser.add_argument("--player", required=True)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--season", required=True)
    parser.add_argument("--day", type=int, required=True)
    parser.add_argument("--origin-x", type=int, required=True)
    parser.add_argument("--origin-y", type=int, required=True)
    parser.add_argument("--target-x", type=int, required=True)
    parser.add_argument("--target-y", type=int, required=True)
    parser.add_argument("--work-origin-x", type=int, required=True)
    parser.add_argument("--work-origin-y", type=int, required=True)
    parser.add_argument("--blocked-x", type=int, required=True)
    parser.add_argument("--blocked-y", type=int, required=True)
    parser.add_argument("--step-target-x", type=int, required=True)
    parser.add_argument("--step-target-y", type=int, required=True)
    parser.add_argument("--port", type=int, default=10783)
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    client = StarDojoClient(SocketTransport(port=args.port, timeout=30))
    report = run_farming_smoke(
        EnvironmentController(client, args.player, timeout=args.timeout, poll_interval=0.25),
        save_id=args.save_id,
        player=args.player,
        expected_date=GameDate(year=args.year, season=args.season, day=args.day),
        origin=(args.origin_x, args.origin_y),
        work_origin=(args.work_origin_x, args.work_origin_y),
        target=(args.target_x, args.target_y),
        step_target=(args.step_target_x, args.step_target_y),
        blocked_target=(args.blocked_x, args.blocked_y),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "output": str(args.output)}))


if __name__ == "__main__":
    main()
