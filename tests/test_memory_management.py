import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from prime_stardew.agent import ScriptedProvider
from prime_stardew.experiments.config import load_run_config
from prime_stardew.experiments.config import MemoryManagementConfig, MemoryManagementPolicy
from prime_stardew.experiments.provenance import CodeProvenance, RunProvenance, RuntimeProvenance
from prime_stardew.experiments.runner import ExperimentRunner
from prime_stardew.memory import (
    AgentMemoryManagementPolicy, MemoryBudgetManager, MemoryKind, MemoryManagementAction,
    MemoryManagementDecision,
    MemoryManagementError, MemoryManagementOperation, MemoryQuery, MemoryRecord,
    MemoryStatus, MemoryStore,
)
from prime_stardew.memory.research import (
    build_m12_report_from_events, load_m12_preregistration, run_condition,
)


BASE_TIME = datetime(2026, 1, 1, tzinfo=UTC)
PROVENANCE = RunProvenance(
    code=CodeProvenance(revision="m12-test", dirty=False, dirty_patch_sha256="0" * 64),
    runtime=RuntimeProvenance(
        game_version="1.6.15", smapi_version="4.5.2", stardojo_version="1.0.0-patched",
        stardojo_dll_sha256="1" * 64, python_version="3.13", platform="test",
    ),
)


def _store(tmp_path: Path, count: int = 4) -> MemoryStore:
    store = MemoryStore(tmp_path / "memory.sqlite3")
    for index in range(count):
        store.add(MemoryRecord(
            memory_id=f"m{index}", kind=MemoryKind.SEMANTIC,
            text=f"rule-{index}-x", created_at=BASE_TIME + timedelta(days=index),
            confidence=0.5 + index / 10, tags=(f"term{index}",),
        ))
    return store


def _config(policy: MemoryManagementPolicy, *, budget: int = 6) -> MemoryManagementConfig:
    return MemoryManagementConfig(
        enabled=True, max_active_tokens=budget, policy=policy,
        overflow_fallback=MemoryManagementPolicy.FIFO,
    )


def test_fifo_enforces_exact_token_budget_without_deleting_history(tmp_path: Path) -> None:
    store = _store(tmp_path)
    report = MemoryBudgetManager(store, _config(MemoryManagementPolicy.FIFO, budget=4)).enforce()
    assert report.after_active_tokens <= 4
    assert report.retained_ids == ("m2", "m3")
    assert store.status("m0") is MemoryStatus.DEACTIVATED
    assert store.get("m0", include_inactive=True) is not None
    store.close()


def test_lru_retains_recently_retrieved_memory(tmp_path: Path) -> None:
    store = _store(tmp_path)
    selected = store.retrieve(MemoryQuery(text="term0", limit=1, token_budget=100)).selected
    assert selected[0].memory_id == "m0"
    report = MemoryBudgetManager(
        store, _config(MemoryManagementPolicy.LEAST_RECENTLY_USED, budget=3),
    ).enforce()
    assert report.retained_ids == ("m0",)
    assert store.access_stats("m0").retrieval_count == 1
    store.close()


