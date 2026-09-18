import json
from pathlib import Path

import pytest

from prime_stardew.agent import ScriptedProvider
from prime_stardew.experiments.config import load_run_config
from prime_stardew.experiments.provenance import CodeProvenance, RunProvenance, RuntimeProvenance
from prime_stardew.experiments.runner import ExperimentRunner
from prime_stardew.memory import MemoryStatus, MemoryStore
from prime_stardew.shifting import (
    AgentShiftPolicy, ShiftCondition, build_m15_report_from_events, load_m15_preregistration,
    run_m15_condition, shift_world_family,
)
from prime_stardew.telemetry import EventStore


PROVENANCE = RunProvenance(
    code=CodeProvenance(revision="m15-test", dirty=False, dirty_patch_sha256="0" * 64),
    runtime=RuntimeProvenance(platform="test"),
)


def test_world_family_is_seeded_hashable_and_shifts_a_strict_subset() -> None:
    first = shift_world_family(406041616)
    same = shift_world_family(406041616)
    other = shift_world_family(406041617)
    assert first == same
    assert first.world_state_sha256 == same.world_state_sha256
    assert first.world_state_sha256 != other.world_state_sha256
    assert sum(item.shifted for item in first.mechanics) == 2
    assert sum(not item.shifted for item in first.mechanics) == 1
    assert first.sequence == ("A", "B", "A")


def test_observable_world_hash_excludes_hidden_optimal_actions() -> None:
    family = shift_world_family(406041616)
    observable = {
        "family_id": family.family_id,
        "visible_context_cues": family.visible_context_cues,
        "choices": [item.choices for item in family.mechanics],
    }
    encoded = json.dumps(observable)
    assert "optimal_in_a" not in encoded
    assert "optimal_in_b" not in encoded
    assert len(family.observable_state_sha256) == 64


def test_contextual_run_retains_a_beliefs_and_cites_interactions(tmp_path: Path) -> None:
    preregistration, digest = load_m15_preregistration(
        Path("configs/m15-stardew-shift-study.yaml")
    )
    run_m15_condition(
        tmp_path, seed=preregistration.seeds[0],
        condition=ShiftCondition.CONTEXTUAL_BELIEFS,
        trials_per_phase=preregistration.trials_per_phase,
        preregistration_sha256=digest,
    )
    run_root = tmp_path / f"m15-contextual_beliefs-s{preregistration.seeds[0]}"
    events = EventStore(run_root / "events.jsonl", run_root.name)
    records = tuple(events.iter_records())
    interactions = {str(item.event_id) for item in records
                    if item.event_type == "shift_interaction_completed"}
    revisions = [item.payload for item in records if item.event_type == "shift_belief_revised"]
    assert revisions
    assert all(set(item["source_evidence"]) <= interactions for item in revisions)
    contextualized = {item["mechanic_id"] for item in revisions if item["contextualized"]}
    assert contextualized == {"crop_growth", "crop_price"}
    with MemoryStore(run_root / "memory.sqlite3") as memory:
        active = memory.records()
        assert any(":crop_growth:A:" in item.memory_id for item in active)
        assert any(":crop_growth:B:" in item.memory_id for item in active)
        assert all(memory.status(item.memory_id) is MemoryStatus.ACTIVE for item in active)


def test_preregistered_m15_report_reconstructs_all_conditions(tmp_path: Path) -> None:
    preregistration, digest = load_m15_preregistration(
        Path("configs/m15-stardew-shift-study.yaml")
    )
    reduced = preregistration.model_copy(update={"seeds": preregistration.seeds[:2]})
    for seed in reduced.seeds:
        for condition in reduced.conditions:
            run_m15_condition(
                tmp_path, seed=seed, condition=condition,
                trials_per_phase=reduced.trials_per_phase,
                preregistration_sha256=digest,
            )
    report = build_m15_report_from_events(tmp_path, reduced, digest)
    assert report["status"] == "failed"  # two seeds cannot satisfy the preregistered alpha
    assert report["runs"] == 8
    assert report["matched_world_observation_start_hashes"]
    assert report["contextual_minus_global_retention_mean"] == pytest.approx(1)
    assert report["b_adaptation_noninferiority_passed"]
    assert report["contextual_negative_transfer_mean"] == pytest.approx(0)
    assert report["failure_analysis"]["hidden_field_leaks"] == 0


def test_authenticated_shift_policy_repairs_and_hides_optima(tmp_path: Path) -> None:
    runner = ExperimentRunner(
        tmp_path / "runs", load_run_config(Path("configs/m15-live.yaml")), PROVENANCE,
    )
    runner.start()
    valid = json.dumps({"choices": [
        {"mechanic_id": "crop_growth", "response": "contextualize",
         "preferred_action": "beta", "rationale": "Observed reward contradicted A."},
        {"mechanic_id": "crop_price", "response": "retain",
         "preferred_action": "alpha", "rationale": "Observed reward matched A."},
    ]})
    provider = ScriptedProvider(["bad", valid], provider="actual", model="actual-model")
    policy = AgentShiftPolicy(runner, provider)
    decision = policy.decide(
        phase="world-b-adaptation", context_cue="cobalt",
        mechanics=[
            {"mechanic_id": "crop_growth", "choices": ["alpha", "beta"]},
            {"mechanic_id": "crop_price", "choices": ["alpha", "beta"]},
        ],
        observations=[
            {"mechanic_id": "crop_growth", "tried_action": "alpha", "observed_reward": 2},
        ],
        memories=[
            {"mechanic_id": "crop_growth", "context_cue": "amber",
             "preferred_action": "alpha"},
        ],
    )
    assert len(decision.choices) == 2
    assert len(provider.requests) == 2
    assert provider.requests[1].repair
    assert "optimal_in_a" not in provider.requests[0].prompt
    assert "optimal_in_b" not in provider.requests[0].prompt
