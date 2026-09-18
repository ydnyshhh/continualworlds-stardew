"""Schemas for lifetime, transfer, retention, and routing experiments."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TransferKind(StrEnum):
    RAW_MODEL = "raw_model"
    FRESH_RECIPIENT = "fresh_recipient"
    MEMORIES_ONLY = "memories_only"
    SKILLS_ONLY = "skills_only"
    FULL_INHERITANCE = "full_inheritance"


class LifetimeStudyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    study_id: str
    donor_model: str
    recipient_model: str
    lifetime_days: Literal[112] = 112
    season_length_days: Literal[28] = 28
    seasons: tuple[str, str, str, str]
    dormant_skill_min_gap_days: int = Field(default=84, ge=1)
    routing_budget_usd: float = Field(default=0.012, gt=0)
    transfer_conditions: tuple[TransferKind, ...]

    @model_validator(mode="after")
    def validate_design(self) -> "LifetimeStudyConfig":
        if self.seasons != ("spring", "summer", "fall", "winter"):
            raise ValueError("M11 requires the ordered four-season year")
        if self.donor_model == self.recipient_model:
            raise ValueError("Donor and recipient model identities must differ")
        if self.transfer_conditions != tuple(TransferKind):
            raise ValueError("M11 requires all raw, fresh, memory, skill, and full conditions")
        return self
class TransferTaskKind(StrEnum):
    KNOWLEDGE = "knowledge"
    PROCEDURE = "procedure"


class TransferTask(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    task_id: str
    kind: TransferTaskKind
    description: str
    query: str
    required_artifact_id: str
    contamination_terms: tuple[str, ...] = Field(min_length=1)


class LifetimeState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    completed_days: int = Field(default=0, ge=0, le=112)
    completed_seasons: int = Field(default=0, ge=0, le=4)
    model_decisions: int = Field(default=0, ge=0)
    memory_retrievals: int = Field(default=0, ge=0)
    skill_uses: int = Field(default=0, ge=0)
    last_skill_use_day: int | None = Field(default=None, ge=1, le=112)


class TransferTaskResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str
    success: bool
    model_decisions: int = Field(ge=0)
    evidence_ids: tuple[str, ...] = ()


class TransferConditionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    condition: TransferKind
    imported_memory_ids: tuple[str, ...] = ()
    imported_skill_versions: tuple[tuple[str, int], ...] = ()
    task_results: tuple[TransferTaskResult, ...]
    success_rate: float = Field(ge=0, le=1)
    model_decisions: int = Field(ge=0)


class RoutingPolicyResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy: str
    attempted: int = Field(ge=0)
    successes: int = Field(ge=0)
    cost_usd: float = Field(ge=0)
    utility_per_dollar: float = Field(ge=0)
    within_budget: bool


class LifetimeReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    status: Literal["passed", "failed"]
    scope: str
    configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    donor_model: str
    recipient_model: str
    lifetime_days: int
    season_checkpoints: int
    checkpoint_days: tuple[int, ...]
    dormant_skill_gap_days: int
    dormant_skill_retained: bool
    seasonal_memory_retrievals: int
    memory_bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    skill_bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    contamination_detected: bool
    transfer_results: tuple[TransferConditionResult, ...]
    transfer_gain: float
    routing_results: tuple[RoutingPolicyResult, ...]
    learned_routing_gain_per_dollar: float
    source_event_count: int = Field(ge=1)
    source_event_last_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    failure_analysis: dict[str, object]
    causal_scope: str
