"""Nightly procedural-skill and reflection lifecycle for live E1 runs."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable
from typing import Any, Protocol

from prime_stardew.agent.models import ProviderRequest
from prime_stardew.agent.provider import AgentProvider
from prime_stardew.experiments.provenance import artifact_sha256
from prime_stardew.experiments.runner import ExperimentRunner, ModelCallUsage
from prime_stardew.reflection import (
    EvidenceItem, EvidenceKind, ReflectionEngine, RefinementPolicy, RefinementTrigger,
)
from prime_stardew.skills import (
    SAFE_ACTIONS, SkillDefinition, SkillStore, SkillValidation,
    ValidationStage, compile_skill,
)
from prime_stardew.skills.models import SkillProposal, normalize_skill_json
from prime_stardew.tasks import ActionCommand

from .models import InferenceCategory, PERSISTENT_OBJECTIVE
from .prime_adapter import PrimeHarnessAdapter


class PauseController(Protocol):
    def pause(self) -> Any: ...
    def resume(self) -> Any: ...


class PausingProvider:
    def __init__(self, provider: AgentProvider, controller: PauseController | None) -> None:
        self.provider = provider
        self.controller = controller

    def complete(self, request: ProviderRequest):
        if self.controller is not None:
            self.controller.pause()
        try:
            return self.provider.complete(request)
        finally:
            if self.controller is not None:
                self.controller.resume()

    def close(self) -> None:
        return None


class E1LearningLifecycle:
    """Run the capabilities enabled by D/E/F at an authenticated day boundary."""

    def __init__(
        self,
        *,
        harness: PrimeHarnessAdapter,
        provider: AgentProvider,
        skill_store: SkillStore | None = None,
        pause_controller: PauseController | None = None,
        disposable_skill_validator: Callable[[SkillDefinition], bool] | None = None,
    ) -> None:
        self.harness = harness
        self.runner = harness.session.runner
        self.provider = PausingProvider(provider, pause_controller)
        self.skill_store = skill_store
        self.disposable_skill_validator = disposable_skill_validator
        self._patterns: dict[str, list[str]] = defaultdict(list)
        self._proposed_signatures: set[str] = set()
        self._last_sequence = 0
        self.reflection = None
        memory = harness.session.memory_store
        if harness.capabilities.reflection:
            if memory is None:
                raise ValueError("E1 reflection requires a persistent MemoryStore")
            self.reflection = ReflectionEngine(
                self.runner, self.provider, memory,
                RefinementPolicy(
                    trigger=RefinementTrigger.NIGHTLY,
                    evidence_token_budget=min(512, self.runner.config.context.memories_tokens),
                    max_evidence=12,
                ),
            )
        if harness.capabilities.procedural_skills and skill_store is None:
            raise ValueError("E1 procedural skills require a SkillStore")

    def end_day(self, elapsed_day: int) -> None:
        records = tuple(
            record for record in self.runner.events.iter_records()
            if record.sequence > self._last_sequence
        )
        if records:
            self._last_sequence = records[-1].sequence
        if self.skill_store is not None and self.harness.capabilities.procedural_skills:
            self._observe_and_propose_skills(elapsed_day, records)
        if self.reflection is not None:
            self._reflect(elapsed_day, records)

    def _observe_and_propose_skills(self, day: int, records: tuple[Any, ...]) -> None:
        completed = [
            record for record in records
            if (
                record.event_type == "action_completed"
                and record.payload.get("name") != "sleep"
                and record.payload.get("task_id") == "e1-broad-objective"
            )
        ]
        failures = {str(record.payload.get("action_id")) for record in completed
                    if not record.payload.get("succeeded")}
        if not completed or failures:
            return
        for record in records:
            if record.event_type != "agent_decision":
                continue
            actions = record.payload.get("decision", {}).get("actions", [])
            if not actions or any(str(item.get("name")) not in SAFE_ACTIONS for item in actions):
                continue
            signature = artifact_sha256(actions)
            evidence_id = f"decision:{record.event_id}"
            self._patterns[signature].append(evidence_id)
            if len(self._patterns[signature]) >= 2 and signature not in self._proposed_signatures:
                self._proposed_signatures.add(signature)
                self._propose_skill(day, signature, actions, tuple(self._patterns[signature]))

    def _propose_skill(
        self,
        day: int,
        signature: str,
        actions: list[dict[str, Any]],
        source_ids: tuple[str, ...],
    ) -> None:
        proposal_number = len(self._proposed_signatures)
        proposal_id = f"e1-skill-proposal-{proposal_number}"
        schema = {
            "skill": {
                "schema_version": 1, "skill_id": "lowercase-hyphen-id", "version": 1,
                "name": "string", "description": "string", "task_kind": "recurring-live-pattern",
                "parameters": [], "preconditions": ["string"], "postconditions": ["string"],
                "steps": [{"action": "typed action", "arguments": [{"literal": "scalar"}]}],
                "source_trajectory_ids": list(source_ids),
            },
            "rationale": "string",
        }
        prompt = "\n".join((
            "Return exactly one JSON object. Convert the repeated successful action sequence into "
            "one immutable literal-only skill. Preserve every action and argument exactly. Do not "
            "add setup, debug, sleep, or privileged actions.",
            f"[objective]\n{json.dumps(PERSISTENT_OBJECTIVE)}",
            f"[repeated_actions]\n{json.dumps(actions, sort_keys=True)}",
            f"[source_trajectory_ids]\n{json.dumps(source_ids)}",
            f"[response_schema]\n{json.dumps(schema, sort_keys=True)}",
        ))
        before = self.runner.state
        response = self._call(proposal_id, prompt)
        after = self.runner.state
        self._account(InferenceCategory.SKILL_PROPOSAL, before, after, day)
        proposal = SkillProposal.model_validate_json(normalize_skill_json(response.text))
        skill = proposal.skill
        if skill.source_trajectory_ids != source_ids:
            raise ValueError("E1 skill proposal must cite all repeated source decisions in order")
        if skill.parameters:
            raise ValueError("E1 recurring-pattern smoke skills must be literal-only")
        compiled = compile_skill(skill, {}, allowed_actions=tuple(SAFE_ACTIONS))
        expected = tuple(ActionCommand(
            name=str(item["name"]), arguments=tuple(item.get("arguments", ())),
        ) for item in actions)
        if compiled != expected:
            raise ValueError("E1 skill proposal changed its source primitive sequence")
        assert self.skill_store is not None
        self.skill_store.add(skill)
        self.runner.events.append("skill_version_created", {
            "skill_id": skill.skill_id, "version": skill.version,
            "content_sha256": skill.content_sha256(),
            "source_trajectory_ids": list(source_ids), "game_day": day,
        })
        replay_hash = artifact_sha256([item.model_dump(mode="json") for item in compiled])
        self.skill_store.record_validation(SkillValidation(
            validation_id=f"{proposal_id}-replay", skill_id=skill.skill_id,
            version=skill.version, stage=ValidationStage.REPLAY,
            fixture_id=f"e1-live-pattern-{signature[:12]}", success=True, score=1,
            trajectory_sha256=replay_hash,
        ))
        self.runner.events.append("skill_validated", {
            "skill_id": skill.skill_id, "version": skill.version,
            "stage": ValidationStage.REPLAY.value, "success": True,
        })
        if self.disposable_skill_validator is None:
            self.runner.events.append("skill_activation_deferred", {
                "skill_id": skill.skill_id, "version": skill.version,
                "reason": "disposable live validator unavailable",
            })
            return
        disposable_ok = bool(self.disposable_skill_validator(skill))
        self.skill_store.record_validation(SkillValidation(
            validation_id=f"{proposal_id}-disposable", skill_id=skill.skill_id,
            version=skill.version, stage=ValidationStage.DISPOSABLE,
            fixture_id=f"e1-disposable-pattern-{signature[:12]}",
            success=disposable_ok, score=float(disposable_ok),
            trajectory_sha256=replay_hash,
            failure_reasons=() if disposable_ok else ("disposable validation failed",),
        ))
        self.runner.events.append("skill_validated", {
            "skill_id": skill.skill_id, "version": skill.version,
            "stage": ValidationStage.DISPOSABLE.value, "success": disposable_ok,
        })
        if disposable_ok:
            self.skill_store.activate(skill.skill_id, skill.version)
            self.harness.register_active_skill(skill)

    def _reflect(self, day: int, records: tuple[Any, ...]) -> None:
        evidence = tuple(_evidence_item(record, day) for record in records if record.event_type in {
            "action_completed", "e1_live_day_started", "e1_live_day_pre_sleep",
        })
        if not evidence:
            return
        before = self.runner.state
        assert self.reflection is not None
        result = self.reflection.reflect(
            trigger=RefinementTrigger.NIGHTLY,
            evidence=evidence,
            objective=PERSISTENT_OBJECTIVE,
        )
        after = self.runner.state
        self._account(InferenceCategory.REFLECTION, before, after, day)
        self.harness.register_refinement(
            result.reflection_id, result.output.goals,
        )

    def _call(self, call_id: str, prompt: str):
        estimated = max(1, (len(prompt.encode("utf-8")) + 3) // 4)
        self.runner.assert_model_call_allowed(input_tokens=estimated)
        request = ProviderRequest(
            call_id=call_id, prompt=prompt, estimated_input_tokens=estimated,
            decoding=self.runner.config.provider.decoding.model_dump(mode="json"),
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
        return response

    def _account(self, category: InferenceCategory, before, after, day: int) -> None:
        self.runner.events.append("e1_inference_accounted", {
            "game_day": day, "category": category.value,
            "calls": after.model_call_count - before.model_call_count,
            "input_tokens": after.input_tokens - before.input_tokens,
            "output_tokens": after.output_tokens - before.output_tokens,
            "cost_usd": after.cost_usd - before.cost_usd,
        })


def _evidence_item(record: Any, day: int) -> EvidenceItem:
    failed = record.event_type == "action_completed" and not record.payload.get("succeeded")
    kind = EvidenceKind.ERROR if failed else (
        EvidenceKind.OUTCOME if record.event_type == "action_completed" else EvidenceKind.OBSERVATION
    )
    return EvidenceItem(
        event_id=str(record.event_id), kind=kind,
        text=json.dumps(record.payload, sort_keys=True, ensure_ascii=False)[:4000],
        game_day=day, success=False if failed else True,
        surprise=1.0 if failed else 0.0,
        tags=(record.event_type,),
    )
