"""Live broad-objective day loop using only guarded StarDojo actions."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from prime_stardew.env.client import ActionResult
from prime_stardew.env.lifecycle import EnvironmentController, MovementResult
from prime_stardew.experiments.runner import ExperimentRunner
from prime_stardew.experiments.lifecycle import RunPhase

from .prime_adapter import PrimeHarnessAdapter


E1_LIVE_ACTIONS = (
    "move", "move_relative", "move_step", "turn", "choose_item", "use", "interact",
    "take_from_chest", "put_to_chest",
)


class LiveDaySummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    elapsed_day: int = Field(ge=1, le=119)
    game_date_before: dict[str, Any]
    game_date_after: dict[str, Any]
    model_decisions: int = Field(ge=0)
    primitive_actions: int = Field(ge=1)
    failed_actions: int = Field(ge=0)


class E1LiveResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    days: tuple[LiveDaySummary, ...]
    total_model_decisions: int = Field(ge=0)
    total_primitive_actions: int = Field(ge=0)
    total_failed_actions: int = Field(ge=0)


def run_live_days(
    harness: PrimeHarnessAdapter,
    controller: EnvironmentController,
    *,
    days: int,
    max_decisions_per_day: int = 8,
    on_day_complete: Callable[[LiveDaySummary], None] | None = None,
    on_learning_boundary: Callable[[int], None] | None = None,
) -> E1LiveResult:
    if days < 1 or days > 119:
        raise ValueError("Live E1 horizon must be between 1 and 119 days")
    if max_decisions_per_day < 1:
        raise ValueError("At least one decision per day is required")
    runner = harness.session.runner
    summaries: list[LiveDaySummary] = []
    for elapsed_day in range(1, days + 1):
        start = controller.observe(radius=2)
        runner.assert_action_allowed(game_date=start.game_state.date)
        runner.events.append("e1_live_day_started", {
            "elapsed_day": elapsed_day,
            "game_date": start.game_state.date.model_dump(mode="json"),
            "observation": start.model_dump(mode="json"),
        })
        decision_count = primitive_count = failed_count = 0
        recent_events: list[dict[str, str]] = []
        for _ in range(max_decisions_per_day):
            observation = controller.observe(radius=2)
            allowed_actions = E1_LIVE_ACTIONS + (
                ("execute_skill",) if harness.export_learning_state().active_skill_refs else ()
            )
            decision = harness.decide({
                "state": observation.model_dump(mode="json"),
                "allowed_actions": allowed_actions,
                "game_day": observation.game_state.date.ordinal(),
                "season": observation.game_state.date.season,
                "recent_events": recent_events[-8:],
            })
            decision_count += 1
            actions = decision.get("actions", [])
            if not actions:
                break
            for action in actions:
                expanded = (
                    harness.expand_skill(tuple(action.get("arguments", ())))
                    if action.get("name") == "execute_skill" else None
                )
                primitive_actions = (
                    [item.model_dump(mode="json") for item in expanded]
                    if expanded is not None else [action]
                )
                if expanded is not None:
                    skill_ref = str(action["arguments"][0])
                    skill_use_id = (
                        f"d{elapsed_day}-m{decision_count}-skill-{primitive_count + 1}"
                    )
                    runner.events.append("e1_skill_invoked", {
                        "game_day": elapsed_day,
                        "skill_ref": skill_ref,
                        "primitive_actions": len(primitive_actions),
                    })
                skill_succeeded = True
                for primitive in primitive_actions:
                    primitive_count += 1
                    outcome = execute_guarded_action(
                        controller, runner, primitive,
                        action_id=f"d{elapsed_day}-m{decision_count}-a{primitive_count}",
                    )
                    if not outcome["succeeded"]:
                        failed_count += 1
                        skill_succeeded = False
                    recent_events.append({
                        "id": str(outcome["action_id"]),
                        "text": str(outcome),
                    })
                if expanded is not None:
                    harness.record_skill_use(
                        skill_ref, use_id=skill_use_id,
                        success=skill_succeeded,
                        primitive_actions=len(primitive_actions),
                    )
        pre_sleep = controller.observe(radius=2)
        runner.events.append("e1_live_day_pre_sleep", {
            "elapsed_day": elapsed_day,
            "observation": pre_sleep.model_dump(mode="json"),
        })
        runner.assert_action_allowed(count=1)
        day_result = controller.finish_day()
        primitive_count += 1
        _record_action_result(
            runner, action_id=f"d{elapsed_day}-sleep", name="sleep", arguments=(),
            result=day_result.sleep_action,
        )
        if on_learning_boundary is not None:
            on_learning_boundary(elapsed_day)
        harness.end_day(game_day=elapsed_day)
        summary = LiveDaySummary(
            elapsed_day=elapsed_day,
            game_date_before=day_result.before.model_dump(mode="json"),
            game_date_after=day_result.after.model_dump(mode="json"),
            model_decisions=decision_count,
            primitive_actions=primitive_count,
            failed_actions=failed_count,
        )
        summaries.append(summary)
        runner.events.append("e1_live_day_completed", summary.model_dump(mode="json"))
        runner.transition(
            RunPhase.DAY_COMPLETE,
            reason=f"E1 live day {elapsed_day} complete",
            operation_id=f"e1-day-{elapsed_day}-complete",
        )
        if on_day_complete is not None:
            on_day_complete(summary)
        if elapsed_day < days:
            runner.transition(
                RunPhase.RUNNING,
                reason=f"continue E1 after day {elapsed_day}",
                operation_id=f"e1-day-{elapsed_day + 1}-start",
            )
    return E1LiveResult(
        run_id=runner.run_id,
        days=tuple(summaries),
        total_model_decisions=sum(item.model_decisions for item in summaries),
        total_primitive_actions=sum(item.primitive_actions for item in summaries),
        total_failed_actions=sum(item.failed_actions for item in summaries),
    )


def execute_guarded_action(
    controller: EnvironmentController,
    runner: ExperimentRunner,
    action: dict[str, Any],
    *,
    action_id: str,
    task_id: str = "e1-broad-objective",
) -> dict[str, Any]:
    name = str(action.get("name", ""))
    arguments = tuple(action.get("arguments", ()))
    runner.assert_action_allowed(count=1)
    started = {
        "task_id": task_id, "action_id": action_id,
        "name": name, "arguments": list(arguments),
    }
    runner.events.append("action_started", started)
    try:
        result = _dispatch(controller, name, arguments)
    except Exception as exc:
        payload = {
            **started, "succeeded": False, "request_id": None, "privileged": False,
            "error_type": type(exc).__name__, "error": str(exc),
        }
        runner.events.append("action_completed", payload)
        return payload
    return _record_action_result(
        runner, action_id=action_id, name=name, arguments=arguments, result=result,
        started=False, task_id=task_id,
    )


def _dispatch(
    controller: EnvironmentController, name: str, arguments: tuple[object, ...],
) -> ActionResult | MovementResult:
    if name == "move":
        return controller.move(int(arguments[0]), int(arguments[1]))
    if name == "move_relative":
        return controller.move_relative(int(arguments[0]), int(arguments[1]))
    if name == "move_step":
        return controller.move_step(int(arguments[0]))
    methods = {
        "turn": controller.client.turn,
        "choose_item": controller.client.choose_item,
        "use": controller.client.use,
        "interact": controller.client.interact,
        "take_from_chest": controller.client.take_from_chest,
        "put_to_chest": controller.client.put_to_chest,
    }
    if name not in methods:
        raise ValueError(f"Action {name!r} is outside the E1 live allowlist")
    return methods[name](*arguments)


def _record_action_result(
    runner: ExperimentRunner,
    *,
    action_id: str,
    name: str,
    arguments: tuple[object, ...],
    result: ActionResult | MovementResult,
    started: bool = True,
    task_id: str = "e1-broad-objective",
) -> dict[str, Any]:
    action_result = result.action if isinstance(result, MovementResult) else result
    base = {
        "task_id": task_id, "action_id": action_id,
        "name": name, "arguments": list(arguments),
    }
    if started:
        runner.events.append("action_started", base)
    payload = {
        **base, "succeeded": True, "request_id": action_result.request_id,
        "privileged": False,
    }
    runner.events.append("action_completed", payload)
    return payload
