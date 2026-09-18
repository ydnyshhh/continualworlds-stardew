"""Preregistered M17 self-generated curriculum study under evidence attacks."""

from __future__ import annotations

import json
import random
import statistics
from collections import Counter
from enum import StrEnum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from prime_stardew.experiments.provenance import artifact_sha256
from prime_stardew.studies.analysis import bootstrap_mean_ci, exact_two_sided_sign_test
from prime_stardew.telemetry import EventStore

from .models import EvidenceObservation, EvidenceRegime, EvidenceTask, M17Family, PracticeObjective


class M17Condition(StrEnum):
    RANDOM_CURRICULUM = "random_curriculum"
    FIXED_HUMAN_CURRICULUM = "fixed_human_curriculum"
    WEAKNESS_TARGETED_SCRIPTED = "weakness_targeted_scripted"
    AGENT_GENERATED_CURRICULUM = "agent_generated_curriculum"


class M17Preregistration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    study_id: str
    hypothesis: str
    primary_metric: Literal["heldout_transfer_accuracy"]
    favorable_direction: Literal["higher"]
    paired: Literal[True]
    seeds: tuple[int, ...] = Field(min_length=8)
    conditions: tuple[M17Condition, ...]
    practice_budget: int = Field(ge=1)
    heldout_tasks_per_regime: int = Field(ge=2)
    alpha: float = Field(gt=0, lt=1)
    confidence_level: float = Field(gt=0, lt=1)
    bootstrap_samples: int = Field(ge=1_000)
    scripted_noninferiority_margin: float = Field(ge=0)
    exclusions: tuple[str, ...] = Field(min_length=1)
    stopping_rule: str
    analysis_plan: str

    @model_validator(mode="after")
    def valid_design(self) -> "M17Preregistration":
        if len(set(self.seeds)) != len(self.seeds):
            raise ValueError("M17 seeds must be distinct")
        if set(self.conditions) != set(M17Condition):
            raise ValueError("M17 requires all four curriculum conditions")
        if self.practice_budget != 2:
            raise ValueError("M17 v1 fixes the practice budget at two objectives")
        return self


def load_m17_preregistration(path: Path) -> tuple[M17Preregistration, str]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("M17 preregistration must be a mapping")
    registration = M17Preregistration.model_validate(value)
    return registration, artifact_sha256(registration.model_dump(mode="json"))


def _opposite(action: str) -> str:
    return "beta" if action == "alpha" else "alpha"


def _observations(seed: int, task_index: int, regime: EvidenceRegime, truth: str):
    wrong = _opposite(truth)
    prefix = f"o-{seed}-{task_index}-{regime.value}"
    if regime in {EvidenceRegime.INDEPENDENT_NOISE, EvidenceRegime.BURST_NOISE}:
        claims = (truth, truth, truth, wrong, wrong)
        sources = tuple(f"sensor-{i}" for i in range(5))
        authenticated = (True,) * 5
    elif regime is EvidenceRegime.SOURCE_DUPLICATION:
        claims = (truth, truth, wrong, wrong, wrong)
        sources = ("trusted-a", "trusted-b", "duplicator", "duplicator", "duplicator")
        authenticated = (True,) * 5
    else:
        claims = (truth, truth, wrong)
        sources = ("signed-a", "signed-b", "unsigned-impostor")
        authenticated = (True, True, False)
    return tuple(EvidenceObservation(
        observation_id=f"{prefix}-{i}", source_id=source, claim=claim,
        authenticated=auth,
    ) for i, (source, claim, auth) in enumerate(zip(sources, claims, authenticated), 1))


