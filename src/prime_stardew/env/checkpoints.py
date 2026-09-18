"""Atomic, hash-verified Stardew save checkpoints."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .errors import ConfigurationError, StarDojoError
from .models import GameDate


class CheckpointError(StarDojoError):
    """A checkpoint could not be created, verified, or restored."""


class CheckpointFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class CheckpointManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = 1
    checkpoint_id: str
    created_at: datetime
    source_save_id: str
    player: str
    game_date: GameDate
    files: list[CheckpointFile]
    environment: dict[str, Any] = Field(default_factory=dict)


class CheckpointManager:
    MANIFEST_NAME = "manifest.json"

    def __init__(
        self,
        saves_root: Path,
        *,
        stable_checks: int = 2,
        stable_interval: float = 0.25,
        stable_timeout: float = 10.0,
    ) -> None:
        self.saves_root = saves_root.resolve()
        if stable_checks < 2 or stable_interval < 0 or stable_timeout <= 0:
            raise ConfigurationError("Invalid checkpoint stability settings")
        self.stable_checks = stable_checks
        self.stable_interval = stable_interval
        self.stable_timeout = stable_timeout

    def create(
        self,
        save_id: str,
        destination: Path,
        *,
        player: str,
        game_date: GameDate,
        environment: dict[str, Any] | None = None,
    ) -> CheckpointManifest:
        _validate_save_id(save_id)
        source = _safe_child(self.saves_root, save_id)
        if not source.is_dir():
            raise CheckpointError(f"Save directory does not exist: {source}")

        destination = destination.resolve()
        if destination.exists():
            raise CheckpointError(f"Checkpoint destination already exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.tmp-{uuid4().hex}")
        temporary.mkdir()
        try:
            stable = self._wait_for_stable_files(source)
            files: list[CheckpointFile] = []
            for relative, expected_hash in stable.items():
                source_file = source / relative
                target = _safe_child(temporary, relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_file, target)
                actual_hash = _sha256(target)
                if actual_hash != expected_hash:
                    raise CheckpointError(f"Save changed while copying: {relative}")
                files.append(
                    CheckpointFile(
                        path=relative.as_posix(),
                        size=target.stat().st_size,
                        sha256=actual_hash,
                    )
                )

            manifest = CheckpointManifest(
                checkpoint_id=str(uuid4()),
                created_at=datetime.now(UTC),
                source_save_id=save_id,
                player=player,
                game_date=game_date,
                files=sorted(files, key=lambda item: item.path),
                environment=environment or {},
            )
            pending_manifest = temporary / f"{self.MANIFEST_NAME}.tmp"
            pending_manifest.write_text(
                manifest.model_dump_json(indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(pending_manifest, temporary / self.MANIFEST_NAME)
            os.replace(temporary, destination)
            return manifest
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise

    def verify(self, checkpoint: Path) -> CheckpointManifest:
        checkpoint = checkpoint.resolve()
        manifest_path = checkpoint / self.MANIFEST_NAME
        if not manifest_path.is_file():
            raise CheckpointError(f"Missing checkpoint manifest: {manifest_path}")
        manifest = CheckpointManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
        for entry in manifest.files:
            path = _safe_child(checkpoint, entry.path)
            if not path.is_file():
                raise CheckpointError(f"Checkpoint file is missing: {entry.path}")
            if path.stat().st_size != entry.size or _sha256(path) != entry.sha256:
                raise CheckpointError(f"Checkpoint hash mismatch: {entry.path}")
        return manifest

    def restore(self, checkpoint: Path, destination_save_id: str) -> Path:
        _validate_save_id(destination_save_id)
        manifest = self.verify(checkpoint)
        checkpoint = checkpoint.resolve()
        destination = _safe_child(self.saves_root, destination_save_id)
        if destination.exists():
            raise CheckpointError(f"Restore destination already exists: {destination}")
        self.saves_root.mkdir(parents=True, exist_ok=True)
        temporary = _safe_child(
            self.saves_root,
            f".{destination_save_id}.restore-{uuid4().hex}",
        )
        temporary.mkdir()
        try:
            for entry in manifest.files:
                source = _safe_child(checkpoint, entry.path)
                relative = Path(entry.path)
                if relative.name == manifest.source_save_id:
                    relative = relative.with_name(destination_save_id)
                elif relative.name == f"{manifest.source_save_id}_old":
                    relative = relative.with_name(f"{destination_save_id}_old")
                target = _safe_child(temporary, relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            os.replace(temporary, destination)
            return destination
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise

    def _wait_for_stable_files(self, source: Path) -> dict[Path, str]:
        deadline = time.monotonic() + self.stable_timeout
        prior: dict[Path, tuple[int, int, str]] | None = None
        consecutive = 0
        last_transient_error: OSError | None = None
        while True:
            try:
                paths = [path for path in sorted(source.rglob("*")) if path.is_file()]
                if any(_is_stardew_temporary(path) for path in paths):
                    raise PermissionError("Stardew temporary save file is still present")
                current = {
                    path.relative_to(source): (
                        path.stat().st_size,
                        path.stat().st_mtime_ns,
                        _sha256(path),
                    )
                    for path in paths
                }
                last_transient_error = None
            except OSError as exc:
                # Stardew creates, locks, replaces, and removes temporary files while
                # saving. A readable directory snapshot is itself a stability gate.
                current = None
                last_transient_error = exc
                prior = None
                consecutive = 0
            if current is None:
                if time.monotonic() >= deadline:
                    raise CheckpointError(
                        f"Save files remained unreadable or temporary for "
                        f"{self.stable_timeout}s: {last_transient_error}"
                    ) from last_transient_error
                time.sleep(self.stable_interval)
                continue
            if not current:
                raise CheckpointError(f"Save directory contains no files: {source}")
            if current == prior:
                consecutive += 1
                if consecutive >= self.stable_checks - 1:
                    return {path: state[2] for path, state in current.items()}
            else:
                prior = current
                consecutive = 0
            if time.monotonic() >= deadline:
                raise CheckpointError(f"Save files did not stabilize within {self.stable_timeout}s")
            time.sleep(self.stable_interval)


def _validate_save_id(save_id: str) -> None:
    if (
        not save_id
        or save_id in {".", ".."}
        or "%" in save_id
        or any(char in save_id for char in "\\/:")
    ):
        raise ConfigurationError(f"Unsafe save ID: {save_id!r}")


def _safe_child(root: Path, relative: str | Path) -> Path:
    root = root.resolve()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ConfigurationError(f"Path escapes managed root: {relative}") from exc
    return candidate


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_stardew_temporary(path: Path) -> bool:
    return path.name.endswith("_STARDEWVALLEYSAVETMP")
