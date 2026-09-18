"""Strict schemas for hidden-mechanic experimentation and its observable decisions."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ExperimentAction(StrEnum):
    EXPLOIT_SAFE = "exploit_safe"
    EXPERIMENT_CANDIDATE = "experiment_candidate"


class ExperimentScenario(BaseModel):
    """Everything the learner may observe before choosing whether to experiment."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    scenario_id: str
    context: str
    safe_value: int = Field(ge=0)
    candidate_low_value: int = Field(ge=0)
    candidate_high_value: int = Field(ge=0)
    prior_high_probability: float = Field(gt=0, lt=1)
    production_cycles: int = Field(gt=0)
    experiment_overhead: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_values(self) -> "ExperimentScenario":
        if not self.candidate_low_value < self.safe_value < self.candidate_high_value:
            raise ValueError("Candidate values must straddle the known safe value")
        return self


class HiddenMechanicScenario(ExperimentScenario):
    """Evaluator-only scenario. `candidate_value` must never enter agent input."""

    candidate_value: int = Field(ge=0)
    expected_experiment_worthwhile: bool

    @model_validator(mode="after")
    def validate_hidden_value(self) -> "HiddenMechanicScenario":
        if self.candidate_value not in {self.candidate_low_value, self.candidate_high_value}:
            raise ValueError("Hidden candidate value must be one of the declared possibilities")
        if (expected_experiment_net_value(self.observable()) > 0) != self.expected_experiment_worthwhile:
            raise ValueError("Worthwhile label must match observable expected-value calculation")
        return self

    def observable(self) -> ExperimentScenario:
        return ExperimentScenario.model_validate(
            self.model_dump(exclude={"candidate_value", "expected_experiment_worthwhile"})
        )


class ExperimentChoice(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_id: str
    action: ExperimentAction
    predicted_high_probability: float = Field(ge=0, le=1)
    expected_information_gain_bits: float = Field(ge=0)
    expected_net_value: float
    reason: str = Field(min_length=1)


class ExperimentDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    choices: tuple[ExperimentChoice, ...]
    policy_summary: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_scenarios(self) -> "ExperimentDecision":
        ids = [choice.scenario_id for choice in self.choices]
        if len(ids) != len(set(ids)):
            raise ValueError("Experiment decision contains duplicate scenario IDs")
        return self


class ExperimentOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_id: str
    action: ExperimentAction
    total_reward: int
    safe_counterfactual_reward: int
    reward_difference_from_safe: int
    experiment_overhead: int = Field(ge=0)
    immediate_opportunity_cost: int
    information_gain_bits: float = Field(ge=0)
    candidate_identified: bool
    identification_cycle: int | None = Field(default=None, ge=1)
    learned_candidate_value: int | None = Field(default=None, ge=0)
    future_reward_attributable_to_information: int = Field(ge=0)
    prior_belief_id: str | None = None
    revised_belief_id: str | None = None


def expected_experiment_net_value(scenario: ExperimentScenario) -> float:
    high_total = (
        scenario.candidate_high_value - scenario.experiment_overhead
        + (scenario.production_cycles - 1)
        * max(scenario.safe_value, scenario.candidate_high_value)
    )
    low_total = (
        scenario.candidate_low_value - scenario.experiment_overhead
        + (scenario.production_cycles - 1)
        * max(scenario.safe_value, scenario.candidate_low_value)
    )
    expected_total = (
        scenario.prior_high_probability * high_total
        + (1 - scenario.prior_high_probability) * low_total
    )
    return expected_total - scenario.safe_value * scenario.production_cycles

