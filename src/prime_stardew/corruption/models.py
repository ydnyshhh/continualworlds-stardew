"""Immutable public and evaluator-only schemas for M16 corruption trials."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from prime_stardew.experiments.provenance import artifact_sha256
from prime_stardew.memory import MemoryKind


class CorruptionType(StrEnum):
    FALSE_SEMANTIC_MEMORY = "false_semantic_memory"
    FALSE_BELIEF = "false_belief"
    STALE_FORMERLY_CORRECT_BELIEF = "stale_formerly_correct_belief"
    OVERCONFIDENT_WEAK_BELIEF = "overconfident_weak_belief"
    INCORRECT_PROCEDURAL_RECOMMENDATION = "incorrect_procedural_recommendation"


class RepairResponse(StrEnum):
    BLINDLY_TRUST = "blindly_trust"
    IGNORE = "ignore"
    CREATE_CONTRADICTION = "create_contradiction"
    REDUCE_CONFIDENCE = "reduce_confidence"
    TEST = "test"
    SUPERSEDE = "supersede"
    CONTEXTUALIZE = "contextualize"
    DELETE_FROM_ACTIVE_RETRIEVAL = "delete_from_active_retrieval"


class RepairScenario(BaseModel):
    """A trial definition. Evaluator-only fields are never placed in decision input."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_id: str
    episode: Literal[1, 2]
    memory_kind: MemoryKind
    memory_text: str
    memory_action: Literal["alpha", "beta"]
    alternatives: tuple[Literal["alpha", "beta"], Literal["alpha", "beta"]] = (
        "alpha", "beta",
    )
    claimed_confidence: float = Field(ge=0, le=1)
    claimed_support_count: int = Field(ge=1)
    source_description: str
    correct_action: Literal["alpha", "beta"]
    corruption_type: CorruptionType | None
    high_utility: int = Field(gt=0)
    low_utility: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_scenario(self) -> "RepairScenario":
        if len(set(self.alternatives)) != 2:
            raise ValueError("M16 alternatives must be distinct")
        if self.memory_action not in self.alternatives or self.correct_action not in self.alternatives:
            raise ValueError("M16 actions must be declared alternatives")
        corrupt = self.corruption_type is not None
        if corrupt != (self.memory_action != self.correct_action):
            raise ValueError("Corruption label must agree with the hidden correct action")
        if self.high_utility <= self.low_utility:
            raise ValueError("High utility must exceed low utility")
        return self

    def observable_record(self) -> dict[str, object]:
        return {
            "scenario_id": self.scenario_id,
            "episode": self.episode,
            "memory_kind": self.memory_kind.value,
            "memory_text": self.memory_text,
            "recommended_action": self.memory_action,
            "alternatives": list(self.alternatives),
            "claimed_confidence": self.claimed_confidence,
            "claimed_support_count": self.claimed_support_count,
            "source_description": self.source_description,
        }


class CorruptionFamily(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    family_id: str = "stardew-memory-corruption-v1"
    seed: int
    scenarios: tuple[RepairScenario, ...]

    @model_validator(mode="after")
    def validate_family(self) -> "CorruptionFamily":
        if len({item.scenario_id for item in self.scenarios}) != len(self.scenarios):
            raise ValueError("M16 scenario IDs must be distinct")
        for episode in (1, 2):
            group = [item for item in self.scenarios if item.episode == episode]
            if {item.corruption_type for item in group if item.corruption_type is not None} != set(CorruptionType):
                raise ValueError("Each M16 episode requires every corruption type")
            if sum(item.corruption_type is None for item in group) < 2:
                raise ValueError("Each M16 episode requires at least two clean decoys")
        return self

    @property
    def world_state_sha256(self) -> str:
        return artifact_sha256(self.model_dump(mode="json"))

    @property
    def observable_state_sha256(self) -> str:
        return artifact_sha256({
            "schema_version": self.schema_version,
            "family_id": self.family_id,
            "seed": self.seed,
            "records": [item.observable_record() for item in self.scenarios],
        })

    @property
    def start_state_sha256(self) -> str:
        return artifact_sha256({
            "world_state_sha256": self.world_state_sha256,
            "learner_state": "empty",
            "policy_revision": 1,
        })
