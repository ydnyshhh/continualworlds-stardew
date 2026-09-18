"""Deterministic 28-day simulator, project scorers, and raw-event report builder."""

from __future__ import annotations

import random
from collections.abc import Iterable

from prime_stardew.experiments.config import LearningConditionConfig
from prime_stardew.telemetry import EventStore

from .models import (
    ProjectAction, ProjectActionKind, ProjectFixture, ProjectKind, ProjectScore,
    SeasonDayResult, SeasonMetrics, SeasonReport, SeasonState,
)


ACTION_PRIMITIVES: dict[ProjectActionKind, int] = {
    ProjectActionKind.EARN_GOLD: 8,
    ProjectActionKind.PLANT_CAULIFLOWER: 14,
    ProjectActionKind.TEND_CAULIFLOWER: 12,
    ProjectActionKind.HARVEST_CAULIFLOWER: 12,
    ProjectActionKind.MINE: 30,
    ProjectActionKind.UPGRADE_PICKAXE: 16,
    ProjectActionKind.BUILD_COOP: 20,
    ProjectActionKind.BUY_CHICKEN: 12,
    ProjectActionKind.IDLE: 0,
}

ACTION_ENERGY: dict[ProjectActionKind, int] = {
    ProjectActionKind.EARN_GOLD: 25,
    ProjectActionKind.PLANT_CAULIFLOWER: 20,
    ProjectActionKind.TEND_CAULIFLOWER: 24,
    ProjectActionKind.HARVEST_CAULIFLOWER: 12,
    ProjectActionKind.MINE: 150,
    ProjectActionKind.UPGRADE_PICKAXE: 5,
    ProjectActionKind.BUILD_COOP: 5,
    ProjectActionKind.BUY_CHICKEN: 5,
    ProjectActionKind.IDLE: 0,
}


class SeasonSimulator:
    """Small deterministic world used to validate benchmark and recovery mechanics."""

    def __init__(
        self,
        seed: int,
        learning: LearningConditionConfig,
        state: SeasonState | None = None,
        *,
        procedural_skills_reduce_decisions: bool = False,
    ) -> None:
        self.seed = seed
        self.learning = learning
        self.state = state or SeasonState()
        self.procedural_skills_reduce_decisions = procedural_skills_reduce_decisions

    def step(
        self,
        actions: tuple[ProjectAction, ...],
        fixtures: tuple[ProjectFixture, ...],
    ) -> SeasonDayResult:
        if self.state.completed_days >= 28:
            raise RuntimeError("Season is already complete")
        day = self.state.completed_days + 1
        before = self.state
        values = before.model_dump()
        invalid = 0
        seen: set[ProjectActionKind] = set()
        for action in actions:
            if action.kind in seen and action.kind is not ProjectActionKind.IDLE:
                invalid += 1
                continue
            seen.add(action.kind)
            if not self._apply(day, action, values):
                invalid += 1
        action_kinds = {action.kind for action in actions}
        learned_watering_applies = (
            self.procedural_skills_reduce_decisions
            and self.learning.skills
            and before.skill_uses >= 3
            and ProjectActionKind.TEND_CAULIFLOWER in action_kinds
            and action_kinds <= {
                ProjectActionKind.EARN_GOLD, ProjectActionKind.TEND_CAULIFLOWER,
            }
        )
        values.update({
            "completed_days": day,
            "decisions": before.decisions + (0 if learned_watering_applies else 1),
            "primitive_actions": before.primitive_actions + sum(
                ACTION_PRIMITIVES[action.kind] for action in actions
            ),
            "invalid_actions": before.invalid_actions + invalid,
            "energy_spent": before.energy_spent + sum(ACTION_ENERGY[action.kind] for action in actions),
            "game_minutes_spent": before.game_minutes_spent + 60 * len(actions),
            "memory_reads": before.memory_reads + int(
                self.learning.persistent_memory and self.learning.retrieval
            ),
            "skill_uses": before.skill_uses + sum(
                action.kind is ProjectActionKind.TEND_CAULIFLOWER for action in actions
            ) * int(self.learning.skills),
        })
        self.state = SeasonState.model_validate(values)
        return SeasonDayResult(
            day=day, before=before, actions=actions, after=self.state,
            project_scores=score_projects(self.state, fixtures),
        )

    def _apply(self, day: int, action: ProjectAction, values: dict[str, object]) -> bool:
        kind = action.kind
        if kind is ProjectActionKind.EARN_GOLD:
            daily = 700 + random.Random(self.seed * 100 + day).randint(-40, 40)
            values["gold"] = int(values["gold"]) + daily
        elif kind is ProjectActionKind.PLANT_CAULIFLOWER:
            if day > 2 or int(values["gold"]) < 800 or int(values["cauliflower_planted"]):
                return False
            values["gold"] = int(values["gold"]) - 800
            values["cauliflower_planted"] = 12
        elif kind is ProjectActionKind.TEND_CAULIFLOWER:
            if not int(values["cauliflower_planted"]) or day > 12:
                return False
            values["cauliflower_tended_days"] = int(values["cauliflower_tended_days"]) + 1
        elif kind is ProjectActionKind.HARVEST_CAULIFLOWER:
            if day < 13 or int(values["cauliflower_tended_days"]) < 11:
                return False
            values["cauliflower_harvested"] = int(values["cauliflower_planted"])
            values["cauliflower_planted"] = 0
        elif kind is ProjectActionKind.MINE:
            values["mine_level"] = min(120, int(values["mine_level"]) + 8)
        elif kind is ProjectActionKind.UPGRADE_PICKAXE:
            if int(values["mine_level"]) < 40 or int(values["gold"]) < 2000:
                return False
            values["gold"] = int(values["gold"]) - 2000
            values["pickaxe_tier"] = max(1, int(values["pickaxe_tier"]))
        elif kind is ProjectActionKind.BUILD_COOP:
            buildings = tuple(values["buildings"])
            if "Coop" in buildings or int(values["gold"]) < 4000:
                return False
            values["gold"] = int(values["gold"]) - 4000
            values["buildings"] = (*buildings, "Coop")
        elif kind is ProjectActionKind.BUY_CHICKEN:
            if "Coop" not in tuple(values["buildings"]) or int(values["gold"]) < 800:
                return False
            values["gold"] = int(values["gold"]) - 800
            values["chickens"] = int(values["chickens"]) + 1
        return True