def test_least_retrieved_keeps_highest_access_count(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for _ in range(2):
        store.retrieve(MemoryQuery(text="term1", limit=1, token_budget=100))
    store.retrieve(MemoryQuery(text="term0", limit=1, token_budget=100))
    report = MemoryBudgetManager(
        store, _config(MemoryManagementPolicy.LEAST_RETRIEVED, budget=3),
    ).enforce()
    assert report.retained_ids == ("m1",)
    store.close()


def test_fixed_seed_random_is_reproducible(tmp_path: Path) -> None:
    retained = []
    for suffix in ("a", "b"):
        store = _store(tmp_path / suffix)
        config = _config(MemoryManagementPolicy.RANDOM_FIXED_SEED)
        config = config.model_copy(update={"random_seed": 17})
        retained.append(MemoryBudgetManager(store, config).enforce().retained_ids)
        store.close()
    assert retained[0] == retained[1]


def test_agent_decision_rejects_unknown_duplicate_and_overflow(tmp_path: Path) -> None:
    store = _store(tmp_path, 2)
    manager = MemoryBudgetManager(
        store,
        MemoryManagementConfig(
            enabled=True, max_active_tokens=3,
            policy=MemoryManagementPolicy.AGENT_SELECTED,
            overflow_fallback=MemoryManagementPolicy.AGENT_SELECTED,
        ),
    )
    unknown = MemoryManagementDecision(
        actions=(MemoryManagementAction(
            memory_id="unknown", operation=MemoryManagementOperation.RETAIN,
        ),), reasoning_summary="retain unknown",
    )
    with pytest.raises(MemoryManagementError, match="cover every active"):
        manager.enforce(unknown)
    with pytest.raises(Exception, match="duplicate"):
        MemoryManagementDecision(
            actions=(
                MemoryManagementAction(memory_id="m0", operation="retain"),
                MemoryManagementAction(memory_id="m0", operation="deactivate"),
            ), reasoning_summary="duplicate",
        )
    overflow = MemoryManagementDecision(
        actions=tuple(MemoryManagementAction(
            memory_id=f"m{index}", operation=MemoryManagementOperation.RETAIN,
        ) for index in range(2)), reasoning_summary="retain all",
    )
    with pytest.raises(MemoryManagementError, match="exceeding"):
        manager.enforce(overflow)
    store.close()


def test_agent_decision_rejects_inactive_memory_id(tmp_path: Path) -> None:
    store = _store(tmp_path, 2)
    store.deactivate("m1", reason="prior-capacity-decision")
    manager = MemoryBudgetManager(
        store,
        MemoryManagementConfig(
            enabled=True, max_active_tokens=3,
            policy=MemoryManagementPolicy.AGENT_SELECTED,
            overflow_fallback=MemoryManagementPolicy.AGENT_SELECTED,
        ),
    )
    decision = MemoryManagementDecision(
        actions=(
            MemoryManagementAction(memory_id="m0", operation="retain"),
            MemoryManagementAction(memory_id="m1", operation="deactivate"),
        ),
        reasoning_summary="incorrectly includes an inactive record",
    )
    with pytest.raises(MemoryManagementError, match="cover every active"):
        manager.enforce(decision)
    store.close()


def test_agent_selection_applies_explicit_logged_fallback(tmp_path: Path) -> None:
    store = _store(tmp_path)
    manager = MemoryBudgetManager(store, _config(MemoryManagementPolicy.AGENT_SELECTED))
    report = manager.enforce(None)
    assert report.fallback_applied
    assert report.after_active_tokens <= report.budget_tokens
    store.close()


def test_agent_policy_repairs_once_with_original_candidates_and_records_calls(tmp_path: Path) -> None:
    store = _store(tmp_path / "memory", 2)
    runner = ExperimentRunner(
        tmp_path / "runs", load_run_config(Path("configs/m12-live.yaml")), PROVENANCE,
    )
    runner.start()
    valid = json.dumps({
        "actions": [
            {"memory_id": "m0", "operation": "retain"},
            {"memory_id": "m1", "operation": "deactivate"},
        ],
        "consolidations": [],
        "reasoning_summary": "retain the first rule",
        "predicted_future_value": {"m0": 0.8, "m1": 0.2},
    })
    provider = ScriptedProvider(["invalid", valid])
    decision = AgentMemoryManagementPolicy(runner, provider).decide(
        store.records(), budget_tokens=3,
    )
    assert decision.actions[0].memory_id == "m0"
    assert len(provider.requests) == 2
    assert provider.requests[1].repair
    assert '"memory_id": "m0"' in provider.requests[1].prompt
    calls = [event for event in runner.events.iter_records()
             if event.event_type == "model_call_completed"]
    assert len(calls) == 2
    store.close()


def test_v2_export_import_preserves_complete_status_history(tmp_path: Path) -> None:
    source = _store(tmp_path / "source", 2)
    source.deactivate("m0", reason="capacity")
    history = source.status_history()
    bundle_path = tmp_path / "bundle.json"
    bundle = source.export(bundle_path)
    assert bundle.schema_version == 2
    target = MemoryStore(tmp_path / "target.sqlite3")
    target.import_bundle(bundle_path)
    assert target.status_history() == history
    assert target.status("m0") is MemoryStatus.DEACTIVATED
    source.close()
    target.close()


def test_snapshot_restores_active_budget_state_and_superseded_is_ineligible(tmp_path: Path) -> None:
    store = _store(tmp_path / "source", 2)
    store.add(MemoryRecord(
        memory_id="m1-v2", kind=MemoryKind.SEMANTIC, text="revised-rule",
        created_at=BASE_TIME + timedelta(days=3), supersedes_id="m1", version=2,
    ))
    MemoryBudgetManager(store, _config(MemoryManagementPolicy.FIFO, budget=3)).enforce()
    active_before = tuple(record.memory_id for record in store.records())
    snapshot = store.snapshot(tmp_path / "checkpoint" / "memory.sqlite3")
    store.close()
    restored = MemoryStore(snapshot)
    assert tuple(record.memory_id for record in restored.records()) == active_before
    assert restored.status("m1") is MemoryStatus.SUPERSEDED
    restored.close()


def test_m12_preregistration_and_raw_event_report(tmp_path: Path) -> None:
    preregistration, digest = load_m12_preregistration(Path("configs/m12-memory-study.yaml"))
    assert len(preregistration.seeds) == 8
    seed = preregistration.seeds[0]
    for condition in preregistration.conditions:
        run_condition(
            tmp_path, seed=seed, policy=condition,
            max_active_tokens=preregistration.max_active_tokens,
            preregistration_sha256=digest,
        )
    reduced = preregistration.model_copy(update={"seeds": (seed,)})
    report = build_m12_report_from_events(tmp_path, reduced, digest)
    # One pair cannot pass the preregistered exact-test threshold, but raw reconstruction works.
    assert report["runs"] == 5
    assert report["matched_candidate_states"]
    assert report["condition_summaries"]["agent_selected"]["mean_held_out_utility"] == 2
    assert report["condition_summaries"]["fifo"]["mean_held_out_utility"] == 1
    assert report["manual_score_edits"] == 0
