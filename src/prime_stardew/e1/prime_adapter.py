"""PrimeSession adapter implementing the E1 harness contract."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from prime_stardew.agent import AgentSessionState, ContextInputs, ContextItem, PrimeSession
from prime_stardew.experiments.lifecycle import RunPhase
from prime_stardew.experiments.provenance import artifact_sha256
from prime_stardew.memory import MemoryKind, MemoryQuery

from .branches import reset_learning_state
from .conditions import learning_config
from .harness import HarnessContractError
from .models import (
    ConditionManifest, HarnessCapabilities, LearningResetSpec, LearningState,
    PERSISTENT_OBJECTIVE,
)


class PrimeHarnessCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    run_id: str
    objective_sha256: str
    condition_manifest: ConditionManifest
    session_state: dict[str, Any]
    learning_state: LearningState


class PrimeHarnessAdapter:
    """Expose a bounded Prime session through E1's condition-faithful interface."""

    def __init__(
        self,
        session: PrimeSession,
        manifest: ConditionManifest,
        *,
        memory_token_budget: int,
        skill_context: dict[str, str] | None = None,
        learning_state: LearningState | None = None,
        runtime_capabilities: HarnessCapabilities | None = None,
    ) -> None:
        self.session = session
        self.manifest = manifest
        self.memory_token_budget = memory_token_budget
        self.skill_context = dict(skill_context or {})
        self._learning_state = learning_state or LearningState()
        self._runtime_capabilities = runtime_capabilities or HarnessCapabilities(
            persistent_memory=session.memory_store is not None,
            relevance_retrieval=(
                session.memory_store is not None and manifest.capabilities.relevance_retrieval
            ),
            procedural_skills=bool(self.skill_context),
        )
        self._objective: str | None = None
        self._run_id: str | None = None
        self._validate_runtime_contract()

    @property
    def capabilities(self) -> HarnessCapabilities:
        return self._runtime_capabilities

    def start(self, *, objective: str, run_id: str) -> None:
        if objective.strip() != PERSISTENT_OBJECTIVE:
            raise HarnessContractError("Prime E1 adapter requires the immutable persistent objective")
        if run_id != self.session.runner.run_id:
            raise HarnessContractError("Prime E1 adapter run ID differs from ExperimentRunner")
        phase = self.session.runner.state.phase
        if phase is RunPhase.CREATED:
            self.session.runner.start()
        elif phase is not RunPhase.RUNNING:
            raise HarnessContractError(f"Prime E1 adapter cannot start while run is {phase}")
        self._objective = objective
        self._run_id = run_id
        self.session.runner.events.append_idempotent(
            "e1_harness_started",
            {
                "run_id": run_id,
                "objective_sha256": artifact_sha256(objective),
                "condition_manifest": self.manifest.model_dump(mode="json"),
                "runtime_capabilities": self.capabilities.model_dump(mode="json"),
            },
            idempotency_key="e1:harness-started",
        )

    def decide(self, observation: dict[str, Any]) -> dict[str, Any]:
        self._assert_started()
        allowed_actions = tuple(str(item) for item in observation.get("allowed_actions", ()))
        state = observation.get("state", observation)
        recent_events = _context_items(observation.get("recent_events", ()), "event")
        skills = tuple(
            ContextItem(item_id=skill_ref, text=self.skill_context[skill_ref])
            for skill_ref in self._learning_state.active_skill_refs
            if skill_ref in self.skill_context
        )
        before = self.session.runner.state
        decision_number = self.session.state.decision_count + 1
        memory_query = MemoryQuery(
            text=f"{self._objective}\n{json.dumps(state, sort_keys=True)}",
            token_budget=self.memory_token_budget,
            game_day=_optional_int(observation.get("game_day")),
            season=_optional_string(observation.get("season")),
        )
        decision = self.session.decide(
            ContextInputs(
                objective=self._objective or "",
                observation=json.dumps(state, sort_keys=True, ensure_ascii=False),
                allowed_actions=allowed_actions,
                skills=skills,
                recent_events=recent_events,
            ),
            memory_query=memory_query,
        )
        after = self.session.runner.state
        self._refresh_learning_state()
        context_event = self.session.runner.events.find_by_idempotency_key(
            f"context:decision-{decision_number}"
        )
        actual_tokens = int(context_event.payload["estimated_tokens"]) if context_event else 0
        self.session.runner.events.append("decision_context_composed", {
            "decision_id": f"decision-{decision_number}",
            "maximum_tokens": self.session.runner.config.context.total_tokens,
            "actual_tokens": actual_tokens,
            "memory_token_limit": self.memory_token_budget,
            "included_ids": list(context_event.payload.get("included_ids", ())) if context_event else [],
            "omitted_ids": list(context_event.payload.get("omitted_ids", ())) if context_event else [],
            "memory_exposure": self.manifest.memory_exposure.value,
        })
        self.session.runner.events.append("e1_inference_accounted", {
            "decision_id": f"decision-{decision_number}",
            "category": "acting",
            "calls": after.model_call_count - before.model_call_count,
            "input_tokens": after.input_tokens - before.input_tokens,
            "output_tokens": after.output_tokens - before.output_tokens,
            "cost_usd": after.cost_usd - before.cost_usd,
        })
        return decision.model_dump(mode="json")

    def end_day(self, *, game_day: int) -> None:
        self._assert_started()
        if game_day < 1:
            raise HarnessContractError("E1 game day must be positive")
        cleared = False
        if not self.manifest.cross_day_agent_state:
            self.session.state = self.session.state.model_copy(update={
                "active_goals": (), "last_context_sha256": None,
            })
            self._learning_state = self._learning_state.model_copy(update={
                "active_goal_ids": (),
            })
            cleared = True
        self.session.runner.events.append("e1_day_learning_boundary", {
            "game_day": game_day,
            "cross_day_agent_state": self.manifest.cross_day_agent_state,
            "semantic_state_cleared": cleared,
            "learning_state_sha256": artifact_sha256(
                self.export_learning_state().model_dump(mode="json")
            ),
        })

    def checkpoint(self, destination: Path) -> str:
        self._assert_started()
        payload = PrimeHarnessCheckpoint(
            run_id=self._run_id or "",
            objective_sha256=artifact_sha256(self._objective or ""),
            condition_manifest=self.manifest,
            session_state=self.session.checkpoint_state(),
            learning_state=self.export_learning_state(),
        )
        destination = destination.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        pending = destination.with_name(f".{destination.name}.tmp-{uuid4().hex}")
        pending.write_text(payload.model_dump_json(indent=2) + "\n", encoding="utf-8")
        os.replace(pending, destination)
        digest = artifact_sha256(payload.model_dump(mode="json"))
        self.session.runner.events.append("e1_harness_checkpointed", {
            "path": str(destination), "sha256": digest,
        })
        return digest

    def restore(self, checkpoint: Path) -> None:
        self._assert_started()
        payload = PrimeHarnessCheckpoint.model_validate_json(
            checkpoint.read_text(encoding="utf-8")
        )
        if payload.run_id != self._run_id:
            raise HarnessContractError("Prime harness checkpoint belongs to another run")
        if payload.objective_sha256 != artifact_sha256(self._objective or ""):
            raise HarnessContractError("Prime harness checkpoint objective differs from this run")
        if payload.condition_manifest != self.manifest:
            raise HarnessContractError("Prime harness checkpoint condition manifest differs")
        self.session.restore_state(payload.session_state)
        self._learning_state = payload.learning_state
        self.session.runner.events.append("e1_harness_restored", {
            "path": str(checkpoint.resolve()),
            "sha256": artifact_sha256(payload.model_dump(mode="json")),
        })

    def export_learning_state(self) -> LearningState:
        self._refresh_learning_state()
        return self._learning_state

    def reset_learning_state(self, reset: LearningResetSpec) -> LearningState:
        self._assert_started()
        before = self.export_learning_state()
        store = self.session.memory_store
        if store is not None and (reset.memory or reset.beliefs):
            for record in store.records():
                is_belief = record.kind is MemoryKind.BELIEF
                if (is_belief and reset.beliefs) or (not is_belief and reset.memory):
                    store.deactivate(record.memory_id, reason="E1 disposable branch reset")
        updated = reset_learning_state(before, reset)
        if reset.goals:
            self.session.state = self.session.state.model_copy(update={"active_goals": ()})
        self._learning_state = updated
        self.session.runner.events.append("e1_harness_learning_reset", {
            "reset": reset.model_dump(mode="json"),
            "before_sha256": artifact_sha256(before.model_dump(mode="json")),
            "after_sha256": artifact_sha256(updated.model_dump(mode="json")),
        })
        return updated

    def _validate_runtime_contract(self) -> None:
        if self.capabilities != self.manifest.capabilities:
            raise HarnessContractError(
                "Prime runtime capabilities differ from the immutable condition manifest: "
                f"expected={self.manifest.capabilities.model_dump(mode='json')}, "
                f"actual={self.capabilities.model_dump(mode='json')}"
            )
        if self.session.runner.config.learning != learning_config(self.manifest):
            raise HarnessContractError(
                "Prime runtime learning configuration differs from the immutable condition manifest"
            )
        if self.session.runner.config.context.memories_tokens != self.memory_token_budget:
            raise HarnessContractError("Prime runtime memory budget differs from the E1 study budget")
        if self.manifest.capabilities.procedural_skills and not self.skill_context:
            raise HarnessContractError("The condition enables skills but no versioned skill context was supplied")

    def _assert_started(self) -> None:
        if self._objective is None or self._run_id is None:
            raise HarnessContractError("Prime E1 adapter has not started")

    def _refresh_learning_state(self) -> None:
        records = self.session.memory_store.records() if self.session.memory_store is not None else ()
        all_records = (
            self.session.memory_store.records(active_only=False)
            if self.session.memory_store is not None else ()
        )
        memory_ids = tuple(record.memory_id for record in records if record.kind is not MemoryKind.BELIEF)
        belief_ids = tuple(record.memory_id for record in records if record.kind is MemoryKind.BELIEF)
        history = tuple(dict.fromkeys((
            *self._learning_state.historical_artifact_ids,
            *(record.memory_id for record in all_records),
            *self._learning_state.active_skill_refs,
            *self._learning_state.active_refinement_ids,
        )))
        self._learning_state = self._learning_state.model_copy(update={
            "active_memory_ids": memory_ids,
            "active_belief_ids": belief_ids,
            "active_goal_ids": self.session.state.active_goals,
            "historical_artifact_ids": history,
        })


def _context_items(value: object, prefix: str) -> tuple[ContextItem, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    result = []
    for index, item in enumerate(value, 1):
        if isinstance(item, dict):
            item_id = str(item.get("id", f"{prefix}-{index}"))
            text = str(item.get("text", json.dumps(item, sort_keys=True)))
        else:
            item_id, text = f"{prefix}-{index}", str(item)
        result.append(ContextItem(item_id=item_id, text=text))
    return tuple(result)


def _optional_int(value: object) -> int | None:
    return value if type(value) is int else None


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
