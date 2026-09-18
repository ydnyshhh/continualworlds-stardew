"""Immutable schemas for the E1 long-horizon continual-learning study."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from prime_stardew.experiments.config import RunConfig


PERSISTENT_OBJECTIVE = (
    "Manage and develop this farm as effectively as possible over the available lifetime. "
    "Increase its long-term economic value and productive capacity while improving useful "
    "tools, infrastructure, resource access, and capabilities. Balance immediate reward "
    "against future progress. Adapt your strategy based on what you learn from experience.\n\n"
    "You may choose your own priorities, intermediate goals, and daily strategy. Avoid "
    "unnecessary bankruptcy, wasted days, preventable failures, and investments that cannot "
    "reasonably pay off. Use only information and actions available through the permitted "
    "game interface."
)


class E1Phase(StrEnum):
    OFFLINE_VALIDATION = "offline_validation"
    SMOKE = "smoke"
    PILOT = "pilot"
    SEASON = "season"
    YEAR = "year"


class E1Condition(StrEnum):
    A_BASE = "A"
    B_MEMORY = "B"
    C_RETRIEVAL = "C"
    D_SKILLS = "D"
    E_REFINE = "E"
    F_FULL = "F"


class MemoryExposurePolicy(StrEnum):
    NONE = "none"
    CHRONOLOGICAL = "chronological"
    RELEVANCE = "relevance"


class HarnessCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    persistent_memory: bool = False
    relevance_retrieval: bool = False
    procedural_skills: bool = False
    reflection: bool = False
    persistent_goal_manager: bool = False
    adaptive_tool_selection: bool = False
    native_compaction: bool = False
    subagents: bool = False


class ConditionManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    condition: E1Condition
    memory_exposure: MemoryExposurePolicy
    capabilities: HarnessCapabilities
    cross_day_agent_state: bool
    full_harness: bool

    @model_validator(mode="after")
    def validate_contract(self) -> "ConditionManifest":
        cap = self.capabilities
        if self.memory_exposure is MemoryExposurePolicy.NONE and cap.persistent_memory:
            raise ValueError("Persistent memory requires an exposure policy")
        if self.memory_exposure is not MemoryExposurePolicy.NONE and not cap.persistent_memory:
            raise ValueError("Memory exposure requires persistent memory")
        if cap.relevance_retrieval != (self.memory_exposure is MemoryExposurePolicy.RELEVANCE):
            raise ValueError("Relevance retrieval must match the exposure policy")
        if self.full_harness != (self.condition is E1Condition.F_FULL):
            raise ValueError("Only Condition F may enable the full harness")
        return self


class ProbeKind(StrEnum):
    FAMILIAR = "familiar"
    TRANSFER = "transfer"
    CONFLICT = "conflict"
    NOVEL = "novel"


class ProbeDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    probe_id: str
    kind: ProbeKind
    capability: str
    description: str
    weight: float = Field(default=1, gt=0)
    maximum_score: float = Field(default=1, gt=0)
    hidden_answer: str = Field(min_length=1)
    contamination_terms: tuple[str, ...] = Field(min_length=1)

    def observable(self) -> dict[str, object]:
        return {
            "probe_id": self.probe_id,
            "kind": self.kind.value,
            "capability": self.capability,
            "description": self.description,
        }


class ProbeSchedule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    days: tuple[int, ...] = Field(min_length=1)
    definitions: tuple[ProbeDefinition, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_schedule(self) -> "ProbeSchedule":
        if tuple(sorted(set(self.days))) != self.days:
            raise ValueError("Probe days must be unique and increasing")
        ids = [probe.probe_id for probe in self.definitions]
        if len(ids) != len(set(ids)):
            raise ValueError("Probe IDs must be unique")
        required = {ProbeKind.FAMILIAR, ProbeKind.TRANSFER, ProbeKind.CONFLICT}
        if not required <= {probe.kind for probe in self.definitions}:
            raise ValueError("E1 requires familiar, transfer, and conflict probes")
        return self


class E1StudyConfig(BaseModel):
    """Study matrix layered over the existing immutable RunConfig."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    study_id: str
    phase: E1Phase
    base_run: RunConfig
    conditions: tuple[E1Condition, ...]
    seeds: tuple[int, ...] = Field(min_length=1)
    horizon_days: int = Field(gt=0, le=119)
    pause_during_inference: Literal[True] = True
    persistent_objective: str
    memory_token_budget: int = Field(gt=0)
    probe_schedule: ProbeSchedule
    refine_policy: Literal["nightly_bounded", "failure_triggered"]
    full_harness_features: HarnessCapabilities
    primary_metric: Literal["standardized_probe_aulc"]
    alpha: float = Field(default=.05, gt=0, lt=1)
    bootstrap_samples: int = Field(default=10_000, ge=1_000)
    exclusions: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_study(self) -> "E1StudyConfig":
        if len(set(self.conditions)) != len(self.conditions):
            raise ValueError("Conditions must be unique")
        if len(set(self.seeds)) != len(self.seeds):
            raise ValueError("Seeds must be unique")
        if any(day > self.horizon_days for day in self.probe_schedule.days):
            raise ValueError("Probe schedule exceeds the horizon")
        if self.phase is E1Phase.PILOT:
            if set(self.conditions) != set(E1Condition) or len(self.seeds) != 2 or self.horizon_days != 28:
                raise ValueError("E1-Pilot requires six conditions, two seeds, and 28 days")
        if self.phase is E1Phase.SMOKE:
            if set(self.conditions) != set(E1Condition) or len(self.seeds) != 1 or self.horizon_days != 7:
                raise ValueError("E1-Smoke requires six conditions, one seed, and seven days")
        if self.phase is E1Phase.SEASON and self.horizon_days != 28:
            raise ValueError("E1-Season requires a 28-day horizon")
        if self.phase is E1Phase.YEAR and self.horizon_days != 119:
            raise ValueError("E1-Year requires a 119-day horizon")
        if self.persistent_objective.strip() != PERSISTENT_OBJECTIVE:
            raise ValueError("The E1 persistent objective must remain unchanged")
        if not self.full_harness_features.persistent_goal_manager:
            raise ValueError("Condition F manifest must explicitly enable persistent goals")
        return self


