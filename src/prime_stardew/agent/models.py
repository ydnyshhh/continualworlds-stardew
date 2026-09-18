"""Provider-neutral schemas for bounded agent decisions."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from prime_stardew.tasks import ActionCommand


class ContextItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    item_id: str
    text: str


class ContextInputs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    objective: str
    observation: str
    allowed_actions: tuple[str, ...] = ()
    goals: tuple[ContextItem, ...] = ()
    memories: tuple[ContextItem, ...] = ()
    skills: tuple[ContextItem, ...] = ()
    recent_events: tuple[ContextItem, ...] = ()


class WorkingContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    prompt: str
    estimated_tokens: int = Field(ge=0)
    included_ids: tuple[str, ...]
    omitted_ids: tuple[str, ...]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class AgentDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    actions: tuple[ActionCommand, ...]
    goal_updates: tuple[str, ...] = ()
    memory_notes: tuple[str, ...] = ()
    rationale: str = ""


class ProviderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    call_id: str
    prompt: str
    estimated_input_tokens: int = Field(ge=0)
    decoding: dict[str, Any]
    repair: bool = False


class ProviderUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    latency_ms: float = Field(ge=0)
    cost_usd: float = Field(ge=0)


class ProviderResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    request_id: str
    provider: str
    model: str
    route: str
    usage: ProviderUsage
