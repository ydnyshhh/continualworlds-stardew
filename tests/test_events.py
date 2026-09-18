import json

import pytest

from prime_stardew.telemetry.events import EventCorruptionError, EventCursor, EventStore


def test_append_reopen_and_cursor_prefix(tmp_path):
    path = tmp_path / "events.jsonl"
    store = EventStore(path, "run-1")
    first = store.append("run_started", {"seed": 7})
    cursor = store.cursor()
    second = store.append("action_completed", {"action": "move right"})

    reopened = EventStore(path, "run-1")
    reopened.verify_cursor(cursor)
    assert [item.sequence for item in reopened.iter_records()] == [1, 2]
    assert list(reopened.events_after(cursor)) == [second]
    assert second.previous_hash == first.event_hash


def test_tamper_is_detected(tmp_path):
    path = tmp_path / "events.jsonl"
    store = EventStore(path, "run-1")
    store.append("observation", {"money": 500})
    value = json.loads(path.read_text(encoding="utf-8"))
    value["payload"]["money"] = 999
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")

    with pytest.raises(EventCorruptionError, match="hash mismatch"):
        EventStore(path, "run-1")


def test_partial_record_and_forged_cursor_are_rejected(tmp_path):
    path = tmp_path / "events.jsonl"
    store = EventStore(path, "run-1")
    store.append("run_started")
    with path.open("ab") as stream:
        stream.write(b'{"partial":')
    with pytest.raises(EventCorruptionError, match="partial"):
        EventStore(path, "run-1")

    clean = EventStore(tmp_path / "clean.jsonl", "run-2")
    clean.append("run_started")
    forged = EventCursor(run_id="run-2", sequence=1, event_hash="0" * 64, byte_offset=1)
    with pytest.raises(EventCorruptionError, match="does not match"):
        clean.verify_cursor(forged)


def test_idempotent_append_returns_existing_and_rejects_conflict(tmp_path):
    store = EventStore(tmp_path / "events.jsonl", "run-1")
    first = store.append_idempotent(
        "action_started", {"action_id": "a1"}, idempotency_key="action:a1:start"
    )
    replay = store.append_idempotent(
        "action_started", {"action_id": "a1"}, idempotency_key="action:a1:start"
    )

    assert replay == first
    assert len(list(store.iter_records())) == 1
    with pytest.raises(Exception, match="different content"):
        store.append_idempotent(
            "action_started", {"action_id": "a2"}, idempotency_key="action:a1:start"
        )
