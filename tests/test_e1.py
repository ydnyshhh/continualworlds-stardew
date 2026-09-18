import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from prime_stardew.e1 import (
    BranchType, CompetencyDomain, CompetencyInstance, E1Condition, HarnessCapabilities,
    LearningResetSpec, LearningState, MemoryExposurePolicy, ProbeDefinition, ProbeKind,
    RefinementAttribution, RefinementOutcome, condition_manifest, exposed_memory_tokens,
    select_memory_context, validate_condition_ladder,
)
from prime_stardew.e1.analysis import learning_curve_slope, score_probe, standardized_probe_aulc
from prime_stardew.e1.branches import ProbeBranchManager, reset_learning_state, spring_y2_reset_matrix
from prime_stardew.e1.competencies import normalized_competency_metrics, within_period_gain
from prime_stardew.e1.config import load_e1_config
from prime_stardew.e1.offline import build_offline_report, run_offline_condition
from prime_stardew.e1.branches import PhysicalProbeBranchManager
from prime_stardew.env.checkpoints import CheckpointManager
from prime_stardew.env.models import GameDate
from prime_stardew.experiments.checkpoints import RunCheckpointManager
from prime_stardew.telemetry import EventStore
from prime_stardew.memory import MemoryKind, MemoryRecord, MemoryStore


def test_condition_capability_matrix_is_cumulative_and_b_differs_only_by_retrieval() -> None:
    extra = HarnessCapabilities(
        persistent_goal_manager=True, adaptive_tool_selection=True, native_compaction=True,
    )
    manifests = tuple(condition_manifest(condition, full_harness_features=extra) for condition in E1Condition)
    validate_condition_ladder(
        manifests, memory_token_budget_by_condition={condition: 1500 for condition in E1Condition},
    )
    by_id = {item.condition: item for item in manifests}
    assert by_id[E1Condition.A_BASE].memory_exposure is MemoryExposurePolicy.NONE
    assert by_id[E1Condition.B_MEMORY].memory_exposure is MemoryExposurePolicy.CHRONOLOGICAL
    assert by_id[E1Condition.C_RETRIEVAL].memory_exposure is MemoryExposurePolicy.RELEVANCE
    assert by_id[E1Condition.D_SKILLS].capabilities.procedural_skills
    assert by_id[E1Condition.E_REFINE].capabilities.reflection
    assert by_id[E1Condition.F_FULL].capabilities.persistent_goal_manager
    assert not by_id[E1Condition.E_REFINE].capabilities.persistent_goal_manager


def test_b_and_c_require_equal_memory_token_budgets() -> None:
    manifests = tuple(condition_manifest(condition) for condition in E1Condition)
    budgets = {condition: 1500 for condition in E1Condition}
    budgets[E1Condition.C_RETRIEVAL] = 1600
    with pytest.raises(ValueError, match="identical memory-token budgets"):
        validate_condition_ladder(manifests, memory_token_budget_by_condition=budgets)


