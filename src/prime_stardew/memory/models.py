"""Typed records for persistent memory, retrieval, and transfer."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MemoryKind(StrEnum):
    EPISODE = "episode"
    SEMANTIC = "semantic"
    BELIEF = "belief"


class MemoryStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    DELETED = "deleted"
    DEACTIVATED = "deactivated"


class MemoryStatusEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    memory_id: str
    sequence: int = Field(ge=1)
    status: MemoryStatus
    reason: str
    created_at: datetime


class MemoryAccessStats(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    memory_id: str
    retrieval_count: int = Field(ge=0)
    last_retrieved_at: datetime | None = None


class MemoryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    memory_id: str
    kind: MemoryKind
    text: str
    payload: dict[str, Any] = Field(default_factory=dict)
    source_event_ids: tuple[str, ...] = ()
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    season: str | None = None
    map_name: str | None = None
    task_domain: str | None = None
    valid_from_day: int | None = Field(default=None, ge=0)
    valid_to_day: int | None = Field(default=None, ge=0)
    confidence: float = Field(default=1.0, ge=0, le=1)
    tags: tuple[str, ...] = ()
    supersedes_id: str | None = None
    version: int = Field(default=1, gt=0)

    @model_validator(mode="after")
    def validate_record(self) -> "MemoryRecord":
        if not self.memory_id.strip() or not self.text.strip():
            raise ValueError("Memory ID and text are required")
        if self.valid_from_day is not None and self.valid_to_day is not None:
            if self.valid_to_day < self.valid_from_day:
                raise ValueError("Memory validity end cannot precede its start")
        return self


class MemoryQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    season: str | None = None
    map_name: str | None = None
    task_domain: str | None = None
    game_day: int | None = Field(default=None, ge=0)
    min_confidence: float = Field(default=0, ge=0, le=1)
    limit: int = Field(default=5, gt=0)
    token_budget: int = Field(default=512, ge=0)


class RankFeatures(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    keyword_overlap: int = Field(ge=0)
    keyword_score: float = Field(ge=0)
    metadata_score: float = Field(ge=0)
    confidence: float = Field(ge=0, le=1)
    total_score: float = Field(ge=0)


class RetrievalCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    memory_id: str
    features: RankFeatures
    estimated_tokens: int = Field(ge=0)
    selected: bool = False


class RetrievalResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query: MemoryQuery
    candidates: tuple[RetrievalCandidate, ...]
    selected: tuple[MemoryRecord, ...]
    selected_tokens: int = Field(ge=0)


class MemoryExport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1, 2] = 2
    export_id: str
    created_at: datetime
    source_store_id: str
    provenance: dict[str, str] = Field(default_factory=dict)
    records: tuple[MemoryRecord, ...]
    statuses: dict[str, MemoryStatus]
    status_events: tuple[MemoryStatusEvent, ...] = ()
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class BeliefPrediction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    prediction_id: str
    belief_id: str
    predicted_event: str
    probability: float = Field(ge=0, le=1)
    source_event_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PredictionOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    prediction_id: str
    occurred: bool
    source_event_id: str
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CalibrationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scored_predictions: int = Field(ge=0)
    brier_score: float | None = Field(default=None, ge=0, le=1)
    mean_confidence: float | None = Field(default=None, ge=0, le=1)
    observed_rate: float | None = Field(default=None, ge=0, le=1)
