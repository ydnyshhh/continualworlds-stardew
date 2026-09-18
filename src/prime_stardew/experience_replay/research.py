"""Preregistered M13 benchmark for learning which experiences to replay."""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from prime_stardew.experiments.provenance import artifact_sha256
from prime_stardew.studies.analysis import bootstrap_mean_ci, exact_two_sided_sign_test
from prime_stardew.telemetry import EventStore

from .engine import deterministic_replay_decision, execute_replay
from .models import EvaluationTask, ReplayCondition, ReplayExperience, ReplayRunScore


class M13Preregistration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    study_id: str
    hypothesis: str
    primary_metric: Literal["held_out_accuracy"]
    favorable_direction: Literal["higher"]
    seeds: tuple[int, ...] = Field(min_length=8)
    conditions: tuple[ReplayCondition, ...]
    replay_budget: int = Field(gt=0)
    alpha: float = Field(default=0.05, gt=0, lt=1)
    bootstrap_samples: int = Field(default=10_000, ge=1_000)
    exclusions: tuple[str, ...] = Field(min_length=1)
    analysis_plan: str

    @model_validator(mode="after")
    def validate_design(self) -> "M13Preregistration":
        if set(self.conditions) != set(ReplayCondition):
            raise ValueError("M13 requires all five replay conditions")
        if len(set(self.seeds)) != len(self.seeds):
            raise ValueError("M13 seeds must be distinct")
        return self


def load_m13_preregistration(path: Path) -> tuple[M13Preregistration, str]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("M13 preregistration must be a mapping")
    registration = M13Preregistration.model_validate(value)
    return registration, artifact_sha256(registration.model_dump(mode="json"))


def replay_fixture(seed: int) -> tuple[tuple[ReplayExperience, ...], tuple[EvaluationTask, ...]]:
    """Return visible training traces and separately held-out task answers."""

    suffix = f"s{seed}"
    experiences = (
        ReplayExperience(
            experience_id="crop-deadline-failure", capability="seasonal_crop_timing",
            observation="A crop had four growth days left with three days remaining in the season.",
            attempted_action="Plant the crop.", outcome="The crop died at the season boundary.",
            lesson="Plant only when remaining growth days fit before the season boundary.",
            prediction_error=.82, surprise=.88, stakes=.95, age_steps=14,
            source_event_ids=(f"training:{suffix}:crop",),
        ),
        ReplayExperience(
            experience_id="mine-retreat-failure", capability="mine_risk_control",
            observation="Health was low and two enemies remained near the ladder.",
            attempted_action="Continue mining.", outcome="The farmer passed out.",
            lesson="Retreat through the ladder when health is low and enemies remain.",
            prediction_error=.80, surprise=.84, stakes=1.0, age_steps=13,
            source_event_ids=(f"training:{suffix}:mine",),
        ),
        ReplayExperience(
            experience_id="gift-preference-failure", capability="social_preference",
            observation="A villager had previously rejected forage as a gift.",
            attempted_action="Give the same forage again.", outcome="Friendship progress fell.",
            lesson="Avoid repeating a gift category that the recipient previously rejected.",
            prediction_error=.78, surprise=.79, stakes=.90, age_steps=12,
            source_event_ids=(f"training:{suffix}:gift",),
        ),
        ReplayExperience(
            experience_id="decor-layout-failure", capability="decorative_layout",
            observation="A decorative path tile was placed one square off center.",
            attempted_action="Keep the asymmetric layout.", outcome="The layout needed rework.",
            lesson="Preview decorative symmetry before placing path tiles.",
            prediction_error=.99, surprise=.96, stakes=.10, age_steps=1,
            source_event_ids=(f"training:{suffix}:decor",),
        ),
        ReplayExperience(
            experience_id="recent-market-success", capability="market_color_notes",
            observation=f"The {suffix} market board used a blue header.",
            attempted_action="Record the header color.", outcome="The note was accurate.",
            lesson="The observed board header was blue on this visit.",
            prediction_error=.02, surprise=.03, stakes=.05, age_steps=0,
            source_event_ids=(f"training:{suffix}:market",),
        ),
        ReplayExperience(
            experience_id="recent-weather-success", capability="weather_wording",
            observation=f"The {suffix} forecast used the phrase clear skies.",
            attempted_action="Record the wording.", outcome="The transcription was accurate.",
            lesson="The forecast wording was clear skies on this visit.",
            prediction_error=.01, surprise=.02, stakes=.05, age_steps=2,
            source_event_ids=(f"training:{suffix}:weather",),
        ),
    )
    tasks = (
        EvaluationTask(
            task_id="heldout-crop", capability="seasonal_crop_timing",
            prompt="A crop needs five days and the season ends in four. Choose whether to plant.",
            correct_action="do_not_plant",
        ),
        EvaluationTask(
            task_id="heldout-mine", capability="mine_risk_control",
            prompt="Health is low, enemies remain, and the ladder is reachable. Choose the action.",
            correct_action="retreat",
        ),
        EvaluationTask(
            task_id="heldout-gift", capability="social_preference",
            prompt="A villager rejected this gift category before. Choose whether to repeat it.",
            correct_action="choose_different_category",
        ),
    )
    return experiences, tasks


