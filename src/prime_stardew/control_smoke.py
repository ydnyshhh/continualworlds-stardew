"""Exercise movement, inventory selection, axe use, tree removal, and wood pickup."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from .env.client import StarDojoClient
from .env.lifecycle import EnvironmentController
from .env.models import GameDate, Observation
from .env.transport import SocketTransport


def run_control_smoke(
    controller: EnvironmentController,
    *,
    save_id: str,
    player: str,
    expected_date: GameDate,
    approach: tuple[int, int],
    target: tuple[int, int],
    axe_slot: int = 0,
    max_swings: int = 20,
) -> dict[str, Any]:
    initial = controller.load(save_id, expected_date=expected_date)
    target_before = _tile(controller.client, target)
    if "Tree" not in str(target_before.get("terrain_at_tile", "")):
        raise RuntimeError(f"Expected a tree at {target}; found {target_before}")

    move_result = controller.client.move(*approach)
    if move_result.payload != "True":
        raise RuntimeError(f"Pathfinding failed to approach {target}: {move_result.payload}")
    approached = controller.wait_until(
        lambda observation: _position(observation) == approach,
        description=f"arrival at {approach}",
    )

    controller.client.choose_item(axe_slot)
    selected = controller.wait_until(
        lambda observation: _current_item(observation) == "Axe",
        description="axe selection",
    )
    direction = _adjacent_direction(approach, target)
    controller.client.turn(direction)
    ready = controller.wait_until(
        lambda observation: observation.player.facing_direction == direction,
        description=f"facing tree at {target}",
    )

    wood_before = _quantity(ready, "Wood")
    stamina_before = ready.player.stamina
    swings = []
    tree_removed = False
    for number in range(1, max_swings + 1):
        action = controller.client.use()
        time.sleep(0.25)
        tile = _tile(controller.client, target)
        terrain = str(tile.get("terrain_at_tile", ""))
        observation = controller.client.observe(radius=2, expected_player=player)
        swings.append(
            {
                "number": number,
                "request_id": action.request_id,
                "stamina": observation.player.stamina,
                "terrain": terrain,
                "wood": _quantity(observation, "Wood"),
            }
        )
        if "Tree" not in terrain:
            tree_removed = True
            break
    if not tree_removed:
        raise RuntimeError(f"Tree still present after {max_swings} axe swings")

    # Walk over and around the former tree tile so magnetic debris pickup completes.
    collection_path = [target, approach]
    for point in collection_path:
        result = controller.client.move(*point)
        if result.payload != "True":
            raise RuntimeError(f"Collection movement failed for {point}: {result.payload}")
        controller.wait_until(
            lambda observation, expected=point: _position(observation) == expected,
            description=f"collection movement to {point}",
        )
    time.sleep(2)
    final = controller.client.observe(radius=2, expected_player=player)
    wood_after = _quantity(final, "Wood")
    if wood_after <= wood_before:
        raise RuntimeError(f"Tree was removed but wood was not collected: {wood_before} -> {wood_after}")

    return {
        "status": "passed",
        "save_id": save_id,
        "player": player,
        "date": expected_date.model_dump(),
        "movement": {
            "start": _position(initial),
            "approach": _position(approached),
            "collection_path": [list(point) for point in collection_path],
            "finish": _position(final),
            "pathfinding_response": move_result.payload,
        },
        "tool": {
            "slot": axe_slot,
            "selected_item": _current_item(selected),
            "target": list(target),
            "target_before": target_before,
            "target_after": _tile(controller.client, target),
            "swings": swings,
            "tree_removed": tree_removed,
        },
        "resources": {
            "stamina_before": stamina_before,
            "stamina_after": final.player.stamina,
            "stamina_used": stamina_before - final.player.stamina,
            "wood_before": wood_before,
            "wood_after": wood_after,
            "wood_collected": wood_after - wood_before,
        },
    }


def _position(observation: Observation) -> tuple[int, int]:
    return observation.player.position.x, observation.player.position.y


def _current_item(observation: Observation) -> str | None:
    current = observation.player.model_extra.get("CurrentInventory", {})
    return current.get("CurrentItem") if isinstance(current, dict) else None


def _quantity(observation: Observation, name: str) -> int:
    return sum(item.quantity or 0 for item in observation.player.inventory if item.name == name)


def _tile(client: StarDojoClient, point: tuple[int, int]) -> dict[str, Any]:
    response = client.raw("get_tile_info", *point, idempotent=True)
    value = json.loads(response.payload)
    if not isinstance(value, dict):
        raise RuntimeError(f"Unexpected tile response for {point}")
    return value


def _adjacent_direction(origin: tuple[int, int], target: tuple[int, int]) -> int:
    delta = target[0] - origin[0], target[1] - origin[1]
    directions = {(0, -1): 0, (1, 0): 1, (0, 1): 2, (-1, 0): 3}
    if delta not in directions:
        raise ValueError(f"Approach {origin} is not adjacent to target {target}")
    return directions[delta]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--save-id", required=True)
    parser.add_argument("--player", required=True)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--season", required=True)
    parser.add_argument("--day", type=int, required=True)
    parser.add_argument("--approach-x", type=int, required=True)
    parser.add_argument("--approach-y", type=int, required=True)
    parser.add_argument("--target-x", type=int, required=True)
    parser.add_argument("--target-y", type=int, required=True)
    parser.add_argument("--axe-slot", type=int, default=0)
    parser.add_argument("--max-swings", type=int, default=20)
    parser.add_argument("--port", type=int, default=10783)
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    client = StarDojoClient(SocketTransport(port=args.port, timeout=30))
    report = run_control_smoke(
        EnvironmentController(client, args.player, timeout=args.timeout, poll_interval=0.25),
        save_id=args.save_id,
        player=args.player,
        expected_date=GameDate(year=args.year, season=args.season, day=args.day),
        approach=(args.approach_x, args.approach_y),
        target=(args.target_x, args.target_y),
        axe_slot=args.axe_slot,
        max_swings=args.max_swings,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "passed", "output": str(args.output)}))


if __name__ == "__main__":
    main()
