"""E1 long-horizon continual-learning study framework."""

from .branches import PhysicalDisposableBranch, PhysicalProbeBranchManager
from .activity import segment_live_competencies
from .conditions import condition_manifest, learning_config, validate_condition_ladder
from .config import materialize_run_config
from .models import (
    BranchIdentity, BranchType, CompetencyDomain, CompetencyInstance, ConditionManifest,
    E1Condition, E1Phase, E1StudyConfig, HarnessCapabilities, InferenceAccounting,
    InferenceCategory, LearningResetSpec, LearningState, MemoryExposurePolicy,
    PERSISTENT_OBJECTIVE, ProbeDefinition, ProbeKind, ProbePoint, ProbeSchedule,
    ProbeTaskScore, RefinementAttribution, RefinementOutcome,
)
from .memory_exposure import exposed_memory_tokens, select_memory_context
from .live import E1LiveResult, LiveDaySummary, execute_guarded_action, run_live_days
from .prime_adapter import PrimeHarnessAdapter, PrimeHarnessCheckpoint

__all__ = [
    "BranchIdentity", "BranchType", "CompetencyDomain", "CompetencyInstance",
    "ConditionManifest", "E1Condition", "E1Phase", "E1StudyConfig",
    "HarnessCapabilities", "InferenceAccounting", "InferenceCategory",
    "E1LiveResult", "LiveDaySummary",
    "LearningResetSpec", "LearningState", "MemoryExposurePolicy", "PERSISTENT_OBJECTIVE",
    "ProbeDefinition", "ProbeKind", "ProbePoint", "ProbeSchedule", "ProbeTaskScore",
    "PhysicalDisposableBranch", "PhysicalProbeBranchManager",
    "PrimeHarnessAdapter", "PrimeHarnessCheckpoint",
    "RefinementAttribution", "RefinementOutcome", "exposed_memory_tokens",
    "select_memory_context",
    "condition_manifest", "learning_config", "validate_condition_ladder",
    "materialize_run_config",
    "execute_guarded_action", "run_live_days",
    "segment_live_competencies",
]
