"""M16 memory-corruption detection, repair, and policy adaptation."""

from .agent import AgentMemoryRepairPolicy, MemoryRepairDecision, PolicyPatchProposal
from .models import CorruptionFamily, CorruptionType, RepairResponse, RepairScenario
from .research import (
    M16Condition,
    M16Preregistration,
    build_m16_report_from_events,
    corruption_family,
    load_m16_preregistration,
    run_m16_condition,
)

__all__ = [
    "AgentMemoryRepairPolicy", "CorruptionFamily", "CorruptionType", "M16Condition",
    "M16Preregistration", "MemoryRepairDecision", "PolicyPatchProposal",
    "RepairResponse", "RepairScenario", "build_m16_report_from_events",
    "corruption_family", "load_m16_preregistration", "run_m16_condition",
]
