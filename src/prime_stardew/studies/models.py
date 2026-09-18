"""Immutable preregistration and result schemas for causal studies."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from prime_stardew.experiments.config import LearningConditionConfig


class StudyCondition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    learning: LearningConditionConfig


class StudyPreregistration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    study_id: str
    title: str
    hypothesis: str
    environment: str
    policy: str
    primary_metric: Literal["model_decisions"] = "model_decisions"
    favorable_direction: Literal["lower"] = "lower"
    paired: Literal[True] = True
    seeds: tuple[int, ...] = Field(min_length=8)
    max_days: Literal[28] = 28
    alpha: float = Field(default=0.05, gt=0, lt=1)
    confidence_level: float = Field(default=0.95, gt=0, lt=1)
    bootstrap_samples: int = Field(default=10_000, ge=1_000)
    outcome_noninferiority_margin: float = Field(default=0, ge=0)
    conditions: tuple[StudyCondition, StudyCondition]
    exclusion_rules: tuple[str, ...] = Field(min_length=1)
    analysis_plan: str

    @model_validator(mode="after")
    def validate_design(self) -> "StudyPreregistration":
        if len(set(self.seeds)) != len(self.seeds):
            raise ValueError("Study seeds must be distinct")
        if len({condition.name for condition in self.conditions}) != 2:
            raise ValueError("Study condition names must be distinct")
        skills = [condition.learning.skills for condition in self.conditions]
        if skills != [False, True]:
            raise ValueError("Conditions must be ordered control without skills, treatment with skills")
        control, treatment = self.conditions
        control_values = control.learning.model_dump()
        treatment_values = treatment.learning.model_dump()
        differing = {
            key for key in control_values if control_values[key] != treatment_values[key]
        }
        if differing != {"skills"}:
            raise ValueError("Paired conditions may differ only in procedural skills")
        return self


class StudyRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    seed: int
    condition: str
    run_id: str
    start_state_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_decisions: int = Field(ge=0)
    primitive_actions: int = Field(ge=0)
    invalid_actions: int = Field(ge=0)
    project_successes: int = Field(ge=0)
    mean_outcome_score: float = Field(ge=0, le=1)
    event_count: int = Field(ge=1)
    event_last_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    excluded_reason: str | None = None


class PairedResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    seed: int
    control_run_id: str
    treatment_run_id: str
    control_decisions: int
    treatment_decisions: int
    decision_reduction: int
    control_outcome: float
    treatment_outcome: float
    outcome_difference: float


class StudyReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    status: Literal["passed", "failed"]
    study_id: str
    scope: str
    preregistration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    primary_metric: str
    paired_runs: int = Field(ge=0)
    decision_reduction_mean: float
    decision_reduction_ci95: tuple[float, float]
    exact_two_sided_sign_test_p: float = Field(ge=0, le=1)
    outcome_difference_mean: float
    outcome_noninferiority_passed: bool
    all_start_states_matched: bool
    pairs: tuple[PairedResult, ...]
    runs: tuple[StudyRunResult, ...]
    failure_analysis: dict[str, object]
    causal_scope: str
