"""Evaluator-side recurring-activity segmentation from authenticated live events."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Iterable

from prime_stardew.telemetry import EventRecord

from .models import CompetencyDomain, CompetencyInstance


def segment_live_competencies(records: Iterable[EventRecord]) -> tuple[CompetencyInstance, ...]:
    """Derive competency instances without asking the agent to name benchmark tasks."""
    by_day: dict[int, dict[str, Any]] = defaultdict(lambda: {
        "actions": [], "decisions": [], "start": None, "pre_sleep": None,
    })
    current_day: int | None = None
    for record in records:
        if record.event_type == "e1_live_day_started":
            current_day = int(record.payload["elapsed_day"])
            by_day[current_day]["start"] = record.payload["observation"]
        elif record.event_type == "e1_live_day_pre_sleep":
            day = int(record.payload["elapsed_day"])
            by_day[day]["pre_sleep"] = record.payload["observation"]
        elif current_day is not None and record.event_type == "agent_decision":
            by_day[current_day]["decisions"].append(record.payload["decision"])
        elif current_day is not None and record.event_type == "action_completed":
            if record.payload.get("name") != "sleep":
                by_day[current_day]["actions"].append(record.payload)
        elif record.event_type == "e1_live_day_completed":
            current_day = None

    instances: list[CompetencyInstance] = []
    for day, data in sorted(by_day.items()):
        start = data["start"]
        end = data["pre_sleep"]
        if not isinstance(start, dict) or not isinstance(end, dict):
            continue
        location = _player_value(start, "location", "Location", "")
        actions: list[dict[str, Any]] = data["actions"]
        decisions: list[dict[str, Any]] = data["decisions"]
        action_domains = [_infer_domain(str(location), str(item.get("name", ""))) for item in actions]
        decision_domains = [
            _infer_domain(str(location), str(action[0].get("name", "")))
            for decision in decisions
            if isinstance((action := decision.get("actions")), list) and action
        ]
        action_counts = Counter(action_domains)
        decision_counts = Counter(decision_domains)
        total_actions = sum(action_counts.values())
        if total_actions == 0:
            continue
        elapsed_minutes = max(0, _game_minutes(end) - _game_minutes(start))
        energy_used = max(
            0.0,
            float(_player_value(start, "stamina", "Stamina", 0))
            - float(_player_value(end, "stamina", "Stamina", 0)),
        )
        for domain, count in sorted(action_counts.items(), key=lambda item: item[0].value):
            share = count / total_actions
            failures = sum(
                not bool(action.get("succeeded"))
                for action, action_domain in zip(actions, action_domains, strict=True)
                if action_domain is domain
            )
            instances.append(CompetencyInstance(
                instance_id=f"live-day-{day}-{domain.value}",
                domain=domain,
                game_day=day,
                work_units=float(count),
                model_decisions=decision_counts[domain],
                primitive_actions=count,
                game_minutes=round(elapsed_minutes * share),
                energy_used=energy_used * share,
                success=failures == 0,
                failures=failures,
                domain_metrics={
                    "segmentation": "evaluator_event_heuristic_v1",
                    "location": str(location),
                    "action_family": ",".join(sorted({
                        str(action.get("name", ""))
                        for action, action_domain in zip(actions, action_domains, strict=True)
                        if action_domain is domain
                    })),
                },
            ))
    return tuple(instances)


def _infer_domain(location: str, action: str) -> CompetencyDomain:
    normalized = location.lower()
    if "mine" in normalized or "skull" in normalized or "volcano" in normalized:
        return CompetencyDomain.MINING
    if action in {"take_from_chest", "put_to_chest"}:
        return CompetencyDomain.INVESTMENT
    if normalized not in {"farm", "farmhouse", "greenhouse"} and action == "interact":
        return CompetencyDomain.SHOPPING
    return CompetencyDomain.FARM_MAINTENANCE


def _player(observation: dict[str, Any]) -> dict[str, Any]:
    value = observation.get("player", observation.get("Player", {}))
    return value if isinstance(value, dict) else {}


def _player_value(
    observation: dict[str, Any], field: str, alias: str, default: object,
) -> object:
    player = _player(observation)
    return player.get(field, player.get(alias, default))


def _game_state(observation: dict[str, Any]) -> dict[str, Any]:
    value = observation.get("game_state", observation.get("GameState", {}))
    return value if isinstance(value, dict) else {}


def _game_minutes(observation: dict[str, Any]) -> int:
    raw = int(_game_state(observation).get("time", _game_state(observation).get("Time", 600)))
    hours, minutes = divmod(raw, 100)
    return hours * 60 + minutes
