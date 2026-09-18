"""Deterministic paired analysis declared by the M10 preregistration."""

from __future__ import annotations

import math
import random
import statistics

from .models import PairedResult, StudyPreregistration, StudyReport, StudyRunResult


def exact_two_sided_sign_test(effects: tuple[int, ...]) -> float:
    nonzero = tuple(effect for effect in effects if effect)
    if not nonzero:
        return 1.0
    positives = sum(effect > 0 for effect in nonzero)
    tail = min(positives, len(nonzero) - positives)
    probability = 2 * sum(math.comb(len(nonzero), k) for k in range(tail + 1)) / 2 ** len(nonzero)
    return min(1.0, probability)


def bootstrap_mean_ci(
    values: tuple[int, ...], *, samples: int, confidence: float, seed: int = 10_2026
) -> tuple[float, float]:
    if not values:
        raise ValueError("Bootstrap requires at least one paired effect")
    generator = random.Random(seed)
    means = sorted(
        statistics.mean(generator.choice(values) for _ in values)
        for _ in range(samples)
    )
    tail = (1 - confidence) / 2
    return (_quantile(means, tail), _quantile(means, 1 - tail))


def analyze_study(
    preregistration: StudyPreregistration,
    preregistration_sha256: str,
    runs: tuple[StudyRunResult, ...],
) -> StudyReport:
    control_name, treatment_name = (condition.name for condition in preregistration.conditions)
    exclusions = tuple(run for run in runs if run.excluded_reason is not None)
    eligible = {(run.seed, run.condition): run for run in runs if run.excluded_reason is None}
    duplicate_count = len(runs) - len({(run.seed, run.condition) for run in runs})
    missing: list[str] = []
    pairs: list[PairedResult] = []
    for seed in preregistration.seeds:
        control = eligible.get((seed, control_name))
        treatment = eligible.get((seed, treatment_name))
        if control is None or treatment is None:
            missing.append(str(seed))
            continue
        pairs.append(PairedResult(
            seed=seed,
            control_run_id=control.run_id,
            treatment_run_id=treatment.run_id,
            control_decisions=control.model_decisions,
            treatment_decisions=treatment.model_decisions,
            decision_reduction=control.model_decisions - treatment.model_decisions,
            control_outcome=control.mean_outcome_score,
            treatment_outcome=treatment.mean_outcome_score,
            outcome_difference=treatment.mean_outcome_score - control.mean_outcome_score,
        ))
    effects = tuple(pair.decision_reduction for pair in pairs)
    outcome_differences = tuple(pair.outcome_difference for pair in pairs)
    if effects:
        ci = bootstrap_mean_ci(
            effects,
            samples=preregistration.bootstrap_samples,
            confidence=preregistration.confidence_level,
        )
        effect_mean = statistics.mean(effects)
        outcome_mean = statistics.mean(outcome_differences)
        p_value = exact_two_sided_sign_test(effects)
    else:
        ci, effect_mean, outcome_mean, p_value = (0.0, 0.0), 0.0, 0.0, 1.0
    start_hashes_match = all(
        next(run for run in runs if run.run_id == pair.control_run_id).start_state_sha256
        == next(run for run in runs if run.run_id == pair.treatment_run_id).start_state_sha256
        for pair in pairs
    )
    noninferior = bool(pairs) and all(
        difference >= -preregistration.outcome_noninferiority_margin
        for difference in outcome_differences
    )
    passed = (
        len(pairs) == len(preregistration.seeds)
        and not exclusions and not missing and duplicate_count == 0
        and start_hashes_match and ci[0] > 0
        and p_value < preregistration.alpha and noninferior
    )
    return StudyReport(
        status="passed" if passed else "failed",
        study_id=preregistration.study_id,
        scope="preregistered paired proceduralization study in deterministic season replay",
        preregistration_sha256=preregistration_sha256,
        primary_metric=preregistration.primary_metric,
        paired_runs=len(pairs),
        decision_reduction_mean=effect_mean,
        decision_reduction_ci95=ci,
        exact_two_sided_sign_test_p=p_value,
        outcome_difference_mean=outcome_mean,
        outcome_noninferiority_passed=noninferior,
        all_start_states_matched=start_hashes_match,
        pairs=tuple(pairs),
        runs=runs,
        failure_analysis={
            "excluded_runs": len(exclusions),
            "exclusion_reasons": [run.excluded_reason for run in exclusions],
            "missing_pair_seeds": missing,
            "duplicate_condition_seed_runs": duplicate_count,
            "invalid_actions": sum(run.invalid_actions for run in runs),
            "project_failures": sum(3 - run.project_successes for run in runs),
            "nonpositive_effect_pairs": sum(pair.decision_reduction <= 0 for pair in pairs),
        },
        causal_scope=(
            "The randomized variable is procedural-skill availability. The causal estimate "
            "applies to the fixed deterministic policy and season-replay environment only."
        ),
    )


def _quantile(sorted_values: list[float], probability: float) -> float:
    position = (len(sorted_values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    weight = position - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight
