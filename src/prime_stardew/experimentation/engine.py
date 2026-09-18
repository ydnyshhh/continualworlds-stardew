"""Decision, validation, belief versioning, and execution for active experiments."""

from __future__ import annotations

import json
import math
import random
from datetime import UTC, datetime

from prime_stardew.agent.models import ProviderRequest
from prime_stardew.agent.provider import AgentProvider
from prime_stardew.experiments.runner import ExperimentRunner, ModelCallUsage
from prime_stardew.memory import MemoryKind, MemoryRecord, MemoryStore
from prime_stardew.telemetry import EventStore

from .models import (
    ExperimentAction, ExperimentChoice, ExperimentDecision, ExperimentOutcome,
    ExperimentScenario, HiddenMechanicScenario, expected_experiment_net_value,
)


class ExperimentValidationError(RuntimeError):
    pass


def deterministic_decision(
    scenarios: tuple[ExperimentScenario, ...], *, policy: str, seed: int,
    hidden: tuple[HiddenMechanicScenario, ...] | None = None,
) -> ExperimentDecision:
    hidden_by_id = {scenario.scenario_id: scenario for scenario in hidden or ()}
    choices = []
    for index, scenario in enumerate(scenarios):
        expected_net = expected_experiment_net_value(scenario)
        if policy in {"no_explicit_uncertainty", "belief_tracking_only"}:
            action = ExperimentAction.EXPLOIT_SAFE
            reason = "Use the known safe treatment without an information-gathering action."
        elif policy == "agent_experimentation":
            action = (
                ExperimentAction.EXPERIMENT_CANDIDATE
                if expected_net > 0 else ExperimentAction.EXPLOIT_SAFE
            )
            reason = "Experiment only when visible expected total value exceeds safe exploitation."
        elif policy == "random_experimentation":
            action = (
                ExperimentAction.EXPERIMENT_CANDIDATE
                if random.Random(seed * 101 + index).random() < 0.5
                else ExperimentAction.EXPLOIT_SAFE
            )
            reason = "Fixed-seed random baseline choice."
        elif policy == "oracle_experimentation":
            realized = hidden_by_id.get(scenario.scenario_id)
            if realized is None:
                raise ExperimentValidationError("Oracle policy requires evaluator mechanics")
            experiment_total = (
                realized.candidate_value - scenario.experiment_overhead
                + (scenario.production_cycles - 1)
                * max(scenario.safe_value, realized.candidate_value)
            )
            action = (
                ExperimentAction.EXPERIMENT_CANDIDATE
                if experiment_total > scenario.safe_value * scenario.production_cycles
                else ExperimentAction.EXPLOIT_SAFE
            )
            reason = "Evaluator-only oracle upper bound."
        else:
            raise ExperimentValidationError(f"Unknown experimentation policy: {policy}")
        choices.append(ExperimentChoice(
            scenario_id=scenario.scenario_id,
            action=action,
            predicted_high_probability=scenario.prior_high_probability,
            expected_information_gain_bits=(
                bernoulli_entropy(scenario.prior_high_probability)
                if action is ExperimentAction.EXPERIMENT_CANDIDATE else 0
            ),
            expected_net_value=expected_net,
            reason=reason,
        ))
    return ExperimentDecision(
        choices=tuple(choices), policy_summary=f"Deterministic {policy} policy.",
    )


def validate_decision(
    decision: ExperimentDecision,
    scenarios: tuple[ExperimentScenario, ...],
    *, experiment_budget: int,
) -> None:
    expected_ids = {scenario.scenario_id for scenario in scenarios}
    actual_ids = {choice.scenario_id for choice in decision.choices}
    if actual_ids != expected_ids:
        raise ExperimentValidationError(
            f"Decision must cover each scenario exactly once; unknown={sorted(actual_ids - expected_ids)}, "
            f"missing={sorted(expected_ids - actual_ids)}"
        )
    experiments = sum(
        choice.action is ExperimentAction.EXPERIMENT_CANDIDATE
        for choice in decision.choices
    )
    if experiments > experiment_budget:
        raise ExperimentValidationError(
            f"Decision requests {experiments} experiments, exceeding budget {experiment_budget}"
        )


