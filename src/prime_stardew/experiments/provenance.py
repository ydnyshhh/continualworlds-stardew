"""Code, runtime, provider, and artifact provenance for experiment events."""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict


class CodeProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    revision: str
    dirty: bool
    dirty_patch_sha256: str


class RuntimeProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    game_version: str = "unknown"
    smapi_version: str = "unknown"
    stardojo_version: str = "unknown"
    stardojo_dll_sha256: str = "unknown"
    python_version: str = platform.python_version()
    platform: str = platform.platform()


class RunProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: CodeProvenance
    runtime: RuntimeProvenance = RuntimeProvenance()


def capture_code_provenance(workspace: Path) -> CodeProvenance:
    workspace = workspace.resolve()
    revision = _git(workspace, "rev-parse", "HEAD", allow_failure=True) or "unborn"
    tracked = _git_bytes(workspace, "diff", "--binary", "HEAD", allow_failure=True)
    status = _git(workspace, "status", "--porcelain=v1", "--untracked-files=all", allow_failure=True)
    untracked_entries: list[tuple[str, str]] = []
    for line in status.splitlines():
        if not line.startswith("?? "):
            continue
        relative = line[3:]
        path = (workspace / relative).resolve()
        try:
            path.relative_to(workspace)
        except ValueError:
            continue
        if path.is_file():
            untracked_entries.append((relative, hashlib.sha256(path.read_bytes()).hexdigest()))
    body = tracked + json.dumps(
        sorted(untracked_entries), separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    dirty = bool(status.strip())
    return CodeProvenance(
        revision=revision,
        dirty=dirty,
        dirty_patch_sha256=hashlib.sha256(body).hexdigest(),
    )


def artifact_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _git(workspace: Path, *args: str, allow_failure: bool = False) -> str:
    result = subprocess.run(
        ["git", "-C", str(workspace), *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode and not allow_failure:
        raise RuntimeError(result.stderr.strip() or "git command failed")
    return result.stdout.strip() if result.returncode == 0 else ""


def _git_bytes(workspace: Path, *args: str, allow_failure: bool = False) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(workspace), *args],
        check=False,
        capture_output=True,
    )
    if result.returncode and not allow_failure:
        raise RuntimeError(result.stderr.decode(errors="replace").strip() or "git command failed")
    return result.stdout if result.returncode == 0 else b""
