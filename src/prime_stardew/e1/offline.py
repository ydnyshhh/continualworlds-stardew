"""Deterministic contract validation for E1; not a scientific pilot outcome."""

from __future__ import annotations

import csv
import json
import statistics
from pathlib import Path
from typing import Any

from prime_stardew.experiments.provenance import artifact_sha256
from prime_stardew.telemetry import EventStore

from .analysis import learning_curve_slope, score_probe, standardized_probe_aulc
from .branches import ProbeBranchManager
from .competencies import normalized_competency_metrics
from .conditions import condition_manifest, validate_condition_ladder
from .models import (
    CompetencyDomain, CompetencyInstance, E1Condition, E1StudyConfig, LearningState,
    ProbeDefinition, ProbeKind, ProbePoint,
)


def run_offline_matrix(root: Path, config: E1StudyConfig, config_sha256: str) -> dict[str, Any]:
    manifests = tuple(
        condition_manifest(condition, full_harness_features=config.full_harness_features)
        for condition in config.conditions
    )
    validate_condition_ladder(
        manifests,
        memory_token_budget_by_condition={condition: config.memory_token_budget for condition in config.conditions},
    )
    for seed in config.seeds:
        for condition in config.conditions:
            run_offline_condition(
                root, config=config, config_sha256=config_sha256,
                seed=seed, condition=condition,
            )
    report = build_offline_report(root, config, config_sha256)
    write_machine_outputs(root / "report", report)
    return report


