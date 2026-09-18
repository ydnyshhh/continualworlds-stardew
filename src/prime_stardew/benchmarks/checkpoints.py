"""Atomic, hash-verified replay checkpoints for the M9 season benchmark."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from prime_stardew.telemetry import EventCursor, EventStore

from .models import SeasonState


class BenchmarkCheckpointError(RuntimeError):
    pass


class BenchmarkCheckpointManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    checkpoint_id: str
    run_id: str
    created_at: datetime
    completed_day: int = Field(ge=0, le=28)
    config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    state_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    cursor_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    event_cursor: EventCursor


class BenchmarkCheckpointManager:
    def create(
        self,
        destination: Path,
        *,
        run_id: str,
        config_sha256: str,
        state: SeasonState,
        event_store: EventStore,
    ) -> BenchmarkCheckpointManifest:
        destination = destination.resolve()
        if destination.exists():
            raise BenchmarkCheckpointError(f"Checkpoint already exists: {destination}")
        if event_store.run_id != run_id:
            raise BenchmarkCheckpointError("Event store belongs to a different run")
        destination.parent.mkdir(parents=True, exist_ok=True)
        pending = destination.with_name(f".{destination.name}.tmp-{uuid4().hex}")
        pending.mkdir()
        try:
            cursor = event_store.cursor()
            state_path = pending / "state.json"
            cursor_path = pending / "cursor.json"
            _write(state_path, state.model_dump(mode="json"))
            _write(cursor_path, cursor.model_dump(mode="json"))
            manifest = BenchmarkCheckpointManifest(
                checkpoint_id=str(uuid4()), run_id=run_id, created_at=datetime.now(UTC),
                completed_day=state.completed_days, config_sha256=config_sha256,
                state_sha256=_sha256(state_path), cursor_sha256=_sha256(cursor_path),
                event_cursor=cursor,
            )
            _write(pending / "manifest.json", manifest.model_dump(mode="json"))
            os.replace(pending, destination)
            return manifest
        except Exception:
            shutil.rmtree(pending, ignore_errors=True)
            raise

    def verify(
        self,
        checkpoint: Path,
        *,
        event_store: EventStore,
        expected_config_sha256: str,
    ) -> BenchmarkCheckpointManifest:
        checkpoint = checkpoint.resolve()
        manifest_path = checkpoint / "manifest.json"
        if not manifest_path.is_file():
            raise BenchmarkCheckpointError("Benchmark checkpoint manifest is missing")
        manifest = BenchmarkCheckpointManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
        if manifest.run_id != event_store.run_id:
            raise BenchmarkCheckpointError("Benchmark checkpoint belongs to a different run")
        if manifest.config_sha256 != expected_config_sha256:
            raise BenchmarkCheckpointError("Benchmark configuration hash changed")
        state_path, cursor_path = checkpoint / "state.json", checkpoint / "cursor.json"
        if not state_path.is_file() or _sha256(state_path) != manifest.state_sha256:
            raise BenchmarkCheckpointError("Benchmark state hash mismatch")
        if not cursor_path.is_file() or _sha256(cursor_path) != manifest.cursor_sha256:
            raise BenchmarkCheckpointError("Benchmark cursor hash mismatch")
        cursor = EventCursor.model_validate_json(cursor_path.read_text(encoding="utf-8"))
        if cursor != manifest.event_cursor:
            raise BenchmarkCheckpointError("Stored cursor does not match the manifest")
        event_store.verify_cursor(cursor)
        state = SeasonState.model_validate_json(state_path.read_text(encoding="utf-8"))
        if state.completed_days != manifest.completed_day:
            raise BenchmarkCheckpointError("Stored day does not match the manifest")
        return manifest

    def restore(
        self,
        checkpoint: Path,
        *,
        event_store: EventStore,
        expected_config_sha256: str,
    ) -> SeasonState:
        self.verify(
            checkpoint, event_store=event_store,
            expected_config_sha256=expected_config_sha256,
        )
        return SeasonState.model_validate_json(
            (checkpoint.resolve() / "state.json").read_text(encoding="utf-8")
        )


def _write(path: Path, value: object) -> None:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ) + "\n"
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
