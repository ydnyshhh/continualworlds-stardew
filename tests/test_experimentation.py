import json
from pathlib import Path

import pytest

from prime_stardew.agent import ScriptedProvider
from prime_stardew.experimentation import (
    AgentExperimentPolicy, ExperimentAction, ExperimentChoice, ExperimentDecision,
    ExperimentValidationError, LiveSamplingPolicy, deterministic_decision,
    execute_decision, validate_decision,
)
from prime_stardew.experimentation.models import expected_experiment_net_value
from prime_stardew.experimentation.research import (
    ExperimentCondition, build_m14_report_from_events, hidden_scenarios,
    load_m14_preregistration, run_m14_condition,
)
from prime_stardew.experiments.config import load_run_config
from prime_stardew.experiments.provenance import CodeProvenance, RunProvenance, RuntimeProvenance
from prime_stardew.experiments.runner import ExperimentRunner
from prime_stardew.memory import MemoryStatus, MemoryStore
from prime_stardew.telemetry import EventStore


PROVENANCE = RunProvenance(
    code=CodeProvenance(revision="m14-test", dirty=False, dirty_patch_sha256="0" * 64),
    runtime=RuntimeProvenance(
        game_version="1.6.15", smapi_version="4.5.2", stardojo_version="1.0.0-patched",
        stardojo_dll_sha256="1" * 64, python_version="3.13", platform="test",
    ),
)


def test_hidden_mechanics_are_excluded_and_expected_value_distinguishes_cases() -> None:
    hidden = hidden_scenarios(406041616)
    visible = tuple(scenario.observable() for scenario in hidden)
    assert all("candidate_value" not in scenario.model_dump() for scenario in visible)
    assert expected_experiment_net_value(visible[0]) == pytest.approx(300)
    assert expected_experiment_net_value(visible[1]) == pytest.approx(300)
    assert expected_experiment_net_value(visible[2]) == pytest.approx(-120)


def test_agent_policy_experiments_only_when_expected_value_is_positive() -> None:
    visible = tuple(scenario.observable() for scenario in hidden_scenarios(406041616))
    decision = deterministic_decision(
        visible, policy=ExperimentCondition.AGENT_EXPERIMENTATION.value, seed=406041616,
    )
    assert [choice.action for choice in decision.choices] == [
        ExperimentAction.EXPERIMENT_CANDIDATE,
        ExperimentAction.EXPERIMENT_CANDIDATE,
        ExperimentAction.EXPLOIT_SAFE,
    ]
    validate_decision(decision, visible, experiment_budget=2)


def test_decision_rejects_unknown_duplicate_and_budget_overflow() -> None:
    visible = tuple(scenario.observable() for scenario in hidden_scenarios(406041616))
    base = deterministic_decision(
        visible, policy=ExperimentCondition.AGENT_EXPERIMENTATION.value, seed=406041616,
    )
    unknown = base.model_copy(update={
        "choices": (*base.choices[:-1], base.choices[-1].model_copy(update={"scenario_id": "x"})),
    })
    with pytest.raises(ExperimentValidationError, match="cover each scenario"):
        validate_decision(unknown, visible, experiment_budget=3)
    with pytest.raises(Exception, match="duplicate"):
        ExperimentDecision(
            choices=(base.choices[0], base.choices[0]), policy_summary="duplicate",
        )
    with pytest.raises(ExperimentValidationError, match="exceeding budget"):
        validate_decision(base, visible, experiment_budget=1)


def test_execution_versions_beliefs_and_preserves_experiment_evidence(tmp_path: Path) -> None:
    hidden = hidden_scenarios(406041616)
    visible = tuple(scenario.observable() for scenario in hidden)
    decision = deterministic_decision(
        visible, policy=ExperimentCondition.AGENT_EXPERIMENTATION.value, seed=406041616,
    )
    events = EventStore(tmp_path / "events.jsonl", "m14-test")
    memory = MemoryStore(tmp_path / "memory.sqlite3")
    outcomes = execute_decision(
        decision, hidden, policy=ExperimentCondition.AGENT_EXPERIMENTATION.value,
        experiment_budget=3, events=events, memory=memory,
    )
    assert sum(outcome.total_reward for outcome in outcomes) == 2280
    first_prior = f"belief:{hidden[0].scenario_id}:v1"
    first_revised = f"belief:{hidden[0].scenario_id}:v2"
    assert memory.status(first_prior) is MemoryStatus.SUPERSEDED
    revised = memory.get(first_revised)
    assert revised is not None
    assert revised.supersedes_id == first_prior
    execution_ids = {
        str(event.event_id) for event in events.iter_records()
        if event.event_type == "experiment_execution"
    }
    assert set(revised.source_event_ids) <= execution_ids
    assert memory.status(f"belief:{hidden[2].scenario_id}:v1") is MemoryStatus.ACTIVE
    memory.close()


