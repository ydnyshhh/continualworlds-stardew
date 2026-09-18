"""Strict provider policy for contextual belief revision and recovery."""

from __future__ import annotations

import json
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from prime_stardew.agent.models import ProviderRequest
from prime_stardew.agent.provider import AgentProvider
from prime_stardew.experiments.runner import ExperimentRunner, ModelCallUsage


class ShiftResponse(StrEnum):
    RETAIN = "retain"
    CONTEXTUALIZE = "contextualize"
    RETRIEVE = "retrieve"


class ShiftChoice(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mechanic_id: str
    response: ShiftResponse
    preferred_action: str
    rationale: str = Field(min_length=1)


class ShiftDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    choices: tuple[ShiftChoice, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_mechanics(self) -> "ShiftDecision":
        ids = [item.mechanic_id for item in self.choices]
        if len(ids) != len(set(ids)):
            raise ValueError("Shift decision contains duplicate mechanic IDs")
        return self


class AgentShiftPolicy:
    def __init__(self, runner: ExperimentRunner, provider: AgentProvider) -> None:
        self.runner = runner
        self.provider = provider

    def decide(
        self,
        *,
        phase: str,
        context_cue: str,
        mechanics: list[dict[str, object]],
        observations: list[dict[str, object]],
        memories: list[dict[str, object]],
    ) -> ShiftDecision:
        payload = {
            "phase": phase,
            "visible_context_cue": context_cue,
            "mechanics": mechanics,
            "observations": observations,
            "context_labeled_memories": memories,
        }
        shape = (
            '{"choices":[{"mechanic_id":"...","response":"retain|contextualize|retrieve",'
            '"preferred_action":"alpha|beta","rationale":"..."}]}'
        )
        prompt = (
            "Choose one response and next action for every listed mechanic. A changed outcome may "
            "be context-specific: preserve useful memories from other visible contexts. Use only "
            "the supplied observations and context-labeled memories. No evaluator optimum or hidden "
            "world definition is supplied. Return JSON only in this exact shape: " + shape + "\n" +
            json.dumps(payload, sort_keys=True, separators=(",", ":"))
        )
        decision = self._call(f"m15-{phase}", prompt, original=prompt, repair=False)
        expected = {str(item["mechanic_id"]): tuple(item["choices"]) for item in mechanics}
        actual = {item.mechanic_id for item in decision.choices}
        if actual != set(expected):
            raise ValueError("Shift decision must cover every mechanic exactly once")
        if any(item.preferred_action not in expected[item.mechanic_id]
               for item in decision.choices):
            raise ValueError("Shift decision selected an undeclared action")
        return decision

    def _call(
        self, call_id: str, prompt: str, *, original: str, repair: bool,
    ) -> ShiftDecision:
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
            return ShiftDecision.model_validate_json(_normalize_json(response.text))
        except Exception as exc:
            if repair:
                raise ValueError("Invalid M15 shift decision after repair") from exc
            return self._call(
                f"{call_id}-repair",
                "Repair as JSON matching the required shape. Return JSON only.\n" + original
                + "\nInvalid response:\n" + response.text,
                original=original, repair=True,
            )


def _normalize_json(text: str) -> str:
    value = text.strip()
    if value.startswith("```") and value.endswith("```"):
        lines = value.splitlines()[1:-1]
        value = "\n".join(lines)
        if value.lstrip().lower().startswith("json"):
            value = value.lstrip()[4:].lstrip()
    return value
