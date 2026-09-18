"""Typed high-level client over StarDojo's raw command transport."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError

from .errors import ObservationError, ProtocolError
from .models import Observation, TileInfo
from .transport import SocketTransport, Transport, TransportResponse


@dataclass(frozen=True, slots=True)
class ActionResult:
    request_id: str
    command: str
    payload: str
    elapsed_ms: float


class StarDojoClient:
    IDEMPOTENT_METHODS = {"observe_v2", "get_surroundings", "get_tile_info", "pause", "resume"}

    def __init__(self, transport: Transport | None = None) -> None:
        self.transport = transport or SocketTransport()

    def raw(self, method: str, *arguments: object, idempotent: bool | None = None) -> TransportResponse:
        if not method or "%" in method:
            raise ValueError("Method must be a non-empty StarDojo method name")
        command = "%".join([method, *(str(value) for value in arguments)])
        safe_retry = method in self.IDEMPOTENT_METHODS if idempotent is None else idempotent
        return self.transport.request(command, idempotent=safe_retry)

    def observe(self, radius: int = 1, *, expected_player: str | None = None) -> Observation:
        if radius < 0:
            raise ValueError("Observation radius must be non-negative")
        response = self.raw("observe_v2", radius, idempotent=True)
        try:
            observation = Observation.model_validate_json(response.payload)
            if expected_player:
                observation.assert_fixture(expected_player)
            return observation
        except (ValidationError, ValueError) as exc:
            raise ObservationError(f"Invalid observation for request {response.request_id}") from exc

    def get_tile_info(self, x: int, y: int) -> TileInfo:
        if x < 0 or y < 0:
            raise ValueError("Tile coordinates must be non-negative")
        response = self.raw("get_tile_info", x, y, idempotent=True)
        try:
            tile = TileInfo.model_validate_json(response.payload)
        except ValidationError as exc:
            raise ObservationError(f"Invalid tile information for request {response.request_id}") from exc
        if tile.position != (x, y):
            raise ObservationError(
                f"Tile response position {tile.position} does not match requested {(x, y)}"
            )
        return tile

    def load_save(self, save_id: str) -> ActionResult:
        if not save_id or "%" in save_id or any(char in save_id for char in "\\/:"):
            raise ValueError("Unsafe save ID")
        return self._action("load_game_record", save_id)

    def turn(self, direction: int) -> ActionResult:
        if direction not in range(4):
            raise ValueError("Direction must be 0, 1, 2, or 3")
        return self._action("turn", direction)

    def move(self, x: int, y: int) -> ActionResult:
        if x < 0 or y < 0:
            raise ValueError("Movement coordinates must be non-negative")
        return self._action("move", x, y)

    def move_relative(self, x: int, y: int) -> ActionResult:
        return self._action("move_relative", x, y)

    def move_step(self, direction: int) -> ActionResult:
        if direction not in range(1, 5):
            raise ValueError("Step direction must be 1 (up), 2 (right), 3 (down), or 4 (left)")
        return self._action("move_step", direction)

    def choose_item(self, slot_index: int) -> ActionResult:
        if slot_index not in range(36):
            raise ValueError("Inventory slot must be between 0 and 35")
        return self._action("choose_item", slot_index)

    def use(self) -> ActionResult:
        return self._action("use")

    def interact(self) -> ActionResult:
        return self._action("interact")

    def take_from_chest(self, item_index: int, quantity: int) -> ActionResult:
        if item_index < 0:
            raise ValueError("Chest item index must be non-negative")
        if quantity <= 0:
            raise ValueError("Chest transfer quantity must be positive")
        return self._action("take_from_chest", item_index, quantity)

    def put_to_chest(self, inventory_index: int, quantity: int) -> ActionResult:
        if inventory_index not in range(36):
            raise ValueError("Inventory slot must be between 0 and 35")
        if quantity <= 0:
            raise ValueError("Chest transfer quantity must be positive")
        return self._action("put_to_chest", inventory_index, quantity)

    def pause(self) -> ActionResult:
        return self._action("pause", idempotent=True)

    def resume(self) -> ActionResult:
        return self._action("resume", idempotent=True)

    def sleep(self) -> ActionResult:
        return self._action("sleep")

    def _action(self, method: str, *arguments: object, idempotent: bool = False) -> ActionResult:
        response = self.raw(method, *arguments, idempotent=idempotent)
        if response.payload not in {"Message received", "True", "False"}:
            raise ProtocolError(f"Unexpected action response for {method}: {response.payload!r}")
        return ActionResult(
            request_id=response.request_id,
            command=response.command,
            payload=response.payload,
            elapsed_ms=response.elapsed_ms,
        )
