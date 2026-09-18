"""Typed contracts for bounded experience-replay selection."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ReplayCondition(StrEnum):
    NO_REPLAY = "no_replay"
    RECENCY = "recency"
    RANDOM_FIXED_SEED = "random_fixed_seed"
    ERROR_PRIORITY = "error_priority"
    AGENT_PRIORITY = "agent_priority"


class ReplayExperience(BaseModel):
    """An agent-visible training experience; held-out answers never appear here."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    experience_id: str
    capability: str
    observation: str
    attempted_action: str
    outcome: str
    lesson: str
    prediction_error: float = Field(ge=0, le=1)
    surprise: float = Field(ge=0, le=1)
    stakes: float = Field(ge=0, le=1)
    age_steps: int = Field(ge=0)
    source_event_ids: tuple[str, ...] = Field(min_length=1)


class ReplayAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    experience_id: str
    replay: bool
    predicted_transfer_value: float = Field(ge=0, le=1)


class ReplayDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    actions: tuple[ReplayAction, ...]
    reasoning_summary: str = Field(min_length=1)

    @model_validator(mode="after")
    def unique_actions(self) -> "ReplayDecision":
        ids = [action.experience_id for action in self.actions]
        if len(ids) != len(set(ids)):
            raise ValueError("Replay decision contains duplicate experience IDs")
        return self


class EvaluationTask(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str
    capability: str
    prompt: str
    correct_action: str

    def observable(self) -> dict[str, str]:
        return {"task_id": self.task_id, "capability": self.capability, "prompt": self.prompt}


class ReplayRunScore(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    seed: int
    condition: ReplayCondition
    candidate_state_sha256: str
    observable_evaluation_sha256: str
    hidden_evaluation_sha256: str
    replayed_ids: tuple[str, ...]
    replayed_source_event_ids: tuple[str, ...]
    replay_count: int = Field(ge=0)
    replay_budget: int = Field(ge=0)
    held_out_correct: int = Field(ge=0)
    held_out_total: int = Field(gt=0)
    held_out_accuracy: float = Field(ge=0, le=1)
    useful_replay_precision: float = Field(ge=0, le=1)
    wasted_replays: int = Field(ge=0)
    hidden_fields_exposed: bool = False
    budget_enforced: bool = True

