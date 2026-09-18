import json
from pathlib import Path

import pytest

from prime_stardew.agent import ScriptedProvider
from prime_stardew.corruption import (
    AgentMemoryRepairPolicy, CorruptionType, M16Condition,
    build_m16_report_from_events, corruption_family, load_m16_preregistration,
    run_m16_condition,
)
from prime_stardew.experiments.config import load_run_config
from prime_stardew.experiments.provenance import CodeProvenance, RunProvenance, RuntimeProvenance
from prime_stardew.experiments.runner import ExperimentRunner
from prime_stardew.memory import MemoryStatus, MemoryStore
from prime_stardew.telemetry import EventStore


PROVENANCE = RunProvenance(
    code=CodeProvenance(revision="m16-test", dirty=False, dirty_patch_sha256="0" * 64),
    runtime=RuntimeProvenance(platform="test"),
)


def test_corruption_family_has_all_types_decoys_and_separate_observable_hash() -> None:
    family = corruption_family(1601)
    same = corruption_family(1601)
    other = corruption_family(1602)
    assert family == same
    assert family.world_state_sha256 == same.world_state_sha256
    assert family.world_state_sha256 != other.world_state_sha256
    for episode in (1, 2):
        rows = [item for item in family.scenarios if item.episode == episode]
        assert {item.corruption_type for item in rows if item.corruption_type} == set(CorruptionType)
        assert sum(item.corruption_type is None for item in rows) == 2
    visible = json.dumps([item.observable_record() for item in family.scenarios])
    assert "correct_action" not in visible
    assert "corruption_type" not in visible
    assert len(family.observable_state_sha256) == 64


def test_adaptive_run_supersedes_records_with_exact_evidence(tmp_path: Path) -> None:
    preregistration, digest = load_m16_preregistration(
        Path("configs/m16-memory-corruption-study.yaml")
    )
    seed = preregistration.seeds[0]
    run_m16_condition(
        tmp_path, seed=seed, condition=M16Condition.ADAPTIVE_REPAIR,
        interactions_per_record=preregistration.interactions_per_record,
        initial_verification_threshold=preregistration.initial_verification_threshold,
        preregistration_sha256=digest,
    )
    run_root = tmp_path / f"m16-adaptive_repair-s{seed}"
    events = EventStore(run_root / "events.jsonl", run_root.name)
    records = tuple(events.iter_records())
    interaction_ids = {
        str(item.event_id) for item in records
        if item.event_type == "corruption_interaction_completed"
    }
    repairs = [item.payload for item in records if item.event_type == "memory_repaired"]
    patches = [item.payload for item in records if item.event_type == "memory_policy_patch_applied"]
    assert len(repairs) == 10
    assert len(patches) == 1
    assert patches[0]["old_threshold"] == 2
    assert patches[0]["new_threshold"] == 1
    assert all(set(item["source_evidence"]) <= interaction_ids for item in repairs)
    with MemoryStore(run_root / "memory.sqlite3") as memory:
        repaired = [item for item in memory.records() if "repaired" in item.tags]
        assert len(repaired) == 10
        assert all(memory.status(item.supersedes_id) is MemoryStatus.SUPERSEDED for item in repaired)
        assert all(set(item.source_event_ids) <= interaction_ids for item in repaired)


def test_m16_report_reconstructs_paired_effect(tmp_path: Path) -> None:
    preregistration, digest = load_m16_preregistration(
        Path("configs/m16-memory-corruption-study.yaml")
    )
    reduced = preregistration.model_copy(update={"seeds": preregistration.seeds[:2]})
    for seed in reduced.seeds:
        for condition in reduced.conditions:
            run_m16_condition(
                tmp_path, seed=seed, condition=condition,
                interactions_per_record=reduced.interactions_per_record,
                initial_verification_threshold=reduced.initial_verification_threshold,
                preregistration_sha256=digest,
            )
    report = build_m16_report_from_events(tmp_path, reduced, digest)
    assert report["status"] == "failed"  # two pairs cannot satisfy alpha=.05
    assert report["runs"] == 8
    assert report["paired_runs"] == 2
    assert report["adaptive_minus_fixed_episode_2_utility_retained_mean"] > 0
    assert report["episode_1_performance_matched_before_patch"]
    assert report["clean_false_repairs"] == 0
    assert report["failure_analysis"]["hidden_field_leaks"] == 0


def test_authenticated_policy_repairs_invalid_json_and_hides_truth(tmp_path: Path) -> None:
    runner = ExperimentRunner(
        tmp_path / "runs", load_run_config(Path("configs/m16-live.yaml")), PROVENANCE,
    )
    runner.start()
    valid = json.dumps({"choices": [
        {"scenario_id": "record-a", "response": "supersede",
         "preferred_action": "beta", "rationale": "The observed result contradicted the record."},
    ]})
    provider = ScriptedProvider(["bad", valid], provider="actual", model="actual-model")
    policy = AgentMemoryRepairPolicy(runner, provider)
    decision = policy.decide(
        episode=1,
        records=[{
            "scenario_id": "record-a", "alternatives": ["alpha", "beta"],
            "memory_text": "Choose alpha", "recommended_action": "alpha",
        }],
        observations=[{
            "scenario_id": "record-a", "tried_action": "alpha", "observed_utility": 1,
            "remembered_expected_utility": 10,
        }],
    )
    assert decision.choices[0].preferred_action == "beta"
    assert len(provider.requests) == 2
    assert provider.requests[1].repair
    assert "correct_action" not in provider.requests[0].prompt
    assert "corruption_type" not in provider.requests[0].prompt


def test_policy_patch_is_restricted_data_only(tmp_path: Path) -> None:
    runner = ExperimentRunner(
        tmp_path / "runs", load_run_config(Path("configs/m16-live.yaml")), PROVENANCE,
    )
    runner.start()
    valid = json.dumps({
        "mechanism_name": "contradiction_counter",
        "tracked_signal": "Count outcomes that contradict a retrieved memory.",
        "state_update_rule": "Increment after an observed contradictory outcome.",
        "trigger": {"signal": "contradiction_count", "comparison": ">=", "threshold": 1},
        "repair_response": "supersede",
        "rationale": "The first episode showed reliable corrective evidence.",
        "success_criterion": "Lower utility loss without changing stable records.",
    })
    provider = ScriptedProvider([valid])
    patch = AgentMemoryRepairPolicy(runner, provider).propose_policy_patch(
        summary={"contradictions": 5, "successful_followups": 5, "stable_matches": 2}
    )
    assert patch.trigger.threshold == 1
    assert patch.mechanism_name == "contradiction_counter"
    assert "source reliability" not in provider.requests[0].prompt.lower()
