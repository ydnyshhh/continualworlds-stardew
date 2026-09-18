import json
from pathlib import Path

import pytest

from prime_stardew.env.client import StarDojoClient
from prime_stardew.env.errors import EnvironmentNotReady, MovementBlocked, MovementInvariantError
from prime_stardew.env.lifecycle import EnvironmentController
from prime_stardew.env.models import GameDate
from prime_stardew.env.transport import ReplayTransport


FIXTURE = Path(__file__).parent / "fixtures" / "observation-minimal.json"


def observation(*, day: int, season: str = "spring", year: int = 1, time: int = 600) -> str:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["GameState"].update(
        Time=time,
        DayOfMonth=day,
        Season=season,
        Year=year,
    )
    return json.dumps(payload)


def observation_at(x: int, y: int, *, location: str = "Farm") -> str:
    payload = json.loads(observation(day=6))
    payload["Player"]["Position"] = {"X": x, "Y": y}
    payload["Player"]["Location"] = location
    return json.dumps(payload)


def tile(x: int, y: int, **changes) -> str:
    payload = {
        "position": [x, y],
        "object_at_tile": "",
        "terrain_at_tile": "",
        "building_info": "",
        "crop_at_tile": None,
        "debris_at_tile": "",
        "furniture_at_tile": "",
        "exit_info": "",
        "npc_info": "",
        "placeable": True,
    }
    payload.update(changes)
    return json.dumps(payload)


def test_finish_day_polls_date_instead_of_waiting_for_event() -> None:
    replay = ReplayTransport(
        {
            "observe_v2%1": [observation(day=5), observation(day=5, time=2600), observation(day=6)],
            "sleep": ["Message received"],
        }
    )
    controller = EnvironmentController(
        StarDojoClient(replay),
        "PrimeStardewSmoke",
        poll_interval=0,
    )

    result = controller.finish_day()

    assert result.before == GameDate(year=1, season="spring", day=5)
    assert result.after == GameDate(year=1, season="spring", day=6)
    assert replay.requests == ["observe_v2%1", "sleep", "observe_v2%1", "observe_v2%1"]


@pytest.mark.parametrize(
    ("current", "expected"),
    [
        (GameDate(year=1, season="spring", day=28), GameDate(year=1, season="summer", day=1)),
        (GameDate(year=1, season="winter", day=28), GameDate(year=2, season="spring", day=1)),
    ],
)
def test_date_rollover(current: GameDate, expected: GameDate) -> None:
    assert current.next_day() == expected


def test_wait_until_times_out_with_farmer_context() -> None:
    replay = ReplayTransport({"observe_v2%1": [observation(day=5)]})
    times = iter([0.0, 1.0, 1.0])
    controller = EnvironmentController(
        StarDojoClient(replay),
        "PrimeStardewSmoke",
        timeout=0.5,
        poll_interval=0,
        monotonic=lambda: next(times),
        sleeper=lambda _: None,
    )

    with pytest.raises(EnvironmentNotReady, match="PrimeStardewSmoke"):
        controller.wait_until(lambda _: False, description="Spring 6")


def test_checked_move_preflights_and_verifies_destination() -> None:
    replay = ReplayTransport(
        {
            "observe_v2%1": [observation_at(64, 15), observation_at(63, 15)],
            "get_tile_info%63%15": [tile(63, 15)],
            "move%63%15": ["True"],
        }
    )
    result = EnvironmentController(StarDojoClient(replay), "PrimeStardewSmoke").move(63, 15)

    assert result.source == (64, 15)
    assert result.destination == (63, 15)
    assert replay.requests == [
        "observe_v2%1",
        "get_tile_info%63%15",
        "move%63%15",
        "observe_v2%1",
    ]


def test_checked_move_rejects_tree_without_sending_mutation() -> None:
    replay = ReplayTransport(
        {
            "observe_v2%1": [observation_at(55, 10)],
            "get_tile_info%54%11": [
                tile(54, 11, terrain_at_tile="StardewValley.TerrainFeatures.Tree")
            ],
        }
    )
    controller = EnvironmentController(StarDojoClient(replay), "PrimeStardewSmoke")

    with pytest.raises(MovementBlocked, match="terrain:.*Tree"):
        controller.move(54, 11)

    assert "move%54%11" not in replay.requests


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("object_at_tile", "Stone"),
        ("building_info", "Farmhouse"),
        ("furniture_at_tile", "Chair"),
        ("npc_info", "Name: Robin"),
        ("exit_info", "Town"),
    ],
)
def test_checked_move_rejects_other_occupied_destinations(field: str, value: str) -> None:
    replay = ReplayTransport(
        {
            "observe_v2%1": [observation_at(64, 15)],
            "get_tile_info%63%15": [tile(63, 15, **{field: value})],
        }
    )
    with pytest.raises(MovementBlocked):
        EnvironmentController(StarDojoClient(replay), "PrimeStardewSmoke").move(63, 15)


def test_checked_move_rejects_false_success_postcondition() -> None:
    replay = ReplayTransport(
        {
            "observe_v2%1": [observation_at(64, 15), observation_at(64, 15)],
            "get_tile_info%63%15": [tile(63, 15)],
            "move%63%15": ["True"],
        }
    )
    with pytest.raises(MovementInvariantError, match="reported success"):
        EnvironmentController(StarDojoClient(replay), "PrimeStardewSmoke").move(63, 15)


def test_relative_and_step_movements_use_same_guard() -> None:
    replay = ReplayTransport(
        {
            "observe_v2%1": [
                observation_at(55, 10),
                observation_at(54, 10),
                observation_at(54, 10),
                observation_at(54, 11),
            ],
            "get_tile_info%54%10": [tile(54, 10)],
            "get_tile_info%54%11": [tile(54, 11, terrain_at_tile="StardewValley.TerrainFeatures.Grass")],
            "move_relative%-1%0": ["True"],
            "move_step%3": ["True"],
        }
    )
    controller = EnvironmentController(StarDojoClient(replay), "PrimeStardewSmoke")

    assert controller.move_relative(-1, 0).destination == (54, 10)
    assert controller.move_step(3).destination == (54, 11)
