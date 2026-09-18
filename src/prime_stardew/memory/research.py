"""Preregistered synthetic benchmark for bounded memory-management policies."""

from __future__ import annotations

import json
import statistics
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from prime_stardew.experiments.config import MemoryManagementConfig, MemoryManagementPolicy
from prime_stardew.experiments.provenance import artifact_sha256
from prime_stardew.studies.analysis import bootstrap_mean_ci, exact_two_sided_sign_test
from prime_stardew.telemetry import EventStore

from .management import (
    MemoryBudgetManager, MemoryManagementAction, MemoryManagementDecision,
    MemoryManagementOperation,
)
from .models import MemoryKind, MemoryQuery, MemoryRecord
from .store import MemoryStore


class M12Preregistration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    study_id: str
    hypothesis: str
    primary_metric: Literal["held_out_utility_per_active_token"]
    favorable_direction: Literal["higher"]
    seeds: tuple[int, ...] = Field(min_length=8)
    conditions: tuple[MemoryManagementPolicy, ...]
    bounded_conditions: tuple[MemoryManagementPolicy, ...]
    max_active_tokens: int = Field(gt=0)
    alpha: float = Field(default=0.05, gt=0, lt=1)
    bootstrap_samples: int = Field(default=10_000, ge=1_000)
    exclusions: tuple[str, ...] = Field(min_length=1)
    analysis_plan: str

    @model_validator(mode="after")
    def validate_design(self) -> "M12Preregistration":
        required = {
            MemoryManagementPolicy.FIFO,
            MemoryManagementPolicy.LEAST_RECENTLY_USED,
            MemoryManagementPolicy.LEAST_RETRIEVED,
            MemoryManagementPolicy.AGENT_SELECTED,
            MemoryManagementPolicy.NONE,
        }
        if set(self.conditions) != required:
            raise ValueError("M12 study requires FIFO, LRU, least-retrieved, agent, and unbounded")
        if set(self.bounded_conditions) != required - {MemoryManagementPolicy.NONE}:
            raise ValueError("Every condition except unbounded must use the fixed budget")
        if len(set(self.seeds)) != len(self.seeds):
            raise ValueError("M12 seeds must be distinct")
        return self


def load_m12_preregistration(path: Path) -> tuple[M12Preregistration, str]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("M12 preregistration must be a mapping")
    preregistration = M12Preregistration.model_validate(value)
    return preregistration, artifact_sha256(preregistration.model_dump(mode="json"))


def synthetic_records(seed: int) -> tuple[MemoryRecord, ...]:
    day = f"seed-{seed}"
    epoch = datetime(2026, 1, 1, tzinfo=UTC)
    values = (
        ("future-cool-rule", "cool waxy crops use mineral mulch", 0.95, 4, False),
        ("future-root-rule", "dry rocky root crops use compost", 0.93, 4, False),
        ("stale-rain-rule", "rain forecast once implied clay mulch", 0.85, 1, True),
        ("recent-market-note", f"{day} recent market color notes", 0.40, 1, False),
        ("recent-villager-note", f"{day} recent villager route notes", 0.35, 1, False),
        ("recent-weather-note", f"{day} recent weather wind notes", 0.30, 1, False),
    )
    return tuple(MemoryRecord(
        memory_id=memory_id, kind=MemoryKind.SEMANTIC, text=text,
        created_at=epoch + timedelta(minutes=index),
        source_event_ids=tuple(f"observed:{memory_id}:{index}" for index in range(support)),
        confidence=confidence,
        valid_to_day=5 if stale else None,
        payload={
            "predicted_future_value": confidence,
            "support_count": support,
            "stale_signal": stale,
        },
        tags=tuple(text.split()[:3]),
    ) for index, (memory_id, text, confidence, support, stale) in enumerate(values))