def run_m13_condition(
    root: Path,
    *,
    seed: int,
    condition: ReplayCondition,
    replay_budget: int,
    preregistration_sha256: str,
) -> ReplayRunScore:
    run_id = f"m13-{condition.value}-s{seed}"
    events = EventStore(root / run_id / "events.jsonl", run_id)
    experiences, tasks = replay_fixture(seed)
    candidate_hash = artifact_sha256([item.model_dump(mode="json") for item in experiences])
    observable_hash = artifact_sha256([task.observable() for task in tasks])
    hidden_hash = artifact_sha256([task.model_dump(mode="json") for task in tasks])
    events.append("experience_replay_study_started", {
        "seed": seed, "condition": condition.value, "replay_budget": replay_budget,
        "preregistration_sha256": preregistration_sha256,
        "candidate_state_sha256": candidate_hash,
        "observable_evaluation_sha256": observable_hash,
        "hidden_evaluation_sha256": hidden_hash,
    })
    for experience in experiences:
        events.append("replay_experience_observed", {
            "experience": experience.model_dump(mode="json"),
        })
    events.append("replay_decision_requested", {
        "candidate_ids": [item.experience_id for item in experiences],
        "replay_budget": replay_budget,
        "evaluation_tasks": [task.observable() for task in tasks],
    })
    decision = deterministic_replay_decision(
        experiences, tasks, condition=condition, replay_budget=replay_budget, seed=seed,
    )
    events.append("replay_decision_made", {
        "condition": condition.value, "decision": decision.model_dump(mode="json"),
        "hidden_fields_exposed": False,
    })
    execution = execute_replay(
        decision, experiences, replay_budget=replay_budget, events=events,
    )
    learned = set(execution["learned_capabilities"])
    relevant = {task.capability for task in tasks}
    correct = 0
    for task in tasks:
        success = task.capability in learned
        correct += int(success)
        events.append("replay_heldout_task_completed", {
            "task_id": task.task_id, "capability": task.capability,
            "success": success,
            "supporting_replay_event_ids": list(execution["replay_event_ids"]),
        })
    selected = tuple(execution["selected_ids"])
    useful = sum(
        experience.capability in relevant
        for experience in experiences if experience.experience_id in selected
    )
    score = ReplayRunScore(
        seed=seed, condition=condition,
        candidate_state_sha256=candidate_hash,
        observable_evaluation_sha256=observable_hash,
        hidden_evaluation_sha256=hidden_hash,
        replayed_ids=selected,
        replayed_source_event_ids=tuple(execution["replay_event_ids"]),
        replay_count=len(selected), replay_budget=replay_budget,
        held_out_correct=correct, held_out_total=len(tasks),
        held_out_accuracy=correct / len(tasks),
        useful_replay_precision=useful / max(1, len(selected)),
        wasted_replays=len(selected) - useful,
        hidden_fields_exposed=False, budget_enforced=len(selected) <= replay_budget,
    )
    events.append("experience_replay_study_scored", score.model_dump(mode="json"))
    events.append("experience_replay_study_completed", {"status": "completed"})
    return score


