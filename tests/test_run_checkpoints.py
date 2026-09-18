import json

import pytest

from prime_stardew.env.checkpoints import CheckpointError, CheckpointManager
from prime_stardew.env.models import GameDate
from prime_stardew.experiments.checkpoints import RunCheckpointManager
from prime_stardew.memory import MemoryStore
from prime_stardew.telemetry.events import EventStore


def _make_save(saves, save_id):
    source = saves / save_id
    source.mkdir(parents=True)
    (source / save_id).write_text("current", encoding="utf-8")
    (source / f"{save_id}_old").write_text("old", encoding="utf-8")


def test_combined_checkpoint_round_trip_and_resume_boundary(tmp_path):
    saves = tmp_path / "saves"
    _make_save(saves, "Fixture_1")
    events = EventStore(tmp_path / "events.jsonl", "experiment-1")
    events.append("day_completed", {"day": 5})
    memory_path = tmp_path / "memory.sqlite3"
    memory = MemoryStore(memory_path)
    memory.add_text("Water crops before noon.", memory_id="watering-rule")
    memory.close()
    manager = RunCheckpointManager(
        CheckpointManager(saves, stable_checks=2, stable_interval=0, stable_timeout=1)
    )
    bundle = tmp_path / "checkpoint"
    manifest = manager.create(
        run_id="experiment-1",
        destination=bundle,
        save_id="Fixture_1",
        player="Fixture",
        game_date=GameDate(year=1, season="spring", day=6),
        agent_state={"step": 42, "memory": ["watered crops"]},
        configuration={"policy": "scripted", "seed": 7},
        event_store=events,
        memory_database=memory_path,
        environment={"game_version": "1.6.15"},
    )

    interrupted = events.append("action_started", {"action": "move"})
    manager.verify(bundle, event_store=events)
    restored = manager.restore(
        bundle, "Restored_1", event_store=events,
        destination_memory_path=tmp_path / "restored-memory.sqlite3",
    )

    assert restored.event_cursor == manifest.event_cursor
    assert restored.agent_state["step"] == 42
    assert restored.configuration["seed"] == 7
    assert (restored.game_save_path / "Restored_1").read_text(encoding="utf-8") == "current"
    assert list(events.events_after(restored.event_cursor)) == [interrupted]
    assert restored.memory_database == (tmp_path / "restored-memory.sqlite3").resolve()
    restored_memory = MemoryStore(restored.memory_database)
    assert restored_memory.get("watering-rule").text == "Water crops before noon."  # type: ignore[union-attr]
    restored_memory.close()


def test_combined_checkpoint_detects_agent_state_tamper(tmp_path):
    saves = tmp_path / "saves"
    _make_save(saves, "Fixture_1")
    events = EventStore(tmp_path / "events.jsonl", "experiment-1")
    manager = RunCheckpointManager(
        CheckpointManager(saves, stable_checks=2, stable_interval=0, stable_timeout=1)
    )
    bundle = tmp_path / "checkpoint"
    manager.create(
        run_id="experiment-1",
        destination=bundle,
        save_id="Fixture_1",
        player="Fixture",
        game_date=GameDate(year=1, season="spring", day=1),
        agent_state={"step": 1},
        configuration={"seed": 7},
        event_store=events,
    )
    (bundle / "agent" / "state.json").write_text(json.dumps({"step": 2}), encoding="utf-8")
    with pytest.raises(CheckpointError, match="hash mismatch"):
        manager.verify(bundle)
