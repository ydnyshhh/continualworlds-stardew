"""Immutable schemas for seeded Stardew-Shift counterfactual worlds."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from prime_stardew.experiments.provenance import artifact_sha256


class MechanicDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mechanic_id: str
    choices: tuple[str, str] = ("alpha", "beta")
    optimal_in_a: str
    optimal_in_b: str
    high_reward: int = Field(gt=0)
    low_reward: int = Field(ge=0)
    shifted: bool

    @model_validator(mode="after")
    def validate_mechanic(self) -> "MechanicDefinition":
        if len(set(self.choices)) != 2:
            raise ValueError("Mechanic choices must be distinct")
        if self.optimal_in_a not in self.choices or self.optimal_in_b not in self.choices:
            raise ValueError("Optimal actions must be declared choices")
        if self.high_reward <= self.low_reward:
            raise ValueError("High reward must exceed low reward")
        if self.shifted != (self.optimal_in_a != self.optimal_in_b):
            raise ValueError("Shifted flag must match the A/B transformation")
        return self

    def optimal_action(self, world: Literal["A", "B"]) -> str:
        return self.optimal_in_a if world == "A" else self.optimal_in_b

    def reward(self, world: Literal["A", "B"], action: str) -> int:
        if action not in self.choices:
            raise ValueError(f"Unknown action for {self.mechanic_id}: {action}")
        return self.high_reward if action == self.optimal_action(world) else self.low_reward


class ShiftWorldFamily(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    family_id: str = "stardew-shift-v1"
    seed: int
    visible_context_cues: dict[str, str]
    sequence: tuple[Literal["A", "B", "A"], ...] = ("A", "B", "A")
    mechanics: tuple[MechanicDefinition, ...]

    @model_validator(mode="after")
    def validate_family(self) -> "ShiftWorldFamily":
        if self.sequence != ("A", "B", "A"):
            raise ValueError("M15 requires the A to B to A sequence")
        if set(self.visible_context_cues) != {"A", "B"}:
            raise ValueError("Both visible world cues are required")
        if len({item.mechanic_id for item in self.mechanics}) != len(self.mechanics):
            raise ValueError("Mechanic IDs must be distinct")
        shifted = sum(item.shifted for item in self.mechanics)
        if shifted == 0 or shifted == len(self.mechanics):
            raise ValueError("A strict subset of mechanics must shift")
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
            "visible_context_cues": self.visible_context_cues,
            "sequence": self.sequence,
            "mechanics": [
                {"mechanic_id": item.mechanic_id, "choices": item.choices}
                for item in self.mechanics
            ],
        })

    @property
    def start_state_sha256(self) -> str:
        return artifact_sha256({
            "world_state_sha256": self.world_state_sha256,
            "initial_phase": "A1",
            "learner_state": "empty",
            "interaction_index": 0,
        })
