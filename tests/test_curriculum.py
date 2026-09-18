import json
from pathlib import Path

from prime_stardew.agent import ScriptedProvider
from prime_stardew.curriculum import (
    AgentCurriculumPolicy, EvidenceRegime, M17Condition,
    build_m17_report_from_events, curriculum_family, load_m17_preregistration,
    run_m17_condition,
)
from prime_stardew.experiments.config import load_run_config
from prime_stardew.experiments.provenance import CodeProvenance, RunProvenance, RuntimeProvenance
from prime_stardew.experiments.runner import ExperimentRunner
from prime_stardew.telemetry import EventStore


PROVENANCE = RunProvenance(
    code=CodeProvenance(revision="m17-test", dirty=False, dirty_patch_sha256="0" * 64),
    runtime=RuntimeProvenance(platform="test"),
)


def test_family_separates_training_view_from_heldout_tasks() -> None:
    family = curriculum_family(1704)
    assert len(family.initial_weaknesses) == 2
    assert set(family.diagnostic_scores) == set(EvidenceRegime)
    assert len(family.heldout_tasks) == 8
    training = json.dumps({
        "scores": family.diagnostic_scores,
        "catalog": [item.model_dump(mode="json") for item in family.practice_catalog],
    })
    assert "heldout-" not in training
    assert "correct_action" not in training
    assert len(family.training_view_sha256) == 64
    assert len(family.heldout_manifest_sha256) == 64


def test_agent_curriculum_has_equal_cost_and_zero_contamination(tmp_path: Path) -> None:
    registration, digest = load_m17_preregistration(
        Path("configs/m17-noisy-curriculum-study.yaml")
    )
    seed = registration.seeds[0]
    run_m17_condition(
        tmp_path, seed=seed, condition=M17Condition.AGENT_GENERATED_CURRICULUM,
        practice_budget=registration.practice_budget,
        heldout_tasks_per_regime=registration.heldout_tasks_per_regime,
        preregistration_sha256=digest,
    )
    run_id = f"m17-agent_generated_curriculum-s{seed}"
    events = EventStore(tmp_path / run_id / "events.jsonl", run_id)
    records = tuple(events.iter_records())
    proposal = next(x.payload for x in records if x.event_type == "curriculum_proposed")
    practices = [x.payload for x in records if x.event_type == "practice_objective_completed"]
    heldout = [x.payload for x in records if x.event_type == "heldout_evidence_task_completed"]
    assert proposal["heldout_task_ids_exposed"] is False
    assert sum(item["cost"] for item in practices) == 2
    assert len({item["capability"] for item in practices}) == 2
    assert all(item["correct"] for item in heldout)
    assert all(item["answer_exposed_before_decision"] is False for item in heldout)


def test_report_reconstructs_positive_hidden_transfer_effect(tmp_path: Path) -> None:
    registration, digest = load_m17_preregistration(
        Path("configs/m17-noisy-curriculum-study.yaml")
    )
    reduced = registration.model_copy(update={"seeds": registration.seeds[:2]})
    for seed in reduced.seeds:
        for condition in reduced.conditions:
            run_m17_condition(
                tmp_path, seed=seed, condition=condition,
                practice_budget=reduced.practice_budget,
                heldout_tasks_per_regime=reduced.heldout_tasks_per_regime,
                preregistration_sha256=digest,
            )
    report = build_m17_report_from_events(tmp_path, reduced, digest)
    assert report["status"] == "failed"  # two pairs cannot satisfy alpha=.05
    assert report["runs"] == 8
    assert report["agent_minus_fixed_heldout_accuracy_mean"] > 0
    assert report["agent_noninferior_to_weakness_scripted"]
    assert report["contamination_events"] == 0


def test_provider_curriculum_validates_ids_budget_and_repair(tmp_path: Path) -> None:
    runner = ExperimentRunner(
        tmp_path / "runs", load_run_config(Path("configs/m17-live.yaml")), PROVENANCE,
    )
    runner.start()
    valid = json.dumps({
        "observed_weakness": ["source_duplication", "source_spoofing"],
        "evidence": ["diag-dup", "diag-spoof"],
        "proposed_training_task": ["practice-source_duplication", "practice-source_spoofing"],
        "expected_transfer": "Improve held-out evidence aggregation.",
        "estimated_cost": 2,
        "success_criterion": "Higher hidden evaluation accuracy.",
    })
    provider = ScriptedProvider(["bad", valid], provider="actual", model="actual-model")
    proposal = AgentCurriculumPolicy(runner, provider).propose(
        diagnostics=[
            {"diagnostic_id": "diag-dup", "score": 0},
            {"diagnostic_id": "diag-spoof", "score": 0},
        ],
        catalog=[
            {"objective_id": "practice-source_duplication"},
            {"objective_id": "practice-source_spoofing"},
        ], budget=2,
    )
    assert len(proposal.proposed_training_task) == 2
    assert len(provider.requests) == 2
    assert provider.requests[1].repair
    assert "heldout-" not in provider.requests[0].prompt


def test_adversarial_streams_require_distinct_defenses() -> None:
    family = curriculum_family(1704)
    duplicate = next(x for x in family.heldout_tasks if x.regime is EvidenceRegime.SOURCE_DUPLICATION)
    spoof = next(x for x in family.heldout_tasks if x.regime is EvidenceRegime.SOURCE_SPOOFING)
    assert duplicate.observations[-1].claim != duplicate.correct_action
    assert len({x.source_id for x in duplicate.observations}) < len(duplicate.observations)
    assert spoof.observations[-1].authenticated is False
    assert spoof.observations[-1].claim != spoof.correct_action
