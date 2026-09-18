"""Fixed-budget reflection and cited, versioned belief persistence."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

from pydantic import ValidationError

from prime_stardew.agent.models import ProviderRequest
from prime_stardew.agent.provider import AgentProvider
from prime_stardew.experiments.runner import ExperimentRunner, ModelCallUsage
from prime_stardew.memory import MemoryKind, MemoryRecord, MemoryStatus, MemoryStore

from .models import (
    BeliefProposal, EvidenceItem, ReflectionOutput, ReflectionResult,
    RefinementPolicy, RefinementTrigger,
)


class ReflectionError(RuntimeError):
    pass


class ReflectionEngine:
    def __init__(
        self,
        runner: ExperimentRunner,
        provider: AgentProvider,
        store: MemoryStore,
        policy: RefinementPolicy,
    ) -> None:
        self.runner = runner
        self.provider = provider
        self.store = store
        self.policy = policy

    def reflect(
        self,
        *,
        trigger: RefinementTrigger,
        evidence: tuple[EvidenceItem, ...],
        objective: str,
    ) -> ReflectionResult:
        if not self.runner.config.learning.refinement:
            raise ReflectionError("Refinement is disabled by the run configuration")
        if not self.policy.allows(trigger, evidence):
            raise ReflectionError(f"Policy {self.policy.trigger.value} does not allow {trigger.value}")
        selected, omitted, tokens = select_evidence(evidence, self.policy)
        count = sum(1 for event in self.runner.events.iter_records()
                    if event.event_type == "reflection_completed") + 1
        reflection_id = f"reflection-{count}"
        self.runner.events.append_idempotent(
            "reflection_evidence_selected",
            {
                "reflection_id": reflection_id, "trigger": trigger.value,
                "selected_event_ids": [item.event_id for item in selected],
                "omitted_event_ids": list(omitted), "selected_tokens": tokens,
                "budget_tokens": self.policy.evidence_token_budget,
            },
            idempotency_key=f"reflection:{reflection_id}:evidence",
        )
        prompt = _prompt(objective, trigger, selected, self._current_beliefs())
        output = self._complete(reflection_id, prompt)
        _validate_citations(output, selected, self.store)
        completed = self.runner.events.append_idempotent(
            "reflection_completed",
            {
                "reflection_id": reflection_id, "trigger": trigger.value,
                "output": output.model_dump(mode="json"),
            },
            idempotency_key=f"reflection:{reflection_id}:completed",
        )
        created, superseded = self._persist(
            reflection_id, output, str(completed.event_id), completed.timestamp,
        )
        if created:
            self.runner.record_memory_access(
                f"{reflection_id}-write", operation="write", memory_ids=created,
            )
        return ReflectionResult(
            reflection_id=reflection_id, trigger=trigger,
            selected_event_ids=tuple(item.event_id for item in selected),
            omitted_event_ids=omitted, selected_tokens=tokens, output=output,
            created_memory_ids=created, superseded_memory_ids=superseded,
        )

    def _complete(self, reflection_id: str, prompt: str) -> ReflectionOutput:
        response = self._call(reflection_id, prompt, repair=False)
        try:
            return ReflectionOutput.model_validate_json(_strip_fence(response.text))
        except (ValidationError, ValueError) as first_error:
            repair = (
                "Repair the candidate into exactly one JSON object matching the reflection "
                f"schema and preserve valid event citations. Error: {first_error}. "
                f"Candidate: {response.text}"
            )
            repaired = self._call(f"{reflection_id}-repair", repair, repair=True)
            try:
                return ReflectionOutput.model_validate_json(_strip_fence(repaired.text))
            except (ValidationError, ValueError) as final_error:
                raise ReflectionError("Invalid reflection after one repair") from final_error

    def _call(self, call_id: str, prompt: str, *, repair: bool):
        estimated = max(1, (len(prompt.encode("utf-8")) + 3) // 4)
        self.runner.assert_model_call_allowed(input_tokens=estimated)
        request = ProviderRequest(
            call_id=call_id, prompt=prompt, estimated_input_tokens=estimated,
            decoding=self.runner.config.provider.decoding.model_dump(mode="json"),
            repair=repair,
        )
        response = self.provider.complete(request)
        self.runner.record_model_call(
            call_id,
            ModelCallUsage(
                request_id=response.request_id, input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                latency_ms=response.usage.latency_ms, cost_usd=response.usage.cost_usd,
            ),
            provider=response.provider, model=response.model, route=response.route,
            decoding=request.decoding,
        )
        return response

    def _current_beliefs(self) -> tuple[MemoryRecord, ...]:
        return tuple(record for record in self.store.records()
                     if record.kind is MemoryKind.BELIEF)

    def _persist(
        self,
        reflection_id: str,
        output: ReflectionOutput,
        reflection_event_id: str,
        created_at: datetime,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        created: list[str] = []
        superseded: list[str] = []
        for index, lesson in enumerate(output.lessons, 1):
            memory_id = f"{self.runner.run_id}:{reflection_id}:lesson-{index}"
            self.store.add(MemoryRecord(
                memory_id=memory_id, kind=MemoryKind.SEMANTIC, text=lesson,
                source_event_ids=(reflection_event_id,), created_at=created_at,
                task_domain="reflection", tags=("lesson",),
            ))
            created.append(memory_id)
        for index, proposal in enumerate(output.beliefs, 1):
            parent = None
            if proposal.supersedes_id:
                parent = self.store.get(proposal.supersedes_id)
                if parent is None:
                    raise ReflectionError(
                        f"Cannot supersede missing or inactive belief: {proposal.supersedes_id}"
                    )
                memory_id = f"{parent.memory_id}-v{parent.version + 1}"
                superseded.append(parent.memory_id)
            else:
                memory_id = f"{self.runner.run_id}:{reflection_id}:belief-{index}-v1"
            sources = tuple(dict.fromkeys((*proposal.source_event_ids, reflection_event_id)))
            self.store.add(MemoryRecord(
                memory_id=memory_id, kind=MemoryKind.BELIEF,
                text=proposal.statement, confidence=proposal.confidence,
                source_event_ids=sources, created_at=created_at,
                task_domain="crop-rule", tags=("belief",),
                supersedes_id=proposal.supersedes_id,
                version=(parent.version + 1 if parent else 1),
                payload={
                    "reflection_id": reflection_id,
                    "supporting_event_ids": list(proposal.supporting_event_ids),
                    "contradicting_event_ids": list(proposal.contradicting_event_ids),
                    "predicted_event": proposal.predicted_event,
                    "predicted_probability": proposal.predicted_probability,
                },
            ))
            created.append(memory_id)
        return tuple(created), tuple(superseded)


def select_evidence(
    evidence: tuple[EvidenceItem, ...], policy: RefinementPolicy,
) -> tuple[tuple[EvidenceItem, ...], tuple[str, ...], int]:
    ranked = sorted(
        evidence,
        key=lambda item: (
            item.success is not False, -item.surprise, -item.game_day, item.event_id,
        ),
    )
    selected: list[EvidenceItem] = []
    omitted: list[str] = []
    used = 0
    for item in ranked:
        tokens = max(1, (len(item.text.encode("utf-8")) + 3) // 4)
        if len(selected) < policy.max_evidence and used + tokens <= policy.evidence_token_budget:
            selected.append(item)
            used += tokens
        else:
            omitted.append(item.event_id)
    return tuple(selected), tuple(omitted), used


def _validate_citations(
    output: ReflectionOutput,
    selected: tuple[EvidenceItem, ...],
    store: MemoryStore,
) -> None:
    allowed = {item.event_id for item in selected}
    for belief in output.beliefs:
        cited = set((*belief.source_event_ids, *belief.supporting_event_ids,
                     *belief.contradicting_event_ids))
        if not cited <= allowed:
            raise ReflectionError(f"Belief cites evidence outside the selected set: {cited - allowed}")
        if belief.supersedes_id:
            parent = store.get(belief.supersedes_id)
            if parent is None or parent.kind is not MemoryKind.BELIEF:
                raise ReflectionError("A revision must supersede an active belief")
            if not belief.contradicting_event_ids:
                raise ReflectionError("A revised belief must cite contradictory evidence")


def _prompt(
    objective: str,
    trigger: RefinementTrigger,
    evidence: tuple[EvidenceItem, ...],
    beliefs: tuple[MemoryRecord, ...],
) -> str:
    schema = {
        "schema_version": 1,
        "lessons": ["string"], "failed_assumptions": ["string"],
        "counterfactuals": ["string"], "goals": ["string"],
        "beliefs": [{
            "statement": "string", "confidence": 0.0,
            "source_event_ids": ["selected event ID"],
            "supporting_event_ids": ["selected event ID"],
            "contradicting_event_ids": ["selected event ID"],
            "supersedes_id": None, "predicted_event": None,
            "predicted_probability": None,
        }],
    }
    return "\n".join((
        "Return exactly one JSON object. Do not cite evidence absent from selected_evidence.",
        f"[objective]\n{json.dumps(objective)}",
        f"[trigger]\n{json.dumps(trigger.value)}",
        "[selected_evidence]\n" + json.dumps(
            [item.model_dump(mode="json") for item in evidence], sort_keys=True,
        ),
        "[current_active_beliefs]\n" + json.dumps([
            {"memory_id": belief.memory_id, "statement": belief.text,
             "confidence": belief.confidence, "version": belief.version}
            for belief in beliefs
        ], sort_keys=True),
        "[response_schema]\n" + json.dumps(schema, sort_keys=True),
    ))


def _strip_fence(value: str) -> str:
    value = value.strip()
    if value.startswith("```") and value.endswith("```"):
        lines = value.splitlines()[1:-1]
        if lines and lines[0].strip().lower() == "json":
            lines = lines[1:]
        return "\n".join(lines)
    return value


class MemoryConsolidator:
    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    def consolidate(
        self,
        *,
        rule_key: str,
        episode_ids: tuple[str, ...],
        statement: str,
        minimum_support: int = 2,
    ) -> MemoryRecord:
        if len(set(episode_ids)) < minimum_support:
            raise ReflectionError("Semantic consolidation has insufficient episode support")
        episodes = []
        for memory_id in episode_ids:
            record = self.store.get(memory_id)
            if record is None or record.kind is not MemoryKind.EPISODE:
                raise ReflectionError(f"Consolidation source is not an active episode: {memory_id}")
            episodes.append(record)
        digest = hashlib.sha256(
            json.dumps([rule_key, sorted(episode_ids), statement], separators=(",", ":")).encode()
        ).hexdigest()[:16]
        memory_id = f"semantic:{rule_key}:{digest}"
        existing = self.store.get(memory_id, include_inactive=True)
        if existing is not None:
            return existing
        sources = tuple(dict.fromkeys(
            event_id for episode in episodes for event_id in episode.source_event_ids
        ))
        return self.store.add(MemoryRecord(
            memory_id=memory_id, kind=MemoryKind.SEMANTIC, text=statement,
            source_event_ids=sources, task_domain="crop-rule",
            tags=("consolidated", rule_key),
            payload={"rule_key": rule_key, "episode_ids": list(episode_ids),
                     "support_count": len(episode_ids)},
        ))

