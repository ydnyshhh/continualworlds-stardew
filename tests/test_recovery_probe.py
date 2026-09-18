from datetime import UTC, datetime
from uuid import uuid4

from prime_stardew.recovery_probe import unfinished_action_ids
from prime_stardew.telemetry.events import EventRecord


def _record(sequence, event_type, action_id):
    body = {
        "schema_version": 1,
        "run_id": "run-1",
        "sequence": sequence,
        "event_id": uuid4(),
        "timestamp": datetime.now(UTC),
        "event_type": event_type,
        "payload": {"action_id": action_id},
        "previous_hash": None if sequence == 1 else "1" * 64,
        "event_hash": "2" * 64,
    }
    return EventRecord.model_validate(body)


def test_unfinished_actions_excludes_completed_logical_actions():
    records = [
        _record(1, "action_started", "completed"),
        _record(2, "action_completed", "completed"),
        _record(3, "action_started", "interrupted"),
    ]

    assert unfinished_action_ids(records) == {"interrupted"}
