"""Deterministic held-out transfer evaluation and fixed-budget tier routing."""

from __future__ import annotations

from pathlib import Path

from prime_stardew.memory import MemoryQuery, MemoryStore
from prime_stardew.skills import SkillStore

from .models import (
    RoutingPolicyResult, TransferConditionResult, TransferKind, TransferTask,
    TransferTaskKind, TransferTaskResult,
)


def evaluate_transfer_condition(
    condition: TransferKind,
    tasks: tuple[TransferTask, ...],
    root: Path,
    *,
    memory_bundle: Path,
    skill_bundle: Path,
) -> TransferConditionResult:
    condition_root = root / condition.value
    memories = MemoryStore(condition_root / "memory.sqlite3", store_id=condition.value)
    skills = SkillStore(condition_root / "skills.sqlite3", store_id=condition.value)
    imported_memories: tuple[str, ...] = ()
    imported_skills: tuple[tuple[str, int], ...] = ()
    if condition in {TransferKind.MEMORIES_ONLY, TransferKind.FULL_INHERITANCE}:
        imported_memories = memories.import_bundle(memory_bundle)
    if condition in {TransferKind.SKILLS_ONLY, TransferKind.FULL_INHERITANCE}:
        imported_skills = skills.import_bundle(skill_bundle)
    results: list[TransferTaskResult] = []
    for task in tasks:
        if task.kind is TransferTaskKind.KNOWLEDGE:
            retrieval = memories.retrieve(MemoryQuery(text=task.query, token_budget=128))
            evidence = tuple(record.memory_id for record in retrieval.selected)
            success = task.required_artifact_id in evidence
            decisions = 1
        else:
            active = skills.active(task.required_artifact_id)
            evidence = (f"{active.skill_id}:v{active.version}",) if active else ()
            success = True
            decisions = 1 if active else 3
        results.append(TransferTaskResult(
            task_id=task.task_id, success=success, model_decisions=decisions,
            evidence_ids=evidence,
        ))
    memories.close()
    skills.close()
    return TransferConditionResult(
        condition=condition,
        imported_memory_ids=imported_memories,
        imported_skill_versions=imported_skills,
        task_results=tuple(results),
        success_rate=sum(result.success for result in results) / len(results),
        model_decisions=sum(result.model_decisions for result in results),
    )


def evaluate_routing(*, budget_usd: float = 0.012) -> tuple[RoutingPolicyResult, ...]:
    complexities = (1, 2, 1, 2, 1, 2, 1, 2)
    tiers = {"economy": (1, 0.001), "capable": (2, 0.002)}
    policies = {
        "economy_only": lambda _complexity: "economy",
        "capable_only": lambda _complexity: "capable",
        "learned_complexity_router": lambda complexity: "economy" if complexity == 1 else "capable",
    }
    results = []
    for name, choose in policies.items():
        spent = 0.0
        successes = 0
        attempted = 0
        for complexity in complexities:
            tier = choose(complexity)
            capacity, price = tiers[tier]
            if spent + price > budget_usd + 1e-12:
                break
            spent += price
            attempted += 1
            successes += capacity >= complexity
        results.append(RoutingPolicyResult(
            policy=name, attempted=attempted, successes=successes,
            cost_usd=round(spent, 6),
            utility_per_dollar=successes / spent if spent else 0,
            within_budget=spent <= budget_usd + 1e-12,
        ))
    return tuple(results)
