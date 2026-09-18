import json
from pathlib import Path

import pytest

from prime_stardew.env.client import StarDojoClient
from prime_stardew.env.errors import ObservationError, ProtocolError
from prime_stardew.env.transport import ReplayTransport


FIXTURE = Path(__file__).parent / "fixtures" / "observation-minimal.json"


def test_observe_parses_recorded_shape_and_checks_farmer() -> None:
    replay = ReplayTransport({"observe_v2%1": [FIXTURE.read_text(encoding="utf-8")]})
    client = StarDojoClient(replay)

    observation = client.observe(expected_player="PrimeStardewSmoke")

    assert observation.player.position.x == 64
    assert observation.game_state.day == 6
    assert observation.screenshot_bytes() == b"\x00" * 4
    assert replay.requests == ["observe_v2%1"]


def test_observe_refuses_unexpected_farmer() -> None:
    replay = ReplayTransport({"observe_v2%1": [FIXTURE.read_text(encoding="utf-8")]})

    with pytest.raises(ObservationError, match="Invalid observation"):
        StarDojoClient(replay).observe(expected_player="Nova")


def test_get_tile_info_validates_requested_position() -> None:
    replay = ReplayTransport(
        {
            "get_tile_info%54%11": [
                json.dumps(
                    {
                        "position": [54, 11],
                        "terrain_at_tile": "StardewValley.TerrainFeatures.Tree",
                    }
                )
            ]
        }
    )
    tile_info = StarDojoClient(replay).get_tile_info(54, 11)

    assert tile_info.position == (54, 11)
    assert tile_info.movement_blockers() == (
        "terrain:StardewValley.TerrainFeatures.Tree",
    )


def test_mutating_action_is_sent_once_and_validated() -> None:
    replay = ReplayTransport({"turn%1": ["Message received"]})

    result = StarDojoClient(replay).turn(1)

    assert result.command == "turn%1"
    assert replay.requests == ["turn%1"]


def test_movement_inventory_and_tool_commands_are_typed() -> None:
    replay = ReplayTransport(
        {
            "move%62%22": ["True"],
            "move_relative%-1%2": ["True"],
            "move_step%3": ["True"],
            "choose_item%0": ["Message received"],
            "use": ["Message received"],
            "interact": ["Message received"],
            "take_from_chest%0%2": ["Message received"],
            "put_to_chest%4%2": ["Message received"],
        }
    )
    client = StarDojoClient(replay)

    assert client.move(62, 22).payload == "True"
    client.move_relative(-1, 2)
    client.move_step(3)
    client.choose_item(0)
    client.use()
    client.interact()
    client.take_from_chest(0, 2)
    client.put_to_chest(4, 2)

    assert replay.requests == [
        "move%62%22",
        "move_relative%-1%2",
        "move_step%3",
        "choose_item%0",
        "use",
        "interact",
        "take_from_chest%0%2",
        "put_to_chest%4%2",
    ]


@pytest.mark.parametrize(
    ("call", "message"),
    [
        (lambda client: client.move(-1, 0), "non-negative"),
        (lambda client: client.move_step(0), "Step direction"),
        (lambda client: client.choose_item(36), "Inventory slot"),
        (lambda client: client.take_from_chest(-1, 1), "Chest item index"),
        (lambda client: client.put_to_chest(0, 0), "transfer quantity"),
    ],
)
def test_control_arguments_are_validated(call, message) -> None:
    with pytest.raises(ValueError, match=message):
        call(StarDojoClient(ReplayTransport({})))


def test_unknown_action_response_is_rejected() -> None:
    replay = ReplayTransport({"sleep": ["No such method"]})

    with pytest.raises(ProtocolError, match="Unexpected action response"):
        StarDojoClient(replay).sleep()


@pytest.mark.parametrize("save_id", ["", "../Nova", "name%extra", "C:\\save"])
def test_unsafe_save_ids_are_rejected(save_id: str) -> None:
    with pytest.raises(ValueError, match="Unsafe save ID"):
        StarDojoClient(ReplayTransport({})).load_save(save_id)
