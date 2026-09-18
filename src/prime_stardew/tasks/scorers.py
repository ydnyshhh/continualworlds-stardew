"""Deterministic, side-effect-free scorers for M3 atomic tasks."""

from __future__ import annotations

from collections.abc import Callable

from .models import (
    ScoreComponent,
    TaskKind,
    TaskScore,
    TaskTrajectory,
    TransferDirection,
)


def score_trajectory(trajectory: TaskTrajectory) -> TaskScore:
    fixture = trajectory.fixture
    components = [
        _check("expected_player_before", trajectory.before.player == fixture.expected_player,
               f"observed={trajectory.before.player!r}"),
        _check("expected_player_after", trajectory.after.player == fixture.expected_player,
               f"observed={trajectory.after.player!r}"),
        _check("date_unchanged", trajectory.before.date == fixture.expected_date == trajectory.after.date,
               f"before={trajectory.before.date}; after={trajectory.after.date}"),
        _check("location_unchanged", trajectory.before.location == fixture.location == trajectory.after.location,
               f"before={trajectory.before.location!r}; after={trajectory.after.location!r}"),
        _check("origin_matches", trajectory.before.position == fixture.origin,
               f"observed={trajectory.before.position}"),
        _check("action_budget", len(trajectory.actions) <= fixture.budget.max_actions,
               f"used={len(trajectory.actions)}; max={fixture.budget.max_actions}"),
        _check("game_time_budget", _game_minutes(trajectory.before.time, trajectory.after.time)
               <= fixture.budget.max_game_minutes,
               f"used={_game_minutes(trajectory.before.time, trajectory.after.time)}; "
               f"max={fixture.budget.max_game_minutes}"),
        _check("allowed_actions", all(action.name in fixture.allowed_actions for action in trajectory.actions),
               f"observed={tuple(action.name for action in trajectory.actions)}"),
        _check("no_privileged_actions", not any(action.privileged for action in trajectory.actions),
               "fixture setup must finish before the scored action sequence"),
        _check("actions_succeeded", all(action.succeeded for action in trajectory.actions),
               "every primitive action reported success"),
    ]
    scorer: Callable[[TaskTrajectory], list[ScoreComponent]] = {
        TaskKind.TURN_MOVE: _score_turn_move,
        TaskKind.WATER_CROP: _score_water_crop,
        TaskKind.CLEAR_DEBRIS: _score_clear_debris,
        TaskKind.HARVEST_CROP: _score_harvest_crop,
        TaskKind.CHEST_TRANSFER: _score_chest_transfer,
    }[fixture.kind]
    components.extend(scorer(trajectory))
    passed = sum(component.passed for component in components)
    failures = tuple(component.name for component in components if not component.passed)
    return TaskScore(
        fixture_id=fixture.fixture_id,
        success=not failures,
        score=passed / len(components),
        components=tuple(components),
        failure_reasons=failures,
    )


def _score_turn_move(run: TaskTrajectory) -> list[ScoreComponent]:
    fixture = run.fixture
    result = [
        _check("destination_reached", run.after.position == fixture.target,
               f"observed={run.after.position}; expected={fixture.target}"),
        _check("movement_recorded", any(action.name in {"move", "move_relative", "move_step"}
                                         for action in run.actions),
               "trajectory contains a movement primitive"),
    ]
    if fixture.expected_facing is not None:
        result.append(_check("facing_reached", run.after.facing == fixture.expected_facing,
                             f"observed={run.after.facing}; expected={fixture.expected_facing}"))
    return result


def _score_water_crop(run: TaskTrajectory) -> list[ScoreComponent]:
    before = run.before.target.crop if run.before.target else None
    after = run.after.target.crop if run.after.target else None
    return [
        _check("target_tile_matches", _target_matches(run), _target_evidence(run)),
        _check("crop_present_before", before is not None, f"crop={before}"),
        _check("crop_preserved", before is not None and after is not None
               and before.seed_id == after.seed_id, f"before={before}; after={after}"),
        _check("crop_watered", after is not None and after.watered is True,
               f"before={getattr(before, 'watered', None)}; after={getattr(after, 'watered', None)}"),
        _check("tool_use_recorded", any(action.name == "use" for action in run.actions),
               "trajectory contains use"),
    ]


