"""Preregistered E1 capability ladder."""

from __future__ import annotations

from prime_stardew.experiments.config import LearningConditionConfig

from .models import (
    ConditionManifest, E1Condition, HarnessCapabilities, MemoryExposurePolicy,
)


def condition_manifest(
    condition: E1Condition,
    *,
    full_harness_features: HarnessCapabilities | None = None,
) -> ConditionManifest:
    memory = condition is not E1Condition.A_BASE
    retrieval = condition in {
        E1Condition.C_RETRIEVAL, E1Condition.D_SKILLS,
        E1Condition.E_REFINE, E1Condition.F_FULL,
    }
    skills = condition in {E1Condition.D_SKILLS, E1Condition.E_REFINE, E1Condition.F_FULL}
    refinement = condition in {E1Condition.E_REFINE, E1Condition.F_FULL}
    full = condition is E1Condition.F_FULL
    extras = full_harness_features or HarnessCapabilities(persistent_goal_manager=True)
    capabilities = HarnessCapabilities(
        persistent_memory=memory,
        relevance_retrieval=retrieval,
        procedural_skills=skills,
        reflection=refinement,
        persistent_goal_manager=extras.persistent_goal_manager if full else False,
        adaptive_tool_selection=extras.adaptive_tool_selection if full else False,
        native_compaction=extras.native_compaction if full else False,
        subagents=extras.subagents if full else False,
    )
    exposure = (
        MemoryExposurePolicy.NONE if not memory
        else MemoryExposurePolicy.RELEVANCE if retrieval
        else MemoryExposurePolicy.CHRONOLOGICAL
    )
    return ConditionManifest(
        condition=condition, memory_exposure=exposure, capabilities=capabilities,
        cross_day_agent_state=memory or skills or refinement or full,
        full_harness=full,
    )


def learning_config(manifest: ConditionManifest) -> LearningConditionConfig:
    cap = manifest.capabilities
    return LearningConditionConfig(
        recent_context=True,
        persistent_memory=cap.persistent_memory,
        retrieval=cap.relevance_retrieval,
        skills=cap.procedural_skills,
        refinement=cap.reflection,
    )


def validate_condition_ladder(
    manifests: tuple[ConditionManifest, ...],
    *,
    memory_token_budget_by_condition: dict[E1Condition, int],
) -> None:
    if {item.condition for item in manifests} != set(E1Condition):
        raise ValueError("The E1 ladder must contain Conditions A through F exactly once")
    by_id = {item.condition: item for item in manifests}
    b = by_id[E1Condition.B_MEMORY]
    c = by_id[E1Condition.C_RETRIEVAL]
    if b.memory_exposure is not MemoryExposurePolicy.CHRONOLOGICAL:
        raise ValueError("Condition B must use deterministic chronological exposure")
    if c.memory_exposure is not MemoryExposurePolicy.RELEVANCE:
        raise ValueError("Condition C must use relevance retrieval")
    if memory_token_budget_by_condition[E1Condition.B_MEMORY] != memory_token_budget_by_condition[E1Condition.C_RETRIEVAL]:
        raise ValueError("Conditions B and C must have identical memory-token budgets")
    ordered = [by_id[condition].capabilities for condition in E1Condition]
    fields = ("persistent_memory", "relevance_retrieval", "procedural_skills", "reflection")
    for index in range(1, len(ordered)):
        for field in fields:
            if getattr(ordered[index - 1], field) and not getattr(ordered[index], field):
                raise ValueError(f"Capability ladder regresses at {field}")

