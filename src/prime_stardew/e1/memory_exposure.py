"""Condition-faithful memory exposure over the existing MemoryStore."""

from __future__ import annotations

from prime_stardew.memory import MemoryQuery, MemoryRecord, MemoryStore, estimate_memory_tokens

from .models import ConditionManifest, MemoryExposurePolicy


def select_memory_context(
    store: MemoryStore,
    manifest: ConditionManifest,
    *,
    query: str,
    token_budget: int,
    game_day: int | None = None,
    season: str | None = None,
) -> tuple[MemoryRecord, ...]:
    policy = manifest.memory_exposure
    if policy is MemoryExposurePolicy.NONE:
        return ()
    if policy is MemoryExposurePolicy.RELEVANCE:
        return store.retrieve(MemoryQuery(
            text=query, token_budget=token_budget, game_day=game_day, season=season,
        )).selected
    if policy is not MemoryExposurePolicy.CHRONOLOGICAL:  # pragma: no cover
        raise ValueError(f"Unsupported memory exposure policy: {policy}")
    selected: list[MemoryRecord] = []
    used = 0
    eligible = (
        record for record in store.records()
        if _eligible_for_chronological_exposure(record, game_day=game_day, season=season)
    )
    for record in sorted(
        eligible,
        key=lambda item: (item.created_at, item.memory_id), reverse=True,
    ):
        tokens = estimate_memory_tokens(record)
        if used + tokens <= token_budget:
            selected.append(record)
            used += tokens
    return tuple(selected)


def exposed_memory_tokens(records: tuple[MemoryRecord, ...]) -> int:
    return sum(estimate_memory_tokens(record) for record in records)


def _eligible_for_chronological_exposure(
    record: MemoryRecord, *, game_day: int | None, season: str | None,
) -> bool:
    """Apply the same temporal/season eligibility rules used by relevance retrieval."""
    if season is not None and record.season not in {None, season}:
        return False
    if game_day is not None:
        if record.valid_from_day is not None and game_day < record.valid_from_day:
            return False
        if record.valid_to_day is not None and game_day > record.valid_to_day:
            return False
    return True
