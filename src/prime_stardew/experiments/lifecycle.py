"""Validated experiment lifecycle state reconstructed from durable events."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from prime_stardew.telemetry.events import EventRecord


class RunPhase(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    DAY_COMPLETE = "day_complete"
    INTERRUPTED = "interrupted"
    RECOVERING = "recovering"
    COMPLETED = "completed"
    FAILED = "failed"


ALLOWED_TRANSITIONS: dict[RunPhase, frozenset[RunPhase]] = {
    RunPhase.CREATED: frozenset({RunPhase.RUNNING, RunPhase.FAILED}),
    RunPhase.RUNNING: frozenset({
        RunPhase.DAY_COMPLETE, RunPhase.INTERRUPTED, RunPhase.COMPLETED, RunPhase.FAILED,
    }),
    RunPhase.DAY_COMPLETE: frozenset({
        RunPhase.RUNNING, RunPhase.INTERRUPTED, RunPhase.COMPLETED, RunPhase.FAILED,
    }),
    RunPhase.INTERRUPTED: frozenset({RunPhase.RECOVERING, RunPhase.FAILED}),
    RunPhase.RECOVERING: frozenset({
        RunPhase.RUNNING, RunPhase.DAY_COMPLETE, RunPhase.FAILED,
    }),
    RunPhase.COMPLETED: frozenset(),
    RunPhase.FAILED: frozenset(),
}


class RunState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    phase: RunPhase = RunPhase.CREATED
    completed_task_ids: tuple[str, ...] = ()
    action_count: int = 0
    model_call_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0
    last_checkpoint_id: str | None = None


def reconstruct_state(records: tuple[EventRecord, ...]) -> RunState:
    phase = RunPhase.CREATED
    completed: list[str] = []
    action_count = model_calls = input_tokens = output_tokens = 0
    cost = 0.0
    checkpoint_id = None
    for record in records:
        payload = record.payload
        if record.event_type == "lifecycle_transition":
            source = RunPhase(payload["from"])
            target = RunPhase(payload["to"])
            if source != phase or target not in ALLOWED_TRANSITIONS[source]:
                raise ValueError(
                    f"Invalid recorded lifecycle transition at sequence {record.sequence}: "
                    f"{source} -> {target} while state is {phase}"
                )
            phase = target
        elif record.event_type == "action_completed":
            action_count += 1
        elif record.event_type == "task_completed":
            task_id = str(payload["task_id"])
            if task_id not in completed:
                completed.append(task_id)
        elif record.event_type == "model_call_completed":
            model_calls += 1
            input_tokens += int(payload.get("input_tokens", 0))
            output_tokens += int(payload.get("output_tokens", 0))
            cost += float(payload.get("cost_usd", 0))
        elif record.event_type == "checkpoint_published":
            checkpoint_id = str(payload["checkpoint_id"])
    return RunState(
        phase=phase,
        completed_task_ids=tuple(completed),
        action_count=action_count,
        model_call_count=model_calls,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost,
        last_checkpoint_id=checkpoint_id,
    )
