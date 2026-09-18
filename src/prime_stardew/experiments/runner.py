"""Auditable experiment runner over events, checkpoints, and M3 trajectories."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from prime_stardew.env.errors import StarDojoError
from prime_stardew.env.models import GameDate
from prime_stardew.tasks.models import TaskScore, TaskTrajectory
from prime_stardew.telemetry.events import EventRecord, EventStore

from .checkpoints import CheckpointKind, RunCheckpointManager, RunCheckpointManifest
from .config import RunConfig
from .lifecycle import ALLOWED_TRANSITIONS, RunPhase, RunState, reconstruct_state
from .provenance import RunProvenance, artifact_sha256


class RunnerError(StarDojoError):
    """An experiment violates its immutable config, lifecycle, or budget."""


class ModelCallUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    latency_ms: float = Field(ge=0)
    cost_usd: float = Field(ge=0)


class ExperimentRunner:
    """Single-run coordinator whose durable event log is the source of truth."""

    CONFIG_NAME = "config.json"

    def __init__(
        self,
        root: Path,
        config: RunConfig,
        provenance: RunProvenance,
    ) -> None:
        self.root = root.resolve()
        self.config = config
        self.provenance = provenance
        self.run_id = config.run_id()
        self.run_dir = self.root / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._persist_config()
        self.events = EventStore(self.run_dir / "events.jsonl", self.run_id)
        self.events.append_idempotent(
            "run_created",
            {
                "config_hash": config.config_hash(),
                "configuration": config.model_dump(mode="json"),
                "provenance": provenance.model_dump(mode="json"),
                "provider": config.provider.model_dump(mode="json"),
            },
            idempotency_key="run:created",
        )

    @property
    def state(self) -> RunState:
        return reconstruct_state(tuple(self.events.iter_records()))

    def start(self) -> RunState:
        return self.transition(RunPhase.RUNNING, reason="run requested", operation_id="start")

    def transition(
        self,
        target: RunPhase,
        *,
        reason: str,
        operation_id: str,
    ) -> RunState:
        state = self.state
        key = f"lifecycle:{operation_id}"
        existing = self.events.find_by_idempotency_key(key)
        if existing is not None:
            if existing.payload.get("to") != target.value:
                raise RunnerError(f"Lifecycle operation {operation_id!r} has conflicting target")
            return self.state
        if target not in ALLOWED_TRANSITIONS[state.phase]:
            raise RunnerError(f"Invalid lifecycle transition: {state.phase} -> {target}")
        self.events.append_idempotent(
            "lifecycle_transition",
            {"from": state.phase.value, "to": target.value, "reason": reason},
            idempotency_key=key,
        )
        return self.state

    def record_task(
        self,
        task_id: str,
        trajectory: TaskTrajectory,
        score: TaskScore,
    ) -> EventRecord:
        if not task_id.strip():
            raise RunnerError("task_id must not be empty")
        if score.fixture_id != trajectory.fixture.fixture_id:
            raise RunnerError("Task score belongs to a different fixture")
        normalized = _normalized_trajectory(trajectory)
        exact_hash = artifact_sha256(trajectory.model_dump(mode="json"))
        normalized_hash = artifact_sha256(normalized)
        completion_key = f"task:{task_id}:completed"
        existing = self.events.find_by_idempotency_key(completion_key)
        if existing is not None:
            if existing.payload.get("normalized_trajectory_sha256") != normalized_hash:
                raise RunnerError(f"Completed task {task_id!r} was replayed with a different trajectory")
            return existing
        if self.state.phase != RunPhase.RUNNING:
            raise RunnerError(f"Cannot record task while run is {self.state.phase}")

        missing_actions = sum(
            self.events.find_by_idempotency_key(f"task:{task_id}:action:{action.sequence}:completed")
            is None
            for action in trajectory.actions
        )
        if self.state.action_count + missing_actions > self.config.budget.max_actions:
            raise RunnerError("Run action budget would be exceeded")

        prefix = f"task:{task_id}"
        self.events.append_idempotent(
            "task_started",
            {
                "task_id": task_id,
                "fixture_id": trajectory.fixture.fixture_id,
                "kind": trajectory.fixture.kind.value,
            },
            idempotency_key=f"{prefix}:started",
        )
        self.events.append_idempotent(
            "observation",
            {
                "task_id": task_id,
                "phase": "before",
                "state": trajectory.before.model_dump(mode="json"),
                "artifact_sha256": artifact_sha256(trajectory.before.model_dump(mode="json")),
            },
            idempotency_key=f"{prefix}:observation:before",
        )
        self.events.append_idempotent(
            "decision",
            {
                "task_id": task_id,
                "policy": "scripted",
                "actions": [
                    {"sequence": action.sequence, "name": action.name,
                     "arguments": list(action.arguments)}
                    for action in trajectory.actions
                ],
            },
            idempotency_key=f"{prefix}:decision",
        )
        for action in trajectory.actions:
            action_id = f"{task_id}:a{action.sequence}"
            action_payload = {
                "task_id": task_id,
                "action_id": action_id,
                "sequence": action.sequence,
                "name": action.name,
                "arguments": list(action.arguments),
            }
            self.events.append_idempotent(
                "action_started",
                action_payload,
                idempotency_key=f"{prefix}:action:{action.sequence}:started",
            )
            self.events.append_idempotent(
                "action_completed",
                {
                    **action_payload,
                    "succeeded": action.succeeded,
                    "request_id": action.request_id,
                    "privileged": action.privileged,
                },
                idempotency_key=f"{prefix}:action:{action.sequence}:completed",
            )
        self.events.append_idempotent(
            "observation",
            {
                "task_id": task_id,
                "phase": "after",
                "state": trajectory.after.model_dump(mode="json"),
                "artifact_sha256": artifact_sha256(trajectory.after.model_dump(mode="json")),
            },
            idempotency_key=f"{prefix}:observation:after",
        )
        self.events.append_idempotent(
            "task_scored",
            {"task_id": task_id, "score": score.model_dump(mode="json")},
            idempotency_key=f"{prefix}:scored",
        )
        return self.events.append_idempotent(
            "task_completed",
            {
                "task_id": task_id,
                "fixture_id": trajectory.fixture.fixture_id,
                "success": score.success,
                "score": score.score,
                "trajectory_sha256": exact_hash,
                "normalized_trajectory_sha256": normalized_hash,
            },
            idempotency_key=completion_key,
        )

    def assert_model_call_allowed(self, *, input_tokens: int = 0) -> None:
        """Fail before inference when a knowable run budget is exhausted."""

        if self.state.phase != RunPhase.RUNNING:
            raise RunnerError(f"Cannot call a model while run is {self.state.phase}")
        self._assert_wall_budget()
        state = self.state
        budget = self.config.budget
        if state.model_call_count + 1 > budget.max_model_calls:
            raise RunnerError("Model-call budget would be exceeded")
        if state.input_tokens + input_tokens > budget.max_input_tokens:
            raise RunnerError("Input-token budget would be exceeded")

    def assert_action_allowed(self, *, count: int = 1, game_date: GameDate | None = None) -> None:
        if count <= 0:
            raise ValueError("Action count must be positive")
        if self.state.phase != RunPhase.RUNNING:
            raise RunnerError(f"Cannot execute an action while run is {self.state.phase}")
        self._assert_wall_budget()
        if self.state.action_count + count > self.config.budget.max_actions:
            raise RunnerError("Run action budget would be exceeded")
        if game_date is not None:
            elapsed = game_date.ordinal() - self.config.fixture.starting_date.ordinal() + 1
            if elapsed > self.config.budget.max_game_days:
                raise RunnerError("Game-day budget would be exceeded")

    def record_model_call(
        self,
        call_id: str,
        usage: ModelCallUsage,
        *,
        provider: str | None = None,
        model: str | None = None,
        route: str | None = None,
        decoding: dict[str, Any] | None = None,
    ) -> EventRecord:
        payload = {
            "call_id": call_id,
            "provider": provider or self.config.provider.provider,
            "model": model or self.config.provider.model,
            "route": route or self.config.provider.route,
            "decoding": decoding or self.config.provider.decoding.model_dump(mode="json"),
            **usage.model_dump(mode="json"),
        }
        existing = self.events.find_by_idempotency_key(f"model-call:{call_id}")
        if existing is not None:
            return self.events.append_idempotent(
                "model_call_completed", payload, idempotency_key=f"model-call:{call_id}"
            )
        self.assert_model_call_allowed(input_tokens=usage.input_tokens)
        state = self.state
        budget = self.config.budget
        if state.model_call_count + 1 > budget.max_model_calls:
            raise RunnerError("Model-call budget would be exceeded")
        if state.input_tokens + usage.input_tokens > budget.max_input_tokens:
            raise RunnerError("Input-token budget would be exceeded")
        if state.output_tokens + usage.output_tokens > budget.max_output_tokens:
            raise RunnerError("Output-token budget would be exceeded")
        if state.cost_usd + usage.cost_usd > budget.max_cost_usd:
            raise RunnerError("Cost budget would be exceeded")
        return self.events.append_idempotent(
            "model_call_completed",
            payload,
            idempotency_key=f"model-call:{call_id}",
        )

    def _assert_wall_budget(self) -> None:
        created = next(self.events.iter_records(), None)
        if created is None:
            raise RunnerError("Run has no creation event")
        elapsed = (datetime.now(UTC) - created.timestamp).total_seconds()
        if elapsed > self.config.budget.max_wall_seconds:
            raise RunnerError("Wall-time budget would be exceeded")

    def record_memory_access(
        self, operation_id: str, *, operation: str, memory_ids: tuple[str, ...]
    ) -> EventRecord:
        if operation not in {"read", "write"}:
            raise RunnerError("Memory operation must be read or write")
        return self.events.append_idempotent(
            f"memory_{operation}",
            {"operation_id": operation_id, "memory_ids": list(memory_ids)},
            idempotency_key=f"memory:{operation_id}",
        )

    def record_memory_retrieval(
        self,
        retrieval_id: str,
        *,
        query: dict[str, Any],
        candidates: tuple[dict[str, Any], ...],
        selected_ids: tuple[str, ...],
        selected_tokens: int,
    ) -> EventRecord:
        return self.events.append_idempotent(
            "memory_retrieval",
            {
                "retrieval_id": retrieval_id,
                "query": query,
                "candidates": list(candidates),
                "selected_ids": list(selected_ids),
                "selected_tokens": selected_tokens,
            },
            idempotency_key=f"memory-retrieval:{retrieval_id}",
        )

    def record_skill_use(
        self,
        use_id: str,
        *,
        skill: str,
        version: str,
        success: bool | None = None,
        score: float | None = None,
        model_decisions_saved: int | None = None,
        primitive_actions_saved: int | None = None,
    ) -> EventRecord:
        payload: dict[str, Any] = {"use_id": use_id, "skill": skill, "version": version}
        optional = {
            "success": success,
            "score": score,
            "model_decisions_saved": model_decisions_saved,
            "primitive_actions_saved": primitive_actions_saved,
        }
        payload.update({key: value for key, value in optional.items() if value is not None})
        return self.events.append_idempotent(
            "skill_used",
            payload,
            idempotency_key=f"skill:{use_id}",
        )

    def record_artifact(
        self, artifact_id: str, *, kind: str, path: str, sha256: str
    ) -> EventRecord:
        return self.events.append_idempotent(
            "artifact_recorded",
            {"artifact_id": artifact_id, "kind": kind, "path": path, "sha256": sha256},
            idempotency_key=f"artifact:{artifact_id}",
        )

    def record_error(
        self,
        error_id: str,
        *,
        error_type: str,
        message: str,
        recoverable: bool,
    ) -> EventRecord:
        return self.events.append_idempotent(
            "error_recorded",
            {
                "error_id": error_id,
                "error_type": error_type,
                "message": message,
                "recoverable": recoverable,
                "phase": self.state.phase.value,
            },
            idempotency_key=f"error:{error_id}",
        )

    def publish_checkpoint(
        self,
        manager: RunCheckpointManager,
        *,
        destination: Path,
        kind: CheckpointKind = CheckpointKind.DAY,
        labels: tuple[str, ...] = (),
        environment: dict[str, Any] | None = None,
        game_date: GameDate | None = None,
        agent_state: dict[str, Any] | None = None,
    ) -> RunCheckpointManifest:
        state = self.state
        if state.phase not in {RunPhase.DAY_COMPLETE, RunPhase.COMPLETED}:
            raise RunnerError("Checkpoints require a completed-day or completed-run boundary")
        operation = f"checkpoint:{destination.resolve()}"
        self.events.append_idempotent(
            "checkpoint_requested",
            {"path": str(destination.resolve()), "kind": kind.value},
            idempotency_key=f"{operation}:requested",
        )
        if destination.exists():
            manifest = manager.verify(destination, event_store=self.events)
            if manifest.run_id != self.run_id:
                raise RunnerError("Existing checkpoint belongs to a different run")
        else:
            manifest = manager.create(
                run_id=self.run_id,
                destination=destination,
                save_id=self.config.fixture.save_id,
                player=self.config.fixture.player,
                game_date=game_date or self.config.fixture.starting_date,
                agent_state=agent_state or self.state.model_dump(mode="json"),
                configuration=self.config.model_dump(mode="json"),
                event_store=self.events,
                kind=kind,
                labels=labels,
                environment=environment,
            )
        self.events.append_idempotent(
            "checkpoint_published",
            {
                "checkpoint_id": manifest.checkpoint_id,
                "path": str(destination.resolve()),
                "cursor": manifest.event_cursor.model_dump(mode="json"),
            },
            idempotency_key=f"{operation}:published",
        )
        return manifest

    def resume_from_checkpoint(
        self,
        manager: RunCheckpointManager,
        checkpoint: Path,
        destination_save_id: str,
        restore_agent_state: Callable[[dict[str, Any]], None] | None = None,
    ) -> RunState:
        if self.state.phase != RunPhase.INTERRUPTED:
            raise RunnerError("Resume requires an interrupted run")
        manifest = manager.verify(checkpoint, event_store=self.events)
        stored_config = json.loads(
            (checkpoint / "config" / "config.json").read_text(encoding="utf-8")
        )
        if RunConfig.model_validate(stored_config).config_hash() != self.config.config_hash():
            raise RunnerError("Checkpoint configuration does not match this run")
        self.transition(
            RunPhase.RECOVERING,
            reason=f"restore checkpoint {manifest.checkpoint_id}",
            operation_id=f"recover:{manifest.checkpoint_id}:start",
        )
        restored = manager.restore(
            checkpoint,
            destination_save_id,
            event_store=self.events,
        )
        if restore_agent_state is not None:
            restore_agent_state(restored.agent_state)
        self.events.append_idempotent(
            "checkpoint_restored",
            {
                "checkpoint_id": manifest.checkpoint_id,
                "destination_save_id": destination_save_id,
                "event_cursor": restored.event_cursor.model_dump(mode="json"),
                "trailing_event_count": len(list(self.events.events_after(restored.event_cursor))),
            },
            idempotency_key=f"checkpoint:{manifest.checkpoint_id}:restored:{destination_save_id}",
        )
        return self.transition(
            RunPhase.RUNNING,
            reason="checkpoint restored and validated",
            operation_id=f"recover:{manifest.checkpoint_id}:complete",
        )

    def latest_completed_day_checkpoint(
        self,
        manager: RunCheckpointManager,
        checkpoints_root: Path,
    ) -> tuple[Path, RunCheckpointManifest]:
        """Return the newest valid day checkpoint for this run."""

        candidates: list[tuple[Path, RunCheckpointManifest]] = []
        root = checkpoints_root.resolve()
        if not root.is_dir():
            raise RunnerError(f"Checkpoint root does not exist: {root}")
        for path in root.iterdir():
            if not path.is_dir():
                continue
            try:
                manifest = manager.verify(path, event_store=self.events)
            except Exception:
                continue
            if manifest.run_id == self.run_id and manifest.kind is CheckpointKind.DAY:
                candidates.append((path, manifest))
        if not candidates:
            raise RunnerError(f"No valid completed-day checkpoint found for {self.run_id}")
        return max(
            candidates,
            key=lambda item: (
                item[1].game_checkpoint.game_date.ordinal(),
                item[1].created_at,
                item[1].checkpoint_id,
            ),
        )

    def resume_latest(
        self,
        manager: RunCheckpointManager,
        checkpoints_root: Path,
        destination_save_id: str,
    ) -> RunState:
        path, _manifest = self.latest_completed_day_checkpoint(manager, checkpoints_root)
        return self.resume_from_checkpoint(manager, path, destination_save_id)

    def equivalence_digest(self) -> str:
        payload = [
            {
                "fixture_id": record.payload["fixture_id"],
                "success": record.payload["success"],
                "score": record.payload["score"],
                "normalized_trajectory_sha256": record.payload[
                    "normalized_trajectory_sha256"
                ],
            }
            for record in self.events.iter_records()
            if record.event_type == "task_completed"
        ]
        return artifact_sha256(payload)

    def _persist_config(self) -> None:
        path = self.run_dir / self.CONFIG_NAME
        encoded = self.config.canonical_json() + "\n"
        if path.exists():
            existing = RunConfig.model_validate_json(path.read_text(encoding="utf-8"))
            if existing.config_hash() != self.config.config_hash():
                raise RunnerError(f"Run directory config mismatch: {self.run_dir}")
            return
        pending = path.with_name(f".{path.name}.tmp-{uuid4().hex}")
        with pending.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(pending, path)


def _normalized_trajectory(trajectory: TaskTrajectory) -> dict[str, Any]:
    value = trajectory.model_dump(mode="json")
    for action in value["actions"]:
        action["request_id"] = None
    return value