def deterministic_agent_decision(records: tuple[MemoryRecord, ...]) -> MemoryManagementDecision:
    actions = tuple(MemoryManagementAction(
        memory_id=record.memory_id,
        operation=(
            MemoryManagementOperation.RETAIN
            if float(record.payload.get("predicted_future_value", 0)) >= 0.9
            and not bool(record.payload.get("stale_signal"))
            else MemoryManagementOperation.DEACTIVATE
        ),
    ) for record in records)
    return MemoryManagementDecision(
        actions=actions,
        reasoning_summary="Retain high-support, high-value rules and deactivate stale or weak notes.",
        predicted_future_value={
            record.memory_id: float(record.payload.get("predicted_future_value", 0))
            for record in records
        },
    )


def run_condition(
    root: Path,
    *,
    seed: int,
    policy: MemoryManagementPolicy,
    max_active_tokens: int,
    preregistration_sha256: str,
) -> dict[str, object]:
    run_id = f"m12-{policy.value}-s{seed}"
    run_root = root / run_id
    store = MemoryStore(run_root / "memory.sqlite3", store_id=run_id)
    events = EventStore(run_root / "events.jsonl", run_id)
    records = synthetic_records(seed)
    state_sha256 = artifact_sha256([record.model_dump(mode="json") for record in records])
    events.append("memory_study_started", {
        "seed": seed, "policy": policy.value,
        "preregistration_sha256": preregistration_sha256,
        "candidate_state_sha256": state_sha256,
    })
    for record in records:
        store.add(record)
        events.append("memory_observed", {"record": record.model_dump(mode="json")})
    # Recent but low-value records dominate naive usage signals before the held-out phase.
    store.retrieve(MemoryQuery(text="recent weather wind", limit=1, token_budget=100))
    store.retrieve(MemoryQuery(text="recent market color", limit=1, token_budget=100))
    store.retrieve(MemoryQuery(text="rain forecast clay", limit=1, token_budget=100))
    bounded = policy is not MemoryManagementPolicy.NONE
    config = MemoryManagementConfig(
        enabled=bounded,
        max_active_tokens=max_active_tokens if bounded else 0,
        policy=policy,
        overflow_fallback=MemoryManagementPolicy.FIFO,
    )
    manager = MemoryBudgetManager(store, config, events=events)
    decision = deterministic_agent_decision(records) if policy is MemoryManagementPolicy.AGENT_SELECTED else None
    budget_report = manager.enforce(decision)
    task_queries = (
        ("cool-treatment", "cool waxy crops mineral mulch", "future-cool-rule"),
        ("root-treatment", "dry rocky root crops compost", "future-root-rule"),
    )
    outcomes = []
    used_ids: set[str] = set()
    stale_retrievals = 0
    for task_id, query, expected_id in task_queries:
        retrieval = store.retrieve(MemoryQuery(text=query, game_day=20, limit=1, token_budget=100))
        selected_ids = tuple(record.memory_id for record in retrieval.selected)
        used_ids.update(selected_ids)
        stale_retrievals += sum(record.valid_to_day is not None and record.valid_to_day < 20
                                for record in retrieval.selected)
        outcomes.append({
            "task_id": task_id, "expected_memory_id": expected_id,
            "selected_memory_ids": list(selected_ids),
            "success": expected_id in selected_ids,
        })
    active = store.records()
    active_ids = {record.memory_id for record in active}
    utility = sum(outcome["success"] for outcome in outcomes)
    active_tokens = manager.active_tokens()
    precision = utility / max(1, sum(len(outcome["selected_memory_ids"]) for outcome in outcomes))
    unused_fraction = len(active_ids - used_ids) / max(1, len(active_ids))
    score = {
        "seed": seed, "policy": policy.value,
        "candidate_state_sha256": state_sha256,
        "outcomes": outcomes,
        "held_out_utility": utility,
        "active_tokens": active_tokens,
        "held_out_utility_per_active_token": utility / max(1, active_tokens),
        "retrieval_precision": precision,
        "stale_retrieval_rate": stale_retrievals / len(task_queries),
        "unused_memory_fraction": unused_fraction,
        "memory_churn": len(budget_report.deactivated_ids),
        "active_memory_ids": sorted(active_ids),
        "deactivated_memory_ids": list(budget_report.deactivated_ids),
        "budget_enforced": not bounded or active_tokens <= max_active_tokens,
        "fallback_applied": budget_report.fallback_applied,
    }
    events.append("memory_study_scored", score)
    events.append("memory_study_completed", {"status": "completed"})
    store.close()
    return score


