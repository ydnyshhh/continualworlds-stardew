"""Selection, validation, and execution for experience replay."""

from __future__ import annotations

import json
import random

from prime_stardew.agent.models import ProviderRequest
from prime_stardew.agent.provider import AgentProvider
from prime_stardew.experiments.runner import ExperimentRunner, ModelCallUsage
from prime_stardew.telemetry import EventStore

from .models import EvaluationTask, ReplayAction, ReplayCondition, ReplayDecision, ReplayExperience


class ReplayValidationError(ValueError):
    pass


def validate_replay_decision(
    decision: ReplayDecision,
    experiences: tuple[ReplayExperience, ...],
    *,
    replay_budget: int,
) -> tuple[str, ...]:
    expected = {experience.experience_id for experience in experiences}
    actual = {action.experience_id for action in decision.actions}
    if actual != expected:
        raise ReplayValidationError(
            f"Replay decision must cover every candidate exactly once; "
            f"unknown={sorted(actual - expected)}, missing={sorted(expected - actual)}"
        )
    selected = tuple(action.experience_id for action in decision.actions if action.replay)
    if len(selected) > replay_budget:
        raise ReplayValidationError(
            f"Replay decision selected {len(selected)} experiences, exceeding budget {replay_budget}"
        )
    return selected


def deterministic_replay_decision(
    experiences: tuple[ReplayExperience, ...],
    tasks: tuple[EvaluationTask, ...],
    *,
    condition: ReplayCondition,
    replay_budget: int,
    seed: int,
) -> ReplayDecision:
    task_capabilities = {task.capability for task in tasks}
    ordered = list(experiences)
    if condition is ReplayCondition.NO_REPLAY:
        selected: set[str] = set()
    elif condition is ReplayCondition.RECENCY:
        ordered.sort(key=lambda item: (item.age_steps, item.experience_id))
        selected = {item.experience_id for item in ordered[:replay_budget]}
    elif condition is ReplayCondition.RANDOM_FIXED_SEED:
        ordered.sort(key=lambda item: item.experience_id)
        random.Random(seed).shuffle(ordered)
        selected = {item.experience_id for item in ordered[:replay_budget]}
    elif condition is ReplayCondition.ERROR_PRIORITY:
        ordered.sort(
            key=lambda item: (item.prediction_error, item.surprise, -item.age_steps), reverse=True,
        )
        selected = {item.experience_id for item in ordered[:replay_budget]}
    elif condition is ReplayCondition.AGENT_PRIORITY:
        ordered.sort(key=lambda item: (
            item.capability in task_capabilities,
            item.prediction_error * 0.45 + item.surprise * 0.25 + item.stakes * 0.30,
            -item.age_steps,
            item.experience_id,
        ), reverse=True)
        selected = {item.experience_id for item in ordered[:replay_budget]}
    else:  # pragma: no cover - enum exhaustiveness
        raise ReplayValidationError(f"Unsupported replay condition: {condition}")
    actions = tuple(ReplayAction(
        experience_id=item.experience_id,
        replay=item.experience_id in selected,
        predicted_transfer_value=(
            0.45 * item.prediction_error + 0.25 * item.surprise + 0.30 * item.stakes
        ) if item.capability in task_capabilities else 0.0,
    ) for item in experiences)
    return ReplayDecision(
        actions=actions,
        reasoning_summary=f"Apply {condition.value} under a {replay_budget}-experience budget.",
    )


def execute_replay(
    decision: ReplayDecision,
    experiences: tuple[ReplayExperience, ...],
    *,
    replay_budget: int,
    events: EventStore,
) -> dict[str, tuple[str, ...]]:
    selected = validate_replay_decision(decision, experiences, replay_budget=replay_budget)
    by_id = {item.experience_id: item for item in experiences}
    learned: dict[str, list[str]] = {}
    replay_event_ids: list[str] = []
    for experience_id in selected:
        experience = by_id[experience_id]
        replayed = events.append("experience_replayed", {
            "experience_id": experience.experience_id,
            "capability": experience.capability,
            "lesson": experience.lesson,
            "source_event_ids": list(experience.source_event_ids),
        })
        replay_event_ids.append(str(replayed.event_id))
        learned.setdefault(experience.capability, []).append(experience.lesson)
        events.append("replay_learning_updated", {
            "capability": experience.capability,
            "lesson": experience.lesson,
            "source_replay_event_id": str(replayed.event_id),
        })
    return {
        "selected_ids": tuple(selected),
        "replay_event_ids": tuple(replay_event_ids),
        "learned_capabilities": tuple(sorted(learned)),
    }


class AgentReplayPolicy:
    """Obtain one strict replay decision through the attributed provider path."""

    def __init__(self, runner: ExperimentRunner, provider: AgentProvider) -> None:
        self.runner = runner
        self.provider = provider

    def decide(
        self,
        experiences: tuple[ReplayExperience, ...],
        tasks: tuple[EvaluationTask, ...],
        *,
        replay_budget: int,
    ) -> ReplayDecision:
        payload = {
            "replay_budget": replay_budget,
            "evaluation_tasks": [task.observable() for task in tasks],
            "experiences": [item.model_dump(mode="json") for item in experiences],
        }
        prompt = (
            "Select prior experiences to replay before held-out tasks. Maximize expected transfer "
            "under the fixed replay budget. Prefer errors whose lessons apply to the named task "
            "capabilities; avoid recent but irrelevant experiences. Return JSON only. The actions "
            "array must cover every experience_id exactly once. Set replay=true for at most "
            f"{replay_budget} items. Do not invent IDs. Schema:\n"
            + json.dumps(ReplayDecision.model_json_schema(), sort_keys=True)
            + "\nInput:\n" + json.dumps(payload, sort_keys=True)
        )
        return self._call("m13-replay-selection", prompt, experiences, replay_budget, repair=False)

    def _call(
        self,
        call_id: str,
        prompt: str,
        experiences: tuple[ReplayExperience, ...],
        replay_budget: int,
        *,
        repair: bool,
    ) -> ReplayDecision:
        estimated = max(1, (len(prompt.encode("utf-8")) + 3) // 4)
        self.runner.assert_model_call_allowed(input_tokens=estimated)
        request = ProviderRequest(
            call_id=call_id, prompt=prompt, estimated_input_tokens=estimated,
            decoding=self.runner.config.provider.decoding.model_dump(mode="json"), repair=repair,
        )
        response = self.provider.complete(request)
        self.runner.record_model_call(
            call_id,
            ModelCallUsage(
                request_id=response.request_id, input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens, latency_ms=response.usage.latency_ms,
                cost_usd=response.usage.cost_usd,
            ),
            provider=response.provider, model=response.model, route=response.route,
            decoding=request.decoding,
        )
        try:
            decision = ReplayDecision.model_validate_json(_normalize_json(response.text))
            validate_replay_decision(decision, experiences, replay_budget=replay_budget)
            return decision
        except Exception as exc:
            if repair:
                raise ReplayValidationError("Invalid replay decision after repair") from exc
            repair_prompt = (
                "Repair this response to satisfy the schema, cover every original experience ID "
                "exactly once, and respect the replay budget. Return JSON only.\nOriginal:\n"
                + prompt + "\nInvalid response:\n" + response.text
            )
            return self._call(
                f"{call_id}-repair", repair_prompt, experiences, replay_budget, repair=True,
            )


def _normalize_json(text: str) -> str:
    value = text.strip()
    if value.startswith("```") and value.endswith("```"):
        value = "\n".join(value.splitlines()[1:-1])
        if value.lstrip().lower().startswith("json"):
            value = value.lstrip()[4:].lstrip()
    return value

