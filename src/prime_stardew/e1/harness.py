"""Harness-neutral boundary used by E1 study runners."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .models import HarnessCapabilities, LearningResetSpec, LearningState


@runtime_checkable
class AgentHarness(Protocol):
    @property
    def capabilities(self) -> HarnessCapabilities: ...

    def start(self, *, objective: str, run_id: str) -> None: ...

    def decide(self, observation: dict[str, Any]) -> dict[str, Any]: ...

    def end_day(self, *, game_day: int) -> None: ...

    def checkpoint(self, destination: Path) -> str: ...

    def restore(self, checkpoint: Path) -> None: ...

    def export_learning_state(self) -> LearningState: ...

    def reset_learning_state(self, reset: LearningResetSpec) -> LearningState: ...


class HarnessContractError(RuntimeError):
    pass


def validate_harness_capabilities(
    harness: AgentHarness, expected: HarnessCapabilities,
) -> None:
    if harness.capabilities != expected:
        raise HarnessContractError(
            "Harness capabilities differ from the immutable condition manifest: "
            f"expected={expected.model_dump(mode='json')}, "
            f"actual={harness.capabilities.model_dump(mode='json')}"
        )

