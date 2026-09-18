"""Contamination-safe probe branches and append-only learning-state resets."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from prime_stardew.experiments.provenance import artifact_sha256
from prime_stardew.telemetry import EventStore

from .models import BranchIdentity, BranchType, LearningResetSpec, LearningState


@dataclass(frozen=True)
class DisposableBranch:
    identity: BranchIdentity
    game_state: dict[str, Any]
    learning_state: LearningState


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

