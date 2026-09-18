import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from prime_stardew.agent import ContextInputs, PrimeSession, ScriptedProvider
from prime_stardew.env.models import GameDate
from prime_stardew.experiments.config import (
    ContextBudgetConfig, ExperimentBudget, FixtureConfig, LearningConditionConfig,
    ProviderConfig, RunConfig,
)
from prime_stardew.experiments.provenance import (
    CodeProvenance, RunProvenance, RuntimeProvenance,
)
from prime_stardew.experiments.runner import ExperimentRunner
from prime_stardew.memory import (
    MemoryKind, MemoryQuery, MemoryRecord, MemoryStatus, MemoryStore, MemoryStoreError,
)


PROVENANCE = RunProvenance(
    code=CodeProvenance(revision="m6-test", dirty=False, dirty_patch_sha256="0" * 64),
    runtime=RuntimeProvenance(
        game_version="1.6.15", smapi_version="4.5.2",
        stardojo_version="1.0.0-patched", stardojo_dll_sha256="1" * 64,
        python_version="3.13", platform="test",
    ),
)


def test_retrieval_filters_metadata_validity_and_ranks_keywords(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite3")
    store.add(MemoryRecord(
        memory_id="relevant", kind="semantic",
        text="Watering Can is stored in inventory slot five for crop watering.",
        season="spring", map_name="Farm", task_domain="watering",
        valid_from_day=1, valid_to_day=28, confidence=0.9,
    ))
    store.add(MemoryRecord(
        memory_id="wrong-season", kind="semantic",
        text="Watering Can is in slot two.", season="winter", task_domain="watering",
    ))
    store.add(MemoryRecord(
        memory_id="expired", kind="semantic",
        text="Watering Can used to be in slot one.", season="spring",
        task_domain="watering", valid_to_day=3,
    ))

    result = store.retrieve(MemoryQuery(
        text="Which inventory slot has the Watering Can for watering crops?",
        season="spring", map_name="Farm", task_domain="watering", game_day=8,
        token_budget=100,
    ))

    assert [record.memory_id for record in result.selected] == ["relevant"]
    assert result.candidates[0].features.keyword_overlap > 0
    assert result.candidates[0].features.metadata_score == pytest.approx(0.75)
    store.close()


def test_superseding_record_preserves_versions_and_append_only_status(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite3")
    original = MemoryRecord(
        memory_id="belief-v1", kind=MemoryKind.BELIEF, text="Parsnips take five days.",
    )
    revised = MemoryRecord(
        memory_id="belief-v2", kind=MemoryKind.BELIEF, text="Parsnips take four days.",
        supersedes_id="belief-v1", version=2,
    )
    store.add(original)
    store.add(revised)

    assert store.status("belief-v1") is MemoryStatus.SUPERSEDED
    assert store.status("belief-v2") is MemoryStatus.ACTIVE
    assert store.get("belief-v1") is None
    assert store.get("belief-v1", include_inactive=True) == original
    with pytest.raises(MemoryStoreError, match="increment"):
        store.add(MemoryRecord(
            memory_id="belief-v4", kind="belief", text="bad version",
            supersedes_id="belief-v2", version=4,
        ))
    store.close()


def test_export_import_is_hashed_and_retains_provenance(tmp_path: Path) -> None:
    source = MemoryStore(tmp_path / "source.sqlite3", store_id="source-run")
    source.add_text(
        "Step onto a cleared twig tile to collect Wood.", memory_id="wood-rule",
        source_event_ids=("event-7",), task_domain="debris",
    )
    bundle_path = tmp_path / "memory-export.json"
    bundle = source.export(bundle_path, provenance={"run_id": "run-7"})
    source.close()

    target = MemoryStore(tmp_path / "target.sqlite3", store_id="recipient")
    assert target.import_bundle(bundle_path) == ("wood-rule",)
    assert target.get("wood-rule").source_event_ids == ("event-7",)  # type: ignore[union-attr]
    assert bundle.provenance == {"run_id": "run-7"}

    value = json.loads(bundle_path.read_text(encoding="utf-8"))
    value["records"][0]["text"] = "tampered"
    bundle_path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(MemoryStoreError, match="hash mismatch"):
        target.import_bundle(bundle_path)
    target.close()


def test_session_retrieves_memory_logs_rank_evidence_and_writes_notes(tmp_path: Path) -> None:
    config = RunConfig(
        suite="m6", condition="memory-retrieval", seed=1, observation_mode="replay",
        fixture=FixtureConfig(
            save_id="Fixture_1", player="PrimeStardewSmoke",
            starting_date=GameDate(year=1, season="spring", day=8),
        ),
        tasks=("water",), provider=ProviderConfig(),
        context=ContextBudgetConfig(memories_tokens=128),
        learning=LearningConditionConfig(
            recent_context=True, persistent_memory=True, retrieval=True,
            skills=False, refinement=False,
        ),
        budget=ExperimentBudget(
            max_actions=5, max_game_days=1, max_model_calls=2,
            max_input_tokens=10000, max_output_tokens=1000,
            max_cost_usd=1, max_wall_seconds=60,
        ),
    )
    runner = ExperimentRunner(tmp_path / "runs", config, PROVENANCE)
    runner.start()
    store = MemoryStore(tmp_path / "memory.sqlite3")
    store.add_text(
        "Watering Can is in inventory slot five.", memory_id="slot-rule",
        season="spring", map_name="Farm", task_domain="watering",
    )
    response = json.dumps({
        "schema_version": 1,
        "actions": [{"name": "choose_item", "arguments": [5]}],
        "goal_updates": [],
        "memory_notes": ["The watering tool was found in slot five."],
        "rationale": "retrieved",
    })
    provider = ScriptedProvider([response])
    session = PrimeSession(runner, provider, memory_store=store)

    session.decide(
        ContextInputs(
            objective="Select the watering tool", observation="Farm in spring",
            allowed_actions=("choose_item",),
        ),
        memory_query=MemoryQuery(
            text="watering tool inventory slot", season="spring", map_name="Farm",
            task_domain="watering", game_day=8, token_budget=128,
        ),
    )

    assert "slot-rule" in provider.requests[0].prompt
    events = list(runner.events.iter_records())
    retrieval = next(event for event in events if event.event_type == "memory_retrieval")
    assert retrieval.payload["selected_ids"] == ["slot-rule"]
    assert retrieval.payload["candidates"][0]["features"]["total_score"] > 0
    assert any(event.event_type == "memory_write" for event in events)
    written = store.get(f"{runner.run_id}:decision-1:note-1")
    assert written is not None and written.source_event_ids
    store.close()


def test_memory_disabled_removes_memories_from_context(tmp_path: Path) -> None:
    config = RunConfig(
        suite="m6", condition="stateless", seed=2, observation_mode="replay",
        fixture=FixtureConfig(
            save_id="Fixture_1", player="PrimeStardewSmoke",
            starting_date=GameDate(year=1, season="spring", day=8),
        ), tasks=("test",), provider=ProviderConfig(),
        learning=LearningConditionConfig(
            recent_context=False, persistent_memory=False, retrieval=False,
            skills=False, refinement=False,
        ),
        budget=ExperimentBudget(
            max_actions=5, max_game_days=1, max_model_calls=1,
            max_input_tokens=10000, max_output_tokens=1000,
            max_cost_usd=1, max_wall_seconds=60,
        ),
    )
    runner = ExperimentRunner(tmp_path / "runs", config, PROVENANCE)
    runner.start()
    response = json.dumps({
        "schema_version": 1, "actions": [], "goal_updates": [],
        "memory_notes": [], "rationale": "none",
    })
    provider = ScriptedProvider([response])
    session = PrimeSession(runner, provider)
    session.decide(ContextInputs(
        objective="test", observation="test",
        memories=(), recent_events=(),
    ))
    assert "[retrieved_memories]\n[]" in provider.requests[0].prompt
    store_events = [e for e in runner.events.iter_records() if e.event_type.startswith("memory_")]
    assert store_events == []
