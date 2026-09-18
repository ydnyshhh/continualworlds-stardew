"""Contamination-safe probe branches and append-only learning-state resets."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
import shutil
from typing import Any

from prime_stardew.experiments.checkpoints import RestoredRunState, RunCheckpointManager
from prime_stardew.experiments.provenance import artifact_sha256
from prime_stardew.telemetry import EventStore

from .models import BranchIdentity, BranchType, LearningResetSpec, LearningState


@dataclass(frozen=True)
class DisposableBranch:
    identity: BranchIdentity
    game_state: dict[str, Any]
    learning_state: LearningState


@dataclass(frozen=True)
class PhysicalDisposableBranch:
    identity: BranchIdentity
    restored: RestoredRunState
    branch_root: Path
    parent_checkpoint: Path


class BranchContaminationError(RuntimeError):
    pass


def reset_learning_state(
    state: LearningState,
    reset: LearningResetSpec,
    *,
    events: EventStore | None = None,
    branch_id: str | None = None,
) -> LearningState:
    updated = state.model_copy(update={
        "active_memory_ids": () if reset.memory else state.active_memory_ids,
        "active_belief_ids": () if reset.beliefs else state.active_belief_ids,
        "active_skill_refs": () if reset.skills else state.active_skill_refs,
        "active_refinement_ids": () if reset.refine_products else state.active_refinement_ids,
        "active_goal_ids": () if reset.goals else state.active_goal_ids,
    })
    if events is not None:
        events.append("learning_state_reset", {
            "branch_id": branch_id,
            "reset": reset.model_dump(mode="json"),
            "before_sha256": artifact_sha256(state.model_dump(mode="json")),
            "after_sha256": artifact_sha256(updated.model_dump(mode="json")),
            "historical_artifact_ids_preserved": list(updated.historical_artifact_ids),
        })
    return updated


class ProbeBranchManager:
    def __init__(
        self,
        *,
        parent_run_id: str,
        game_state: dict[str, Any],
        learning_state: LearningState,
        events: EventStore,
    ) -> None:
        self.parent_run_id = parent_run_id
        self._parent_game_state = deepcopy(game_state)
        self._parent_learning_state = learning_state
        self.events = events
        self._game_hash = artifact_sha256(game_state)
        self._learning_hash = artifact_sha256(learning_state.model_dump(mode="json"))

    def fork(
        self,
        *,
        branch_id: str,
        day: int,
        branch_type: BranchType = BranchType.PROBE,
        reset: LearningResetSpec | None = None,
    ) -> DisposableBranch:
        reset = reset or LearningResetSpec()
        checkpoint_hash = artifact_sha256({
            "parent_run_id": self.parent_run_id,
            "day": day,
            "game_state_sha256": self._game_hash,
            "learning_state_sha256": self._learning_hash,
        })
        identity = BranchIdentity(
            branch_id=branch_id,
            parent_run_id=self.parent_run_id,
            parent_checkpoint_sha256=checkpoint_hash,
            parent_game_state_sha256=self._game_hash,
            parent_learning_state_sha256=self._learning_hash,
            fork_reason="standardized_probe" if branch_type is BranchType.PROBE else "retention_reset",
            fork_day=day, branch_type=branch_type, reset=reset,
        )
        branch_learning = reset_learning_state(
            self._parent_learning_state, reset, events=self.events, branch_id=branch_id,
        ) if any(reset.model_dump().values()) else self._parent_learning_state.model_copy(deep=True)
        self.events.append("probe_branch_created", {
            "identity": identity.model_dump(mode="json"),
        })
        return DisposableBranch(
            identity=identity, game_state=deepcopy(self._parent_game_state),
            learning_state=branch_learning,
        )

    def discard(self, branch: DisposableBranch) -> None:
        if artifact_sha256(self._parent_game_state) != self._game_hash:
            raise BranchContaminationError("Parent game state changed during disposable branch")
        if artifact_sha256(self._parent_learning_state.model_dump(mode="json")) != self._learning_hash:
            raise BranchContaminationError("Parent learning state changed during disposable branch")
        self.events.append("probe_branch_discarded", {
            "branch_id": branch.identity.branch_id,
            "parent_game_state_sha256": self._game_hash,
            "parent_learning_state_sha256": self._learning_hash,
            "parent_unchanged": True,
        })


class PhysicalProbeBranchManager:
    """Restore and discard isolated probe artifacts from an immutable combined checkpoint."""

    def __init__(
        self,
        *,
        checkpoints: RunCheckpointManager,
        branches_root: Path,
        parent_run_id: str,
        events: EventStore,
    ) -> None:
        self.checkpoints = checkpoints
        self.branches_root = branches_root.resolve()
        self.parent_run_id = parent_run_id
        self.events = events

    def fork(
        self,
        *,
        checkpoint: Path,
        branch_id: str,
        destination_save_id: str,
        day: int,
    ) -> PhysicalDisposableBranch:
        branch_root = _safe_branch_child(self.branches_root, branch_id)
        if branch_root.exists():
            raise BranchContaminationError(f"Physical branch already exists: {branch_root}")
        checkpoint = checkpoint.resolve()
        manifest = self.checkpoints.verify(checkpoint, event_store=self.events)
        checkpoint_hash = artifact_sha256(manifest.model_dump(mode="json"))
        memory_destination = (
            branch_root / "memory" / "store.sqlite3"
            if (checkpoint / "memory" / "store.sqlite3").is_file() else None
        )
        learning_destinations = {
            path.stem: branch_root / "learning" / path.name
            for path in sorted((checkpoint / "learning").glob("*.sqlite3"))
        }
        try:
            branch_root.mkdir(parents=True)
            restored = self.checkpoints.restore(
                checkpoint,
                destination_save_id,
                event_store=self.events,
                destination_memory_path=memory_destination,
                destination_learning_paths=learning_destinations,
            )
        except Exception:
            shutil.rmtree(branch_root, ignore_errors=True)
            raise
        learning_payload = restored.agent_state.get("learning_state", restored.agent_state)
        identity = BranchIdentity(
            branch_id=branch_id,
            parent_run_id=self.parent_run_id,
            parent_checkpoint_sha256=checkpoint_hash,
            parent_game_state_sha256=artifact_sha256(
                manifest.game_checkpoint.model_dump(mode="json")
            ),
            parent_learning_state_sha256=artifact_sha256(learning_payload),
            fork_reason="standardized_probe",
            fork_day=day,
            branch_type=BranchType.PROBE,
            reset=LearningResetSpec(),
        )
        self.events.append("physical_probe_branch_created", {
            "identity": identity.model_dump(mode="json"),
            "destination_save_id": destination_save_id,
            "memory_database": str(restored.memory_database) if restored.memory_database else None,
            "learning_databases": {
                name: str(path) for name, path in restored.learning_databases.items()
            },
        })
        return PhysicalDisposableBranch(
            identity=identity,
            restored=restored,
            branch_root=branch_root,
            parent_checkpoint=checkpoint,
        )

    def discard(self, branch: PhysicalDisposableBranch) -> None:
        branch_root = _safe_branch_child(self.branches_root, branch.identity.branch_id)
        if branch_root != branch.branch_root.resolve():
            raise BranchContaminationError("Physical branch root does not match its identity")
        save_root = self.checkpoints.game_checkpoints.saves_root.resolve()
        save_path = branch.restored.game_save_path.resolve()
        if not save_path.is_relative_to(save_root) or save_path == save_root:
            raise BranchContaminationError("Physical branch save escaped the configured saves root")
        manifest = self.checkpoints.verify(branch.parent_checkpoint, event_store=self.events)
        checkpoint_hash = artifact_sha256(manifest.model_dump(mode="json"))
        if checkpoint_hash != branch.identity.parent_checkpoint_sha256:
            raise BranchContaminationError("Parent combined checkpoint changed during probe")
        if save_path.exists():
            shutil.rmtree(save_path)
        if branch_root.exists():
            shutil.rmtree(branch_root)
        self.events.append("physical_probe_branch_discarded", {
            "branch_id": branch.identity.branch_id,
            "parent_checkpoint_sha256": checkpoint_hash,
            "parent_unchanged": True,
        })


def spring_y2_reset_matrix() -> dict[BranchType, LearningResetSpec]:
    return {
        BranchType.FULL_STATE: LearningResetSpec(),
        BranchType.MEMORY_RESET: LearningResetSpec(memory=True, beliefs=True),
        BranchType.SKILLS_RESET: LearningResetSpec(skills=True),
        BranchType.LEARNING_RESET: LearningResetSpec(
            memory=True, beliefs=True, skills=True, refine_products=True, goals=True,
        ),
        BranchType.REFINEMENT_RESET: LearningResetSpec(refine_products=True),
    }


def _safe_branch_child(root: Path, branch_id: str) -> Path:
    if not branch_id or branch_id in {".", ".."} or any(char in branch_id for char in "\\/:"):
        raise BranchContaminationError(f"Unsafe physical branch ID: {branch_id!r}")
    candidate = (root / branch_id).resolve()
    if not candidate.is_relative_to(root) or candidate == root:
        raise BranchContaminationError("Physical branch path escaped the configured root")
    return candidate
