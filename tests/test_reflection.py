import json
from pathlib import Path

import pytest

from prime_stardew.agent import ScriptedProvider
from prime_stardew.env.models import GameDate
from prime_stardew.experiments.config import (
    ExperimentBudget, FixtureConfig, LearningConditionConfig, ProviderConfig, RunConfig,
)
from prime_stardew.experiments.provenance import (
    CodeProvenance, RunProvenance, RuntimeProvenance,
)
from prime_stardew.experiments.runner import ExperimentRunner
from prime_stardew.memory import (
    BeliefPrediction, MemoryKind, MemoryQuery, MemoryRecord, MemoryStatus, MemoryStore,
    PredictionOutcome,
)
from prime_stardew.reflection import (
    EvidenceItem, MemoryConsolidator, ReflectionEngine, ReflectionError,
    RefinementPolicy, RefinementTrigger, select_evidence,
)


PROVENANCE = RunProvenance(
    code=CodeProvenance(revision="m7-test", dirty=False, dirty_patch_sha256="0" * 64),
    runtime=RuntimeProvenance(
        game_version="1.6.15", smapi_version="4.5.2",
        stardojo_version="1.0.0-patched", stardojo_dll_sha256="1" * 64,
        python_version="3.13", platform="test",
    ),
)


def _runner(tmp_path: Path) -> ExperimentRunner:
    config = RunConfig(
        suite="m7", condition="reflection", seed=7, observation_mode="replay",
        fixture=FixtureConfig(
            save_id="Fixture_1", player="PrimeStardewSmoke",
            starting_date=GameDate(year=1, season="spring", day=8),
        ), tasks=("crop-rule",), provider=ProviderConfig(),
        learning=LearningConditionConfig(
            recent_context=True, persistent_memory=True, retrieval=True,
            skills=False, refinement=True,
        ),
        budget=ExperimentBudget(
            max_actions=5, max_game_days=7, max_model_calls=5,
            max_input_tokens=20000, max_output_tokens=5000,
            max_cost_usd=5, max_wall_seconds=60,
        ),
    )
    runner = ExperimentRunner(tmp_path / "runs", config, PROVENANCE)
    runner.start()
    return runner


def _evidence() -> tuple[EvidenceItem, ...]:
    return (
        EvidenceItem(
            event_id="crop-day-4", kind="observation", game_day=4,
            text="After three watered nights, Moonroot is not mature.",
        ),
        EvidenceItem(
            event_id="crop-day-5", kind="outcome", game_day=5,
            text="After four watered nights, Moonroot is mature and harvestable.",
            success=True, surprise=0.4,
        ),
    )


def test_fixed_budget_selection_prioritizes_failure_and_surprise() -> None:
    evidence = (
        EvidenceItem(event_id="ordinary", kind="observation", text="x" * 40, game_day=2),
        EvidenceItem(
            event_id="failure", kind="error", text="failed crop prediction",
            game_day=3, success=False, surprise=0.9,
        ),
    )
    policy = RefinementPolicy(trigger="failure", evidence_token_budget=8, max_evidence=1)
    selected, omitted, tokens = select_evidence(evidence, policy)
    assert [item.event_id for item in selected] == ["failure"]
    assert omitted == ("ordinary",)
    assert tokens <= 8


def test_all_refinement_trigger_policies_have_explicit_guards() -> None:
    evidence = _evidence()
    assert RefinementPolicy(trigger="nightly").allows(RefinementTrigger.NIGHTLY, evidence)
    assert not RefinementPolicy(trigger="failure").allows(RefinementTrigger.FAILURE, evidence)
    failed = evidence + (EvidenceItem(
        event_id="failed", kind="error", text="failed", game_day=5, success=False,
    ),)
    assert RefinementPolicy(trigger="failure").allows(RefinementTrigger.FAILURE, failed)
    assert RefinementPolicy(trigger="surprise", surprise_threshold=0.3).allows(
        RefinementTrigger.SURPRISE, evidence,
    )
    assert RefinementPolicy(trigger="agent_selected").allows(
        RefinementTrigger.AGENT_SELECTED, evidence,
    )


