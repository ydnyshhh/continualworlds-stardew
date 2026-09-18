from pathlib import Path

import pytest

from prime_stardew.agent import (
    AgentSessionState, ContextInputs, DecisionError, PrimeSession, ScriptedProvider,
)
from prime_stardew.env.models import GameDate
from prime_stardew.experiments.config import (
    ExperimentBudget, FixtureConfig, ProviderConfig, RunConfig,
)
from prime_stardew.experiments.provenance import CodeProvenance, RunProvenance, RuntimeProvenance
from prime_stardew.experiments.runner import ExperimentRunner, RunnerError


PROVENANCE = RunProvenance(
    code=CodeProvenance(revision="m5-test", dirty=False, dirty_patch_sha256="0" * 64),
    runtime=RuntimeProvenance(
        game_version="1.6.15", smapi_version="4.5.2", stardojo_version="1.0.0-patched",
        stardojo_dll_sha256="1" * 64, python_version="3.13", platform="test",
    ),
)


def _runner(tmp_path: Path, *, calls: int = 3) -> ExperimentRunner:
    config = RunConfig(
        suite="m5", condition="agent", seed=5, observation_mode="replay",
        fixture=FixtureConfig(
            save_id="Fixture_1", player="PrimeStardewSmoke",
            starting_date=GameDate(year=1, season="spring", day=8),
        ),
        tasks=("atomic",),
        provider=ProviderConfig(provider="configured", model="configured-model", route="fallback"),
        budget=ExperimentBudget(
            max_actions=20, max_game_days=1, max_model_calls=calls,
            max_input_tokens=10000, max_output_tokens=1000, max_cost_usd=1,
            max_wall_seconds=60,
        ),
    )
    runner = ExperimentRunner(tmp_path / "runs", config, PROVENANCE)
    runner.start()
    return runner


class Pauser:
    def __init__(self) -> None:
        self.events: list[str] = []

    def pause(self):
        self.events.append("pause")

    def resume(self):
        self.events.append("resume")


VALID = '{"schema_version":1,"actions":[{"name":"turn","arguments":[1]}],"goal_updates":["face east"],"memory_notes":[],"rationale":"clear"}'


def test_decision_repairs_once_pauses_game_and_records_actual_route(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    provider = ScriptedProvider(["bad json", VALID], provider="actual", model="actual-model")
    pauser = Pauser()
    session = PrimeSession(runner, provider, pause_controller=pauser)

    decision = session.decide(ContextInputs(objective="move", observation="clear east tile"))

    assert decision.actions[0].name == "turn"
    assert pauser.events == ["pause", "resume", "pause", "resume"]
    assert len(provider.requests) == 2
    assert provider.requests[1].repair is True
    calls = [r for r in runner.events.iter_records() if r.event_type == "model_call_completed"]
    assert [(r.payload["provider"], r.payload["model"], r.payload["route"]) for r in calls] == [
        ("actual", "actual-model", "deterministic-replay"),
        ("actual", "actual-model", "deterministic-replay"),
    ]


def test_second_invalid_decision_fails_visibly_and_still_resumes(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    pauser = Pauser()
    session = PrimeSession(runner, ScriptedProvider(["bad", "also bad"]), pause_controller=pauser)

    with pytest.raises(DecisionError, match="one repair"):
        session.decide(ContextInputs(objective="move", observation="clear"))

    assert pauser.events == ["pause", "resume", "pause", "resume"]
    assert any(r.event_type == "error_recorded" for r in runner.events.iter_records())


def test_disallowed_action_is_repaired_before_execution(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    bad = VALID.replace('"turn"', '"debug_warp"')
    provider = ScriptedProvider([bad, VALID])
    session = PrimeSession(runner, provider)

    decision = session.decide(ContextInputs(
        objective="turn", observation="clear", allowed_actions=("turn",),
    ))

    assert decision.actions[0].name == "turn"
    assert provider.requests[1].repair is True


def test_invalid_action_arguments_are_repaired_before_execution(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    bad = '{"schema_version":1,"actions":[{"name":"choose_item","arguments":["Watering Can"]}],"goal_updates":[],"memory_notes":[],"rationale":"select can"}'
    fixed = bad.replace('"Watering Can"', "1")
    provider = ScriptedProvider([bad, fixed])
    session = PrimeSession(runner, provider)

    decision = session.decide(ContextInputs(
        objective="water", observation='{"inventory_slots":[{"slot":1,"name":"Watering Can"}]}',
        allowed_actions=("choose_item",),
    ))

    assert decision.actions[0].arguments == (1,)
    assert provider.requests[1].repair is True


def test_checkpoint_state_preserves_goals_without_transcript(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    session = PrimeSession(runner, ScriptedProvider([VALID]))
    session.decide(ContextInputs(objective="move", observation="clear"))
    state = session.checkpoint_state()

    restored = PrimeSession(runner, ScriptedProvider([]), state=AgentSessionState())
    restored.restore_state(state)

    assert restored.state.active_goals == ("face east",)
    assert restored.state.decision_count == 1
    assert set(state) == {"schema_version", "active_goals", "decision_count", "last_context_sha256"}


def test_model_call_budget_stops_before_provider_invocation(tmp_path: Path) -> None:
    runner = _runner(tmp_path, calls=1)
    provider = ScriptedProvider([VALID, VALID])
    session = PrimeSession(runner, provider)
    session.decide(ContextInputs(objective="move", observation="clear"))

    with pytest.raises(RunnerError, match="Model-call budget"):
        session.decide(ContextInputs(objective="move again", observation="clear"))

    assert len(provider.requests) == 1


def test_resume_runs_even_when_provider_raises(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    pauser = Pauser()
    session = PrimeSession(runner, ScriptedProvider([]), pause_controller=pauser)
    with pytest.raises(Exception, match="no response"):
        session.decide(ContextInputs(objective="move", observation="clear"))
    assert pauser.events == ["pause", "resume"]