def execute_decision(
    decision: ExperimentDecision,
    hidden: tuple[HiddenMechanicScenario, ...],
    *, policy: str,
    experiment_budget: int,
    events: EventStore,
    memory: MemoryStore,
) -> tuple[ExperimentOutcome, ...]:
    visible = tuple(scenario.observable() for scenario in hidden)
    validate_decision(decision, visible, experiment_budget=experiment_budget)
    choice_by_id = {choice.scenario_id: choice for choice in decision.choices}
    outcomes = []
    for scenario in hidden:
        choice = choice_by_id[scenario.scenario_id]
        prior_id = None
        if policy != "no_explicit_uncertainty":
            prior_id = f"belief:{scenario.scenario_id}:v1"
            created = events.append("hypothesis_created", {
                "scenario_id": scenario.scenario_id,
                "memory_id": prior_id,
                "statement": "The candidate treatment may have the high declared value.",
                "confidence": scenario.prior_high_probability,
                "context": scenario.context,
                "policy": policy,
            })
            memory.add(MemoryRecord(
                memory_id=prior_id, kind=MemoryKind.BELIEF,
                text="The candidate treatment may have the high declared value.",
                confidence=scenario.prior_high_probability,
                source_event_ids=(str(created.event_id),), task_domain="hidden-crop-mechanic",
                tags=("m14", "candidate-value", scenario.scenario_id),
                payload={
                    "possible_values": [scenario.candidate_low_value,
                                        scenario.candidate_high_value],
                    "predicted_event": "candidate value is high",
                    "predicted_probability": scenario.prior_high_probability,
                    "context": scenario.context,
                },
            ))
        proposal = events.append("experiment_proposal", {
            **choice.model_dump(mode="json"),
            "policy": policy,
            "causal_parent_ids": [prior_id] if prior_id else [],
        })
        safe_total = scenario.safe_value * scenario.production_cycles
        revised_id = None
        if choice.action is ExperimentAction.EXPERIMENT_CANDIDATE:
            first_reward = scenario.candidate_value - scenario.experiment_overhead
            remaining_reward = (scenario.production_cycles - 1) * max(
                scenario.safe_value, scenario.candidate_value,
            )
            total_reward = first_reward + remaining_reward
            execution = events.append("experiment_execution", {
                "scenario_id": scenario.scenario_id,
                "proposal_event_id": str(proposal.event_id),
                "observed_candidate_value": scenario.candidate_value,
                "experiment_overhead": scenario.experiment_overhead,
                "reward_this_cycle": first_reward,
                "source_evidence": [str(proposal.event_id)],
                "policy": policy,
            })
            if prior_id is not None:
                revised_id = f"belief:{scenario.scenario_id}:v2"
                statement = f"The candidate treatment value is {scenario.candidate_value}."
                memory.add(MemoryRecord(
                    memory_id=revised_id, kind=MemoryKind.BELIEF, text=statement,
                    confidence=1, source_event_ids=(str(execution.event_id),),
                    task_domain="hidden-crop-mechanic",
                    tags=("m14", "candidate-value", scenario.scenario_id),
                    supersedes_id=prior_id, version=2,
                    payload={
                        "observed_candidate_value": scenario.candidate_value,
                        "supporting_event_ids": [str(execution.event_id)],
                        "context": scenario.context,
                    },
                ))
                events.append("hypothesis_revised", {
                    "scenario_id": scenario.scenario_id,
                    "memory_id": revised_id,
                    "supersedes_id": prior_id,
                    "source_evidence": [str(execution.event_id)],
                    "statement": statement,
                    "confidence": 1,
                    "policy": policy,
                })
            information_gain = bernoulli_entropy(scenario.prior_high_probability)
            learned_value = scenario.candidate_value
            identification_cycle = 1
            future_attribution = max(
                0, scenario.candidate_value - scenario.safe_value,
            ) * (scenario.production_cycles - 1)
            immediate_cost = scenario.safe_value - first_reward
        else:
            total_reward = safe_total
            information_gain = 0.0
            learned_value = None
            identification_cycle = None
            future_attribution = 0
            immediate_cost = 0
        outcome = ExperimentOutcome(
            scenario_id=scenario.scenario_id,
            action=choice.action,
            total_reward=total_reward,
            safe_counterfactual_reward=safe_total,
            reward_difference_from_safe=total_reward - safe_total,
            experiment_overhead=(scenario.experiment_overhead
                                 if choice.action is ExperimentAction.EXPERIMENT_CANDIDATE else 0),
            immediate_opportunity_cost=immediate_cost,
            information_gain_bits=information_gain,
            candidate_identified=learned_value is not None,
            identification_cycle=identification_cycle,
            learned_candidate_value=learned_value,
            future_reward_attributable_to_information=future_attribution,
            prior_belief_id=prior_id,
            revised_belief_id=revised_id,
        )
        events.append("active_experiment_scenario_completed", {
            "outcome": outcome.model_dump(mode="json"),
            "choice": choice.model_dump(mode="json"),
            "hidden_candidate_value": scenario.candidate_value,
            "candidate_high_value": scenario.candidate_high_value,
            "expected_experiment_worthwhile": scenario.expected_experiment_worthwhile,
            "policy": policy,
        })
        outcomes.append(outcome)
    return tuple(outcomes)


