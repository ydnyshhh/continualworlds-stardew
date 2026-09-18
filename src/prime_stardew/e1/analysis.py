"""Probe normalization and learning-curve analysis."""

from __future__ import annotations

import statistics

from .models import ProbeDefinition, ProbePoint, ProbeTaskScore


def score_probe(
    day: int,
    definitions: tuple[ProbeDefinition, ...],
    raw_scores: dict[str, float],
) -> ProbePoint:
    expected = {probe.probe_id for probe in definitions}
    if set(raw_scores) != expected:
        raise ValueError(
            f"Probe results must cover every definition exactly once; "
            f"missing={sorted(expected - set(raw_scores))}, "
            f"unknown={sorted(set(raw_scores) - expected)}"
        )
    task_scores = []
    for probe in definitions:
        raw = raw_scores[probe.probe_id]
        if not 0 <= raw <= probe.maximum_score:
            raise ValueError(f"Probe score outside range for {probe.probe_id}")
        task_scores.append(ProbeTaskScore(
            probe_id=probe.probe_id, kind=probe.kind,
            raw_score=raw, maximum_score=probe.maximum_score,
            normalized_score=raw / probe.maximum_score, weight=probe.weight,
        ))
    total_weight = sum(item.weight for item in task_scores)
    aggregate = sum(item.normalized_score * item.weight for item in task_scores) / total_weight
    return ProbePoint(day=day, task_scores=tuple(task_scores), aggregate_score=aggregate)


def standardized_probe_aulc(
    points: tuple[ProbePoint, ...],
    *,
    normalize_by_horizon: bool = True,
) -> float:
    ordered = tuple(sorted(points, key=lambda point: point.day))
    if len(ordered) < 2:
        raise ValueError("AULC requires at least two probe points")
    if len({point.day for point in ordered}) != len(ordered):
        raise ValueError("Probe days must be unique")
    area = sum(
        (right.day - left.day) * (left.aggregate_score + right.aggregate_score) / 2
        for left, right in zip(ordered, ordered[1:])
    )
    if not normalize_by_horizon:
        return area
    span = ordered[-1].day - ordered[0].day
    return area / span if span else 0


def learning_curve_slope(points: tuple[ProbePoint, ...]) -> float:
    if len(points) < 2:
        raise ValueError("Learning-curve slope requires at least two points")
    xs = [float(point.day) for point in points]
    ys = [point.aggregate_score for point in points]
    x_mean = statistics.mean(xs)
    y_mean = statistics.mean(ys)
    denominator = sum((x - x_mean) ** 2 for x in xs)
    return sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys)) / denominator

