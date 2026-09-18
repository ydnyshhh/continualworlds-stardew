"""Schemas for bounded reflection, belief revision, and refinement policies."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EvidenceKind(StrEnum):
    OBSERVATION = "observation"
    OUTCOME = "outcome"
    PREDICTION = "prediction"
    ERROR = "error"


class RefinementTrigger(StrEnum):
    NIGHTLY = "nightly"
    FAILURE = "failure"
    SURPRISE = "surprise"
    AGENT_SELECTED = "agent_selected"


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str
    kind: EvidenceKind
    text: str
    game_day: int = Field(ge=0)
    surprise: float = Field(default=0, ge=0, le=1)
    success: bool | None = None
    tags: tuple[str, ...] = ()


class RefinementPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    trigger: RefinementTrigger
    evidence_token_budget: int = Field(default=512, gt=0)
    max_evidence: int = Field(default=12, gt=0)
    surprise_threshold: float = Field(default=0.5, ge=0, le=1)

    def allows(self, trigger: RefinementTrigger, evidence: tuple[EvidenceItem, ...]) -> bool:
        if trigger is not self.trigger:
            return False
        if trigger is RefinementTrigger.FAILURE:
            return any(item.success is False for item in evidence)
        if trigger is RefinementTrigger.SURPRISE:
            return any(item.surprise >= self.surprise_threshold for item in evidence)
        return True


class BeliefProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    statement: str
    confidence: float = Field(ge=0, le=1)
    source_event_ids: tuple[str, ...]
    supporting_event_ids: tuple[str, ...] = ()
    contradicting_event_ids: tuple[str, ...] = ()
    supersedes_id: str | None = None
    predicted_event: str | None = None
    predicted_probability: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_prediction(self) -> "BeliefProposal":
        if (self.predicted_event is None) != (self.predicted_probability is None):
            raise ValueError("Belief prediction text and probability must be supplied together")
        if not self.source_event_ids:
            raise ValueError("Beliefs must cite at least one source event")
        return self


class ReflectionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    lessons: tuple[str, ...] = ()
    failed_assumptions: tuple[str, ...] = ()
    counterfactuals: tuple[str, ...] = ()
    goals: tuple[str, ...] = ()
    beliefs: tuple[BeliefProposal, ...] = ()


class ReflectionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reflection_id: str
    trigger: RefinementTrigger
    selected_event_ids: tuple[str, ...]
    omitted_event_ids: tuple[str, ...]
    selected_tokens: int = Field(ge=0)
    output: ReflectionOutput
    created_memory_ids: tuple[str, ...]
    superseded_memory_ids: tuple[str, ...]

