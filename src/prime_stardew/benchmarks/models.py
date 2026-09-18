"""Immutable schemas for multi-day project and season benchmarks."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProjectKind(StrEnum):
    CAULIFLOWER_RESERVE = "cauliflower_reserve"
    MINE_PICKAXE = "mine_pickaxe"
    COOP_CHICKEN = "coop_chicken"


class ProjectFixture(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    fixture_id: str
    kind: ProjectKind
    description: str
    horizon_days: int = Field(default=28, ge=1)
    target_quantity: int = Field(default=0, ge=0)
    minimum_gold: int = Field(default=0, ge=0)
    target_mine_level: int = Field(default=0, ge=0, le=120)
    target_pickaxe_tier: int = Field(default=0, ge=0)
    required_building: str | None = None
    required_animal: str | None = None


class ProjectActionKind(StrEnum):
    EARN_GOLD = "earn_gold"
    PLANT_CAULIFLOWER = "plant_cauliflower"
    TEND_CAULIFLOWER = "tend_cauliflower"
    HARVEST_CAULIFLOWER = "harvest_cauliflower"
    MINE = "mine"
    UPGRADE_PICKAXE = "upgrade_pickaxe"
    BUILD_COOP = "build_coop"
    BUY_CHICKEN = "buy_chicken"
    IDLE = "idle"


class ProjectAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: ProjectActionKind
    amount: int = Field(default=0, ge=0)


class SeasonState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    completed_days: int = Field(default=0, ge=0, le=28)
    gold: int = Field(default=500, ge=0)
    cauliflower_planted: int = Field(default=0, ge=0)
    cauliflower_tended_days: int = Field(default=0, ge=0)
    cauliflower_harvested: int = Field(default=0, ge=0)
    mine_level: int = Field(default=0, ge=0, le=120)
    pickaxe_tier: int = Field(default=0, ge=0)
    buildings: tuple[str, ...] = ()
    chickens: int = Field(default=0, ge=0)
    decisions: int = Field(default=0, ge=0)
    primitive_actions: int = Field(default=0, ge=0)
    retries: int = Field(default=0, ge=0)
    invalid_actions: int = Field(default=0, ge=0)
    energy_spent: int = Field(default=0, ge=0)
    game_minutes_spent: int = Field(default=0, ge=0)
    memory_reads: int = Field(default=0, ge=0)
    skill_uses: int = Field(default=0, ge=0)


class ProjectScore(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fixture_id: str
    success: bool
    progress: float = Field(ge=0, le=1)
    criterion_day: int | None = Field(default=None, ge=1, le=28)
    components: dict[str, bool]


class SeasonDayResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    day: int = Field(ge=1, le=28)
    before: SeasonState
    actions: tuple[ProjectAction, ...]
    after: SeasonState
    project_scores: tuple[ProjectScore, ...]

    @model_validator(mode="after")
    def validate_day_boundary(self) -> "SeasonDayResult":
        if self.before.completed_days != self.day - 1 or self.after.completed_days != self.day:
            raise ValueError("Day result state boundary is not contiguous")
        return self


class SeasonMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    environment: dict[str, object]
    efficiency: dict[str, float | int]
    learning: dict[str, float | int | None]
    memory: dict[str, float | int]
    skills: dict[str, float | int]
    systems: dict[str, float | int]


class SeasonReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    run_id: str
    seed: int
    condition: str
    completed_days: int
    project_scores: tuple[ProjectScore, ...]
    progress_curves: dict[str, tuple[float, ...]]
    metrics: SeasonMetrics
    source_event_count: int = Field(ge=1)
    source_event_last_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