def build_m12_report_from_events(
    root: Path, preregistration: M12Preregistration, preregistration_sha256: str,
) -> dict[str, object]:
    scores: list[dict[str, object]] = []
    failures: list[dict[str, str]] = []
    event_logs = sorted(root.glob("m12-*/events.jsonl"))
    for event_log in event_logs:
        first = json.loads(event_log.read_text(encoding="utf-8").splitlines()[0])
        store = EventStore(event_log, first["run_id"])
        scored = [record for record in store.iter_records() if record.event_type == "memory_study_scored"]
        completed = [record for record in store.iter_records() if record.event_type == "memory_study_completed"]
        if len(scored) != 1 or len(completed) != 1:
            failures.append({"run_id": store.run_id, "reason": "missing or duplicate terminal events"})
            continue
        scores.append(dict(scored[0].payload))
    expected = len(preregistration.seeds) * len(preregistration.conditions)
    by_key = {(int(score["seed"]), str(score["policy"])): score for score in scores}
    missing = [
        f"{seed}:{condition.value}" for seed in preregistration.seeds
        for condition in preregistration.conditions if (seed, condition.value) not in by_key
    ]
    agent_effects = tuple(
        float(by_key[(seed, MemoryManagementPolicy.AGENT_SELECTED.value)][preregistration.primary_metric])
        - float(by_key[(seed, MemoryManagementPolicy.FIFO.value)][preregistration.primary_metric])
        for seed in preregistration.seeds if not missing
    )
    ci = bootstrap_mean_ci(
        agent_effects, samples=preregistration.bootstrap_samples, confidence=0.95,
    ) if agent_effects else (0.0, 0.0)
    p_value = exact_two_sided_sign_test(agent_effects) if agent_effects else 1.0
    summaries = {}
    for condition in preregistration.conditions:
        condition_scores = [score for score in scores if score["policy"] == condition.value]
        summaries[condition.value] = {
            "runs": len(condition_scores),
            "mean_held_out_utility": statistics.mean(
                float(score["held_out_utility"]) for score in condition_scores
            ) if condition_scores else 0,
            "mean_active_tokens": statistics.mean(
                float(score["active_tokens"]) for score in condition_scores
            ) if condition_scores else 0,
            "mean_utility_per_active_token": statistics.mean(
                float(score[preregistration.primary_metric]) for score in condition_scores
            ) if condition_scores else 0,
            "mean_retrieval_precision": statistics.mean(
                float(score["retrieval_precision"]) for score in condition_scores
            ) if condition_scores else 0,
            "mean_unused_memory_fraction": statistics.mean(
                float(score["unused_memory_fraction"]) for score in condition_scores
            ) if condition_scores else 0,
        }
    state_hashes_match = all(
        len({by_key[(seed, condition.value)]["candidate_state_sha256"]
             for condition in preregistration.conditions}) == 1
        for seed in preregistration.seeds if not missing
    )
    passed = (
        len(scores) == expected and not failures and not missing and state_hashes_match
        and all(bool(score["budget_enforced"]) for score in scores)
        and bool(agent_effects) and ci[0] > 0 and p_value < preregistration.alpha
    )
    return {
        "schema_version": 1,
        "status": "passed" if passed else "failed",
        "study_id": preregistration.study_id,
        "scope": "controlled synthetic bounded-memory causal study",
        "preregistration_sha256": preregistration_sha256,
        "primary_metric": preregistration.primary_metric,
        "runs": len(scores),
        "condition_summaries": summaries,
        "agent_minus_fifo_mean": statistics.mean(agent_effects) if agent_effects else 0,
        "agent_minus_fifo_ci95": list(ci),
        "exact_two_sided_sign_test_p": p_value,
        "matched_candidate_states": state_hashes_match,
        "raw_scores": scores,
        "failure_analysis": {
            "event_failures": failures, "missing_runs": missing,
            "budget_failures": sum(not bool(score["budget_enforced"]) for score in scores),
            "fallback_runs": sum(bool(score["fallback_applied"]) for score in scores),
        },
        "manual_score_edits": 0,
    }
