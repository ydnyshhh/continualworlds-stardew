"""Append-only, hash-chained JSONL experiment events."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from prime_stardew.env.errors import StarDojoError


class EventStoreError(StarDojoError):
    """An event store operation failed."""


class EventCorruptionError(EventStoreError):
    """The append-only event chain is malformed or has been changed."""


class EventCursor(BaseModel):
    """An authenticated position immediately after an event record."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    sequence: int = Field(ge=0)
    event_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    byte_offset: int = Field(ge=0)


class EventRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    run_id: str
    sequence: int = Field(ge=1)
    event_id: UUID
    timestamp: datetime
    event_type: str
    payload: dict[str, Any]
    previous_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    event_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class EventStore:
    """Single-writer event log that refuses malformed or altered history."""

    def __init__(self, path: Path, run_id: str) -> None:
        if not run_id.strip():
            raise EventStoreError("run_id must not be empty")
        self.path = path.resolve()
        self.run_id = run_id
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.touch()
        self._records, self._offsets = self._scan()

    def append(self, event_type: str, payload: dict[str, Any] | None = None) -> EventRecord:
        if not event_type.strip():
            raise EventStoreError("event_type must not be empty")
        with self._lock:
            # Re-scan before every mutation so external changes never get appended over.
            self._records, self._offsets = self._scan()
            previous_hash = self._records[-1].event_hash if self._records else None
            body = {
                "schema_version": 1,
                "run_id": self.run_id,
                "sequence": len(self._records) + 1,
                "event_id": str(uuid4()),
                "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "event_type": event_type,
                "payload": payload or {},
                "previous_hash": previous_hash,
            }
            record = EventRecord.model_validate({**body, "event_hash": _hash_body(body)})
            encoded = _canonical_json(record.model_dump(mode="json")).encode("utf-8") + b"\n"
            with self.path.open("ab", buffering=0) as stream:
                written = stream.write(encoded)
                if written != len(encoded):
                    raise EventStoreError("Event append was incomplete")
                os.fsync(stream.fileno())
            self._records.append(record)
            self._offsets.append(self.path.stat().st_size)
            return record

    def append_idempotent(
        self,
        event_type: str,
        payload: dict[str, Any] | None,
        *,
        idempotency_key: str,
    ) -> EventRecord:
        """Append once, returning the existing identical event on replay."""

        if not idempotency_key.strip():
            raise EventStoreError("idempotency_key must not be empty")
        body = dict(payload or {})
        if "idempotency_key" in body:
            raise EventStoreError("payload must not define idempotency_key")
        body["idempotency_key"] = idempotency_key
        with self._lock:
            records, _ = self._scan()
            matches = [
                record
                for record in records
                if record.payload.get("idempotency_key") == idempotency_key
            ]
            if matches:
                existing = matches[0]
                if len(matches) != 1:
                    raise EventCorruptionError(
                        f"Duplicate idempotency key in event log: {idempotency_key}"
                    )
                if existing.event_type != event_type or existing.payload != body:
                    raise EventStoreError(
                        f"Idempotency key {idempotency_key!r} was reused with different content"
                    )
                return existing
            return self.append(event_type, body)

    def find_by_idempotency_key(self, idempotency_key: str) -> EventRecord | None:
        with self._lock:
            records, _ = self._scan()
        matches = [
            record
            for record in records
            if record.payload.get("idempotency_key") == idempotency_key
        ]
        if len(matches) > 1:
            raise EventCorruptionError(
                f"Duplicate idempotency key in event log: {idempotency_key}"
            )
        return matches[0] if matches else None

    def cursor(self) -> EventCursor:
        with self._lock:
            self._records, self._offsets = self._scan()
            if not self._records:
                return EventCursor(run_id=self.run_id, sequence=0, byte_offset=0)
            return EventCursor(
                run_id=self.run_id,
                sequence=len(self._records),
                event_hash=self._records[-1].event_hash,
                byte_offset=self._offsets[-1],
            )

    def verify_cursor(self, cursor: EventCursor) -> None:
        if cursor.run_id != self.run_id:
            raise EventCorruptionError("Event cursor belongs to a different run")
        with self._lock:
            records, offsets = self._scan()
        if cursor.sequence == 0:
            if cursor.event_hash is not None or cursor.byte_offset != 0:
                raise EventCorruptionError("Invalid empty event cursor")
            return
        if cursor.sequence > len(records):
            raise EventCorruptionError("Event cursor is beyond the end of the log")
        record = records[cursor.sequence - 1]
        if record.event_hash != cursor.event_hash or offsets[cursor.sequence - 1] != cursor.byte_offset:
            raise EventCorruptionError("Event cursor does not match the authenticated log prefix")

    def iter_records(self) -> Iterator[EventRecord]:
        with self._lock:
            records, _ = self._scan()
        yield from records

    def events_after(self, cursor: EventCursor) -> Iterator[EventRecord]:
        self.verify_cursor(cursor)
        with self._lock:
            records, _ = self._scan()
        yield from records[cursor.sequence :]

    def _scan(self) -> tuple[list[EventRecord], list[int]]:
        data = self.path.read_bytes()
        if data and not data.endswith(b"\n"):
            raise EventCorruptionError("Event log ends with a partial record")
        records: list[EventRecord] = []
        event_ids: set[UUID] = set()
        offsets: list[int] = []
        offset = 0
        for line_number, raw_line in enumerate(data.splitlines(keepends=True), start=1):
            offset += len(raw_line)
            try:
                value = json.loads(raw_line)
                record = EventRecord.model_validate(value)
            except Exception as exc:
                raise EventCorruptionError(f"Invalid event record at line {line_number}") from exc
            expected_sequence = len(records) + 1
            expected_previous = records[-1].event_hash if records else None
            if record.run_id != self.run_id:
                raise EventCorruptionError(f"Run ID mismatch at line {line_number}")
            if record.sequence != expected_sequence:
                raise EventCorruptionError(f"Sequence gap at line {line_number}")
            if record.previous_hash != expected_previous:
                raise EventCorruptionError(f"Hash-chain break at line {line_number}")
            if record.event_id in event_ids:
                raise EventCorruptionError(f"Duplicate event ID at line {line_number}")
            body = record.model_dump(mode="json", exclude={"event_hash"})
            if record.event_hash != _hash_body(body):
                raise EventCorruptionError(f"Event hash mismatch at line {line_number}")
            records.append(record)
            event_ids.add(record.event_id)
            offsets.append(offset)
        return records, offsets


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _hash_body(body: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(body).encode("utf-8")).hexdigest()
