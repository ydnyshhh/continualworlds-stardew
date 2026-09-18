"""Recurring-competency normalization and aggregation."""

from __future__ import annotations

import statistics

from .models import CompetencyDomain, CompetencyInstance


def normalized_competency_metrics(instance: CompetencyInstance) -> dict[str, float]:
    scale = 10 / instance.work_units
    return {
        "model_decisions_per_10_units": instance.model_decisions * scale,
        "primitive_actions_per_10_units": instance.primitive_actions * scale,
        "game_minutes_per_10_units": instance.game_minutes * scale,
        "energy_per_10_units": instance.energy_used * scale,
        "failures_per_10_units": instance.failures * scale,
        "success": float(instance.success),
    }


def competency_curve(
    instances: tuple[CompetencyInstance, ...],
    domain: CompetencyDomain,
    metric: str,
) -> tuple[tuple[int, float], ...]:
    selected = sorted((item for item in instances if item.domain is domain), key=lambda x: x.game_day)
    return tuple(
        (item.game_day, normalized_competency_metrics(item)[metric]) for item in selected
    )


def within_period_gain(
    instances: tuple[CompetencyInstance, ...],
    domain: CompetencyDomain,
    metric: str,
    *,
    early_end: int = 7,
    late_start: int = 22,
) -> float | None:
    curve = competency_curve(instances, domain, metric)
    early = [value for day, value in curve if day <= early_end]
    late = [value for day, value in curve if day >= late_start]
    if not early or not late:
        return None
    return statistics.mean(late) - statistics.mean(early)

