"""Immutable contracts for deterministic atomic-task evaluation."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from prime_stardew.env.models import GameDate


class TaskKind(StrEnum):
    TURN_MOVE = "turn_move"
    WATER_CROP = "water_crop"
    CLEAR_DEBRIS = "clear_debris"
    HARVEST_CROP = "harvest_crop"
    CHEST_TRANSFER = "chest_transfer"


class TransferDirection(StrEnum):
    PLAYER_TO_CHEST = "player_to_chest"
    CHEST_TO_PLAYER = "chest_to_player"


class ActionBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_actions: int = Field(gt=0)
    max_game_minutes: int = Field(default=20, ge=0)


class TaskFixture(BaseModel):
    """A versioned fixture definition. Setup occurs before the scored trajectory."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=1, frozen=True)
    fixture_id: str
    kind: TaskKind
    description: str
    expected_player: str
    expected_date: GameDate
    location: str
    origin: tuple[int, int]
    target: tuple[int, int]
    expected_facing: int | None = Field(default=None, ge=0, le=3)
    item_name: str | None = None
    quantity: int = Field(default=1, gt=0)
    transfer_direction: TransferDirection | None = None
    allowed_actions: tuple[str, ...]
    budget: ActionBudget

    @model_validator(mode="after")
    def validate_task_fields(self) -> "TaskFixture":
        if not self.fixture_id or not self.expected_player or not self.allowed_actions:
            raise ValueError("Fixture ID, expected player, and allowed actions are required")
        if self.kind in {TaskKind.CLEAR_DEBRIS, TaskKind.HARVEST_CROP, TaskKind.CHEST_TRANSFER}:
            if not self.item_name:
                raise ValueError(f"{self.kind} requires item_name")
        if self.kind is TaskKind.CHEST_TRANSFER and self.transfer_direction is None:
            raise ValueError("chest_transfer requires transfer_direction")
        return self


class ItemCount(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    quantity: int = Field(ge=0)


class NormalizedCrop(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    seed_id: str | None = None
    harvest_id: str | None = None
    watered: bool | None = None
    dead: bool | None = None
    phase: int | None = None


class NormalizedTile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    position: tuple[int, int]
    object_name: str | None = None
    terrain: str | None = None
    debris_name: str | None = None
    crop: NormalizedCrop | None = None


class NormalizedState(BaseModel):
    """Small, stable state used by scorers and stored task traces."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    player: str
    date: GameDate
    time: int
    location: str
    position: tuple[int, int]
    facing: int
    stamina: float
    money: int
    inventory: tuple[ItemCount, ...] = ()
    target: NormalizedTile | None = None
    menu_type: str = "No Menu"
    chest: tuple[ItemCount, ...] = ()

    def inventory_quantity(self, name: str) -> int:
        return next((item.quantity for item in self.inventory if item.name == name), 0)

    def chest_quantity(self, name: str) -> int:
        return next((item.quantity for item in self.chest if item.name == name), 0)


class PrimitiveAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sequence: int = Field(ge=1)
    name: str
    arguments: tuple[str | int | float | bool, ...] = ()
    succeeded: bool = True
    privileged: bool = False
    request_id: str | None = None


class TaskTrajectory(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fixture: TaskFixture
    before: NormalizedState
    after: NormalizedState
    actions: tuple[PrimitiveAction, ...]

    @model_validator(mode="after")
    def validate_action_sequence(self) -> "TaskTrajectory":
        sequences = tuple(action.sequence for action in self.actions)
        expected = tuple(range(1, len(self.actions) + 1))
        if sequences != expected:
            raise ValueError(f"Action sequences must be contiguous from 1; got {sequences}")
        return self


class ScoreComponent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    passed: bool
    evidence: str


class TaskScore(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fixture_id: str
    success: bool
    score: float = Field(ge=0, le=1)
    components: tuple[ScoreComponent, ...]
    failure_reasons: tuple[str, ...] = ()
