"""Schemas separating M17 training information from hidden evaluation tasks."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from prime_stardew.experiments.provenance import artifact_sha256


class EvidenceRegime(StrEnum):
    INDEPENDENT_NOISE = "independent_noise"
    BURST_NOISE = "burst_noise"
    SOURCE_DUPLICATION = "source_duplication"
    SOURCE_SPOOFING = "source_spoofing"


class EvidenceObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    observation_id: str
    source_id: str
    claim: Literal["alpha", "beta"]
    authenticated: bool


class EvidenceTask(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str
    regime: EvidenceRegime
    alternatives: tuple[Literal["alpha", "beta"], Literal["alpha", "beta"]] = (
        "alpha", "beta",
    )
    observations: tuple[EvidenceObservation, ...] = Field(min_length=2)
    correct_action: Literal["alpha", "beta"]
    high_utility: int = Field(gt=0)
    low_utility: int = Field(ge=0)

    @model_validator(mode="after")
    def valid_task(self) -> "EvidenceTask":
        if self.correct_action not in self.alternatives:
            raise ValueError("Correct action must be declared")
        if self.high_utility <= self.low_utility:
            raise ValueError("High utility must exceed low utility")
        return self

    def observable(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "alternatives": list(self.alternatives),
            "observations": [item.model_dump(mode="json") for item in self.observations],
        }


class PracticeObjective(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    objective_id: str
    capability: EvidenceRegime
    description: str
    estimated_cost: int = Field(gt=0)


class M17Family(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    family_id: str = "m17-noisy-evidence-curriculum-v1"
    seed: int
    initial_weaknesses: tuple[EvidenceRegime, EvidenceRegime]
    diagnostic_scores: dict[EvidenceRegime, int]
    practice_catalog: tuple[PracticeObjective, ...]
    heldout_tasks: tuple[EvidenceTask, ...]

    @model_validator(mode="after")
    def valid_family(self) -> "M17Family":
        if len(set(self.initial_weaknesses)) != 2:
            raise ValueError("M17 requires two distinct initial weaknesses")
        if set(self.diagnostic_scores) != set(EvidenceRegime):
            raise ValueError("M17 requires a diagnostic score for every regime")
        if {item.capability for item in self.practice_catalog} != set(EvidenceRegime):
            raise ValueError("Practice catalog must cover every regime")
        if {item.regime for item in self.heldout_tasks} != set(EvidenceRegime):
            raise ValueError("Held-out tasks must cover every regime")
        return self

    @property
    def world_state_sha256(self) -> str:
        return artifact_sha256(self.model_dump(mode="json"))

    @property
    def training_view_sha256(self) -> str:
        return artifact_sha256({
            "family_id": self.family_id, "seed": self.seed,
            "diagnostic_scores": self.diagnostic_scores,
            "practice_catalog": [item.model_dump(mode="json") for item in self.practice_catalog],
        })

    @property
    def heldout_manifest_sha256(self) -> str:
        return artifact_sha256([
            {"task_id": item.task_id, "regime": item.regime.value}
            for item in self.heldout_tasks
        ])

    @property
    def start_state_sha256(self) -> str:
        return artifact_sha256({
            "world_state_sha256": self.world_state_sha256,
            "learner_state": "base-evidence-policy-v1",
            "practice_count": 0,
        })