def _score_clear_debris(run: TaskTrajectory) -> list[ScoreComponent]:
    before = run.before.target
    after = run.after.target
    item = run.fixture.item_name or ""
    before_obstacle = before.object_name or before.debris_name if before else None
    after_obstacle = after.object_name or after.debris_name if after else None
    gain = run.after.inventory_quantity(item) - run.before.inventory_quantity(item)
    return [
        _check("target_tile_matches", _target_matches(run), _target_evidence(run)),
        _check("debris_present_before", bool(before_obstacle), f"obstacle={before_obstacle!r}"),
        _check("debris_removed", not after_obstacle, f"remaining={after_obstacle!r}"),
        _check("resource_collected", gain >= run.fixture.quantity,
               f"item={item!r}; gained={gain}; required={run.fixture.quantity}"),
        _check("tool_use_recorded", any(action.name == "use" for action in run.actions),
               "trajectory contains use"),
    ]


def _score_harvest_crop(run: TaskTrajectory) -> list[ScoreComponent]:
    before = run.before.target.crop if run.before.target else None
    after = run.after.target.crop if run.after.target else None
    item = run.fixture.item_name or ""
    gain = run.after.inventory_quantity(item) - run.before.inventory_quantity(item)
    return [
        _check("target_tile_matches", _target_matches(run), _target_evidence(run)),
        _check("crop_present_before", before is not None, f"crop={before}"),
        _check("crop_removed", after is None, f"remaining={after}"),
        _check("harvest_collected", gain >= run.fixture.quantity,
               f"item={item!r}; gained={gain}; required={run.fixture.quantity}"),
        _check("interact_recorded", any(action.name == "interact" for action in run.actions),
               "trajectory contains interact"),
    ]


def _score_chest_transfer(run: TaskTrajectory) -> list[ScoreComponent]:
    item = run.fixture.item_name or ""
    player_delta = run.after.inventory_quantity(item) - run.before.inventory_quantity(item)
    chest_delta = run.after.chest_quantity(item) - run.before.chest_quantity(item)
    quantity = run.fixture.quantity
    if run.fixture.transfer_direction is TransferDirection.PLAYER_TO_CHEST:
        expected_player, expected_chest, action_name = -quantity, quantity, "put_to_chest"
    else:
        expected_player, expected_chest, action_name = quantity, -quantity, "take_from_chest"
    return [
        _check("chest_open_before", run.before.menu_type == "Chest",
               f"menu={run.before.menu_type!r}"),
        _check("chest_open_after", run.after.menu_type == "Chest",
               f"menu={run.after.menu_type!r}"),
        _check("player_inventory_delta", player_delta == expected_player,
               f"item={item!r}; delta={player_delta}; expected={expected_player}"),
        _check("chest_inventory_delta", chest_delta == expected_chest,
               f"item={item!r}; delta={chest_delta}; expected={expected_chest}"),
        _check("transfer_conserved", player_delta + chest_delta == 0,
               f"player_delta={player_delta}; chest_delta={chest_delta}"),
        _check("transfer_recorded", any(action.name == action_name for action in run.actions),
               f"required action={action_name}"),
    ]


def _target_matches(run: TaskTrajectory) -> bool:
    return (
        run.before.target is not None
        and run.after.target is not None
        and run.before.target.position == run.fixture.target
        and run.after.target.position == run.fixture.target
    )


def _target_evidence(run: TaskTrajectory) -> str:
    return f"before={getattr(run.before.target, 'position', None)}; " \
           f"after={getattr(run.after.target, 'position', None)}; expected={run.fixture.target}"


def _game_minutes(before: int, after: int) -> int:
    def minutes(value: int) -> int:
        return (value // 100) * 60 + value % 100
    return max(0, minutes(after) - minutes(before))


def _check(name: str, passed: bool, evidence: str) -> ScoreComponent:
    return ScoreComponent(name=name, passed=passed, evidence=evidence)
