"""Strict provider plan for live Stardew sampling without supplied priors."""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field

from prime_stardew.agent.models import ProviderRequest
from prime_stardew.agent.provider import AgentProvider
from prime_stardew.experiments.runner import ExperimentRunner, ModelCallUsage


class LiveSamplingError(RuntimeError):
    pass


class LiveSamplingPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sample_count: int = Field(ge=1, le=2)
    expected_learning_value: float = Field(ge=0, le=1)
    stopping_rule: str = Field(min_length=1)
    rationale: str = Field(min_length=1)


class LiveSamplingPolicy:
    def __init__(self, runner: ExperimentRunner, provider: AgentProvider) -> None:
        self.runner = runner
        self.provider = provider

    def decide(self) -> LiveSamplingPlan:
        prompt = (
            "You are controlling a disposable live Stardew Valley experiment. Three identical "
            "mystery farm twigs are available. Clearing a twig costs real stamina and game time "
            "and produces an uncertain amount of Wood under the live game RNG. Reserve the third "
            "twig as a held-out validation. Choose whether to sample one or two of the first two "
            "twigs before predicting the held-out yield. No numeric yield prior, expected-value "
            "feature, hidden object ID, or evaluator outcome is supplied. Return JSON only with "
            "this exact shape: {\"sample_count\":1,\"expected_learning_value\":0.0," 
            "\"stopping_rule\":\"...\",\"rationale\":\"...\"}. sample_count must be 1 or 2."
        )
        return self._call("live-sampling-plan", prompt, original=prompt, repair=False)

    def _call(
        self, call_id: str, prompt: str, *, original: str, repair: bool,
    ) -> LiveSamplingPlan:
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
            return LiveSamplingPlan.model_validate_json(_normalize_json(response.text))
        except Exception as exc:
            if repair:
                raise LiveSamplingError("Invalid live sampling plan after repair") from exc
            repair_prompt = (
                "Repair this as JSON matching the original exact shape. Return JSON only.\n"
                + original + "\nInvalid response:\n" + response.text
            )
            return self._call(
                f"{call_id}-repair", repair_prompt, original=original, repair=True,
            )


def _normalize_json(text: str) -> str:
    value = text.strip()
    if value.startswith("```") and value.endswith("```"):
        lines = value.splitlines()[1:-1]
        value = "\n".join(lines)
        if value.lstrip().lower().startswith("json"):
            value = value.lstrip()[4:].lstrip()
    return value

