"""Experiment lifecycle operations built from observable state transitions."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from .client import ActionResult, StarDojoClient
from .errors import EnvironmentNotReady, MovementBlocked, MovementInvariantError, StarDojoError
from .models import GameDate, Observation, TileInfo


@dataclass(frozen=True, slots=True)
class DayResult:
    before: GameDate
    after: GameDate
    sleep_action: ActionResult
    observation: Observation


@dataclass(frozen=True, slots=True)
class MovementResult:
    source: tuple[int, int]
    destination: tuple[int, int]
    tile: TileInfo
    action: ActionResult
    observation: Observation


class EnvironmentController:
    """Coordinates one isolated farmer without relying on transient SMAPI events."""

    def __init__(
        self,
        client: StarDojoClient,
        expected_player: str,
        *,
        timeout: float = 60.0,
        poll_interval: float = 0.5,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not expected_player:
            raise ValueError("Expected player is required for save isolation")
        if timeout <= 0 or poll_interval < 0:
            raise ValueError("Timeout must be positive and poll interval non-negative")
        self.client = client
        self.expected_player = expected_player
        self.timeout = timeout
        self.poll_interval = poll_interval
        self._monotonic = monotonic
        self._sleep = sleeper

    def observe(self, radius: int = 1) -> Observation:
        return self.client.observe(radius, expected_player=self.expected_player)

    def load(self, save_id: str, *, expected_date: GameDate | None = None) -> Observation:
        self.client.load_save(save_id)
        return self.wait_until(
            lambda observation: expected_date is None
            or observation.game_state.date == expected_date,
            description=f"save {save_id!r}"
            + (f" at {expected_date}" if expected_date is not None else ""),
        )

    def finish_day(self) -> DayResult:
        before_observation = self.observe()
        before = before_observation.game_state.date
        expected = before.next_day()
        action = self.client.sleep()
        after_observation = self.wait_until(
            lambda observation: observation.game_state.date == expected
            and observation.game_state.time <= 700,
            description=f"new day {expected}",
        )
        return DayResult(
            before=before,
            after=after_observation.game_state.date,
            sleep_action=action,
            observation=after_observation,
        )

    def move(self, x: int, y: int) -> MovementResult:
        """Move to an empty tile and verify the observed postcondition."""

        return self._checked_move((x, y), lambda: self.client.move(x, y))

    def move_relative(self, x: int, y: int) -> MovementResult:
        before = self.observe()
        source = _position(before)
        destination = source[0] + x, source[1] + y
        return self._checked_move(
            destination,
            lambda: self.client.move_relative(x, y),
            before=before,
        )

    def move_step(self, direction: int) -> MovementResult:
        if direction not in range(1, 5):
            raise ValueError("Step direction must be 1 (up), 2 (right), 3 (down), or 4 (left)")
        before = self.observe()
        source = _position(before)
        dx, dy = {1: (0, -1), 2: (1, 0), 3: (0, 1), 4: (-1, 0)}[direction]
        destination = source[0] + dx, source[1] + dy
        return self._checked_move(
            destination,
            lambda: self.client.move_step(direction),
            before=before,
        )

    def _checked_move(
        self,
        destination: tuple[int, int],
        action: Callable[[], ActionResult],
        *,
        before: Observation | None = None,
    ) -> MovementResult:
        before = before or self.observe()
        source = _position(before)
        tile = self.client.get_tile_info(*destination)
        blockers = tile.movement_blockers()
        if blockers:
            raise MovementBlocked(
                f"Refusing movement from {source} to {destination}: {', '.join(blockers)}"
            )
        result = action()
        after = self.observe()
        if result.payload != "True":
            raise MovementInvariantError(
                f"Movement from {source} to {destination} returned {result.payload!r}"
            )
        if after.player.location != before.player.location:
            raise MovementInvariantError(
                f"Movement unexpectedly changed location from {before.player.location!r} "
                f"to {after.player.location!r}"
            )
        actual = _position(after)
        if actual != destination:
            raise MovementInvariantError(
                f"Movement reported success for {destination}, but observed {actual}"
            )
        return MovementResult(
            source=source,
            destination=destination,
            tile=tile,
            action=result,
            observation=after,
        )

    def wait_until(
        self,
        predicate: Callable[[Observation], bool],
        *,
        description: str,
    ) -> Observation:
        deadline = self._monotonic() + self.timeout
        last_error: StarDojoError | None = None
        while True:
            try:
                observation = self.observe()
                if predicate(observation):
                    return observation
            except StarDojoError as exc:
                last_error = exc
            if self._monotonic() >= deadline:
                detail = f" Last adapter error: {last_error}" if last_error else ""
                raise EnvironmentNotReady(
                    f"Timed out waiting for {description} for farmer "
                    f"{self.expected_player!r}.{detail}"
                ) from last_error
            self._sleep(self.poll_interval)


def _position(observation: Observation) -> tuple[int, int]:
    return observation.player.position.x, observation.player.position.y
