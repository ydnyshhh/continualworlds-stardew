"""Live task-session adapter with a hard boundary around privileged setup."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable

from pydantic import BaseModel, ConfigDict

from prime_stardew.env.client import ActionResult
from prime_stardew.env.lifecycle import EnvironmentController, MovementResult

from .models import PrimitiveAction, TaskFixture, TaskTrajectory
from .normalization import normalize_state


class ActionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    arguments: tuple[int | str | float | bool, ...] = ()


class LiveTaskHarness:
    """Capture a scored live trajectory using only typed, allowlisted actions.

    Fixture setup belongs before ``begin``. The class deliberately has no raw or
    debug-command escape hatch, so setup operations cannot leak into scored runs.
    """

    def __init__(
        self,
        controller: EnvironmentController,
        fixture: TaskFixture,
        *,
        settle_seconds: float = 0.35,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if controller.expected_player != fixture.expected_player:
            raise ValueError(
                f"Controller farmer {controller.expected_player!r} does not match "
                f"fixture farmer {fixture.expected_player!r}"
            )
        if settle_seconds < 0:
            raise ValueError("Settle time must be non-negative")
        self.controller = controller
        self.fixture = fixture
        self.settle_seconds = settle_seconds
        self._sleep = sleeper
        self._before = None
        self._actions: list[PrimitiveAction] = []

    @property
    def before(self):
        if self._before is None:
            raise RuntimeError("Call begin before reading the baseline")
        return self._before

    def begin(self) -> None:
        if self._before is not None:
            raise RuntimeError("Task session has already begun")
        self._before = self._snapshot()
        if self._before.player != self.fixture.expected_player:
            raise RuntimeError("Fixture player precondition failed")
        if self._before.date != self.fixture.expected_date:
            raise RuntimeError(
                f"Fixture date precondition failed: {self._before.date} != "
                f"{self.fixture.expected_date}"
            )
        if self._before.location != self.fixture.location:
            raise RuntimeError(
                f"Fixture location precondition failed: {self._before.location!r} != "
                f"{self.fixture.location!r}"
            )
        if self._before.position != self.fixture.origin:
            raise RuntimeError(
                f"Fixture origin precondition failed: {self._before.position} != "
                f"{self.fixture.origin}"
            )

    def perform(self, command: ActionCommand) -> PrimitiveAction:
        if self._before is None:
            raise RuntimeError("Call begin before performing actions")
        if command.name not in self.fixture.allowed_actions:
            raise ValueError(f"Action {command.name!r} is not allowed by {self.fixture.fixture_id}")
        if len(self._actions) >= self.fixture.budget.max_actions:
            raise RuntimeError(f"Action budget exhausted for {self.fixture.fixture_id}")
        result = self._dispatch(command)
        primitive = PrimitiveAction(
            sequence=len(self._actions) + 1,
            name=command.name,
            arguments=command.arguments,
            succeeded=result.payload != "False",
            privileged=False,
            request_id=result.request_id,
        )
        self._actions.append(primitive)
        if self.settle_seconds:
            self._sleep(self.settle_seconds)
        return primitive

    def run(self, commands: Iterable[ActionCommand]) -> TaskTrajectory:
        self.begin()
        for command in commands:
            self.perform(command)
        return self.finish()

    def finish(self) -> TaskTrajectory:
        if self._before is None:
            raise RuntimeError("Call begin before finishing a task")
        return TaskTrajectory(
            fixture=self.fixture,
            before=self._before,
            after=self._snapshot(),
            actions=tuple(self._actions),
        )

    def _snapshot(self):
        observation = self.controller.observe(radius=2)
        tile = self.controller.client.get_tile_info(*self.fixture.target)
        return normalize_state(observation, target_tile=tile)

    def _dispatch(self, command: ActionCommand) -> ActionResult:
        name = command.name
        args = command.arguments
        if name == "move":
            return _movement_action(self.controller.move(*_ints(args, 2)))
        if name == "move_relative":
            return _movement_action(self.controller.move_relative(*_ints(args, 2)))
        if name == "move_step":
            return _movement_action(self.controller.move_step(*_ints(args, 1)))
        methods = {
            "turn": self.controller.client.turn,
            "choose_item": self.controller.client.choose_item,
            "use": self.controller.client.use,
            "interact": self.controller.client.interact,
            "take_from_chest": self.controller.client.take_from_chest,
            "put_to_chest": self.controller.client.put_to_chest,
        }
        method = methods.get(name)
        if method is None:
            raise ValueError(f"No typed task action dispatcher for {name!r}")
        return method(*args)  # type: ignore[arg-type]


def _movement_action(result: MovementResult) -> ActionResult:
    return result.action


def _ints(values: tuple[int | str | float | bool, ...], count: int) -> tuple[int, ...]:
    if len(values) != count or any(type(value) is not int for value in values):
        raise ValueError(f"Expected {count} integer action argument(s); got {values}")
    return tuple(values)  # type: ignore[return-value]
