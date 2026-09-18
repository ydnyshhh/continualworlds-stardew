import json
from pathlib import Path

import pytest

from prime_stardew.env.client import StarDojoClient
from prime_stardew.env.lifecycle import EnvironmentController
from prime_stardew.env.transport import ReplayTransport
from prime_stardew.tasks import ActionCommand, LiveTaskHarness, TaskKind, load_atomic_fixtures


OBSERVATION = Path(__file__).parent / "fixtures" / "observation-minimal.json"


def _observation(*, x: int, y: int, facing: int) -> str:
    payload = json.loads(OBSERVATION.read_text(encoding="utf-8"))
    payload["Player"]["Position"] = {"X": x, "Y": y}
    payload["Player"]["FacingDirection"] = facing
    payload["GameState"]["DayOfMonth"] = 8
    return json.dumps(payload)


def test_live_harness_records_typed_guarded_actions() -> None:
    fixture = next(item for item in load_atomic_fixtures() if item.kind is TaskKind.TURN_MOVE)
    replay = ReplayTransport({
        "observe_v2%2": [_observation(x=62, y=17, facing=2), _observation(x=63, y=17, facing=1)],
        "observe_v2%1": [_observation(x=62, y=17, facing=1), _observation(x=63, y=17, facing=1)],
        "get_tile_info%63%17": [json.dumps({"position": [63, 17]})] * 3,
        "turn%1": ["Message received"],
        "move_step%2": ["True"],
    })
    controller = EnvironmentController(
        StarDojoClient(replay), fixture.expected_player, poll_interval=0, sleeper=lambda _: None
    )
    harness = LiveTaskHarness(controller, fixture, settle_seconds=0, sleeper=lambda _: None)

    trajectory = harness.run((ActionCommand(name="turn", arguments=(1,)),
                              ActionCommand(name="move_step", arguments=(2,))))

    assert trajectory.before.position == (62, 17)
    assert trajectory.after.position == (63, 17)
    assert [action.name for action in trajectory.actions] == ["turn", "move_step"]
    assert all(not action.privileged for action in trajectory.actions)


def test_live_harness_rejects_debug_and_budget_overflow_before_dispatch() -> None:
    fixture = next(item for item in load_atomic_fixtures() if item.kind is TaskKind.TURN_MOVE)
    replay = ReplayTransport({
        "observe_v2%2": [_observation(x=62, y=17, facing=2)],
        "get_tile_info%63%17": [json.dumps({"position": [63, 17]})],
    })
    harness = LiveTaskHarness(
        EnvironmentController(StarDojoClient(replay), fixture.expected_player),
        fixture,
        settle_seconds=0,
    )
    harness.begin()

    with pytest.raises(ValueError, match="not allowed"):
        harness.perform(ActionCommand(name="grow_crop", arguments=(20, 65, 15)))
    assert replay.requests == ["observe_v2%2", "get_tile_info%63%17"]