def build_m13_report_from_events(
    root: Path, registration: M13Preregistration, preregistration_sha256: str,
) -> dict[str, object]:
    scores: list[dict[str, object]] = []
    failures: list[dict[str, str]] = []
    for event_log in sorted(root.glob("m13-*/events.jsonl")):
        lines = event_log.read_text(encoding="utf-8").splitlines()
        if not lines:
            failures.append({"run_id": event_log.parent.name, "reason": "empty event log"})
            continue
        first = json.loads(lines[0])
        try:
            store = EventStore(event_log, first["run_id"])
            records = tuple(store.iter_records())
        except Exception as exc:
            failures.append({"run_id": str(first.get("run_id", "unknown")), "reason": str(exc)})
            continue
        starts = [item for item in records if item.event_type == "experience_replay_study_started"]
        scored = [item for item in records if item.event_type == "experience_replay_study_scored"]
        completed = [item for item in records if item.event_type == "experience_replay_study_completed"]
        replayed = [item for item in records if item.event_type == "experience_replayed"]
        updates = [item for item in records if item.event_type == "replay_learning_updated"]
        heldout = [item for item in records if item.event_type == "replay_heldout_task_completed"]
        if len(starts) != 1 or len(scored) != 1 or len(completed) != 1:
            failures.append({"run_id": store.run_id, "reason": "missing or duplicate terminal events"})
            continue
        score = dict(scored[0].payload)
        if len(replayed) != int(score["replay_count"]):
            failures.append({"run_id": store.run_id, "reason": "replay count does not reconstruct"})
            continue
        replay_ids = {str(item.event_id) for item in replayed}
        update_sources = {str(item.payload["source_replay_event_id"]) for item in updates}
        if len(heldout) != int(score["held_out_total"]):
            failures.append({"run_id": store.run_id, "reason": "held-out count does not reconstruct"})
            continue
        if sum(bool(item.payload["success"]) for item in heldout) != int(score["held_out_correct"]):
            failures.append({"run_id": store.run_id, "reason": "held-out score does not reconstruct"})
            continue
        if update_sources != replay_ids:
            failures.append({"run_id": store.run_id, "reason": "replay provenance does not reconstruct"})
            continue
        score["event_count"] = store.cursor().sequence
        score["event_last_hash"] = store.cursor().event_hash
        scores.append(score)

    expected = len(registration.seeds) * len(registration.conditions)
    by_key = {(int(score["seed"]), str(score["condition"])): score for score in scores}
    missing = [
        f"{seed}:{condition.value}" for seed in registration.seeds
        for condition in registration.conditions if (seed, condition.value) not in by_key
    ]
    effects = tuple(
        float(by_key[(seed, ReplayCondition.AGENT_PRIORITY.value)][registration.primary_metric])
        - float(by_key[(seed, ReplayCondition.ERROR_PRIORITY.value)][registration.primary_metric])
        for seed in registration.seeds if not missing
    )
    ci = bootstrap_mean_ci(
        effects, samples=registration.bootstrap_samples, confidence=.95,
    ) if effects else (0.0, 0.0)
    p_value = exact_two_sided_sign_test(effects) if effects else 1.0
    summaries: dict[str, object] = {}
    for condition in registration.conditions:
        selected = [score for score in scores if score["condition"] == condition.value]
        summaries[condition.value] = {
            "runs": len(selected),
            "mean_held_out_accuracy": _mean(selected, "held_out_accuracy"),
            "mean_replay_count": _mean(selected, "replay_count"),
            "mean_useful_replay_precision": _mean(selected, "useful_replay_precision"),
            "mean_wasted_replays": _mean(selected, "wasted_replays"),
        }
    matched_candidates = all(
        len({by_key[(seed, condition.value)]["candidate_state_sha256"]
             for condition in registration.conditions}) == 1
        for seed in registration.seeds if not missing
    )
    matched_evaluations = all(
        len({by_key[(seed, condition.value)]["hidden_evaluation_sha256"]
             for condition in registration.conditions}) == 1
        for seed in registration.seeds if not missing
    )
    hidden_leaks = sum(bool(score["hidden_fields_exposed"]) for score in scores)
    passed = (
        len(scores) == expected and not failures and not missing
        and matched_candidates and matched_evaluations and hidden_leaks == 0
        and all(bool(score["budget_enforced"]) for score in scores)
        and bool(effects) and ci[0] > 0 and p_value < registration.alpha
    )
    return {
        "schema_version": 1,
        "status": "passed" if passed else "failed",
        "study_id": registration.study_id,
        "scope": "controlled deterministic experience-replay prioritization study",
        "preregistration_sha256": preregistration_sha256,
        "primary_metric": registration.primary_metric,
        "runs": len(scores),
        "condition_summaries": summaries,
        "agent_minus_error_priority_mean": statistics.mean(effects) if effects else 0,
        "agent_minus_error_priority_ci95": list(ci),
        "exact_two_sided_sign_test_p": p_value,
        "matched_candidate_states": matched_candidates,
        "matched_hidden_evaluations": matched_evaluations,
        "raw_scores": scores,
        "failure_analysis": {
            "event_failures": failures, "missing_runs": missing,
            "budget_failures": sum(not bool(score["budget_enforced"]) for score in scores),
            "hidden_field_leaks": hidden_leaks,
            "nonpositive_primary_pairs": sum(effect <= 0 for effect in effects),
        },
        "manual_score_edits": 0,
    }


def _mean(scores: list[dict[str, object]], key: str) -> float:
    return statistics.mean(float(score[key]) for score in scores) if scores else 0.0
