"""Stable models for the subset of StarDojo state used by the harness."""

from __future__ import annotations

import base64
import hashlib
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ObservationMode(StrEnum):
    STRUCTURED_LOCAL = "structured_local"
    MULTIMODAL_LOCAL = "multimodal_local"
    STRUCTURED_GLOBAL = "structured_global"
    REPLAY = "replay"


class Position(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: int = Field(alias="X")
    y: int = Field(alias="Y")


class InventoryItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str | None = Field(default=None, alias="Name")
    quantity: int | None = Field(default=None, alias="Quantity")


class CropState(BaseModel):
    """A crop from StarDojo's global crop list."""

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    position: Position | None = None
    is_watered: bool = Field(default=False, alias="isWatered")
    is_dead: bool = Field(default=False, alias="isDead")
    forage_crop: bool = False
    current_phase: int = 0

    @field_validator("position", mode="before")
    @classmethod
    def parse_vector_position(cls, value: Any) -> Any:
        # Newtonsoft serializes XNA Vector2 as "x, y" in the live payload,
        # while recorded fixtures may contain an {X, Y} object.
        if isinstance(value, str):
            parts = [part.strip() for part in value.split(",")]
            if len(parts) == 2:
                try:
                    return {"X": int(float(parts[0])), "Y": int(float(parts[1]))}
                except ValueError:
                    pass
        return value


class TileCropState(BaseModel):
    """Crop fields currently returned by ``get_tile_info``."""

    model_config = ConfigDict(extra="allow")

    seed_id: str | None = None
    index_harvest: str | None = None
    is_watered: bool | None = Field(default=None, alias="isWatered")
    current_phase: int | None = None


class ChestItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str = Field(alias="Name")
    quantity: int = Field(alias="Quantity", ge=0)


class CurrentMenuData(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str = "No Menu"
    # StarDojo serializes this as null outside a chest menu.
    items_in_chest: list[ChestItem] | None = Field(default=None, alias="ItemsInChest")


class PlayerState(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str = Field(alias="Name")
    health: int = Field(alias="Health")
    stamina: float = Field(alias="Stamina")
    money: int = Field(alias="Money")
    location: str = Field(alias="Location")
    position: Position = Field(alias="Position")
    facing_direction: int = Field(alias="FacingDirection", ge=0, le=3)
    inventory: list[InventoryItem] = Field(default_factory=list, alias="Inventory")


class GameState(BaseModel):
    model_config = ConfigDict(extra="allow")

    time: int = Field(alias="Time", ge=0, le=2800)
    day: int = Field(alias="DayOfMonth", ge=1, le=28)
    season: str = Field(alias="Season")
    year: int = Field(alias="Year", ge=1)
    weather: str = Field(alias="Weather")

    @property
    def date(self) -> "GameDate":
        return GameDate(year=self.year, season=self.season, day=self.day)


class GameDate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    year: int = Field(ge=1)
    season: str
    day: int = Field(ge=1, le=28)

    @field_validator("season")
    @classmethod
    def validate_season(cls, value: str) -> str:
        normalized = value.lower()
        if normalized not in {"spring", "summer", "fall", "winter"}:
            raise ValueError(f"Unknown season: {value!r}")
        return normalized

    def next_day(self) -> "GameDate":
        if self.day < 28:
            return self.model_copy(update={"day": self.day + 1})
        seasons = ("spring", "summer", "fall", "winter")
        index = seasons.index(self.season)
        if index < len(seasons) - 1:
            return GameDate(year=self.year, season=seasons[index + 1], day=1)
        return GameDate(year=self.year + 1, season="spring", day=1)

    def __str__(self) -> str:
        return f"{self.season} {self.day}, year {self.year}"

    def ordinal(self) -> int:
        season_index = ("spring", "summer", "fall", "winter").index(self.season)
        return (self.year - 1) * 112 + season_index * 28 + self.day


class TileInfo(BaseModel):
    """Structured destination state used to guard autonomous movement."""

    model_config = ConfigDict(extra="allow")

    position: tuple[int, int]
    object_at_tile: str = ""
    terrain_at_tile: str = ""
    building_info: str = ""
    crop_at_tile: TileCropState | None = None
    debris_at_tile: str = ""
    furniture_at_tile: str = ""
    exit_info: str = ""
    npc_info: str = ""
    placeable: bool | None = None

    @field_validator("crop_at_tile", mode="before")
    @classmethod
    def normalize_empty_crop(cls, value: Any) -> Any:
        # The mod uses an empty string for no crop and an object for a crop.
        return None if value == "" else value

    def movement_blockers(self) -> tuple[str, ...]:
        blockers: list[str] = []
        if self.object_at_tile.strip():
            blockers.append(f"object:{self.object_at_tile}")
        terrain = self.terrain_at_tile.strip()
        passable_terrain = ("HoeDirt", "Grass", "Flooring")
        if terrain and not terrain.endswith(passable_terrain):
            blockers.append(f"terrain:{terrain}")
        if self.building_info.strip():
            blockers.append(f"building:{self.building_info}")
        if self.furniture_at_tile.strip():
            blockers.append(f"furniture:{self.furniture_at_tile}")
        if self.npc_info.strip():
            blockers.append(f"npc:{self.npc_info}")
        if self.exit_info.strip():
            blockers.append(f"exit:{self.exit_info}")
        return tuple(blockers)


class CallbackData(BaseModel):
    model_config = ConfigDict(extra="allow")

    on_day_started: int = Field(default=0, alias="OnDayStarted")


class Observation(BaseModel):
    """Validated StarDojo observation with forward-compatible extra fields."""

    model_config = ConfigDict(extra="allow")

    player: PlayerState = Field(alias="Player")
    game_state: GameState = Field(alias="GameState")
    screenshot: str | None = Field(default=None, alias="ScreenShot", repr=False)
    callback_data: CallbackData = Field(default_factory=CallbackData, alias="CallBackData")
    surroundings: list[dict[str, Any]] = Field(default_factory=list, alias="SurroundingsData")
    crops: list[CropState] = Field(default_factory=list, alias="Crops")
    current_menu: CurrentMenuData = Field(
        default_factory=CurrentMenuData,
        alias="CurrentMenuData",
    )

    @field_validator("screenshot")
    @classmethod
    def validate_screenshot_base64(cls, value: str | None) -> str | None:
        if value:
            try:
                base64.b64decode(value, validate=True)
            except ValueError as exc:
                raise ValueError("ScreenShot is not valid base64") from exc
        return value

    def screenshot_bytes(self) -> bytes | None:
        return base64.b64decode(self.screenshot) if self.screenshot else None

    def raw_screenshot_sha256(self) -> str | None:
        data = self.screenshot_bytes()
        return hashlib.sha256(data).hexdigest() if data is not None else None

    def validate_screenshot_size(self, width: int, height: int, channels: int = 4) -> None:
        data = self.screenshot_bytes()
        if data is None:
            raise ValueError("Observation has no screenshot")
        expected = width * height * channels
        if len(data) != expected:
            raise ValueError(
                f"Screenshot has {len(data)} decoded bytes; expected {expected} "
                f"for {width}x{height}x{channels}"
            )

    def assert_fixture(self, expected_player: str) -> None:
        if self.player.name != expected_player:
            raise ValueError(
                f"Refusing unexpected farmer {self.player.name!r}; expected {expected_player!r}"
            )
