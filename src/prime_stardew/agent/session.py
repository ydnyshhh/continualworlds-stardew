"""Bounded persistent agent session with attributable model calls."""

from __future__ import annotations

import json
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, ValidationError

from prime_stardew.experiments.runner import ExperimentRunner, ModelCallUsage
from prime_stardew.memory import MemoryKind, MemoryQuery, MemoryStore

from .context import ACTION_SCHEMAS, WorkingContextAssembler
from .models import AgentDecision, ContextInputs, ContextItem, ProviderRequest, ProviderResponse
from .provider import AgentProvider


class DecisionError(RuntimeError):
    pass


class PauseController(Protocol):
    def pause(self) -> Any: ...
    def resume(self) -> Any: ...


class AgentSessionState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = 1
    active_goals: tuple[str, ...] = ()
    decision_count: int = 0
    last_context_sha256: str | None = None


class PrimeSession:
    def __init__(
        self,
        runner: ExperimentRunner,
        provider: AgentProvider,
        *,
        pause_controller: PauseController | None = None,
        state: AgentSessionState | None = None,
        memory_store: MemoryStore | None = None,
    ) -> None:
        self.runner = runner
        self.provider = provider
        self.pause_controller = pause_controller
        self.state = state or AgentSessionState()
        self.memory_store = memory_store
        self.assembler = WorkingContextAssembler(runner.config.context)

    def decide(
        self, inputs: ContextInputs, *, memory_query: MemoryQuery | None = None,
    ) -> AgentDecision:
        decision_id = f"decision-{self.state.decision_count + 1}"
        prepared = self._prepare_inputs(inputs, decision_id, memory_query)
        context = self.assembler.assemble(prepared.model_copy(update={
            "goals": inputs.goals or tuple(
                ContextItem(item_id=f"goal-{index}", text=goal)
                for index, goal in enumerate(self.state.active_goals, 1)
            )
        }))
        self.runner.events.append_idempotent(
            "context_assembled",
            {
                "decision_id": decision_id, "sha256": context.sha256,
                "estimated_tokens": context.estimated_tokens,
                "included_ids": list(context.included_ids),
                "omitted_ids": list(context.omitted_ids),
            },
            idempotency_key=f"context:{decision_id}",
        )
        response = self._call(decision_id, context.prompt, context.estimated_tokens, repair=False)
        try:
            decision = _parse_decision(response.text)
            _validate_decision(decision, inputs.allowed_actions)
        except (json.JSONDecodeError, ValidationError, ValueError) as first_error:
            repair_prompt = _repair_prompt(response.text, first_error)
            repaired = self._call(
                f"{decision_id}-repair", repair_prompt,
                max(1, len(repair_prompt.encode("utf-8")) // 4), repair=True,
            )
            try:
                decision = _parse_decision(repaired.text)
                _validate_decision(decision, inputs.allowed_actions)
            except (json.JSONDecodeError, ValidationError, ValueError) as final_error:
                self.runner.record_error(
                    f"{decision_id}-invalid", error_type="DecisionValidationError",
                    message=str(final_error), recoverable=False,
                )
                raise DecisionError("Provider returned an invalid decision after one repair") from final_error
        if decision.actions:
            self.runner.assert_action_allowed(count=len(decision.actions))
        self.state = AgentSessionState(
            active_goals=decision.goal_updates or self.state.active_goals,
            decision_count=self.state.decision_count + 1,
            last_context_sha256=context.sha256,
        )
        decision_event = self.runner.events.append_idempotent(
            "agent_decision",
            {"decision_id": decision_id, "decision": decision.model_dump(mode="json")},
            idempotency_key=f"agent-decision:{decision_id}",
        )
        self._write_memory_notes(
            decision_id, decision, str(decision_event.event_id), decision_event.timestamp,
        )
        return decision

    def _prepare_inputs(
        self,
        inputs: ContextInputs,
        decision_id: str,
        memory_query: MemoryQuery | None,
    ) -> ContextInputs:
        learning = self.runner.config.learning
        updates: dict[str, object] = {}
        if not learning.recent_context:
            updates["recent_events"] = ()
        if not learning.skills:
            updates["skills"] = ()
        if not learning.persistent_memory:
            updates["memories"] = ()
        elif not inputs.memories and self.memory_store is not None:
            if learning.retrieval:
                query = memory_query or MemoryQuery(
                    text=f"{inputs.objective}\n{inputs.observation}",
                    token_budget=self.runner.config.context.memories_tokens,
                )
                result = self.memory_store.retrieve(query)
            else:
                query = MemoryQuery(
                    text="", limit=100,
                    token_budget=self.runner.config.context.memories_tokens,
                )
                result = self.memory_store.recent(
                    limit=query.limit, token_budget=query.token_budget,
                )
            updates["memories"] = tuple(
                ContextItem(item_id=record.memory_id, text=record.text)
                for record in result.selected
            )
            self.runner.record_memory_retrieval(
                decision_id,
                query=result.query.model_dump(mode="json"),
                candidates=tuple(
                    candidate.model_dump(mode="json") for candidate in result.candidates
                ),
                selected_ids=tuple(record.memory_id for record in result.selected),
                selected_tokens=result.selected_tokens,
            )
        return inputs.model_copy(update=updates)

    def _write_memory_notes(
        self,
        decision_id: str,
        decision: AgentDecision,
        source_event_id: str,
        created_at: Any,
    ) -> None:
        if (
            self.memory_store is None
            or not self.runner.config.learning.persistent_memory
            or not decision.memory_notes
        ):
            return
        ids = []
        for index, note in enumerate(decision.memory_notes, 1):
            memory_id = f"{self.runner.run_id}:{decision_id}:note-{index}"
            self.memory_store.add_text(
                note, kind=MemoryKind.SEMANTIC, memory_id=memory_id,
                source_event_ids=(source_event_id,), created_at=created_at,
            )
            ids.append(memory_id)
        self.runner.record_memory_access(
            f"{decision_id}-write", operation="write", memory_ids=tuple(ids),
        )

    def checkpoint_state(self) -> dict[str, Any]:
        """Durable bounded state; provider transcripts are deliberately excluded."""

        return self.state.model_dump(mode="json")

    def restore_state(self, value: dict[str, Any]) -> None:
        self.state = AgentSessionState.model_validate(value)

    def close(self) -> None:
        self.provider.close()

    def _call(self, call_id: str, prompt: str, estimated_tokens: int, *, repair: bool) -> ProviderResponse:
        self.runner.assert_model_call_allowed(input_tokens=estimated_tokens)
        request = ProviderRequest(
            call_id=call_id, prompt=prompt, estimated_input_tokens=estimated_tokens,
            decoding=self.runner.config.provider.decoding.model_dump(mode="json"), repair=repair,
        )
        if self.pause_controller is not None:
            self.pause_controller.pause()
        try:
            response = self.provider.complete(request)
        finally:
            if self.pause_controller is not None:
                self.pause_controller.resume()
        self.runner.record_model_call(
            call_id,
            ModelCallUsage(
                request_id=response.request_id,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                latency_ms=response.usage.latency_ms,
                cost_usd=response.usage.cost_usd,
            ),
            provider=response.provider, model=response.model, route=response.route,
            decoding=request.decoding,
        )
        return response


def _parse_decision(text: str) -> AgentDecision:
    value = text.strip()
    if value.startswith("```"):
        lines = value.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            value = "\n".join(lines[1:-1])
            if value.lstrip().startswith("json"):
                value = value.lstrip()[4:].lstrip("\r\n")
    return AgentDecision.model_validate_json(value)


def _repair_prompt(text: str, error: Exception) -> str:
    return (
        "Repair the candidate into exactly one JSON object matching this schema: "
        '{"schema_version":1,"actions":[{"name":"string","arguments":[]}],'
        '"goal_updates":[],"memory_notes":[],"rationale":"string"}. '
        f"Validation error: {error}. Candidate: {text}"
    )


def _validate_decision(decision: AgentDecision, allowed_actions: tuple[str, ...]) -> None:
    if not allowed_actions:
        return
    invalid = tuple(action.name for action in decision.actions if action.name not in allowed_actions)
    if invalid:
        raise ValueError(f"Decision contains actions outside the allowlist: {invalid}")
    for action in decision.actions:
        _validate_action_arguments(action.name, action.arguments)


def _validate_action_arguments(name: str, arguments: tuple[object, ...]) -> None:
    def integers(count: int) -> tuple[int, ...]:
        if len(arguments) != count or any(type(value) is not int for value in arguments):
            raise ValueError(f"{name} requires {ACTION_SCHEMAS.get(name, 'valid arguments')}; got {arguments}")
        return tuple(arguments)  # type: ignore[return-value]

    if name in {"use", "interact"}:
        if arguments:
            raise ValueError(f"{name} requires []; got {arguments}")
    elif name == "move":
        values = integers(2)
        if any(value < 0 for value in values):
            raise ValueError(f"move requires {ACTION_SCHEMAS[name]}; got {arguments}")
    elif name == "move_relative":
        integers(2)
    elif name == "move_step":
        if integers(1)[0] not in range(1, 5):
            raise ValueError(f"move_step requires {ACTION_SCHEMAS[name]}; got {arguments}")
    elif name == "turn":
        if integers(1)[0] not in range(4):
            raise ValueError(f"turn requires {ACTION_SCHEMAS[name]}; got {arguments}")
    elif name == "choose_item":
        if integers(1)[0] not in range(36):
            raise ValueError(f"choose_item requires {ACTION_SCHEMAS[name]}; got {arguments}")
    elif name == "take_from_chest":
        index, quantity = integers(2)
        if index < 0 or quantity <= 0:
            raise ValueError(f"take_from_chest requires {ACTION_SCHEMAS[name]}; got {arguments}")
    elif name == "put_to_chest":
        index, quantity = integers(2)
        if index not in range(36) or quantity <= 0:
            raise ValueError(f"put_to_chest requires {ACTION_SCHEMAS[name]}; got {arguments}")
    elif name == "execute_skill":
        if not arguments or not isinstance(arguments[0], str) or not arguments[0].strip():
            raise ValueError(f"execute_skill requires {ACTION_SCHEMAS[name]}; got {arguments}")