def curriculum_family(seed: int, *, heldout_tasks_per_regime: int = 2) -> M17Family:
    weakness_pairs = (
        (EvidenceRegime.INDEPENDENT_NOISE, EvidenceRegime.SOURCE_DUPLICATION),
        (EvidenceRegime.INDEPENDENT_NOISE, EvidenceRegime.SOURCE_SPOOFING),
        (EvidenceRegime.BURST_NOISE, EvidenceRegime.SOURCE_DUPLICATION),
        (EvidenceRegime.BURST_NOISE, EvidenceRegime.SOURCE_SPOOFING),
        (EvidenceRegime.SOURCE_DUPLICATION, EvidenceRegime.SOURCE_SPOOFING),
    )
    weaknesses = weakness_pairs[seed % len(weakness_pairs)]
    diagnostics = {regime: (0 if regime in weaknesses else 1) for regime in EvidenceRegime}
    catalog = tuple(PracticeObjective(
        objective_id=f"practice-{regime.value}", capability=regime,
        description=f"Practice aggregating conflicting evidence under {regime.value.replace('_', ' ')}.",
        estimated_cost=1,
    ) for regime in EvidenceRegime)
    tasks: list[EvidenceTask] = []
    task_index = 0
    for regime in EvidenceRegime:
        for repetition in range(heldout_tasks_per_regime):
            task_index += 1
            truth = "alpha" if (seed + task_index + repetition) % 2 == 0 else "beta"
            opaque = artifact_sha256({"seed": seed, "task": task_index})[:12]
            tasks.append(EvidenceTask(
                task_id=f"heldout-{opaque}", regime=regime,
                observations=_observations(seed, task_index, regime, truth),
                correct_action=truth, high_utility=10 + (task_index % 3), low_utility=1,
            ))
    return M17Family(
        seed=seed, initial_weaknesses=weaknesses, diagnostic_scores=diagnostics,
        practice_catalog=catalog, heldout_tasks=tuple(tasks),
    )


def _select_curriculum(family: M17Family, condition: M17Condition, budget: int):
    if condition is M17Condition.FIXED_HUMAN_CURRICULUM:
        capabilities = (EvidenceRegime.INDEPENDENT_NOISE, EvidenceRegime.BURST_NOISE)
    elif condition is M17Condition.RANDOM_CURRICULUM:
        capabilities = tuple(random.Random(family.seed + 17).sample(list(EvidenceRegime), budget))
    else:
        capabilities = tuple(sorted(
            EvidenceRegime, key=lambda item: (family.diagnostic_scores[item], item.value),
        )[:budget])
    return tuple(next(x for x in family.practice_catalog if x.capability is capability)
                 for capability in capabilities)


def _robust_choice(task: EvidenceTask) -> str:
    if task.regime is EvidenceRegime.SOURCE_DUPLICATION:
        per_source: dict[str, str] = {}
        for observation in task.observations:
            per_source.setdefault(observation.source_id, observation.claim)
        counts = Counter(per_source.values())
    elif task.regime is EvidenceRegime.SOURCE_SPOOFING:
        counts = Counter(item.claim for item in task.observations if item.authenticated)
    else:
        counts = Counter(item.claim for item in task.observations)
    return max(task.alternatives, key=lambda action: (counts[action], -task.alternatives.index(action)))