def test_b_uses_chronology_and_c_uses_relevance_under_same_budget(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite3")
    epoch = datetime(2026, 1, 1, tzinfo=UTC)
    for index, (memory_id, text) in enumerate((
        ("relevant-old", "Pierre shop schedule opens Wednesday"),
        ("irrelevant-middle", "A blue bird crossed the farm"),
        ("irrelevant-new", "The latest decorative path note"),
    )):
        store.add(MemoryRecord(
            memory_id=memory_id, kind=MemoryKind.SEMANTIC, text=text,
            created_at=epoch + timedelta(days=index),
        ))
    budget = 10
    b = select_memory_context(
        store, condition_manifest(E1Condition.B_MEMORY),
        query="When does Pierre shop open Wednesday?", token_budget=budget,
    )
    c = select_memory_context(
        store, condition_manifest(E1Condition.C_RETRIEVAL),
        query="When does Pierre shop open Wednesday?", token_budget=budget,
    )
    assert b and b[0].memory_id == "irrelevant-new"
    assert c and c[0].memory_id == "relevant-old"
    assert exposed_memory_tokens(b) <= budget
    assert exposed_memory_tokens(c) <= budget
    store.close()


def test_probe_normalization_aulc_and_slope() -> None:
    definitions = (
        ProbeDefinition(
            probe_id="a", kind=ProbeKind.FAMILIAR, capability="x", description="x",
            hidden_answer="secret-a", contamination_terms=("secret-a",),
        ),
        ProbeDefinition(
            probe_id="b", kind=ProbeKind.TRANSFER, capability="y", description="y",
            hidden_answer="secret-b", contamination_terms=("secret-b",),
        ),
    )
    points = (
        score_probe(7, definitions, {"a": .2, "b": .4}),
        score_probe(14, definitions, {"a": .4, "b": .6}),
        score_probe(28, definitions, {"a": .8, "b": 1}),
    )
    assert points[0].aggregate_score == pytest.approx(.3)
    assert standardized_probe_aulc(points) == pytest.approx(.6)
    assert learning_curve_slope(points) > 0


def test_disposable_probe_branch_preserves_parent_state(tmp_path: Path) -> None:
    events = EventStore(tmp_path / "events.jsonl", "parent")
    game = {"day": 14, "gold": 1000}
    learning = LearningState(
        active_memory_ids=("m1",), active_skill_refs=("s1:v1",),
        historical_artifact_ids=("m1", "s1:v1"),
    )
    manager = ProbeBranchManager(
        parent_run_id="parent", game_state=game, learning_state=learning, events=events,
    )
    branch = manager.fork(branch_id="probe-14", day=14)
    branch.game_state["gold"] = 0
    manager.discard(branch)
    discarded = [item.payload for item in events.iter_records()
                 if item.event_type == "probe_branch_discarded"]
    assert discarded == [{
        "branch_id": "probe-14",
        "parent_game_state_sha256": branch.identity.parent_game_state_sha256,
        "parent_learning_state_sha256": branch.identity.parent_learning_state_sha256,
        "parent_unchanged": True,
    }]


def test_physical_probe_branch_restores_and_discards_all_registered_state(tmp_path: Path) -> None:
    saves = tmp_path / "saves"
    source = saves / "Fixture_1"
    source.mkdir(parents=True)
    (source / "Fixture_1").write_text("parent-save", encoding="utf-8")
    events = EventStore(tmp_path / "events.jsonl", "parent-run")
    memory_path = tmp_path / "memory.sqlite3"
    memory = MemoryStore(memory_path)
    memory.add_text("Spring rule", memory_id="spring-rule")
    memory.close()
    skills_path = tmp_path / "skills.sqlite3"
    with sqlite3.connect(skills_path) as database:
        database.execute("CREATE TABLE skills (name TEXT NOT NULL)")
        database.execute("INSERT INTO skills VALUES ('watering-v1')")
    checkpoints = RunCheckpointManager(
        CheckpointManager(saves, stable_checks=2, stable_interval=0, stable_timeout=1)
    )
    checkpoint = tmp_path / "checkpoint"
    checkpoints.create(
        run_id="parent-run", destination=checkpoint, save_id="Fixture_1", player="Fixture",
        game_date=GameDate(year=1, season="spring", day=7),
        agent_state={"learning_state": {"memory_ids": ["spring-rule"]}},
        configuration={"study": "E1"}, event_store=events,
        memory_database=memory_path, learning_databases={"skills": skills_path},
    )
    manager = PhysicalProbeBranchManager(
        checkpoints=checkpoints, branches_root=tmp_path / "branches",
        parent_run_id="parent-run", events=events,
    )
    branch = manager.fork(
        checkpoint=checkpoint, branch_id="probe-day-7",
        destination_save_id="Probe_7", day=7,
    )
    assert (branch.restored.game_save_path / "Probe_7").read_text(encoding="utf-8") == "parent-save"
    assert branch.restored.memory_database is not None
    assert branch.restored.learning_databases["skills"].is_file()
    (branch.restored.game_save_path / "Probe_7").write_text("mutated-probe", encoding="utf-8")
    manager.discard(branch)
    assert not branch.restored.game_save_path.exists()
    assert not branch.branch_root.exists()
    assert (checkpoint / "game" / "Fixture_1").read_text(encoding="utf-8") == "parent-save"
    records = tuple(events.iter_records())
    assert records[-1].event_type == "physical_probe_branch_discarded"
    assert records[-1].payload["parent_unchanged"] is True


def test_learning_reset_deactivates_without_destroying_history(tmp_path: Path) -> None:
    events = EventStore(tmp_path / "events.jsonl", "reset")
    state = LearningState(
        active_memory_ids=("m1",), active_belief_ids=("b1",),
        active_skill_refs=("s1:v1",), active_refinement_ids=("r1",),
        active_goal_ids=("g1",), historical_artifact_ids=("m1", "b1", "s1:v1", "r1", "g1"),
    )
    reset = reset_learning_state(
        state, LearningResetSpec(memory=True, beliefs=True, skills=True), events=events,
        branch_id="spring-y2-reset",
    )
    assert not reset.active_memory_ids and not reset.active_belief_ids and not reset.active_skill_refs
    assert reset.active_refinement_ids == ("r1",)
    assert reset.historical_artifact_ids == state.historical_artifact_ids
    assert len(spring_y2_reset_matrix()) == 5


def test_competency_metrics_normalize_workload_and_measure_change() -> None:
    items = (
        CompetencyInstance(
            instance_id="early", domain=CompetencyDomain.FARM_MAINTENANCE, game_day=2,
            work_units=20, model_decisions=4, primitive_actions=25, game_minutes=80,
            energy_used=40, success=True,
        ),
        CompetencyInstance(
            instance_id="late", domain=CompetencyDomain.FARM_MAINTENANCE, game_day=25,
            work_units=80, model_decisions=3, primitive_actions=90, game_minutes=240,
            energy_used=160, success=True,
        ),
    )
    assert normalized_competency_metrics(items[0])["model_decisions_per_10_units"] == 2
    gain = within_period_gain(
        items, CompetencyDomain.FARM_MAINTENANCE, "model_decisions_per_10_units",
    )
    assert gain == pytest.approx(.375 - 2)


def test_refinement_attribution_requires_evidence_and_computes_conservative_roi() -> None:
    attribution = RefinementAttribution(
        refinement_id="r1", source_experience_ids=("experience-1",),
        artifact_ids=("belief-v2",), future_retrieval_event_ids=("retrieve-1",),
        future_decision_event_ids=("decision-1",), future_outcome_event_ids=("outcome-1",),
        outcome=RefinementOutcome.BENEFICIAL, reflection_cost_usd=.02,
        attributable_utility=.1,
    )
    assert attribution.utilized
    assert attribution.roi == pytest.approx(5)


def test_offline_matrix_reconstructs_matched_runs_without_scientific_claims(tmp_path: Path) -> None:
    config, digest = load_e1_config(Path("configs/e1-offline-validation.yaml"))
    seed = config.seeds[0]
    for condition in config.conditions:
        run_offline_condition(
            tmp_path, config=config, config_sha256=digest, seed=seed, condition=condition,
        )
    reduced = config.model_copy(update={"seeds": (seed,)})
    report = build_offline_report(tmp_path, reduced, digest)
    assert report["status"] == "passed"
    assert report["runs"] == 6
    assert report["matched_starting_states"]
    assert report["b_c_memory_budgets_equal"]
    assert report["scientific_claims_permitted"] is False
    assert not report["failure_analysis"]["event_failures"]


def test_probe_hidden_answers_do_not_enter_decision_input(tmp_path: Path) -> None:
    config, digest = load_e1_config(Path("configs/e1-offline-validation.yaml"))
    run_offline_condition(
        tmp_path, config=config, config_sha256=digest,
        seed=config.seeds[0], condition=E1Condition.C_RETRIEVAL,
    )
    log = next(tmp_path.glob("runs/*/events.jsonl"))
    text = log.read_text(encoding="utf-8")
    started_inputs = [
        item.payload["decision_input"] for item in EventStore(log, f"e1-offline-c-s{config.seeds[0]}").iter_records()
        if item.event_type == "probe_started"
    ]
    assert started_inputs
    assert all("hidden_answer" not in json.dumps(value) for value in started_inputs)
    assert "evaluator-only-do-not-plant" not in json.dumps(started_inputs)
