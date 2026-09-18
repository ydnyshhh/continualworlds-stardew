import json
from pathlib import Path

from prime_stardew.env.client import StarDojoClient
from prime_stardew.env.lifecycle import EnvironmentController
from prime_stardew.env.models import GameDate
from prime_stardew.env.transport import ReplayTransport
from prime_stardew.smoke import run_smoke


FIXTURE = Path(__file__).parent / "fixtures" / "observation-minimal.json"


def observation(direction: int) -> str:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["Player"]["FacingDirection"] = direction
    return json.dumps(payload)


def test_smoke_changes_and_restores_direction() -> None:
    replay = ReplayTransport(
        {
            "load_game_record%PrimeStardewSmoke_406041616": ["True"],
            "observe_v2%1": [observation(2), observation(3), observation(2)],
            "pause": ["Message received"],
            "resume": ["Message received"],
            "turn%3": ["Message received"],
            "turn%2": ["Message received"],
        }
    )
    controller = EnvironmentController(
        StarDojoClient(replay),
        "PrimeStardewSmoke",
        poll_interval=0,
    )

    report = run_smoke(
        controller,
        "PrimeStardewSmoke_406041616",
        GameDate(year=1, season="spring", day=6),
        screenshot_width=1,
        screenshot_height=1,
    )

    assert report["status"] == "passed"
    assert report["facing_direction"] == {"before": 2, "changed": 3, "restored": 2}
    assert report["screenshot"]["bytes"] == 4
