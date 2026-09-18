"""Strict authenticated-provider interface for M16 repair and meta-policy proposals."""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field, model_validator

from prime_stardew.agent.models import ProviderRequest
from prime_stardew.agent.provider import AgentProvider
from prime_stardew.experiments.runner import ExperimentRunner, ModelCallUsage

from .models import RepairResponse


class MemoryRepairChoice(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_id: str
    response: RepairResponse
    preferred_action: str
    rationale: str = Field(min_length=1, max_length=400)


class MemoryRepairDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    choices: tuple[MemoryRepairChoice, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_scenarios(self) -> "MemoryRepairDecision":
        ids = [item.scenario_id for item in self.choices]
        if len(ids) != len(set(ids)):
            raise ValueError("M16 repair decision contains duplicate scenario IDs")
        return self


class PolicyTrigger(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    signal: str = Field(pattern=r"^[a-z][a-z0-9_]{2,47}$")
    comparison: str = Field(pattern=r"^(>=|>|==)$")
    threshold: int = Field(ge=1, le=3)


class PolicyPatchProposal(BaseModel):
    """Restricted data-only DSL: descriptive fields plus one bounded trigger."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mechanism_name: str = Field(pattern=r"^[a-z][a-z0-9_]{2,47}$")
    tracked_signal: str = Field(min_length=3, max_length=300)
    state_update_rule: str = Field(min_length=3, max_length=800)
    trigger: PolicyTrigger
    repair_response: RepairResponse
    rationale: str = Field(min_length=3, max_length=1_000)
    success_criterion: str = Field(min_length=3, max_length=600)


class AgentMemoryRepairPolicy:
    def __init__(self, runner: ExperimentRunner, provider: AgentProvider) -> None:
        self.runner = runner
        self.provider = provider

    def decide(
        self, *, episode: int, records: list[dict[str, object]],
        observations: list[dict[str, object]], policy_patch: dict[str, object] | None = None,
        batch_id: str = "all",
    ) -> MemoryRepairDecision:
        payload = {
            "episode": episode, "memory_records": records,
            "observed_outcomes": observations, "active_policy_patch": policy_patch,
        }
        responses = "|".join(item.value for item in RepairResponse)
        shape = (
            '{"choices":[{"scenario_id":"...","response":"' + responses
            + '","preferred_action":"alpha|beta","rationale":"..."}]}'
        )
        prompt = (
            "For every memory record, choose a response and next action using only the record and "
            "observed outcomes. Some records may be correct. You are not told which records, if any, "
            "are corrupted. Return JSON only in this exact shape: " + shape + "\n"
            + json.dumps(payload, sort_keys=True, separators=(",", ":"))
        )
        decision = self._call_decision(
            f"m16-episode-{episode}-{batch_id}", prompt, prompt, False,
        )
        expected = {str(item["scenario_id"]): tuple(item["alternatives"]) for item in records}
        if {item.scenario_id for item in decision.choices} != set(expected):
            raise ValueError("M16 decision must cover every record exactly once")
        if any(item.preferred_action not in expected[item.scenario_id] for item in decision.choices):
            raise ValueError("M16 decision selected an undeclared action")
        return decision

    def propose_policy_patch(self, *, summary: dict[str, object]) -> PolicyPatchProposal:
        shape = (
            '{"mechanism_name":"lower_snake_case","tracked_signal":"...",'
            '"state_update_rule":"...","trigger":{"signal":"lower_snake_case",'
            '"comparison":">=|>|==","threshold":1},"repair_response":"one repair response",'
            '"rationale":"...","success_criterion":"..."}'
        )
        prompt = (
            "Review the first episode summary and propose one schema-level memory-policy change for "
            "a held-out second corruption episode. The patch is a restricted data-only rule: no code, "
            "commands, tools, paths, or evaluator labels. Infer the mechanism yourself; no desired "
            "policy is supplied. Return JSON only in this exact shape: " + shape + "\n"
            + json.dumps(summary, sort_keys=True, separators=(",", ":"))
        )
        return self._call_patch("m16-policy-patch", prompt, prompt, False)

    def _complete(self, call_id: str, prompt: str, repair: bool) -> str:
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
        return response.text

    def _call_decision(
        self, call_id: str, prompt: str, original: str, repair: bool,
    ) -> MemoryRepairDecision:
        text = self._complete(call_id, prompt, repair)
        try:
            return MemoryRepairDecision.model_validate_json(_normalize_json(text))
        except Exception as exc:
            if repair:
                raise ValueError("Invalid M16 repair decision after repair") from exc
            return self._call_decision(
                f"{call_id}-repair",
                "Repair as JSON matching the required shape. Return JSON only.\n" + original
                + "\nInvalid response:\n" + text,
                original, True,
            )

    def _call_patch(
        self, call_id: str, prompt: str, original: str, repair: bool,
    ) -> PolicyPatchProposal:
        text = self._complete(call_id, prompt, repair)
        try:
            return PolicyPatchProposal.model_validate_json(_normalize_json(text))
        except Exception as exc:
            if repair:
                raise ValueError("Invalid M16 policy patch after repair") from exc
            return self._call_patch(
                f"{call_id}-repair",
                "Repair as JSON matching the required shape. Return JSON only.\n" + original
                + "\nInvalid response:\n" + text,
                original, True,
            )


def _normalize_json(text: str) -> str:
    value = text.strip()
    if value.startswith("```") and value.endswith("```"):
        lines = value.splitlines()[1:-1]
        value = "\n".join(lines)
        if value.lstrip().lower().startswith("json"):
            value = value.lstrip()[4:].lstrip()
    return value
