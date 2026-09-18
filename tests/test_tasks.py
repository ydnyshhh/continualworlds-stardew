import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from prime_stardew.env.models import Observation, TileInfo
from prime_stardew.tasks import (
    ItemCount,
    NormalizedState,
    PrimitiveAction,
    TaskKind,
    TaskTrajectory,
    load_atomic_fixtures,
    normalize_state,
    score_trajectory,
)
from prime_stardew.tasks.models import NormalizedCrop, NormalizedTile


FIXTURE_OBSERVATION = Path(__file__).parent / "fixtures" / "observation-minimal.json"
FIXTURES = {fixture.kind: fixture for fixture in load_atomic_fixtures()}


def _state(
    kind: TaskKind,
    *,
    after: bool,
    target: NormalizedTile | None = None,
    inventory: tuple[ItemCount, ...] = (),
    chest: tuple[ItemCount, ...] = (),
    menu_type: str = "No Menu",
) -> NormalizedState:
    fixture = FIXTURES[kind]
    return NormalizedState(
        player=fixture.expected_player,
        date=fixture.expected_date,
        time=610 if after else 600,
        location=fixture.location,
        position=fixture.target if after and kind is TaskKind.TURN_MOVE else fixture.origin,
        facing=fixture.expected_facing if after and fixture.expected_facing is not None else 2,
        stamina=268 if after else 270,
        money=50000,
        inventory=inventory,
        target=target,
        menu_type=menu_type,
        chest=chest,
    )


def _run(
    kind: TaskKind,
    before: NormalizedState,
    after: NormalizedState,
    *action_names: str,
) -> TaskTrajectory:
    return TaskTrajectory(
        fixture=FIXTURES[kind],
        before=before,
        after=after,
        actions=tuple(
            PrimitiveAction(sequence=index, name=name)
            for index, name in enumerate(action_names, start=1)
        ),
    )


def test_fixture_catalog_is_versioned_unique_and_immutable() -> None:
    fixtures = load_atomic_fixtures()

    assert len(fixtures) == 5
    assert {fixture.kind for fixture in fixtures} == set(TaskKind)
    assert all(fixture.schema_version == 1 for fixture in fixtures)
    with pytest.raises(ValidationError, match="frozen"):
        fixtures[0].location = "Town"  # type: ignore[misc]


def test_normalization_merges_global_crop_and_aggregates_items() -> None:
    payload = json.loads(FIXTURE_OBSERVATION.read_text(encoding="utf-8"))
    payload["Player"]["Inventory"] = [
        {"Name": "Wood", "Quantity": 2},
        {"Name": "Wood", "Quantity": 3},
    ]
    payload["Crops"] = [{
        "id": "472",
        "position": "64, 14",
        "isWatered": True,
        "isDead": False,
        "current_phase": 2,
    }]
    payload["CurrentMenuData"] = {
        "type": "Chest",
        "ItemsInChest": [{"Name": "Stone", "Quantity": 4}],
    }
    observation = Observation.model_validate(payload)
    tile = TileInfo.model_validate({
        "position": [64, 14],
        "terrain_at_tile": "StardewValley.TerrainFeatures.HoeDirt",
        "crop_at_tile": {"seed_id": "472", "index_harvest": "24"},
    })

    state = normalize_state(observation, target_tile=tile)

    assert state.inventory_quantity("Wood") == 5
    assert state.chest_quantity("Stone") == 4
    assert state.target is not None
    assert state.target.crop == NormalizedCrop(
        seed_id="472", harvest_id="24", watered=True, dead=False, phase=2
    )


def test_turn_move_scores_exact_destination_and_facing() -> None:
    before = _state(TaskKind.TURN_MOVE, after=False)
    after = _state(TaskKind.TURN_MOVE, after=True)

    result = score_trajectory(_run(TaskKind.TURN_MOVE, before, after, "turn", "move_step"))

    assert result.success
    assert result.score == 1


