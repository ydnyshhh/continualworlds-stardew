import json
from pathlib import Path

import pytest

from prime_stardew.lifetime import (
    TransferKind, evaluate_routing, evaluate_transfer_condition, load_transfer_tasks,
    load_lifetime_config,
)
from prime_stardew.m11_gate import _bundle_contamination, _seed_donor_external_state
from prime_stardew.memory import MemoryStore
from prime_stardew.skills import SkillStore, SkillStoreError


def _bundles(tmp_path: Path) -> tuple[Path, Path]:
    memories = MemoryStore(tmp_path / "donor-memory.sqlite3", store_id="donor")
    skills = SkillStore(tmp_path / "donor-skills.sqlite3", store_id="donor")
    _seed_donor_external_state(memories, skills)
    memory_path = tmp_path / "memory.json"
    skill_path = tmp_path / "skills.json"
    memories.export(memory_path, provenance={"model": "donor"})
    skills.export(skill_path, provenance={"model": "donor"})
    memories.close()
    skills.close()
    return memory_path, skill_path


def test_lifetime_configuration_is_immutable_and_complete() -> None:
    config, digest = load_lifetime_config(Path("configs/m11-lifetime-transfer.yaml"))
    assert config.lifetime_days == 112
    assert config.seasons == ("spring", "summer", "fall", "winter")
    assert config.transfer_conditions == tuple(TransferKind)
    assert len(digest) == 64
    with pytest.raises(Exception):
        config.donor_model = "changed"  # type: ignore[misc]


def test_skill_export_import_is_hashed_and_restores_activation(tmp_path: Path) -> None:
    _, skill_path = _bundles(tmp_path)
    recipient = SkillStore(tmp_path / "recipient.sqlite3", store_id="recipient")
    imported = recipient.import_bundle(skill_path)
    assert imported == (("tend-crop-row", 1),)
    assert recipient.active("tend-crop-row") is not None
    value = json.loads(skill_path.read_text(encoding="utf-8"))
    value["skills"][0]["description"] = "tampered"
    skill_path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(SkillStoreError, match="hash mismatch"):
        recipient.import_bundle(skill_path)
    recipient.close()


def test_held_out_fixtures_are_absent_from_donor_bundles(tmp_path: Path) -> None:
    memory_path, skill_path = _bundles(tmp_path)
    assert _bundle_contamination(memory_path, skill_path, load_transfer_tasks()) == []


def test_memory_and_skill_transfers_have_independent_effects(tmp_path: Path) -> None:
    memory_path, skill_path = _bundles(tmp_path)
    tasks = load_transfer_tasks()
    results = {
        condition: evaluate_transfer_condition(
            condition, tasks, tmp_path / "recipients",
            memory_bundle=memory_path, skill_bundle=skill_path,
        )
        for condition in TransferKind
    }
    fresh = results[TransferKind.FRESH_RECIPIENT]
    memory = results[TransferKind.MEMORIES_ONLY]
    skills = results[TransferKind.SKILLS_ONLY]
    full = results[TransferKind.FULL_INHERITANCE]
    assert fresh.success_rate == 0.5
    assert memory.success_rate == full.success_rate == 1
    assert skills.success_rate == 0.5
    assert fresh.model_decisions == memory.model_decisions == 8
    assert skills.model_decisions == full.model_decisions == 4


def test_raw_model_baseline_matches_fresh_recipient(tmp_path: Path) -> None:
    memory_path, skill_path = _bundles(tmp_path)
    tasks = load_transfer_tasks()
    raw = evaluate_transfer_condition(
        TransferKind.RAW_MODEL, tasks, tmp_path / "recipients",
        memory_bundle=memory_path, skill_bundle=skill_path,
    )
    fresh = evaluate_transfer_condition(
        TransferKind.FRESH_RECIPIENT, tasks, tmp_path / "recipients",
        memory_bundle=memory_path, skill_bundle=skill_path,
    )
    assert raw.success_rate == fresh.success_rate
    assert raw.model_decisions == fresh.model_decisions


def test_learned_router_improves_success_and_utility_per_dollar() -> None:
    results = {result.policy: result for result in evaluate_routing()}
    economy = results["economy_only"]
    capable = results["capable_only"]
    learned = results["learned_complexity_router"]
    assert learned.successes == 8
    assert learned.successes > economy.successes
    assert learned.utility_per_dollar > economy.utility_per_dollar
    assert capable.attempted == 6
    assert all(result.within_budget for result in results.values())