def run_offline_condition(
    root: Path,
    *,
    config: E1StudyConfig,
    config_sha256: str,
    seed: int,
    condition: E1Condition,
) -> dict[str, Any]:
    manifest = condition_manifest(condition, full_harness_features=config.full_harness_features)
    run_id = f"e1-offline-{condition.value.lower()}-s{seed}"
    events = EventStore(root / "runs" / run_id / "events.jsonl", run_id)
    initial_game = {
        "schema_version": 1, "seed": seed, "day": 0, "gold": 500,
        "farm_value": 500, "mine_depth": 0, "season": "spring",
    }
    starting_hash = artifact_sha256(initial_game)
    events.append("e1_run_started", {
        "study_id": config.study_id, "phase": config.phase.value,
        "condition_manifest": manifest.model_dump(mode="json"),
        "seed": seed, "horizon_days": config.horizon_days,
        "configuration_sha256": config_sha256,
        "starting_game_state_sha256": starting_hash,
        "persistent_objective_sha256": artifact_sha256(config.persistent_objective),
        "synthetic_contract_validation": True,
    })
    learning = LearningState()
    game = dict(initial_game)
    probe_points: list[ProbePoint] = []
    competency_instances: list[CompetencyInstance] = []
    total_cost = 0.0
    total_input = 0
    total_output = 0

    for day in range(1, config.horizon_days + 1):
        cap = manifest.capabilities
        if cap.persistent_memory:
            memories = (*learning.active_memory_ids, f"memory-day-{day}")
            learning = learning.model_copy(update={
                "active_memory_ids": memories,
                "historical_artifact_ids": (*learning.historical_artifact_ids, f"memory-day-{day}"),
            })
        if cap.procedural_skills and day == 10:
            learning = learning.model_copy(update={
                "active_skill_refs": ("routine-farm-maintenance:v1",),
                "historical_artifact_ids": (*learning.historical_artifact_ids, "routine-farm-maintenance:v1"),
            })
            events.append("skill_activated", {"day": day, "skill_ref": "routine-farm-maintenance:v1"})
        if cap.reflection and day % 7 == 0:
            refinement_id = f"refinement-day-{day}"
            learning = learning.model_copy(update={
                "active_refinement_ids": (*learning.active_refinement_ids, refinement_id),
                "historical_artifact_ids": (*learning.historical_artifact_ids, refinement_id),
            })
            events.append("refinement_created", {
                "day": day, "refinement_id": refinement_id,
                "source_experience_ids": [f"competency-day-{day - 1}"],
                "inference_category": "reflection",
            })

        memory_tokens = min(
            config.memory_token_budget,
            len(learning.active_memory_ids) * 24,
        ) if cap.persistent_memory else 0
        actual_context = 1500 + memory_tokens + (180 if cap.procedural_skills else 0)
        events.append("decision_context_composed", {
            "day": day, "maximum_tokens": config.base_run.context.total_tokens,
            "actual_tokens": actual_context, "memory_token_limit": config.memory_token_budget,
            "memory_tokens": memory_tokens, "skill_tokens": 180 if cap.procedural_skills else 0,
            "recent_event_tokens": min(300, config.base_run.context.recent_events_tokens),
            "memory_exposure": manifest.memory_exposure.value,
            "omitted_items": max(0, len(learning.active_memory_ids) - config.memory_token_budget // 24),
        })
        input_tokens = actual_context
        output_tokens = 120
        acting_cost = (input_tokens * .0000006) + (output_tokens * .000002)
        total_input += input_tokens
        total_output += output_tokens
        total_cost += acting_cost
        events.append("e1_inference_accounted", {
            "day": day, "category": "acting", "calls": 1,
            "input_tokens": input_tokens, "output_tokens": output_tokens,
            "cost_usd": acting_cost, "latency_ms": 100,
        })
        if cap.reflection and day % 7 == 0:
            total_input += 500
            total_output += 100
            total_cost += .0005
            events.append("e1_inference_accounted", {
                "day": day, "category": "reflection", "calls": 1,
                "input_tokens": 500, "output_tokens": 100,
                "cost_usd": .0005, "latency_ms": 50,
            })

        domain = tuple(CompetencyDomain)[(day - 1) % len(CompetencyDomain)]
        decisions = max(0, 4 - int(cap.procedural_skills and day >= 10) - int(cap.reflection and day >= 15))
        instance = CompetencyInstance(
            instance_id=f"competency-day-{day}", domain=domain, game_day=day,
            work_units=10 + day % 5, model_decisions=decisions,
            primitive_actions=12 + day % 4, game_minutes=60 + day % 3 * 10,
            energy_used=20 + day % 6, success=True,
            skill_ids=learning.active_skill_refs if cap.procedural_skills else (),
            memory_ids=learning.active_memory_ids[-1:] if cap.persistent_memory else (),
            domain_metrics={"offline_fixture": True},
        )
        competency_instances.append(instance)
        events.append("competency_instance", {
            "instance": instance.model_dump(mode="json"),
            "normalized": normalized_competency_metrics(instance),
        })

        game.update({
            "day": day,
            "gold": int(game["gold"]) + 100 + day,
            "farm_value": int(game["farm_value"]) + 125 + day,
            "mine_depth": min(120, int(game["mine_depth"]) + int(day % 4 == 0) * 5),
        })
        events.append("e1_day_completed", {
            "day": day, "game_state": game,
            "game_state_sha256": artifact_sha256(game),
            "learning_state_sha256": artifact_sha256(learning.model_dump(mode="json")),
        })

        if day in config.probe_schedule.days:
            manager = ProbeBranchManager(
                parent_run_id=run_id, game_state=game, learning_state=learning, events=events,
            )
            branch = manager.fork(branch_id=f"{run_id}-probe-d{day}", day=day)
            events.append("probe_started", {
                "branch_id": branch.identity.branch_id, "day": day,
                "decision_input": [probe.observable() for probe in config.probe_schedule.definitions],
            })
            raw_scores = {
                probe.probe_id: _offline_probe_score(probe, day, condition)
                for probe in config.probe_schedule.definitions
            }
            point = score_probe(day, config.probe_schedule.definitions, raw_scores)
            probe_points.append(point)
            for score in point.task_scores:
                events.append("probe_task_scored", {
                    "branch_id": branch.identity.branch_id, "day": day,
                    "score": score.model_dump(mode="json"),
                })
            events.append("probe_completed", {
                "branch_id": branch.identity.branch_id, "day": day,
                "aggregate_score": point.aggregate_score,
            })
            manager.discard(branch)

    aulc = standardized_probe_aulc(tuple(probe_points))
    score = {
        "run_id": run_id, "seed": seed, "condition": condition.value,
        "starting_game_state_sha256": starting_hash,
        "configuration_sha256": config_sha256,
        "probe_points": [point.model_dump(mode="json") for point in probe_points],
        "standardized_probe_aulc": aulc,
        "probe_slope": learning_curve_slope(tuple(probe_points)),
        "final_environment": game,
        "competency_instances": len(competency_instances),
        "input_tokens": total_input, "output_tokens": total_output,
        "cost_usd": total_cost,
        "context_budget": config.base_run.context.total_tokens,
        "memory_token_budget": config.memory_token_budget,
        "synthetic_contract_validation": True,
    }
    events.append("e1_run_scored", score)
    events.append("e1_run_completed", {"status": "completed"})
    return score


def build_offline_report(
    root: Path, config: E1StudyConfig, config_sha256: str,
) -> dict[str, Any]:
    runs: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    competency_rows: list[dict[str, Any]] = []
    inference_rows: list[dict[str, Any]] = []
    memory_rows: list[dict[str, Any]] = []
    skill_rows: list[dict[str, Any]] = []
    refinement_rows: list[dict[str, Any]] = []
    for event_log in sorted((root / "runs").glob("*/events.jsonl")):
        first = json.loads(event_log.read_text(encoding="utf-8").splitlines()[0])
        try:
            store = EventStore(event_log, first["run_id"])
            records = tuple(store.iter_records())
        except Exception as exc:
            failures.append({"run_id": str(first.get("run_id", "unknown")), "reason": str(exc)})
            continue
        starts = [item for item in records if item.event_type == "e1_run_started"]
        scored = [item for item in records if item.event_type == "e1_run_scored"]
        completed = [item for item in records if item.event_type == "e1_run_completed"]
        points = [item for item in records if item.event_type == "probe_completed"]
        branches = [item for item in records if item.event_type == "probe_branch_discarded"]
        contexts = [item for item in records if item.event_type == "decision_context_composed"]
        competencies = [item for item in records if item.event_type == "competency_instance"]
        inferences = [item for item in records if item.event_type == "e1_inference_accounted"]
        skills = [item for item in records if item.event_type == "skill_activated"]
        refinements = [item for item in records if item.event_type == "refinement_created"]
        if len(starts) != 1 or len(scored) != 1 or len(completed) != 1:
            failures.append({"run_id": store.run_id, "reason": "missing or duplicate run events"})
            continue
        if len(points) != len(config.probe_schedule.days) or len(branches) != len(points):
            failures.append({"run_id": store.run_id, "reason": "probe branches do not reconstruct"})
            continue
        if not all(bool(item.payload["parent_unchanged"]) for item in branches):
            failures.append({"run_id": store.run_id, "reason": "probe contamination detected"})
            continue
        if len(contexts) != config.horizon_days or any(
            int(item.payload["actual_tokens"]) > int(item.payload["maximum_tokens"])
            for item in contexts
        ):
            failures.append({"run_id": store.run_id, "reason": "context accounting failed"})
            continue
        score = dict(scored[0].payload)
        score["event_count"] = store.cursor().sequence
        score["event_last_hash"] = store.cursor().event_hash
        runs.append(score)
        for item in competencies:
            row = dict(item.payload["normalized"])
            instance = item.payload["instance"]
            row.update({
                "run_id": store.run_id, "condition": score["condition"],
                "seed": score["seed"], "day": instance["game_day"],
                "domain": instance["domain"],
            })
            competency_rows.append(row)
        for item in inferences:
            inference_rows.append({
                "run_id": store.run_id, "condition": score["condition"],
                "seed": score["seed"], **item.payload,
            })
        for item in contexts:
            memory_rows.append({
                "run_id": store.run_id, "condition": score["condition"],
                "seed": score["seed"], **item.payload,
            })
        for item in skills:
            skill_rows.append({
                "run_id": store.run_id, "condition": score["condition"],
                "seed": score["seed"], **item.payload,
            })
        for item in refinements:
            refinement_rows.append({
                "run_id": store.run_id, "condition": score["condition"],
                "seed": score["seed"], **item.payload,
            })

    expected = len(config.conditions) * len(config.seeds)
    by_key = {(int(run["seed"]), str(run["condition"])): run for run in runs}
    missing = [
        f"{seed}:{condition.value}" for seed in config.seeds for condition in config.conditions
        if (seed, condition.value) not in by_key
    ]
    matched_starts = all(
        len({by_key[(seed, condition.value)]["starting_game_state_sha256"]
             for condition in config.conditions}) == 1
        for seed in config.seeds if not missing
    )
    b_c_budgets_equal = all(
        by_key[(seed, "B")]["memory_token_budget"] == by_key[(seed, "C")]["memory_token_budget"]
        for seed in config.seeds if not missing
    )
    summaries = {
        condition.value: {
            "runs": len(selected := [run for run in runs if run["condition"] == condition.value]),
            "mean_probe_aulc": statistics.mean(float(run["standardized_probe_aulc"]) for run in selected) if selected else 0,
            "mean_cost_usd": statistics.mean(float(run["cost_usd"]) for run in selected) if selected else 0,
        }
        for condition in config.conditions
    }
    passed = (
        len(runs) == expected and not failures and not missing and matched_starts
        and b_c_budgets_equal
    )
    return {
        "schema_version": 1,
        "status": "passed" if passed else "failed",
        "study_id": config.study_id,
        "scope": "deterministic synthetic E1 contract validation; not an E1-Pilot result",
        "configuration_sha256": config_sha256,
        "runs": len(runs), "condition_summaries": summaries,
        "matched_starting_states": matched_starts,
        "b_c_memory_budgets_equal": b_c_budgets_equal,
        "raw_runs": runs, "competency_rows": competency_rows,
        "inference_rows": inference_rows, "memory_rows": memory_rows,
        "skill_rows": skill_rows, "refinement_rows": refinement_rows,
        "failure_analysis": {"event_failures": failures, "missing_runs": missing},
        "scientific_claims_permitted": False,
    }


def write_machine_outputs(root: Path, report: dict[str, Any]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    detail_keys = {
        "raw_runs", "competency_rows", "inference_rows", "memory_rows",
        "skill_rows", "refinement_rows",
    }
    summary = {key: value for key, value in report.items() if key not in detail_keys}
    (root / "study_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    (root / "failure_analysis.json").write_text(
        json.dumps(report["failure_analysis"], indent=2) + "\n", encoding="utf-8",
    )
    _write_csv(root / "run_metrics.csv", report["raw_runs"], (
        "run_id", "seed", "condition", "standardized_probe_aulc", "probe_slope",
        "input_tokens", "output_tokens", "cost_usd", "event_count", "event_last_hash",
    ))
    probe_rows = []
    for run in report["raw_runs"]:
        for point in run["probe_points"]:
            for score in point["task_scores"]:
                probe_rows.append({
                    "run_id": run["run_id"], "seed": run["seed"],
                    "condition": run["condition"], "day": point["day"],
                    "probe_id": score["probe_id"], "kind": score["kind"],
                    "normalized_score": score["normalized_score"],
                    "aggregate_score": point["aggregate_score"],
                })
    _write_csv(root / "probe_scores.csv", probe_rows, (
        "run_id", "seed", "condition", "day", "probe_id", "kind",
        "normalized_score", "aggregate_score",
    ))
    curve_rows = [
        {
            "run_id": run["run_id"], "seed": run["seed"], "condition": run["condition"],
            "day": point["day"], "aggregate_score": point["aggregate_score"],
            "standardized_probe_aulc": run["standardized_probe_aulc"],
            "probe_slope": run["probe_slope"],
        }
        for run in report["raw_runs"] for point in run["probe_points"]
    ]
    _write_csv(root / "learning_curves.csv", curve_rows, (
        "run_id", "seed", "condition", "day", "aggregate_score",
        "standardized_probe_aulc", "probe_slope",
    ))
    _write_csv(root / "competency_metrics.csv", report["competency_rows"], (
        "run_id", "seed", "condition", "day", "domain",
        "model_decisions_per_10_units", "primitive_actions_per_10_units",
        "game_minutes_per_10_units", "energy_per_10_units", "failures_per_10_units", "success",
    ))
    _write_csv(root / "inference_accounting.csv", report["inference_rows"], (
        "run_id", "seed", "condition", "day", "category", "calls",
        "input_tokens", "output_tokens", "cost_usd", "latency_ms",
    ))
    _write_csv(root / "memory_exposure.csv", report["memory_rows"], (
        "run_id", "seed", "condition", "day", "memory_exposure",
        "memory_token_limit", "memory_tokens", "omitted_items", "actual_tokens",
        "maximum_tokens", "recent_event_tokens", "skill_tokens",
    ))
    _write_csv(root / "skill_events.csv", report["skill_rows"], (
        "run_id", "seed", "condition", "day", "skill_ref",
    ))
    _write_csv(root / "refinement_events.csv", report["refinement_rows"], (
        "run_id", "seed", "condition", "day", "refinement_id",
        "inference_category", "source_experience_ids",
    ))


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: tuple[str, ...]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _offline_probe_score(probe: ProbeDefinition, day: int, condition: E1Condition) -> float:
    """Exercise curve machinery without encoding a preferred condition ordering."""
    del condition
    kind_offset = {
        ProbeKind.FAMILIAR: .02,
        ProbeKind.TRANSFER: 0,
        ProbeKind.CONFLICT: -.02,
        ProbeKind.NOVEL: -.04,
    }[probe.kind]
    return max(0, min(probe.maximum_score, .20 + day / 200 + kind_offset))