def test_water_crop_scores_state_transition() -> None:
    fixture = FIXTURES[TaskKind.WATER_CROP]
    before_tile = NormalizedTile(
        position=fixture.target,
        terrain="StardewValley.TerrainFeatures.HoeDirt",
        crop=NormalizedCrop(seed_id="472", harvest_id="24", watered=False, phase=1),
    )
    after_tile = before_tile.model_copy(
        update={"crop": before_tile.crop.model_copy(update={"watered": True})}
    )

    result = score_trajectory(_run(
        TaskKind.WATER_CROP,
        _state(TaskKind.WATER_CROP, after=False, target=before_tile),
        _state(TaskKind.WATER_CROP, after=True, target=after_tile),
        "choose_item", "turn", "use",
    ))

    assert result.success


def test_clear_debris_requires_removal_and_resource_gain() -> None:
    fixture = FIXTURES[TaskKind.CLEAR_DEBRIS]
    before_tile = NormalizedTile(position=fixture.target, object_name="Twig")
    after_tile = NormalizedTile(position=fixture.target)
    result = score_trajectory(_run(
        TaskKind.CLEAR_DEBRIS,
        _state(TaskKind.CLEAR_DEBRIS, after=False, target=before_tile,
               inventory=(ItemCount(name="Wood", quantity=2),)),
        _state(TaskKind.CLEAR_DEBRIS, after=True, target=after_tile,
               inventory=(ItemCount(name="Wood", quantity=3),)),
        "choose_item", "turn", "use",
    ))

    assert result.success


def test_harvest_requires_crop_removal_and_inventory_gain() -> None:
    fixture = FIXTURES[TaskKind.HARVEST_CROP]
    before_tile = NormalizedTile(
        position=fixture.target,
        crop=NormalizedCrop(seed_id="472", harvest_id="24", watered=True, phase=4),
    )
    after_tile = NormalizedTile(position=fixture.target)
    result = score_trajectory(_run(
        TaskKind.HARVEST_CROP,
        _state(TaskKind.HARVEST_CROP, after=False, target=before_tile),
        _state(TaskKind.HARVEST_CROP, after=True, target=after_tile,
               inventory=(ItemCount(name="Parsnip", quantity=1),)),
        "turn", "interact",
    ))

    assert result.success


def test_chest_transfer_requires_mirrored_quantity_delta() -> None:
    result = score_trajectory(_run(
        TaskKind.CHEST_TRANSFER,
        _state(TaskKind.CHEST_TRANSFER, after=False, menu_type="Chest",
               inventory=(ItemCount(name="Wood", quantity=3),),
               chest=(ItemCount(name="Wood", quantity=2),)),
        _state(TaskKind.CHEST_TRANSFER, after=True, menu_type="Chest",
               inventory=(ItemCount(name="Wood", quantity=2),),
               chest=(ItemCount(name="Wood", quantity=3),)),
        "put_to_chest",
    ))

    assert result.success


def test_privileged_disallowed_and_over_budget_actions_fail_explicitly() -> None:
    fixture = FIXTURES[TaskKind.TURN_MOVE]
    before = _state(TaskKind.TURN_MOVE, after=False)
    after = _state(TaskKind.TURN_MOVE, after=True)
    actions = (
        PrimitiveAction(sequence=1, name="move_step", privileged=True),
        PrimitiveAction(sequence=2, name="move_step"),
        PrimitiveAction(sequence=3, name="debug_warp"),
    )

    result = score_trajectory(TaskTrajectory(
        fixture=fixture,
        before=before,
        after=after,
        actions=actions,
    ))

    assert not result.success
    assert {"action_budget", "allowed_actions", "no_privileged_actions"} <= set(
        result.failure_reasons
    )


def test_action_sequence_must_be_contiguous() -> None:
    with pytest.raises(ValidationError, match="contiguous"):
        TaskTrajectory(
            fixture=FIXTURES[TaskKind.TURN_MOVE],
            before=_state(TaskKind.TURN_MOVE, after=False),
            after=_state(TaskKind.TURN_MOVE, after=True),
            actions=(PrimitiveAction(sequence=2, name="move"),),
        )