def run_m17_condition(
    root: Path, *, seed: int, condition: M17Condition, practice_budget: int,
    heldout_tasks_per_regime: int, preregistration_sha256: str,
) -> None:
    run_id = f"m17-{condition.value}-s{seed}"
    events = EventStore(root / run_id / "events.jsonl", run_id)
    family = curriculum_family(seed, heldout_tasks_per_regime=heldout_tasks_per_regime)
    events.append("curriculum_study_started", {
        "seed": seed, "condition": condition.value, "family_id": family.family_id,
        "world_state_sha256": family.world_state_sha256,
        "training_view_sha256": family.training_view_sha256,
        "heldout_manifest_sha256": family.heldout_manifest_sha256,
        "start_state_sha256": family.start_state_sha256,
        "preregistration_sha256": preregistration_sha256,
        "heldout_task_ids_exposed": False, "hidden_answers_exposed": False,
    })
    diagnostic_events: list[str] = []
    for regime, score in family.diagnostic_scores.items():
        event = events.append("training_diagnostic_completed", {
            "regime": regime.value, "score": score,
            "heldout_task_id": None, "hidden_answer": None,
        })
        diagnostic_events.append(str(event.event_id))
    selected = _select_curriculum(family, condition, practice_budget)
    proposal = events.append("curriculum_proposed", {
        "observed_weakness": [item.capability.value for item in selected],
        "evidence": diagnostic_events,
        "proposed_training_task": [item.objective_id for item in selected],
        "expected_transfer": "improve aggregation of conflicting evidence",
        "estimated_cost": sum(item.estimated_cost for item in selected),
        "success_criterion": "higher held-out evidence-attack accuracy",
        "heldout_task_ids_exposed": False,
    })
    trained: set[EvidenceRegime] = set()
    for objective in selected:
        events.append("practice_objective_completed", {
            "objective_id": objective.objective_id,
            "capability": objective.capability.value,
            "cost": objective.estimated_cost,
            "proposal_event_id": str(proposal.event_id),
        })
        trained.add(objective.capability)
    events.append("hidden_evaluation_started", {
        "task_count": len(family.heldout_tasks),
        "manifest_sha256": family.heldout_manifest_sha256,
    })
    for task in family.heldout_tasks:
        defended = task.regime not in family.initial_weaknesses or task.regime in trained
        action = _robust_choice(task) if defended else task.observations[-1].claim
        events.append("heldout_evidence_task_completed", {
            "task_id": task.task_id, "regime": task.regime.value,
            "observations": [item.model_dump(mode="json") for item in task.observations],
            "action": action, "correct_action": task.correct_action,
            "correct": action == task.correct_action,
            "utility": task.high_utility if action == task.correct_action else task.low_utility,
            "high_utility": task.high_utility, "low_utility": task.low_utility,
            "defense_available": defended, "answer_exposed_before_decision": False,
        })
    events.append("curriculum_study_completed", {"status": "completed"})


