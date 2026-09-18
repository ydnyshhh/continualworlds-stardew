"""Atomic checkpoints joining game, agent, configuration, and event position."""

from __future__ import annotations

import json
import os
import shutil
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from prime_stardew.env.checkpoints import (
    CheckpointError,
    CheckpointFile,
    CheckpointManager,
    CheckpointManifest,
    _safe_child,
    _sha256,
)
from prime_stardew.env.models import GameDate
from prime_stardew.telemetry.events import EventCursor, EventStore


class CheckpointKind(StrEnum):
    BASE = "base"
    DAY = "day"
    MILESTONE = "milestone"
    FAILURE = "failure"


class RunCheckpointManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    checkpoint_id: str
    run_id: str
    created_at: datetime
    game_checkpoint: CheckpointManifest
    event_cursor: EventCursor
    files: list[CheckpointFile]
    kind: CheckpointKind = CheckpointKind.DAY
    labels: tuple[str, ...] = ()
    environment: dict[str, Any] = Field(default_factory=dict)


class RestoredRunState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    game_save_path: Path
    agent_state: dict[str, Any]
    configuration: dict[str, Any]
    event_cursor: EventCursor
    memory_database: Path | None = None
    learning_databases: dict[str, Path] = Field(default_factory=dict)


class RunCheckpointManager:
    MANIFEST_NAME = "manifest.json"

    def __init__(self, game_checkpoints: CheckpointManager) -> None:
        self.game_checkpoints = game_checkpoints

    def create(
        self,
        *,
        run_id: str,
        destination: Path,
        save_id: str,
        player: str,
        game_date: GameDate,
        agent_state: dict[str, Any],
        configuration: dict[str, Any],
        event_store: EventStore,
        memory_database: Path | None = None,
        learning_databases: dict[str, Path] | None = None,
        kind: CheckpointKind = CheckpointKind.DAY,
        labels: tuple[str, ...] = (),
        environment: dict[str, Any] | None = None,
    ) -> RunCheckpointManifest:
        if event_store.run_id != run_id:
            raise CheckpointError("Event store belongs to a different run")
        destination = destination.resolve()
        if destination.exists():
            raise CheckpointError(f"Run checkpoint destination already exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.tmp-{uuid4().hex}")
        temporary.mkdir()
        try:
            cursor = event_store.cursor()
            game_manifest = self.game_checkpoints.create(
                save_id,
                temporary / "game",
                player=player,
                game_date=game_date,
                environment=environment,
            )
            _write_json(temporary / "agent" / "state.json", agent_state)
            _write_json(temporary / "config" / "config.json", configuration)
            _write_json(
                temporary / "events" / "cursor.json",
                cursor.model_dump(mode="json"),
            )
            if memory_database is not None:
                memory_database = memory_database.resolve()
                if not memory_database.is_file():
                    raise CheckpointError(f"Memory database does not exist: {memory_database}")
                memory_target = temporary / "memory" / "store.sqlite3"
                memory_target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(memory_database, memory_target)
            for name, database in sorted((learning_databases or {}).items()):
                _validate_database_name(name)
                database = database.resolve()
                if not database.is_file():
                    raise CheckpointError(f"Learning database does not exist: {database}")
                target = temporary / "learning" / f"{name}.sqlite3"
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(database, target)
            files = _inventory(temporary)
            manifest = RunCheckpointManifest(
                checkpoint_id=str(uuid4()),
                run_id=run_id,
                created_at=datetime.now(UTC),
                game_checkpoint=game_manifest,
                event_cursor=cursor,
                files=files,
                kind=kind,
                labels=labels,
                environment=environment or {},
            )
            _write_json(temporary / self.MANIFEST_NAME, manifest.model_dump(mode="json"))
            os.replace(temporary, destination)
            return manifest
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise

    def verify(self, checkpoint: Path, *, event_store: EventStore | None = None) -> RunCheckpointManifest:
        checkpoint = checkpoint.resolve()
        manifest_path = checkpoint / self.MANIFEST_NAME
        if not manifest_path.is_file():
            raise CheckpointError(f"Missing run checkpoint manifest: {manifest_path}")
        manifest = RunCheckpointManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        for entry in manifest.files:
            path = _safe_child(checkpoint, entry.path)
            if not path.is_file():
                raise CheckpointError(f"Run checkpoint file is missing: {entry.path}")
            if path.stat().st_size != entry.size or _sha256(path) != entry.sha256:
                raise CheckpointError(f"Run checkpoint hash mismatch: {entry.path}")
        nested = self.game_checkpoints.verify(checkpoint / "game")
        if nested != manifest.game_checkpoint:
            raise CheckpointError("Nested game manifest does not match run manifest")
        stored_cursor = EventCursor.model_validate_json(
            (checkpoint / "events" / "cursor.json").read_text(encoding="utf-8")
        )
        if stored_cursor != manifest.event_cursor:
            raise CheckpointError("Stored event cursor does not match run manifest")
        if event_store is not None:
            event_store.verify_cursor(manifest.event_cursor)
        return manifest

    def restore(
        self,
        checkpoint: Path,
        destination_save_id: str,
        *,
        event_store: EventStore | None = None,
        destination_memory_path: Path | None = None,
        destination_learning_paths: dict[str, Path] | None = None,
    ) -> RestoredRunState:
        manifest = self.verify(checkpoint, event_store=event_store)
        checkpoint = checkpoint.resolve()
        save_path = self.game_checkpoints.restore(checkpoint / "game", destination_save_id)
        agent_state = _read_object(checkpoint / "agent" / "state.json")
        configuration = _read_object(checkpoint / "config" / "config.json")
        memory_source = checkpoint / "memory" / "store.sqlite3"
        restored_memory: Path | None = None
        if memory_source.is_file():
            if destination_memory_path is None:
                restored_memory = memory_source
            else:
                restored_memory = destination_memory_path.resolve()
                if restored_memory.exists():
                    raise CheckpointError(
                        f"Memory restore destination already exists: {restored_memory}"
                    )
                restored_memory.parent.mkdir(parents=True, exist_ok=True)
                pending = restored_memory.with_name(f".{restored_memory.name}.tmp-{uuid4().hex}")
                shutil.copy2(memory_source, pending)
                os.replace(pending, restored_memory)
        restored_learning: dict[str, Path] = {}
        learning_root = checkpoint / "learning"
        for source in sorted(learning_root.glob("*.sqlite3")):
            name = source.stem
            _validate_database_name(name)
            requested = (destination_learning_paths or {}).get(name)
            if requested is None:
                restored_learning[name] = source
                continue
            destination = requested.resolve()
            if destination.exists():
                raise CheckpointError(f"Learning restore destination already exists: {destination}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            pending = destination.with_name(f".{destination.name}.tmp-{uuid4().hex}")
            shutil.copy2(source, pending)
            os.replace(pending, destination)
            restored_learning[name] = destination
        unexpected = set(destination_learning_paths or {}) - set(restored_learning)
        if unexpected:
            raise CheckpointError(
                f"Requested learning databases are absent from checkpoint: {sorted(unexpected)}"
            )
        return RestoredRunState(
            game_save_path=save_path,
            agent_state=agent_state,
            configuration=configuration,
            event_cursor=manifest.event_cursor,
            memory_database=restored_memory,
            learning_databases=restored_learning,
        )


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"
    pending = path.with_name(f".{path.name}.tmp-{uuid4().hex}")
    with pending.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(pending, path)


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise CheckpointError(f"Expected JSON object: {path}")
    return value


def _validate_database_name(name: str) -> None:
    if not name or any(not (char.isalnum() or char in "-_") for char in name):
        raise CheckpointError(f"Unsafe learning database name: {name!r}")


def _inventory(root: Path) -> list[CheckpointFile]:
    entries = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        if relative == RunCheckpointManager.MANIFEST_NAME:
            continue
        entries.append(CheckpointFile(path=relative, size=path.stat().st_size, sha256=_sha256(path)))
    return entries