class AgentExperimentPolicy:
    """One strict, attributable provider decision over observable scenarios only."""

    def __init__(self, runner: ExperimentRunner, provider: AgentProvider) -> None:
        self.runner = runner
        self.provider = provider

    def decide(
        self, scenarios: tuple[ExperimentScenario, ...], *, experiment_budget: int,
        batch_size: int | None = None,
    ) -> ExperimentDecision:
        if batch_size is not None and batch_size < 1:
            raise ValueError("batch_size must be positive")
        groups = (
            tuple(scenarios[index:index + batch_size]
                  for index in range(0, len(scenarios), batch_size))
            if batch_size else (scenarios,)
        )
        choices: list[ExperimentChoice] = []
        summaries: list[str] = []
        remaining_budget = experiment_budget
        for index, group in enumerate(groups, 1):
            decision = self._decide_batch(
                group, experiment_budget=remaining_budget,
                call_id=("active-experiment-selection" if len(groups) == 1
                         else f"active-experiment-selection-{index}"),
            )
            choices.extend(decision.choices)
            summaries.append(decision.policy_summary)
            remaining_budget -= sum(
                choice.action is ExperimentAction.EXPERIMENT_CANDIDATE
                for choice in decision.choices
            )
        combined = ExperimentDecision(
            choices=tuple(choices), policy_summary=" ".join(summaries),
        )
        validate_decision(combined, scenarios, experiment_budget=experiment_budget)
        return combined

    def _decide_batch(
        self, scenarios: tuple[ExperimentScenario, ...], *, experiment_budget: int,
        call_id: str,
    ) -> ExperimentDecision:
        observable_inputs = [{
            **scenario.model_dump(mode="json"),
            "derived_expected_experiment_net_value": expected_experiment_net_value(scenario),
        } for scenario in scenarios]
        response_shape = {
            "schema_version": 1,
            "choices": [{
                "scenario_id": "exact supplied ID",
                "action": "exploit_safe or experiment_candidate",
                "predicted_high_probability": 0.0,
                "expected_information_gain_bits": 0.0,
                "expected_net_value": 0.0,
                "reason": "short explanation",
            }],
            "policy_summary": "short summary",
        }
        prompt = (
            "Choose whether to exploit the known safe treatment or run one informative candidate "
            "experiment in each scenario. Test when derived_expected_experiment_net_value is "
            "positive and exploit safe when it is negative. The derived value uses only the "
            "visible prior, possible values, horizon, safe reward, and test overhead. Hidden "
            "realized values are unavailable. Return JSON only, cover every exact scenario ID "
            "once, use only the two listed action strings, and do not exceed experiment_budget. "
            "Required response shape:\n" + json.dumps(response_shape, sort_keys=True)
            + "\nInput:\n" + json.dumps({
                "experiment_budget": experiment_budget,
                "scenarios": observable_inputs,
            }, sort_keys=True)
        )
        decision = self._call(call_id, prompt, original_prompt=prompt, repair=False)
        validate_decision(decision, scenarios, experiment_budget=experiment_budget)
        return decision

    def _call(
        self, call_id: str, prompt: str, *, original_prompt: str, repair: bool,
    ) -> ExperimentDecision:
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
            return ExperimentDecision.model_validate_json(_normalize_json(response.text))
        except Exception as exc:
            if repair:
                raise ExperimentValidationError("Invalid experiment decision after repair") from exc
            repair_prompt = (
                "Repair the response into strict JSON satisfying the original request. Preserve "
                "every exact scenario ID and return JSON only.\nOriginal request:\n"
                + original_prompt + "\nInvalid response:\n" + response.text
            )
            return self._call(
                f"{call_id}-repair", repair_prompt,
                original_prompt=original_prompt, repair=True,
            )


def bernoulli_entropy(probability: float) -> float:
    return -sum(
        value * math.log2(value) for value in (probability, 1 - probability) if value > 0
    )


def _normalize_json(text: str) -> str:
    value = text.strip()
    if value.startswith("```") and value.endswith("```"):
        lines = value.splitlines()[1:-1]
        value = "\n".join(lines)
        if value.lstrip().lower().startswith("json"):
            value = value.lstrip()[4:].lstrip()
    return value
