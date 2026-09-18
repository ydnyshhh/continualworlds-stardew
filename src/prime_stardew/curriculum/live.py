"""Strict model decision for repairing a corrupted belief from live evidence."""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field

from prime_stardew.agent.models import ProviderRequest
from prime_stardew.agent.provider import AgentProvider
from prime_stardew.corruption import RepairResponse
from prime_stardew.experiments.runner import ExperimentRunner, ModelCallUsage


class LiveEvidenceRepairDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    response: RepairResponse
    estimated_heldout_yield: int = Field(ge=0, le=20)
    trusted_evidence_ids: tuple[str, ...] = Field(min_length=1)
    rejected_evidence_ids: tuple[str, ...] = ()
    rationale: str = Field(min_length=3, max_length=600)


class LiveEvidenceRepairPolicy:
    def __init__(self, runner: ExperimentRunner, provider: AgentProvider) -> None:
        self.runner = runner
        self.provider = provider

    def decide(self, *, memory: dict[str, object], evidence: list[dict[str, object]]):
        responses = "|".join(item.value for item in RepairResponse)
        shape = (
            '{"response":"' + responses + '","estimated_heldout_yield":0,'
            '"trusted_evidence_ids":["..."],"rejected_evidence_ids":["..."],'
            '"rationale":"..."}'
        )
        prompt = (
            "Decide how to handle one stored resource-yield belief after conflicting evidence. "
            "Evidence marked live_game=true is an observed game outcome; other reports may be "
            "unverified. Cite only supplied evidence IDs. Return JSON only in this exact shape: "
            + shape + "\n" + json.dumps(
                {"memory": memory, "evidence": evidence}, sort_keys=True, separators=(",", ":"),
            )
        )
        decision = self._call("m17-live-corruption", prompt, prompt, False)
        allowed = {str(item["evidence_id"]) for item in evidence}
        cited = set(decision.trusted_evidence_ids) | set(decision.rejected_evidence_ids)
        if not cited <= allowed:
            raise ValueError("Live repair decision cited unknown evidence")
        return decision

    def _call(self, call_id: str, prompt: str, original: str, repair: bool):
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
            ), provider=response.provider, model=response.model, route=response.route,
            decoding=request.decoding,
        )
        try:
            return LiveEvidenceRepairDecision.model_validate_json(_normalize_json(response.text))
        except Exception as exc:
            if repair:
                raise ValueError("Invalid live evidence repair after repair") from exc
            return self._call(
                f"{call_id}-repair",
                "Repair as JSON matching the required shape. Return JSON only.\n" + original
                + "\nInvalid response:\n" + response.text,
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
