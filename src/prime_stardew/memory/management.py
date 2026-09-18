"""Bounded, auditable memory-management policies over the existing immutable store."""

from __future__ import annotations

import json
import random
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from prime_stardew.agent.models import ProviderRequest
from prime_stardew.agent.provider import AgentProvider
from prime_stardew.experiments.config import MemoryManagementConfig, MemoryManagementPolicy
from prime_stardew.experiments.runner import ExperimentRunner, ModelCallUsage
from prime_stardew.telemetry import EventStore

from .models import MemoryRecord
from .store import MemoryStore, MemoryStoreError, estimate_memory_tokens


class MemoryManagementError(MemoryStoreError):
    pass


class MemoryManagementOperation(StrEnum):
    RETAIN = "retain"
    DEACTIVATE = "deactivate"


class MemoryManagementAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    memory_id: str
    operation: MemoryManagementOperation


class MemoryConsolidationSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_memory_ids: tuple[str, ...] = Field(min_length=2)
    proposed_summary: str = Field(min_length=1)


class MemoryManagementDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    actions: tuple[MemoryManagementAction, ...]
    consolidations: tuple[MemoryConsolidationSelection, ...] = ()
    reasoning_summary: str = Field(min_length=1)
    predicted_future_value: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_unique_ids(self) -> "MemoryManagementDecision":
        ids = [action.memory_id for action in self.actions]
        if len(ids) != len(set(ids)):
            raise ValueError("Memory-management actions contain duplicate memory IDs")
        for value in self.predicted_future_value.values():
            if not 0 <= value <= 1:
                raise ValueError("Predicted future values must be between zero and one")
        return self


class MemoryBudgetReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy: MemoryManagementPolicy
    budget_tokens: int = Field(ge=0)
    before_active_tokens: int = Field(ge=0)
    after_active_tokens: int = Field(ge=0)
    retained_ids: tuple[str, ...]
    deactivated_ids: tuple[str, ...]
    fallback_applied: bool = False
    overflow_tokens: int = Field(ge=0)


