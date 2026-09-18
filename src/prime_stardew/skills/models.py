"""Immutable schemas for proposed, validated, and executed procedural skills."""

from __future__ import annotations

import hashlib
import json
import re
from enum import StrEnum
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


Scalar = str | int | float | bool


class ParameterType(StrEnum):
    INTEGER = "integer"
    NUMBER = "number"
    STRING = "string"
    BOOLEAN = "boolean"


class SkillParameter(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    type: ParameterType
    minimum: float | None = None
    maximum: float | None = None

    @model_validator(mode="after")
    def validate_bounds(self) -> "SkillParameter":
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("Parameter minimum cannot exceed maximum")
        if self.type not in {ParameterType.INTEGER, ParameterType.NUMBER} and (
            self.minimum is not None or self.maximum is not None
        ):
            raise ValueError("Only numeric parameters may define bounds")
        return self


class SkillArgument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    literal: Scalar | None = None
    parameter: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]*$")

    @model_validator(mode="after")
    def exactly_one_source(self) -> "SkillArgument":
        if (self.literal is None) == (self.parameter is None):
            raise ValueError("Skill argument requires exactly one of literal or parameter")
        return self


class SkillStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action: str = Field(min_length=1)
    arguments: tuple[SkillArgument, ...] = ()


class SkillDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    skill_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    version: int = Field(ge=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    task_kind: str = Field(min_length=1)
    parameters: tuple[SkillParameter, ...] = ()
    preconditions: tuple[str, ...] = Field(min_length=1)
    postconditions: tuple[str, ...] = Field(min_length=1)
    steps: tuple[SkillStep, ...] = Field(min_length=1)
    source_trajectory_ids: tuple[str, ...] = Field(min_length=2)

    @model_validator(mode="after")
    def validate_definition(self) -> "SkillDefinition":
        parameter_names = [parameter.name for parameter in self.parameters]
        if len(set(parameter_names)) != len(parameter_names):
            raise ValueError("Skill parameter names must be unique")
        referenced = {
            argument.parameter
            for step in self.steps
            for argument in step.arguments
            if argument.parameter is not None
        }
        unknown = referenced - set(parameter_names)
        if unknown:
            raise ValueError(f"Skill steps reference unknown parameters: {sorted(unknown)}")
        if len(set(self.source_trajectory_ids)) != len(self.source_trajectory_ids):
            raise ValueError("Source trajectory IDs must be unique")
        return self

    def content_sha256(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ValidationStage(StrEnum):
    REPLAY = "replay"
    DISPOSABLE = "disposable"


class SkillValidation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    validation_id: str
    skill_id: str
    version: int = Field(ge=1)
    stage: ValidationStage
    fixture_id: str
    success: bool
    score: float = Field(ge=0, le=1)
    trajectory_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    failure_reasons: tuple[str, ...] = ()


class SkillUseMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    use_id: str
    skill_id: str
    version: int = Field(ge=1)
    success: bool
    score: float = Field(ge=0, le=1)
    baseline_model_decisions: int = Field(ge=0)
    actual_model_decisions: int = Field(ge=0)
    baseline_primitive_actions: int = Field(ge=0)
    actual_primitive_actions: int = Field(ge=0)

    @property
    def model_decisions_saved(self) -> int:
        return self.baseline_model_decisions - self.actual_model_decisions

    @property
    def primitive_actions_saved(self) -> int:
        return self.baseline_primitive_actions - self.actual_primitive_actions


class SkillProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    skill: SkillDefinition
    rationale: str = Field(min_length=1)


class SkillExport(BaseModel):
    """Hash-authenticated, model-independent procedural-state transfer bundle."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    export_id: str
    created_at: datetime
    source_store_id: str
    provenance: dict[str, str] = Field(default_factory=dict)
    skills: tuple[SkillDefinition, ...]
    validations: tuple[SkillValidation, ...]
    active_versions: dict[str, int]
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def validate_parameter_value(parameter: SkillParameter, value: Scalar) -> None:
    valid = {
        ParameterType.INTEGER: type(value) is int,
        ParameterType.NUMBER: type(value) in {int, float},
        ParameterType.STRING: type(value) is str,
        ParameterType.BOOLEAN: type(value) is bool,
    }[parameter.type]
    if not valid:
        raise ValueError(f"Parameter {parameter.name!r} requires {parameter.type.value}")
    if type(value) in {int, float}:
        numeric = float(value)
        if parameter.minimum is not None and numeric < parameter.minimum:
            raise ValueError(f"Parameter {parameter.name!r} is below its minimum")
        if parameter.maximum is not None and numeric > parameter.maximum:
            raise ValueError(f"Parameter {parameter.name!r} exceeds its maximum")


def normalize_skill_json(text: str) -> str:
    value = text.strip()
    if value.startswith("```") and value.endswith("```"):
        lines = value.splitlines()[1:-1]
        value = "\n".join(lines)
    return re.sub(r"^json\s*", "", value, count=1, flags=re.IGNORECASE).strip()
