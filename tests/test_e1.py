import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from prime_stardew.agent import PrimeSession, ScriptedProvider
from prime_stardew.agent.models import ProviderResponse, ProviderUsage
from prime_stardew.e1 import (
    BranchType, CompetencyDomain, CompetencyInstance, E1Condition, HarnessCapabilities,
    LearningResetSpec, LearningState, MemoryExposurePolicy, ProbeDefinition, ProbeKind,
    PERSISTENT_OBJECTIVE, RefinementAttribution, RefinementOutcome,
    condition_manifest, exposed_memory_tokens, materialize_run_config,
    select_memory_context, validate_condition_ladder,
)
from prime_stardew.e1.analysis import learning_curve_slope, score_probe, standardized_probe_aulc
from prime_stardew.e1.branches import ProbeBranchManager, reset_learning_state, spring_y2_reset_matrix
from prime_stardew.e1.competencies import normalized_competency_metrics, within_period_gain
from prime_stardew.e1.config import load_e1_config
from prime_stardew.e1.offline import build_offline_report, run_offline_condition
from prime_stardew.e1.branches import PhysicalProbeBranchManager
from prime_stardew.env.checkpoints import CheckpointManager
from prime_stardew.env.client import StarDojoClient
from prime_stardew.env.lifecycle import EnvironmentController
from prime_stardew.env.models import GameDate
from prime_stardew.env.transport import ReplayTransport
from prime_stardew.experiments.checkpoints import RunCheckpointManager
from prime_stardew.experiments.config import (
    ContextBudgetConfig, ExperimentBudget, FixtureConfig, RunConfig,
)
from prime_stardew.experiments.provenance import (
    CodeProvenance, RunProvenance, RuntimeProvenance,
)
from prime_stardew.experiments.runner import ExperimentRunner
from prime_stardew.telemetry import EventStore
from prime_stardew.memory import MemoryKind, MemoryRecord, MemoryStore
from prime_stardew.e1.conditions import learning_config
from prime_stardew.e1.harness import HarnessContractError
from prime_stardew.e1.prime_adapter import PrimeHarnessAdapter
from prime_stardew.e1.live import run_live_days
from prime_stardew.e1.activity import segment_live_competencies
from prime_stardew.e1.learning import E1LearningLifecycle
from prime_stardew.skills import SkillStore


E1_PROVENANCE = RunProvenance(
    code=CodeProvenance(revision="e1-test", dirty=False, dirty_patch_sha256="0" * 64),
    runtime=RuntimeProvenance(
        game_version="1.6.15", smapi_version="4.5.2", stardojo_version="patched",
        stardojo_dll_sha256="1" * 64, python_version="3.13", platform="test",
    ),
)


def _live_observation(day: int, *, radius_fixture: str = "observation-minimal.json") -> str:
    fixture = Path("tests/fixtures") / radius_fixture
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    payload["GameState"].update(DayOfMonth=day, Season="spring", Year=1, Time=600)
    return json.dumps(payload)


def _prime_adapter(
    tmp_path: Path, condition: E1Condition, responses: list[str],
    *, memory: MemoryStore | None = None,
) -> PrimeHarnessAdapter:
    manifest = condition_manifest(condition)
    config = RunConfig(
        suite="e1-adapter", condition=condition.value, seed=1, observation_mode="replay",
        fixture=FixtureConfig(
            save_id="Fixture_1", player="Fixture",
            starting_date=GameDate(year=1, season="spring", day=1),
        ),
        tasks=("broad-objective",), learning=learning_config(manifest),
        context=ContextBudgetConfig(
            total_tokens=1000, objective_tokens=300, observation_tokens=300,
            goals_tokens=100, memories_tokens=50, skills_tokens=100, recent_events_tokens=100,
        ),
        budget=ExperimentBudget(
            max_actions=50, max_game_days=7, max_model_calls=20,
            max_input_tokens=20000, max_output_tokens=5000, max_cost_usd=5,
            max_wall_seconds=60,
        ),
    )
    runner = ExperimentRunner(tmp_path / f"runs-{condition.value}", config, E1_PROVENANCE)
    session = PrimeSession(runner, ScriptedProvider(responses), memory_store=memory)
    skills = SkillStore(tmp_path / f"skills-{condition.value}.sqlite3") if manifest.capabilities.procedural_skills else None
    return PrimeHarnessAdapter(
        session, manifest, memory_token_budget=50,
        skill_store=skills,
        runtime_capabilities=manifest.capabilities,
    )