class MemoryBudgetManager:
    def __init__(
        self,
        store: MemoryStore,
        config: MemoryManagementConfig,
        *,
        events: EventStore | None = None,
    ) -> None:
        self.store = store
        self.config = config
        self.events = events

    def active_tokens(self) -> int:
        return sum(estimate_memory_tokens(record) for record in self.store.records())

    def enforce(
        self, decision: MemoryManagementDecision | None = None,
    ) -> MemoryBudgetReport:
        records = self.store.records()
        before = sum(estimate_memory_tokens(record) for record in records)
        self._event("memory_budget_evaluated", {
            "policy": self.config.policy.value,
            "budget_tokens": self.config.max_active_tokens,
            "active_tokens": before,
            "active_memory_ids": [record.memory_id for record in records],
        })
        if not self.config.enabled or self.config.policy is MemoryManagementPolicy.NONE:
            return MemoryBudgetReport(
                policy=self.config.policy, budget_tokens=self.config.max_active_tokens,
                before_active_tokens=before, after_active_tokens=before,
                retained_ids=tuple(record.memory_id for record in records),
                deactivated_ids=(), overflow_tokens=max(0, before - self.config.max_active_tokens),
            )
        fallback = False
        policy = self.config.policy
        if policy is MemoryManagementPolicy.AGENT_SELECTED:
            self._event("memory_management_requested", {
                "candidate_ids": [record.memory_id for record in records],
                "budget_tokens": self.config.max_active_tokens,
            })
            try:
                retained = self._validate_agent_decision(records, decision)
            except MemoryManagementError as exc:
                fallback = True
                policy = self.config.overflow_fallback
                if policy in {MemoryManagementPolicy.NONE, MemoryManagementPolicy.AGENT_SELECTED}:
                    raise
                self._event("memory_management_decided", {
                    "valid": False, "error": str(exc), "fallback": policy.value,
                })
                retained = self._select(records, policy)
            else:
                self._event("memory_management_decided", {
                    "valid": True,
                    "actions": [action.model_dump(mode="json") for action in decision.actions],
                    "predicted_future_value": decision.predicted_future_value,
                })
                for consolidation in decision.consolidations:
                    self._event("memory_consolidation_selected", consolidation.model_dump(mode="json"))
        else:
            retained = self._select(records, policy)
            self._event("memory_management_decided", {
                "valid": True, "policy": policy.value,
                "retained_ids": sorted(retained),
            })
        deactivated = tuple(
            record.memory_id for record in records if record.memory_id not in retained
        )
        for memory_id in deactivated:
            self.store.deactivate(memory_id, reason=f"memory-budget:{self.config.policy.value}")
            self._event("memory_status_changed", {
                "memory_id": memory_id, "status": "deactivated",
                "reason": f"memory-budget:{self.config.policy.value}",
            })
        after = self.active_tokens()
        if after > self.config.max_active_tokens:
            raise MemoryManagementError("Memory policy failed to enforce the active-token budget")
        return MemoryBudgetReport(
            policy=self.config.policy,
            budget_tokens=self.config.max_active_tokens,
            before_active_tokens=before,
            after_active_tokens=after,
            retained_ids=tuple(record.memory_id for record in records if record.memory_id in retained),
            deactivated_ids=deactivated,
            fallback_applied=fallback,
            overflow_tokens=max(0, after - self.config.max_active_tokens),
        )

    def _validate_agent_decision(
        self,
        records: tuple[MemoryRecord, ...],
        decision: MemoryManagementDecision | None,
    ) -> set[str]:
        if decision is None:
            raise MemoryManagementError("Agent-selected policy requires a decision")
        active_ids = {record.memory_id for record in records}
        action_ids = {action.memory_id for action in decision.actions}
        if action_ids != active_ids:
            unknown = action_ids - active_ids
            missing = active_ids - action_ids
            raise MemoryManagementError(
                f"Agent decision must cover every active memory; unknown={sorted(unknown)}, "
                f"missing={sorted(missing)}"
            )
        for consolidation in decision.consolidations:
            if not set(consolidation.source_memory_ids) <= active_ids:
                raise MemoryManagementError("Consolidation references an inactive or unknown memory")
        retained = {
            action.memory_id for action in decision.actions
            if action.operation is MemoryManagementOperation.RETAIN
        }
        tokens = sum(
            estimate_memory_tokens(record) for record in records if record.memory_id in retained
        )
        if tokens > self.config.max_active_tokens:
            raise MemoryManagementError(
                f"Agent selection uses {tokens} tokens, exceeding {self.config.max_active_tokens}"
            )
        return retained

    def _select(
        self, records: tuple[MemoryRecord, ...], policy: MemoryManagementPolicy,
    ) -> set[str]:
        if policy is MemoryManagementPolicy.FIFO:
            ordered = sorted(records, key=lambda record: (record.created_at, record.memory_id), reverse=True)
        elif policy is MemoryManagementPolicy.LEAST_RECENTLY_USED:
            ordered = sorted(records, key=lambda record: (
                self.store.access_stats(record.memory_id).last_retrieved_at is not None,
                self.store.access_stats(record.memory_id).last_retrieved_at or record.created_at,
                record.created_at, record.memory_id,
            ), reverse=True)
        elif policy is MemoryManagementPolicy.LEAST_RETRIEVED:
            ordered = sorted(records, key=lambda record: (
                self.store.access_stats(record.memory_id).retrieval_count,
                record.created_at, record.memory_id,
            ), reverse=True)
        elif policy is MemoryManagementPolicy.RANDOM_FIXED_SEED:
            ordered = list(sorted(records, key=lambda record: record.memory_id))
            random.Random(self.config.random_seed).shuffle(ordered)
        else:
            raise MemoryManagementError(f"Unsupported deterministic policy: {policy.value}")
        retained: set[str] = set()
        used = 0
        for record in ordered:
            tokens = estimate_memory_tokens(record)
            if used + tokens <= self.config.max_active_tokens:
                retained.add(record.memory_id)
                used += tokens
        return retained

    def _event(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.events is not None:
            self.events.append(event_type, payload)


class AgentMemoryManagementPolicy:
    """One strict provider decision using the existing runner attribution path."""

    def __init__(self, runner: ExperimentRunner, provider: AgentProvider) -> None:
        self.runner = runner
        self.provider = provider

    def decide(
        self, records: tuple[MemoryRecord, ...], *, budget_tokens: int,
    ) -> MemoryManagementDecision:
        candidates = [{
            "memory_id": record.memory_id,
            "kind": record.kind.value,
            "text": record.text,
            "confidence": record.confidence,
            "tags": list(record.tags),
            "source_event_count": len(record.source_event_ids),
            "estimated_tokens": estimate_memory_tokens(record),
            "observable_metadata": record.payload,
        } for record in records]
        prompt = (
            "Manage active memories under a fixed token budget. Maximize expected future task "
            "utility per retained token using only the observable candidate fields. Return JSON "
            "only. The actions array MUST cover every candidate ID exactly once with operation "
            "retain or deactivate. The estimated_tokens sum for retained candidates MUST be at "
            "most budget_tokens. Do not invent IDs. Use an empty consolidations array unless a "
            "consolidation is explicitly justified; selecting one does not create or replace a "
            "memory in this decision. Include a short reasoning_summary and predicted_future_value "
            "numbers from 0 to 1. Required schema:\n"
            + json.dumps(MemoryManagementDecision.model_json_schema(), sort_keys=True)
            + "\nInput:\n" + json.dumps({
                "budget_tokens": budget_tokens, "candidates": candidates,
            }, sort_keys=True)
        )
        return self._call("memory-management", prompt, repair=False)

    def _call(self, call_id: str, prompt: str, *, repair: bool) -> MemoryManagementDecision:
        estimated = max(1, (len(prompt.encode("utf-8")) + 3) // 4)
        self.runner.assert_model_call_allowed(input_tokens=estimated)
        request = ProviderRequest(
            call_id=call_id, prompt=prompt, estimated_input_tokens=estimated,
            decoding=self.runner.config.provider.decoding.model_dump(mode="json"), repair=repair,
        )
        response = self.provider.complete(request)
        self.runner.record_model_call(
            call_id,
            ModelCallUsage(
                request_id=response.request_id, input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens, latency_ms=response.usage.latency_ms,
                cost_usd=response.usage.cost_usd,
            ),
            provider=response.provider, model=response.model, route=response.route,
            decoding=request.decoding,
        )
        try:
            return MemoryManagementDecision.model_validate_json(_normalize_json(response.text))
        except Exception as exc:
            if repair:
                raise MemoryManagementError("Invalid memory-management decision after repair") from exc
            repair_prompt = (
                "Repair the response into JSON that satisfies every original requirement. Return "
                "JSON only, preserve the exact candidate IDs, cover each candidate exactly once, "
                "and respect the token budget.\nOriginal request:\n" + prompt
                + "\nInvalid response:\n" + response.text
            )
            return self._call(f"{call_id}-repair", repair_prompt, repair=True)


def _normalize_json(text: str) -> str:
    value = text.strip()
    if value.startswith("```") and value.endswith("```"):
        lines = value.splitlines()[1:-1]
        value = "\n".join(lines)
        if value.lstrip().lower().startswith("json"):
            value = value.lstrip()[4:].lstrip()
    return value