def test_authenticated_policy_repairs_and_records_attributed_calls(tmp_path: Path) -> None:
    visible = tuple(scenario.observable() for scenario in hidden_scenarios(406041616))
    valid = deterministic_decision(
        visible, policy=ExperimentCondition.AGENT_EXPERIMENTATION.value, seed=406041616,
    ).model_dump_json()
    runner = ExperimentRunner(
        tmp_path / "runs", load_run_config(Path("configs/m14-live.yaml")), PROVENANCE,
    )
    runner.start()
    provider = ScriptedProvider(["bad", valid], provider="actual", model="actual-model")
    decision = AgentExperimentPolicy(runner, provider).decide(visible, experiment_budget=3)
    assert len(decision.choices) == 3
    assert len(provider.requests) == 2
    assert provider.requests[1].repair
    assert visible[0].scenario_id in provider.requests[1].prompt
    assert "derived_expected_experiment_net_value" in provider.requests[0].prompt
    assert '"candidate_value"' not in provider.requests[0].prompt
    calls = [event for event in runner.events.iter_records()
             if event.event_type == "model_call_completed"]
    assert len(calls) == 2


def test_authenticated_policy_combines_bounded_batches(tmp_path: Path) -> None:
    visible = tuple(scenario.observable() for scenario in hidden_scenarios(406041616))
    first = deterministic_decision(
        visible[:2], policy=ExperimentCondition.AGENT_EXPERIMENTATION.value, seed=406041616,
    ).model_dump_json()
    second = deterministic_decision(
        visible[2:], policy=ExperimentCondition.AGENT_EXPERIMENTATION.value, seed=406041616,
    ).model_dump_json()
    runner = ExperimentRunner(
        tmp_path / "runs", load_run_config(Path("configs/m14-live.yaml")), PROVENANCE,
    )
    runner.start()
    provider = ScriptedProvider([first, second])
    decision = AgentExperimentPolicy(runner, provider).decide(
        visible, experiment_budget=3, batch_size=2,
    )
    assert len(decision.choices) == 3
    assert len(provider.requests) == 2
    assert visible[2].scenario_id not in provider.requests[0].prompt
    assert visible[2].scenario_id in provider.requests[1].prompt


def test_live_sampling_policy_withholds_game_mechanics_and_records_call(tmp_path: Path) -> None:
    runner = ExperimentRunner(
        tmp_path / "runs", load_run_config(Path("configs/m14-stardew-live.yaml")), PROVENANCE,
    )
    runner.start()
    response = json.dumps({
        "sample_count": 2,
        "expected_learning_value": 0.75,
        "stopping_rule": "Stop after the second independent observation.",
        "rationale": "Two samples can reveal whether the yield varies before validation.",
    })
    provider = ScriptedProvider([response], provider="actual", model="actual-model")
    plan = LiveSamplingPolicy(runner, provider).decide()
    assert plan.sample_count == 2
    assert len(provider.requests) == 1
    prompt = provider.requests[0].prompt
    assert "(O)294" not in prompt
    assert "derived_expected" not in prompt
    assert "No numeric yield prior" in prompt
    calls = [event for event in runner.events.iter_records()
             if event.event_type == "model_call_completed"]
    assert len(calls) == 1


def test_live_sampling_policy_repairs_once(tmp_path: Path) -> None:
    runner = ExperimentRunner(
        tmp_path / "runs", load_run_config(Path("configs/m14-stardew-live.yaml")), PROVENANCE,
    )
    runner.start()
    valid = json.dumps({
        "sample_count": 1,
        "expected_learning_value": 0.5,
        "stopping_rule": "Stop after one observation.",
        "rationale": "Preserve one candidate while still collecting live evidence.",
    })
    provider = ScriptedProvider(["invalid", valid])
    plan = LiveSamplingPolicy(runner, provider).decide()
    assert plan.sample_count == 1
    assert len(provider.requests) == 2
    assert provider.requests[1].repair


def test_preregistered_report_reconstructs_one_seed_from_events(tmp_path: Path) -> None:
    preregistration, digest = load_m14_preregistration(
        Path("configs/m14-active-experimentation-study.yaml")
    )
    seed = preregistration.seeds[0]
    for condition in preregistration.conditions:
        run_m14_condition(
            tmp_path, seed=seed, condition=condition,
            experiment_budget=preregistration.experiment_budget,
            preregistration_sha256=digest,
        )
    reduced = preregistration.model_copy(update={"seeds": (seed,)})
    report = build_m14_report_from_events(tmp_path, reduced, digest)
    assert report["runs"] == 5
    assert report["matched_hidden_world_states"]
    assert report["matched_observable_states"]
    assert report["agent_restraint_passed"]
    assert report["condition_summaries"]["agent_experimentation"]["mean_net_total_reward"] == 2280
    assert report["condition_summaries"]["belief_tracking_only"]["mean_net_total_reward"] == 1680
    assert report["manual_score_edits"] == 0
