"""Verified, reviewable retention for combined experiment checkpoints."""

from __future__ import annotations

import shutil
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from prime_stardew.env.checkpoints import CheckpointError

from .checkpoints import CheckpointKind, RunCheckpointManager, RunCheckpointManifest


class RetentionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    latest_completed_days: int = Field(default=2, ge=1)


class RetentionEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: Path
    checkpoint_id: str
    run_id: str
    kind: CheckpointKind
    game_day_ordinal: int
    reason: str


class RetentionIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: Path
    reason: str


class RetentionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    root: Path
    created_at: datetime
    policy: RetentionPolicy
    keep: tuple[RetentionEntry, ...]
    delete: tuple[RetentionEntry, ...]
    issues: tuple[RetentionIssue, ...]


class RetentionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dry_run: bool
    deleted: tuple[Path, ...]
    retained: tuple[Path, ...]
    issues: tuple[RetentionIssue, ...]


class CheckpointRetentionManager:
    def __init__(self, root: Path, checkpoints: RunCheckpointManager) -> None:
        self.root = root.resolve()
        self.checkpoints = checkpoints

    def plan(self, policy: RetentionPolicy | None = None) -> RetentionPlan:
        policy = policy or RetentionPolicy()
        self.root.mkdir(parents=True, exist_ok=True)
        manifests: list[tuple[Path, RunCheckpointManifest]] = []
        issues: list[RetentionIssue] = []
        for path in sorted(item for item in self.root.iterdir() if item.is_dir()):
            try:
                manifest = self.checkpoints.verify(path)
            except Exception as exc:
                issues.append(RetentionIssue(path=path, reason=str(exc)))
                continue
            manifests.append((path, manifest))

        keep: list[RetentionEntry] = []
        delete: list[RetentionEntry] = []
        daily: dict[str, list[tuple[Path, RunCheckpointManifest]]] = defaultdict(list)
        for path, manifest in manifests:
            protected_reason = _protected_reason(manifest.kind)
            if protected_reason:
                keep.append(_entry(path, manifest, protected_reason))
            elif manifest.kind == CheckpointKind.DAY:
                daily[manifest.run_id].append((path, manifest))
            else:
                # A deliberately unprotected special class participates in pruning
                # only after being explicitly categorized as a normal day bundle.
                keep.append(_entry(path, manifest, "policy-disabled special checkpoint"))

        for _run_id, candidates in daily.items():
            candidates.sort(
                key=lambda item: (
                    item[1].game_checkpoint.game_date.ordinal(),
                    item[1].created_at,
                    item[1].checkpoint_id,
                ),
                reverse=True,
            )
            retained_dates: set[int] = set()
            for path, manifest in candidates:
                ordinal = manifest.game_checkpoint.game_date.ordinal()
                if ordinal not in retained_dates and len(retained_dates) < policy.latest_completed_days:
                    retained_dates.add(ordinal)
                    keep.append(_entry(path, manifest, "latest completed day"))
                elif ordinal in retained_dates:
                    delete.append(_entry(path, manifest, "older duplicate for retained day"))
                else:
                    delete.append(_entry(path, manifest, "older than retained day window"))

        return RetentionPlan(
            root=self.root,
            created_at=datetime.now(UTC),
            policy=policy,
            keep=tuple(sorted(keep, key=lambda item: str(item.path))),
            delete=tuple(sorted(delete, key=lambda item: str(item.path))),
            issues=tuple(issues),
        )

    def apply(self, plan: RetentionPlan, *, dry_run: bool = True) -> RetentionResult:
        if plan.root.resolve() != self.root:
            raise CheckpointError("Retention plan belongs to a different checkpoint root")
        fresh = self.plan(plan.policy)
        planned = {(entry.checkpoint_id, entry.path.resolve()) for entry in plan.delete}
        current = {(entry.checkpoint_id, entry.path.resolve()) for entry in fresh.delete}
        if planned != current:
            raise CheckpointError("Retention plan is stale; generate and review a new plan")
        if dry_run:
            return RetentionResult(
                dry_run=True,
                deleted=(),
                retained=tuple(entry.path for entry in fresh.keep),
                issues=fresh.issues,
            )

        deleted: list[Path] = []
        for entry in fresh.delete:
            path = entry.path.resolve()
            if path.parent != self.root:
                raise CheckpointError(f"Refusing retention path outside checkpoint root: {path}")
            manifest = self.checkpoints.verify(path)
            if manifest.checkpoint_id != entry.checkpoint_id or manifest.kind != CheckpointKind.DAY:
                raise CheckpointError(f"Checkpoint changed after retention planning: {path}")
            shutil.rmtree(path)
            deleted.append(path)
        return RetentionResult(
            dry_run=False,
            deleted=tuple(deleted),
            retained=tuple(entry.path for entry in fresh.keep),
            issues=fresh.issues,
        )


def _protected_reason(kind: CheckpointKind) -> str | None:
    if kind == CheckpointKind.BASE:
        return "protected base fixture"
    if kind == CheckpointKind.MILESTONE:
        return "protected milestone"
    if kind == CheckpointKind.FAILURE:
        return "protected failure snapshot"
    return None


def _entry(path: Path, manifest: RunCheckpointManifest, reason: str) -> RetentionEntry:
    return RetentionEntry(
        path=path,
        checkpoint_id=manifest.checkpoint_id,
        run_id=manifest.run_id,
        kind=manifest.kind,
        game_day_ordinal=manifest.game_checkpoint.game_date.ordinal(),
        reason=reason,
    )
