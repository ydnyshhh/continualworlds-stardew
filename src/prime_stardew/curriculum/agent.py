"""Authenticated provider interface for M17 curriculum generation."""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field, model_validator

from prime_stardew.agent.models import ProviderRequest
from prime_stardew.agent.provider import AgentProvider
from prime_stardew.experiments.runner import ExperimentRunner, ModelCallUsage


class CurriculumProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    observed_weakness: tuple[str, ...] = Field(min_length=1)
    evidence: tuple[str, ...] = Field(min_length=1)
    proposed_training_task: tuple[str, ...] = Field(min_length=1)
    expected_transfer: str = Field(min_length=3, max_length=600)
    estimated_cost: int = Field(gt=0)
    success_criterion: str = Field(min_length=3, max_length=600)

    @model_validator(mode="after")
    def unique_items(self) -> "CurriculumProposal":
        if len(set(self.proposed_training_task)) != len(self.proposed_training_task):
            raise ValueError("Curriculum objectives must be unique")
        return self


class AgentCurriculumPolicy:
    def __init__(self, runner: ExperimentRunner, provider: AgentProvider) -> None:
        self.runner = runner
        self.provider = provider

    def propose(
        self, *, diagnostics: list[dict[str, object]],
        catalog: list[dict[str, object]], budget: int,
    ) -> CurriculumProposal:
        shape = (
            '{"observed_weakness":["..."],"evidence":["diagnostic-id"],'
            '"proposed_training_task":["objective-id"],"expected_transfer":"...",'
            '"estimated_cost":2,"success_criterion":"..."}'
        )
        payload = {
            "diagnostics": diagnostics, "practice_catalog": catalog,
            "objective_budget": budget,
        }
        prompt = (
            "Select a curriculum from observed weaknesses. Choose exactly the allowed number of "
            "catalog objective IDs. Exact held-out tasks and answers are unavailable. Cite only "
            "diagnostic IDs supplied here. Return JSON only in this exact shape: " + shape + "\n"
            + json.dumps(payload, sort_keys=True, separators=(",", ":"))
        )
        proposal = self._call("m17-curriculum", prompt, prompt, False)
        allowed_tasks = {str(item["objective_id"]) for item in catalog}
        allowed_evidence = {str(item["diagnostic_id"]) for item in diagnostics}
        if len(proposal.proposed_training_task) != budget:
            raise ValueError("M17 proposal must exhaust the fixed objective budget")
        if not set(proposal.proposed_training_task) <= allowed_tasks:
            raise ValueError("M17 proposal selected an unknown objective")
        if not set(proposal.evidence) <= allowed_evidence:
            raise ValueError("M17 proposal cited unknown evidence")
        if proposal.estimated_cost != budget:
            raise ValueError("M17 proposal cost must equal the fixed budget")
        return proposal

    def _call(
        self, call_id: str, prompt: str, original: str, repair: bool,
    ) -> CurriculumProposal:
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
            return CurriculumProposal.model_validate_json(_normalize_json(response.text))
        except Exception as exc:
            if repair:
                raise ValueError("Invalid M17 curriculum proposal after repair") from exc
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