def benchmark_policy(day: int) -> tuple[ProjectAction, ...]:
    actions = [ProjectAction(kind=ProjectActionKind.EARN_GOLD)]
    if day == 1:
        actions.append(ProjectAction(kind=ProjectActionKind.PLANT_CAULIFLOWER))
    if 1 <= day <= 12:
        actions.append(ProjectAction(kind=ProjectActionKind.TEND_CAULIFLOWER))
    if day == 13:
        actions.append(ProjectAction(kind=ProjectActionKind.HARVEST_CAULIFLOWER))
    if day in {2, 4, 6, 8, 10}:
        actions.append(ProjectAction(kind=ProjectActionKind.MINE))
    if day == 15:
        actions.append(ProjectAction(kind=ProjectActionKind.UPGRADE_PICKAXE))
    if day == 18:
        actions.append(ProjectAction(kind=ProjectActionKind.BUILD_COOP))
    if day == 20:
        actions.append(ProjectAction(kind=ProjectActionKind.BUY_CHICKEN))
    return tuple(actions)


def score_projects(
    state: SeasonState,
    fixtures: tuple[ProjectFixture, ...],
    criterion_days: dict[str, int] | None = None,
) -> tuple[ProjectScore, ...]:
    scores = []
    for fixture in fixtures:
        if fixture.kind is ProjectKind.CAULIFLOWER_RESERVE:
            components = {
                "quantity": state.cauliflower_harvested >= fixture.target_quantity,
                "gold_reserve": state.gold >= fixture.minimum_gold,
            }
            progress = (
                min(1, state.cauliflower_harvested / max(1, fixture.target_quantity))
                + min(1, state.gold / max(1, fixture.minimum_gold))
            ) / 2
        elif fixture.kind is ProjectKind.MINE_PICKAXE:
            components = {
                "mine_depth": state.mine_level >= fixture.target_mine_level,
                "pickaxe_upgrade": state.pickaxe_tier >= fixture.target_pickaxe_tier,
            }
            progress = (
                min(1, state.mine_level / max(1, fixture.target_mine_level))
                + min(1, state.pickaxe_tier / max(1, fixture.target_pickaxe_tier))
            ) / 2
        else:
            components = {
                "building": fixture.required_building in state.buildings,
                "animal": state.chickens >= fixture.target_quantity,
            }
            progress = sum(components.values()) / len(components)
        success = all(components.values())
        scores.append(ProjectScore(
            fixture_id=fixture.fixture_id, success=success, progress=progress,
            criterion_day=(criterion_days or {}).get(fixture.fixture_id), components=components,
        ))
    return tuple(scores)


