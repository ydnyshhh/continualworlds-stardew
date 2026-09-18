import json
from pathlib import Path

from prime_stardew.env.client import StarDojoClient
from prime_stardew.env.lifecycle import EnvironmentController
from prime_stardew.env.models import GameDate
from prime_stardew.env.transport import ReplayTransport
from prime_stardew.soak import run_soak


FIXTURE = Path(__file__).parent / "fixtures" / "observation-minimal.json"


def observation(direction: int) -> str:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["Player"]["FacingDirection"] = direction
    return json.dumps(payload)


def test_soak_checks_each_cycle_and_restores_direction() -> None:
    replay = ReplayTransport(
        {
            "load_game_record%fixture": ["True"],
            "observe_v2%1": [observation(2), observation(3), observation(0), observation(1), observation(2)],
            "turn%3": ["Message received"],
            "turn%0": ["Message received"],
            "turn%1": ["Message received"],
            "turn%2": ["Message received"],
        }
    )
    controller = EnvironmentController(
        StarDojoClient(replay),
        "PrimeStardewSmoke",
        poll_interval=0,
    )

    report = run_soak(
        controller,
        "fixture",
        GameDate(year=1, season="spring", day=6),
        cycles=3,
    )

    assert report["cycles"] == 3
    assert report["restored_direction"] == 2