def test_belief_forms_revises_and_inactive_versions_never_retrieve(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    store = MemoryStore(tmp_path / "memory.sqlite3")
    first_id = f"{runner.run_id}:reflection-1:belief-1-v1"
    first = json.dumps({
        "schema_version": 1,
        "lessons": ["Track watered nights rather than calendar labels."],
        "failed_assumptions": [], "counterfactuals": [], "goals": [],
        "beliefs": [{
            "statement": "Moonroot matures after four watered nights.",
            "confidence": 0.8,
            "source_event_ids": ["crop-day-4", "crop-day-5"],
            "supporting_event_ids": ["crop-day-4", "crop-day-5"],
            "contradicting_event_ids": [], "supersedes_id": None,
            "predicted_event": "Moonroot is ready after four watered nights.",
            "predicted_probability": 0.8,
        }],
    })
    contradiction = EvidenceItem(
        event_id="crop-day-5-contradiction", kind="outcome", game_day=9,
        text="A new Moonroot batch was not mature after four watered nights.",
        success=False, surprise=1,
    )
    confirmation = EvidenceItem(
        event_id="crop-day-6", kind="outcome", game_day=10,
        text="The new Moonroot batch matured after five watered nights.", success=True,
    )
    second = json.dumps({
        "schema_version": 1, "lessons": [],
        "failed_assumptions": ["The four-night rule was stable."],
        "counterfactuals": ["Wait one more watered night."],
        "goals": ["Test another Moonroot batch."],
        "beliefs": [{
            "statement": "Moonroot currently matures after five watered nights.",
            "confidence": 0.9,
            "source_event_ids": ["crop-day-5-contradiction", "crop-day-6"],
            "supporting_event_ids": ["crop-day-6"],
            "contradicting_event_ids": ["crop-day-5-contradiction"],
            "supersedes_id": first_id,
            "predicted_event": "Moonroot is ready after five watered nights.",
            "predicted_probability": 0.9,
        }],
    })
    engine = ReflectionEngine(
        runner, ScriptedProvider([first, second]), store,
        RefinementPolicy(trigger="nightly", evidence_token_budget=512),
    )
    first_result = engine.reflect(
        trigger=RefinementTrigger.NIGHTLY, evidence=_evidence(),
        objective="Infer the hidden Moonroot maturation rule.",
    )
    second_result = engine.reflect(
        trigger=RefinementTrigger.NIGHTLY, evidence=(contradiction, confirmation),
        objective="Revise any crop belief contradicted by new evidence.",
    )
    revised_id = second_result.created_memory_ids[0]

    assert first_id in first_result.created_memory_ids
    assert store.status(first_id) is MemoryStatus.SUPERSEDED
    assert store.status(revised_id) is MemoryStatus.ACTIVE
    assert store.get(revised_id).version == 2  # type: ignore[union-attr]
    current = store.retrieve(MemoryQuery(text="Moonroot watered nights", token_budget=256))
    assert revised_id in [record.memory_id for record in current.selected]
    assert first_id not in [record.memory_id for record in current.selected]
    store.delete(revised_id, reason="test deletion")
    current = store.retrieve(MemoryQuery(text="Moonroot watered nights", token_budget=256))
    assert revised_id not in [record.memory_id for record in current.selected]
    store.close()


def test_reflection_rejects_uncited_evidence(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    store = MemoryStore(tmp_path / "memory.sqlite3")
    bad = json.dumps({
        "schema_version": 1, "lessons": [], "failed_assumptions": [],
        "counterfactuals": [], "goals": [], "beliefs": [{
            "statement": "unsupported", "confidence": 0.5,
            "source_event_ids": ["not-selected"], "supporting_event_ids": [],
            "contradicting_event_ids": [], "supersedes_id": None,
            "predicted_event": None, "predicted_probability": None,
        }],
    })
    engine = ReflectionEngine(
        runner, ScriptedProvider([bad]), store,
        RefinementPolicy(trigger="nightly"),
    )
    with pytest.raises(ReflectionError, match="outside the selected set"):
        engine.reflect(
            trigger=RefinementTrigger.NIGHTLY, evidence=_evidence(), objective="infer",
        )
    store.close()


def test_episode_consolidation_preserves_all_source_events(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite3")
    store.add(MemoryRecord(
        memory_id="episode-1", kind=MemoryKind.EPISODE, text="batch one",
        source_event_ids=("event-1",),
    ))
    store.add(MemoryRecord(
        memory_id="episode-2", kind=MemoryKind.EPISODE, text="batch two",
        source_event_ids=("event-2",),
    ))
    semantic = MemoryConsolidator(store).consolidate(
        rule_key="moonroot-growth", episode_ids=("episode-1", "episode-2"),
        statement="Moonroot maturation depends on watered nights.",
    )
    assert semantic.kind is MemoryKind.SEMANTIC
    assert semantic.source_event_ids == ("event-1", "event-2")
    assert semantic.payload["support_count"] == 2
    store.close()


def test_prediction_calibration_uses_observed_outcomes(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite3")
    store.add(MemoryRecord(
        memory_id="belief", kind=MemoryKind.BELIEF, text="crop will mature",
        source_event_ids=("event-1",),
    ))
    store.record_prediction(BeliefPrediction(
        prediction_id="p1", belief_id="belief", predicted_event="ready",
        probability=0.8, source_event_id="event-2",
    ))
    store.record_outcome(PredictionOutcome(
        prediction_id="p1", occurred=False, source_event_id="event-3",
    ))
    store.record_prediction(BeliefPrediction(
        prediction_id="p2", belief_id="belief", predicted_event="ready later",
        probability=0.9, source_event_id="event-4",
    ))
    store.record_outcome(PredictionOutcome(
        prediction_id="p2", occurred=True, source_event_id="event-5",
    ))
    report = store.calibration()
    assert report.scored_predictions == 2
    assert report.brier_score == pytest.approx((0.8 ** 2 + 0.1 ** 2) / 2)
    assert report.mean_confidence == pytest.approx(0.85)
    assert report.observed_rate == pytest.approx(0.5)
    store.close()
