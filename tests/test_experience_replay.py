import json
from pathlib import Path

import pytest

from prime_stardew.agent import ScriptedProvider
from prime_stardew.experience_replay import (
    AgentReplayPolicy, ReplayAction, ReplayCondition, ReplayDecision,
    ReplayValidationError, deterministic_replay_decision, execute_replay,
    validate_replay_decision,
)
from prime_stardew.experience_replay.research import (
    build_m13_report_from_events, load_m13_preregistration, replay_fixture,
    run_m13_condition,
)
from prime_stardew.experiments.config import load_run_config
from prime_stardew.experiments.provenance import CodeProvenance, RunProvenance, RuntimeProvenance
from prime_stardew.experiments.runner import ExperimentRunner
from prime_stardew.telemetry import EventStore


PROVENANCE = RunProvenance(
    code=CodeProvenance(revision="m13-test", dirty=False, dirty_patch_sha256="0" * 64),
    runtime=RuntimeProvenance(
        game_version="1.6.15", smapi_version="4.5.2", stardojo_version="1.0.0-patched",
        stardojo_dll_sha256="1" * 64, python_version="3.13", platform="test",
    ),
)


def test_agent_priority_selects_transferable_failures_under_budget() -> None:
    experiences, tasks = replay_fixture(1301)
    decision = deterministic_replay_decision(
        experiences, tasks, condition=ReplayCondition.AGENT_PRIORITY,
        replay_budget=3, seed=1301,
    )
    selected = validate_replay_decision(decision, experiences, replay_budget=3)
    assert set(selected) == {
        "crop-deadline-failure", "mine-retreat-failure", "gift-preference-failure",
    }


def test_error_priority_wastes_one_replay_on_irrelevant_high_error() -> None:
    experiences, tasks = replay_fixture(1301)
    decision = deterministic_replay_decision(
        experiences, tasks, condition=ReplayCondition.ERROR_PRIORITY,
        replay_budget=3, seed=1301,
    )
    selected = validate_replay_decision(decision, experiences, replay_budget=3)
    assert "decor-layout-failure" in selected
    assert "gift-preference-failure" not in selected


def test_replay_decision_rejects_missing_unknown_and_budget_overflow() -> None:
    experiences, _ = replay_fixture(1301)
    missing = ReplayDecision(
        actions=(ReplayAction(
            experience_id=experiences[0].experience_id, replay=True,
            predicted_transfer_value=1,
        ),), reasoning_summary="incomplete",
    )
    with pytest.raises(ReplayValidationError, match="cover every candidate"):
        validate_replay_decision(missing, experiences, replay_budget=3)
    overflow = ReplayDecision(
        actions=tuple(ReplayAction(
            experience_id=item.experience_id, replay=True, predicted_transfer_value=1,
        ) for item in experiences), reasoning_summary="too many",
    )
    with pytest.raises(ReplayValidationError, match="exceeding budget"):
        validate_replay_decision(overflow, experiences, replay_budget=3)


def test_execution_records_exact_replay_provenance(tmp_path: Path) -> None:
    experiences, tasks = replay_fixture(1301)
    decision = deterministic_replay_decision(
        experiences, tasks, condition=ReplayCondition.AGENT_PRIORITY,
        replay_budget=3, seed=1301,
    )
    events = EventStore(tmp_path / "events.jsonl", "m13-test")
    result = execute_replay(decision, experiences, replay_budget=3, events=events)
    replay_ids = {
        str(item.event_id) for item in events.iter_records()
        if item.event_type == "experience_replayed"
    }
    updates = [
        item.payload for item in events.iter_records()
        if item.event_type == "replay_learning_updated"
    ]
    assert set(result["replay_event_ids"]) == replay_ids
    assert {str(item["source_replay_event_id"]) for item in updates} == replay_ids


def test_agent_policy_repairs_once_and_records_attributed_calls(tmp_path: Path) -> None:
    experiences, tasks = replay_fixture(1301)
    valid = deterministic_replay_decision(
        experiences, tasks, condition=ReplayCondition.AGENT_PRIORITY,
        replay_budget=3, seed=1301,
    ).model_dump_json()
    runner = ExperimentRunner(
        tmp_path / "runs", load_run_config(Path("configs/m13-live.yaml")), PROVENANCE,
    )
    runner.start()
    provider = ScriptedProvider(["bad", valid], provider="actual", model="actual-model")
    decision = AgentReplayPolicy(runner, provider).decide(
        experiences, tasks, replay_budget=3,
    )
    assert len(validate_replay_decision(decision, experiences, replay_budget=3)) == 3
    assert len(provider.requests) == 2
    assert provider.requests[1].repair
    assert "correct_action" not in provider.requests[0].prompt
    calls = [
        item for item in runner.events.iter_records()
        if item.event_type == "model_call_completed"
    ]
    assert len(calls) == 2


def test_preregistered_report_reconstructs_one_seed_from_raw_events(tmp_path: Path) -> None:
    registration, digest = load_m13_preregistration(
        Path("configs/m13-experience-replay-study.yaml")
    )
    seed = registration.seeds[0]
    for condition in registration.conditions:
        run_m13_condition(
            tmp_path, seed=seed, condition=condition,
            replay_budget=registration.replay_budget,
            preregistration_sha256=digest,
        )
    reduced = registration.model_copy(update={"seeds": (seed,)})
    report = build_m13_report_from_events(tmp_path, reduced, digest)
    assert report["runs"] == 5
    assert report["matched_candidate_states"]
    assert report["matched_hidden_evaluations"]
    assert report["condition_summaries"]["agent_priority"]["mean_held_out_accuracy"] == 1
    assert report["condition_summaries"]["error_priority"]["mean_held_out_accuracy"] == pytest.approx(2 / 3)
    assert report["manual_score_edits"] == 0

