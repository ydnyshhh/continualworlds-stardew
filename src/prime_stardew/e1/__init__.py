"""E1 long-horizon continual-learning study framework."""

from .branches import PhysicalDisposableBranch, PhysicalProbeBranchManager
from .conditions import condition_manifest, learning_config, validate_condition_ladder
from .models import (
    BranchIdentity, BranchType, CompetencyDomain, CompetencyInstance, ConditionManifest,
    E1Condition, E1Phase, E1StudyConfig, HarnessCapabilities, InferenceAccounting,
    InferenceCategory, LearningResetSpec, LearningState, MemoryExposurePolicy,
    PERSISTENT_OBJECTIVE, ProbeDefinition, ProbeKind, ProbePoint, ProbeSchedule,
    ProbeTaskScore, RefinementAttribution, RefinementOutcome,
)
from .memory_exposure import exposed_memory_tokens, select_memory_context

__all__ = [
    "BranchIdentity", "BranchType", "CompetencyDomain", "CompetencyInstance",
    "ConditionManifest", "E1Condition", "E1Phase", "E1StudyConfig",
    "HarnessCapabilities", "InferenceAccounting", "InferenceCategory",
    "LearningResetSpec", "LearningState", "MemoryExposurePolicy", "PERSISTENT_OBJECTIVE",
    "ProbeDefinition", "ProbeKind", "ProbePoint", "ProbeSchedule", "ProbeTaskScore",
    "PhysicalDisposableBranch", "PhysicalProbeBranchManager",
    "RefinementAttribution", "RefinementOutcome", "exposed_memory_tokens",
    "select_memory_context",
    "condition_manifest", "learning_config", "validate_condition_ladder",
]
