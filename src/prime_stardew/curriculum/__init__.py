"""M17 self-generated curricula for noisy and adversarial evidence."""

from .agent import AgentCurriculumPolicy, CurriculumProposal
from .live import LiveEvidenceRepairDecision, LiveEvidenceRepairPolicy
from .models import EvidenceObservation, EvidenceRegime, EvidenceTask, M17Family
from .research import (
    M17Condition, M17Preregistration, build_m17_report_from_events,
    curriculum_family, load_m17_preregistration, run_m17_condition,
)

__all__ = [
    "AgentCurriculumPolicy", "CurriculumProposal", "EvidenceObservation",
    "EvidenceRegime", "EvidenceTask", "M17Condition", "M17Family",
    "M17Preregistration", "LiveEvidenceRepairDecision", "LiveEvidenceRepairPolicy",
    "build_m17_report_from_events", "curriculum_family",
    "load_m17_preregistration", "run_m17_condition",
]