class E1LifecycleProvider:
    def __init__(self) -> None:
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        if request.call_id.startswith("decision"):
            value = {
                "schema_version": 1,
                "actions": [{"name": "turn", "arguments": [1]}],
                "goal_updates": [], "memory_notes": [], "rationale": "repeat safe turn",
            }
        elif request.call_id.startswith("e1-skill-proposal"):
            sources_text = request.prompt.split("[source_trajectory_ids]\n", 1)[1].split("\n", 1)[0]
            sources = json.loads(sources_text)
            value = {
                "skill": {
                    "schema_version": 1, "skill_id": "repeat-safe-turn", "version": 1,
                    "name": "Repeat safe turn", "description": "Turn east using a validated macro.",
                    "task_kind": "recurring-live-pattern", "parameters": [],
                    "preconditions": ["Turning is permitted"],
                    "postconditions": ["Player faces east"],
                    "steps": [{"action": "turn", "arguments": [{"literal": 1}]}],
                    "source_trajectory_ids": sources,
                },
                "rationale": "The successful primitive sequence repeated exactly.",
            }
        elif request.call_id.startswith("reflection"):
            selected_text = request.prompt.split("[selected_evidence]\n", 1)[1].split(
                "\n[current_active_beliefs]", 1
            )[0]
            selected = json.loads(selected_text)
            value = {
                "schema_version": 1,
                "lessons": ["The east-facing turn completed without failure."],
                "failed_assumptions": [], "counterfactuals": [],
                "goals": ["Reuse validated routines when applicable"],
                "beliefs": [],
            }
            assert selected
        else:  # pragma: no cover
            raise AssertionError(request.call_id)
        text = json.dumps(value)
        return ProviderResponse(
            text=text, request_id=f"lifecycle-{len(self.requests)}",
            provider="scripted", model="e1-lifecycle", route="test",
            usage=ProviderUsage(
                input_tokens=request.estimated_input_tokens,
                output_tokens=max(1, len(text) // 4), latency_ms=0, cost_usd=0,
            ),
        )

    def close(self):
        return None


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


def test_live_smoke_materializes_stable_condition_specific_run_configs() -> None:
    study, _ = load_e1_config(Path("configs/e1-live-smoke.yaml"))
    assert study.phase.value == "smoke" and study.horizon_days == 7
    seed = study.seeds[0]
    configs = {
        condition: materialize_run_config(study, condition, seed)
        for condition in study.conditions
    }
    assert len({config.run_id() for config in configs.values()}) == 6
    assert not configs[E1Condition.A_BASE].learning.persistent_memory
    assert configs[E1Condition.B_MEMORY].learning.persistent_memory
    assert not configs[E1Condition.B_MEMORY].learning.retrieval
    assert configs[E1Condition.C_RETRIEVAL].learning.retrieval
    assert configs[E1Condition.D_SKILLS].learning.skills
    assert configs[E1Condition.E_REFINE].learning.refinement
    assert all(config.budget.max_game_days == 7 for config in configs.values())


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


def test_prime_adapter_enforces_manifest_accounts_calls_and_clears_condition_a_state(
    tmp_path: Path,
) -> None:
    response = json.dumps({
        "schema_version": 1, "actions": [{"name": "turn", "arguments": [1]}],
        "goal_updates": ["inspect east field"], "memory_notes": [], "rationale": "observe",
    })
    adapter = _prime_adapter(tmp_path, E1Condition.A_BASE, [response])
    adapter.start(objective=PERSISTENT_OBJECTIVE, run_id=adapter.session.runner.run_id)
    decision = adapter.decide({
        "state": {"day": 1, "weather": "sunny"},
        "allowed_actions": ["turn"], "game_day": 1, "season": "spring",
    })
    assert decision["actions"][0]["name"] == "turn"
    assert adapter.session.state.active_goals == ("inspect east field",)
    adapter.end_day(game_day=1)
    assert adapter.session.state.active_goals == ()
    assert adapter.session.state.decision_count == 1
    events = tuple(adapter.session.runner.events.iter_records())
    accounted = [event for event in events if event.event_type == "e1_inference_accounted"]
    assert len(accounted) == 1 and accounted[0].payload["calls"] == 1
    assert any(event.event_type == "decision_context_composed" for event in events)


def test_live_day_loop_uses_broad_objective_and_records_sleep_as_primitive(tmp_path: Path) -> None:
    response = json.dumps({
        "schema_version": 1, "actions": [], "goal_updates": [],
        "memory_notes": [], "rationale": "day plan complete",
    })
    adapter = _prime_adapter(tmp_path, E1Condition.A_BASE, [response])
    adapter.start(objective=PERSISTENT_OBJECTIVE, run_id=adapter.session.runner.run_id)
    replay = ReplayTransport({
        "observe_v2%2": [_live_observation(1), _live_observation(1), _live_observation(1)],
        "observe_v2%1": [_live_observation(1), _live_observation(2)],
        "sleep": ["Message received"],
    })
    controller = EnvironmentController(
        StarDojoClient(replay), "PrimeStardewSmoke", poll_interval=0,
    )
    callback_phases = []
    result = run_live_days(
        adapter, controller, days=1, max_decisions_per_day=2,
        on_day_complete=lambda _: callback_phases.append(adapter.session.runner.state.phase.value),
    )
    assert result.total_model_decisions == 1
    assert result.total_primitive_actions == 1
    assert result.total_failed_actions == 0
    assert callback_phases == ["day_complete"]
    assert result.days[0].game_date_after["day"] == 2
    events = tuple(adapter.session.runner.events.iter_records())
    actions = [event for event in events if event.event_type == "action_completed"]
    assert len(actions) == 1 and actions[0].payload["name"] == "sleep"
    assert actions[0].payload["privileged"] is False


def test_live_activity_segmentation_uses_events_and_observation_deltas(tmp_path: Path) -> None:
    events = EventStore(tmp_path / "activity.jsonl", "activity-run")
    start = json.loads(_live_observation(1))
    end = json.loads(_live_observation(1))
    start["GameState"]["Time"], end["GameState"]["Time"] = 600, 700
    start["Player"]["Stamina"], end["Player"]["Stamina"] = 100, 98
    events.append("e1_live_day_started", {"elapsed_day": 1, "observation": start})
    events.append("agent_decision", {
        "decision_id": "decision-1",
        "decision": {"actions": [{"name": "use", "arguments": []}]},
    })
    events.append("action_completed", {
        "task_id": "e1-broad-objective", "action_id": "a1", "name": "use",
        "arguments": [], "succeeded": True, "request_id": "r1", "privileged": False,
    })
    events.append("action_completed", {
        "task_id": "e1-skill-validation", "action_id": "validation-a1", "name": "use",
        "arguments": [], "succeeded": True, "request_id": "r2", "privileged": False,
    })
    events.append("e1_live_day_pre_sleep", {"elapsed_day": 1, "observation": end})
    events.append("e1_live_day_completed", {"elapsed_day": 1})
    instances = segment_live_competencies(events.iter_records())
    assert len(instances) == 1
    instance = instances[0]
    assert instance.domain is CompetencyDomain.FARM_MAINTENANCE
    assert instance.model_decisions == 1 and instance.primitive_actions == 1
    assert instance.game_minutes == 60 and instance.energy_used == 2
    assert instance.domain_metrics["segmentation"] == "evaluator_event_heuristic_v1"


def test_condition_f_nightly_learning_creates_validates_and_activates_skill_and_refines(
    tmp_path: Path,
) -> None:
    memory = MemoryStore(tmp_path / "f-memory.sqlite3")
    adapter = _prime_adapter(tmp_path, E1Condition.F_FULL, [], memory=memory)
    provider = E1LifecycleProvider()
    adapter.session.provider = provider
    lifecycle = E1LearningLifecycle(
        harness=adapter, provider=provider,
        skill_store=adapter.skill_store,
        disposable_skill_validator=lambda _: True,
    )
    adapter.start(objective=PERSISTENT_OBJECTIVE, run_id=adapter.session.runner.run_id)
    for day in (1, 2):
        decision = adapter.decide({
            "state": {"day": day}, "allowed_actions": ["turn"],
            "game_day": day, "season": "spring",
        })
        adapter.session.runner.events.append("action_completed", {
            "task_id": "e1-broad-objective", "action_id": f"d{day}-a1",
            "name": "turn", "arguments": [1], "succeeded": True,
            "request_id": f"turn-{day}", "privileged": False,
        })
        adapter.session.runner.events.append("e1_live_day_pre_sleep", {
            "elapsed_day": day, "observation": {"day": day},
        })
        assert decision["actions"][0]["name"] == "turn"
        lifecycle.end_day(day)
        adapter.end_day(game_day=day)
    state = adapter.export_learning_state()
    assert state.active_skill_refs == ("repeat-safe-turn:v1",)
    assert len(state.active_refinement_ids) == 2
    commands = adapter.expand_skill(("repeat-safe-turn:v1",))
    assert [(item.name, item.arguments) for item in commands] == [("turn", (1,))]
    adapter.record_skill_use(
        "repeat-safe-turn:v1", use_id="e1-test-use", success=True,
        primitive_actions=len(commands),
    )
    assert adapter.skill_store.uses("repeat-safe-turn")[0].model_decisions_saved == 0
    assert adapter.session.state.active_goals == ("Reuse validated routines when applicable",)
    categories = [
        event.payload["category"] for event in adapter.session.runner.events.iter_records()
        if event.event_type == "e1_inference_accounted"
    ]
    assert "skill_proposal" in categories and categories.count("reflection") == 2
    assert adapter.skill_store is not None
    adapter.skill_store.close()
    memory.close()


def test_prime_adapter_checkpoint_restore_and_branch_reset_deactivate_memory(tmp_path: Path) -> None:
    memory = MemoryStore(tmp_path / "adapter-memory.sqlite3")
    memory.add_text("Water crops before noon", memory_id="semantic-1")
    memory.add_text("Parsnips prefer spring", kind=MemoryKind.BELIEF, memory_id="belief-1")
    adapter = _prime_adapter(tmp_path, E1Condition.C_RETRIEVAL, [], memory=memory)
    adapter.start(objective=PERSISTENT_OBJECTIVE, run_id=adapter.session.runner.run_id)
    checkpoint = tmp_path / "adapter-checkpoint.json"
    digest = adapter.checkpoint(checkpoint)
    assert len(digest) == 64
    reset = adapter.reset_learning_state(LearningResetSpec(memory=True, beliefs=True))
    assert not reset.active_memory_ids and not reset.active_belief_ids
    assert not memory.records()
    adapter.restore(checkpoint)
    restored = adapter.export_learning_state()
    # Logical state is restored, while the reset copy remains deactivated. Physical probe forks
    # restore their own copied SQLite database before applying a reset.
    assert restored.historical_artifact_ids == ("semantic-1", "belief-1")
    memory.close()


def test_prime_adapter_rejects_runtime_capability_mismatch(tmp_path: Path) -> None:
    adapter = _prime_adapter(tmp_path, E1Condition.A_BASE, [])
    with pytest.raises(HarnessContractError, match="capabilities differ"):
        PrimeHarnessAdapter(
            adapter.session, condition_manifest(E1Condition.B_MEMORY), memory_token_budget=50,
        )


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