class LearningState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    active_memory_ids: tuple[str, ...] = ()
    active_belief_ids: tuple[str, ...] = ()
    active_skill_refs: tuple[str, ...] = ()
    active_refinement_ids: tuple[str, ...] = ()
    active_goal_ids: tuple[str, ...] = ()
    historical_artifact_ids: tuple[str, ...] = ()


class LearningResetSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    memory: bool = False
    beliefs: bool = False
    skills: bool = False
    refine_products: bool = False
    goals: bool = False


class BranchType(StrEnum):
    PROBE = "probe"
    FULL_STATE = "full_state"
    MEMORY_RESET = "memory_reset"
    SKILLS_RESET = "skills_reset"
    LEARNING_RESET = "learning_reset"
    REFINEMENT_RESET = "refinement_reset"


class BranchIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    branch_id: str
    parent_run_id: str
    parent_checkpoint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parent_game_state_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parent_learning_state_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fork_reason: str
    fork_day: int = Field(ge=1, le=119)
    branch_type: BranchType
    reset: LearningResetSpec


class ProbeTaskScore(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    probe_id: str
    kind: ProbeKind
    raw_score: float = Field(ge=0)
    maximum_score: float = Field(gt=0)
    normalized_score: float = Field(ge=0, le=1)
    weight: float = Field(gt=0)


class ProbePoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    day: int = Field(ge=1, le=119)
    task_scores: tuple[ProbeTaskScore, ...] = Field(min_length=1)
    aggregate_score: float = Field(ge=0, le=1)


class CompetencyDomain(StrEnum):
    FARM_MAINTENANCE = "farm_maintenance"
    SHOPPING = "shopping"
    CROP_PLANNING = "crop_planning"
    MINING = "mining"
    INVESTMENT = "investment"


class CompetencyInstance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instance_id: str
    domain: CompetencyDomain
    game_day: int = Field(ge=1, le=119)
    work_units: float = Field(gt=0)
    model_decisions: int = Field(ge=0)
    primitive_actions: int = Field(ge=0)
    game_minutes: int = Field(ge=0)
    energy_used: float = Field(ge=0)
    success: bool
    failures: int = Field(default=0, ge=0)
    skill_ids: tuple[str, ...] = ()
    memory_ids: tuple[str, ...] = ()
    domain_metrics: dict[str, float | int | bool | str] = Field(default_factory=dict)


class InferenceCategory(StrEnum):
    ACTING = "acting"
    REFLECTION = "reflection"
    SKILL_PROPOSAL = "skill_proposal"
    MEMORY_MANAGEMENT = "memory_management"
    SUBAGENT = "subagent"
    REPAIR = "repair"
    REPLANNING = "replanning"


class InferenceAccounting(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    category: InferenceCategory
    calls: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost_usd: float = Field(ge=0)
    latency_ms: int = Field(ge=0)


class RefinementOutcome(StrEnum):
    NEVER_USED = "never_used"
    NO_BEHAVIORAL_EFFECT = "retrieved_no_behavioral_effect"
    HARMFUL = "behaviorally_harmful"
    NEUTRAL = "behaviorally_neutral"
    BENEFICIAL = "behaviorally_beneficial"
    UNKNOWN = "unknown"


class RefinementAttribution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    refinement_id: str
    source_experience_ids: tuple[str, ...] = Field(min_length=1)
    artifact_ids: tuple[str, ...] = Field(min_length=1)
    future_retrieval_event_ids: tuple[str, ...] = ()
    future_decision_event_ids: tuple[str, ...] = ()
    future_outcome_event_ids: tuple[str, ...] = ()
    outcome: RefinementOutcome
    reflection_cost_usd: float = Field(ge=0)
    attributable_utility: float | None = None

    @property
    def utilized(self) -> bool:
        return bool(self.future_retrieval_event_ids or self.future_decision_event_ids)

    @property
    def roi(self) -> float | None:
        if self.attributable_utility is None or self.reflection_cost_usd == 0:
            return None
        return self.attributable_utility / self.reflection_cost_usd
