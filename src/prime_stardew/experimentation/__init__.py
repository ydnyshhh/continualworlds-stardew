"""Controlled active experimentation over hidden, seeded mechanics."""

from .engine import (
    AgentExperimentPolicy, ExperimentValidationError, deterministic_decision,
    execute_decision, validate_decision,
)
from .models import (
    ExperimentAction, ExperimentChoice, ExperimentDecision, ExperimentOutcome,
    ExperimentScenario, HiddenMechanicScenario,
)
from .live import LiveSamplingError, LiveSamplingPlan, LiveSamplingPolicy

__all__ = [
    "AgentExperimentPolicy", "ExperimentAction", "ExperimentChoice", "ExperimentDecision",
    "ExperimentOutcome", "ExperimentScenario", "ExperimentValidationError",
    "HiddenMechanicScenario", "deterministic_decision", "execute_decision",
    "validate_decision", "LiveSamplingError", "LiveSamplingPlan", "LiveSamplingPolicy",
]