def append_day_events(store: EventStore, result: SeasonDayResult) -> None:
    prefix = f"season-day:{result.day}"
    store.append_idempotent(
        "season_day_started", {"day": result.day, "before": result.before.model_dump(mode="json")},
        idempotency_key=f"{prefix}:started",
    )
    for index, action in enumerate(result.actions, 1):
        store.append_idempotent(
            "project_action_completed",
            {"day": result.day, "sequence": index, "action": action.model_dump(mode="json")},
            idempotency_key=f"{prefix}:action:{index}",
        )
    store.append_idempotent(
        "season_day_completed",
        {
            "day": result.day, "after": result.after.model_dump(mode="json"),
            "project_scores": [score.model_dump(mode="json") for score in result.project_scores],
        },
        idempotency_key=f"{prefix}:completed",
    )


def build_report_from_events(
    store: EventStore,
    fixtures: tuple[ProjectFixture, ...],
    *,
    seed: int,
    condition: str,
) -> SeasonReport:
    records = tuple(store.iter_records())
    completed = [record for record in records if record.event_type == "season_day_completed"]
    days = [int(record.payload["day"]) for record in completed]
    if days != list(range(1, 29)):
        raise ValueError(f"Expected exactly one completion for days 1..28; got {days}")
    states = [SeasonState.model_validate(record.payload["after"]) for record in completed]
    criterion: dict[str, int] = {}
    curves: dict[str, list[float]] = {fixture.fixture_id: [] for fixture in fixtures}
    for day, state in enumerate(states, 1):
        for score in score_projects(state, fixtures):
            curves[score.fixture_id].append(score.progress)
            if score.success:
                criterion.setdefault(score.fixture_id, day)
    final = states[-1]
    final_scores = score_projects(final, fixtures, criterion)
    first_progress = sum(values[0] for values in curves.values()) / len(curves)
    final_progress = sum(values[-1] for values in curves.values()) / len(curves)
    model_events = [record for record in records if record.event_type == "model_call_completed"]
    cursor = store.cursor()
    metrics = SeasonMetrics(
        environment={
            "gold": final.gold, "cauliflower_harvested": final.cauliflower_harvested,
            "mine_level": final.mine_level, "pickaxe_tier": final.pickaxe_tier,
            "buildings": list(final.buildings), "chickens": final.chickens,
        },
        efficiency={
            "decisions": final.decisions, "primitive_actions": final.primitive_actions,
            "retries": final.retries, "invalid_actions": final.invalid_actions,
            "energy_spent": final.energy_spent, "game_minutes_spent": final.game_minutes_spent,
            "primitive_actions_per_day": final.primitive_actions / 28,
        },
        learning={
            "first_day_mean_progress": first_progress,
            "final_mean_progress": final_progress,
            "learning_gain": final_progress - first_progress,
            "observations_to_all_criteria": max(criterion.values()) if len(criterion) == len(fixtures) else None,
        },
        memory={"reads": final.memory_reads, "reads_per_day": final.memory_reads / 28},
        skills={"uses": final.skill_uses, "uses_per_day": final.skill_uses / 28},
        systems={
            "events": cursor.sequence,
            "model_calls": len(model_events),
            "input_tokens": sum(int(record.payload.get("input_tokens", 0)) for record in model_events),
            "output_tokens": sum(int(record.payload.get("output_tokens", 0)) for record in model_events),
            "cost_usd": sum(float(record.payload.get("cost_usd", 0)) for record in model_events),
            "checkpoints": sum(record.event_type == "benchmark_checkpoint_published" for record in records),
            "interruptions": sum(record.event_type == "benchmark_interrupted" for record in records),
        },
    )
    return SeasonReport(
        run_id=store.run_id, seed=seed, condition=condition, completed_days=28,
        project_scores=final_scores,
        progress_curves={key: tuple(values) for key, values in curves.items()},
        metrics=metrics, source_event_count=cursor.sequence,
        source_event_last_hash=cursor.event_hash or "0" * 64,
    )
