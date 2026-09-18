"""Immutable, canonical experiment configuration and stable run identity."""

from __future__ import annotations

import hashlib
import json
import re
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from prime_stardew.env.models import GameDate, ObservationMode


class DecodingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    temperature: float = Field(default=0, ge=0, le=2)
    top_p: float = Field(default=1, gt=0, le=1)
    max_output_tokens: int = Field(default=1024, gt=0)


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = "scripted"
    model: str = "scripted-v1"
    route: str = "local"
    decoding: DecodingConfig = Field(default_factory=DecodingConfig)


class ContextBudgetConfig(BaseModel):
    """Deterministic budgets for the prompt assembled for one decision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    total_tokens: int = Field(default=4096, ge=128)
    objective_tokens: int = Field(default=512, gt=0)
    observation_tokens: int = Field(default=1536, gt=0)
    goals_tokens: int = Field(default=512, ge=0)
    memories_tokens: int = Field(default=512, ge=0)
    skills_tokens: int = Field(default=512, ge=0)
    recent_events_tokens: int = Field(default=512, ge=0)


class LearningConditionConfig(BaseModel):
    """Independent continual-learning capabilities used by ablation runs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    recent_context: bool = True
    persistent_memory: bool = False
    retrieval: bool = False
    skills: bool = True
    refinement: bool = False


class MemoryManagementPolicy(StrEnum):
    NONE = "none"
    FIFO = "fifo"
    LEAST_RECENTLY_USED = "least_recently_used"
    LEAST_RETRIEVED = "least_retrieved"
    RANDOM_FIXED_SEED = "random_fixed_seed"
    AGENT_SELECTED = "agent_selected"


class MemoryManagementConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    budget_type: Literal["token_estimate"] = "token_estimate"
    max_active_tokens: int = Field(default=0, ge=0)
    policy: MemoryManagementPolicy = MemoryManagementPolicy.NONE
    random_seed: int = 0
    overflow_fallback: MemoryManagementPolicy = MemoryManagementPolicy.FIFO


class ExperimentBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_actions: int = Field(default=100, gt=0)
    max_game_days: int = Field(default=1, gt=0)
    max_model_calls: int = Field(default=0, ge=0)
    max_input_tokens: int = Field(default=0, ge=0)
    max_output_tokens: int = Field(default=0, ge=0)
    max_cost_usd: float = Field(default=0, ge=0)
    max_wall_seconds: int = Field(default=3600, gt=0)


class FixtureConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    save_id: str
    player: str
    starting_date: GameDate
    checkpoint: str | None = None


class MetadataEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    value: str


class RunConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    suite: str
    condition: str
    seed: int
    repetition: int = Field(default=0, ge=0)
    observation_mode: ObservationMode = ObservationMode.STRUCTURED_LOCAL
    fixture: FixtureConfig
    tasks: tuple[str, ...]
    provider: ProviderConfig = Field(default_factory=ProviderConfig)
    context: ContextBudgetConfig = Field(default_factory=ContextBudgetConfig)
    learning: LearningConditionConfig = Field(default_factory=LearningConditionConfig)
    memory_management: MemoryManagementConfig = Field(default_factory=MemoryManagementConfig)
    budget: ExperimentBudget = Field(default_factory=ExperimentBudget)
    tags: tuple[str, ...] = ()
    metadata: tuple[MetadataEntry, ...] = ()

    def canonical_json(self) -> str:
        return _canonical_json(self.model_dump(mode="json"))

    def config_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    def run_id(self) -> str:
        parts = (
            _slug(self.suite),
            _slug(self.provider.model),
            _slug(self.condition),
            f"s{self.seed}",
            self.config_hash()[:12],
        )
        return "-".join(parts)


def load_run_config(path: Path) -> RunConfig:
    """Safely load a YAML or JSON configuration into the frozen schema."""

    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Run configuration must be a mapping: {path}")
    return RunConfig.model_validate(value)


def _slug(value: str) -> str:
    result = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    if not result:
        raise ValueError(f"Cannot create run ID component from {value!r}")
    return result[:48]


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