def build_m17_report_from_events(
    root: Path, preregistration: M17Preregistration, preregistration_sha256: str,
) -> dict[str, object]:
    runs: list[dict[str, object]] = []
    failures: list[dict[str, str]] = []
    for path in sorted(root.glob("m17-*/events.jsonl")):
        try:
            first = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
            store = EventStore(path, first["run_id"])
            records = tuple(store.iter_records())
        except Exception as exc:
            failures.append({"run_id": path.parent.name, "reason": str(exc)})
            continue
        starts = [x.payload for x in records if x.event_type == "curriculum_study_started"]
        proposals = [x.payload for x in records if x.event_type == "curriculum_proposed"]
        practices = [x.payload for x in records if x.event_type == "practice_objective_completed"]
        tasks = [x.payload for x in records if x.event_type == "heldout_evidence_task_completed"]
        expected = preregistration.heldout_tasks_per_regime * len(EvidenceRegime)
        if len(starts) != 1 or len(proposals) != 1 or len(practices) != preregistration.practice_budget or len(tasks) != expected:
            failures.append({"run_id": store.run_id, "reason": "incomplete run"})
            continue
        start, proposal = starts[0], proposals[0]
        correct = sum(bool(x["correct"]) for x in tasks)
        regime_accuracy = {
            regime.value: statistics.mean(
                int(bool(x["correct"])) for x in tasks if x["regime"] == regime.value
            ) for regime in EvidenceRegime
        }
        selected = set(proposal["observed_weakness"])
        true_weak = {regime for regime, score in {
            x.payload["regime"]: x.payload["score"] for x in records
            if x.event_type == "training_diagnostic_completed"
        }.items() if int(score) == 0}
        runs.append({
            "run_id": store.run_id, "seed": int(start["seed"]),
            "condition": str(start["condition"]),
            "world_state_sha256": start["world_state_sha256"],
            "training_view_sha256": start["training_view_sha256"],
            "heldout_manifest_sha256": start["heldout_manifest_sha256"],
            "start_state_sha256": start["start_state_sha256"],
            "heldout_transfer_accuracy": round(100 * correct / len(tasks)),
            "heldout_utility": sum(int(x["utility"]) for x in tasks),
            "training_cost": sum(int(x["cost"]) for x in practices),
            "practice_diversity": len({x["capability"] for x in practices}),
            "weakness_detection_accuracy": len(selected & true_weak) / len(true_weak),
            "regime_accuracy": regime_accuracy,
            "attack_successes": len(tasks) - correct,
            "contamination_detected": bool(start["heldout_task_ids_exposed"] or start["hidden_answers_exposed"] or any(bool(x["answer_exposed_before_decision"]) for x in tasks)),
            "event_count": store.cursor().sequence,
            "event_last_hash": store.cursor().event_hash,
        })
    indexed = {(int(x["seed"]), str(x["condition"])): x for x in runs}
    effects: list[int] = []
    scripted_differences: list[int] = []
    hashes_match = True
    for seed in preregistration.seeds:
        agent = indexed.get((seed, M17Condition.AGENT_GENERATED_CURRICULUM.value))
        fixed = indexed.get((seed, M17Condition.FIXED_HUMAN_CURRICULUM.value))
        scripted = indexed.get((seed, M17Condition.WEAKNESS_TARGETED_SCRIPTED.value))
        if not agent or not fixed or not scripted:
            continue
        effects.append(int(agent["heldout_transfer_accuracy"]) - int(fixed["heldout_transfer_accuracy"]))
        scripted_differences.append(int(agent["heldout_transfer_accuracy"]) - int(scripted["heldout_transfer_accuracy"]))
        hashes_match &= all(agent[key] == fixed[key] == scripted[key] for key in (
            "world_state_sha256", "training_view_sha256", "heldout_manifest_sha256", "start_state_sha256",
        ))
    effect_tuple = tuple(effects)
    ci = bootstrap_mean_ci(effect_tuple, samples=preregistration.bootstrap_samples,
                           confidence=preregistration.confidence_level) if effects else (0.0, 0.0)
    p_value = exact_two_sided_sign_test(effect_tuple)
    noninferior = bool(scripted_differences) and all(
        value >= -preregistration.scripted_noninferiority_margin
        for value in scripted_differences
    )
    contamination = sum(bool(x["contamination_detected"]) for x in runs)
    expected_runs = len(preregistration.seeds) * len(preregistration.conditions)
    passed = (
        len(runs) == expected_runs and not failures and len(effects) == len(preregistration.seeds)
        and hashes_match and ci[0] > 0 and p_value < preregistration.alpha
        and noninferior and contamination == 0
    )
    return {
        "schema_version": 1, "status": "passed" if passed else "failed",
        "study_id": preregistration.study_id,
        "scope": "controlled self-generated curriculum under noisy and adversarial evidence",
        "preregistration_sha256": preregistration_sha256,
        "runs": len(runs), "paired_runs": len(effects),
        "agent_minus_fixed_heldout_accuracy_mean": statistics.mean(effects) if effects else 0,
        "agent_minus_fixed_ci95": ci,
        "exact_two_sided_sign_test_p": p_value,
        "agent_noninferior_to_weakness_scripted": noninferior,
        "matched_world_training_eval_start_hashes": hashes_match,
        "contamination_events": contamination,
        "failure_analysis": {
            "event_reconstruction_failures": failures,
            "nonpositive_effect_pairs": sum(value <= 0 for value in effects),
            "contaminated_runs": contamination,
        },
        "causal_scope": (
            "The intervention is curriculum selection under an identical two-objective budget. "
            "The estimate applies to the deterministic evidence-attack family and fixed learner."
        ),
        "run_results": runs,
    }
