"""Deterministic, inspectable working-context assembly."""

from __future__ import annotations

import hashlib
import json

from prime_stardew.experiments.config import ContextBudgetConfig

from .models import ContextInputs, ContextItem, WorkingContext


ACTION_SCHEMAS: dict[str, str] = {
    "move": "[x: integer >= 0, y: integer >= 0]",
    "move_relative": "[delta_x: integer, delta_y: integer]",
    "move_step": (
        "[direction: integer 1..4]; 1=up, 2=right/east, 3=down, 4=left/west; "
        "does not turn, so first use the matching turn action unless already facing that way"
    ),
    "turn": "[direction: integer 0..3]; 0=up, 1=right/east, 2=down, 3=left/west",
    "choose_item": "[zero_based_inventory_slot: integer 0..35]",
    "use": "[]",
    "interact": "[]",
    "take_from_chest": "[chest_item_index: integer >= 0, quantity: integer > 0]",
    "put_to_chest": "[inventory_slot: integer 0..35, quantity: integer > 0]",
    "execute_skill": "[skill_ref: string, followed by parameter values in declared order]",
}


def estimate_tokens(text: str) -> int:
    """Conservative provider-neutral estimate used for preflight budgets."""

    return (len(text.encode("utf-8")) + 3) // 4


class WorkingContextAssembler:
    def __init__(self, budget: ContextBudgetConfig) -> None:
        self.budget = budget

    def assemble(self, inputs: ContextInputs) -> WorkingContext:
        included: list[str] = []
        omitted: list[str] = []
        sections: list[tuple[str, object]] = []
        remaining = self.budget.total_tokens

        objective, used = _trim(inputs.objective, min(self.budget.objective_tokens, remaining))
        sections.append(("objective", objective))
        remaining -= used
        observation, used = _trim(
            inputs.observation, min(self.budget.observation_tokens, remaining)
        )
        sections.append(("observation", observation))
        remaining -= used

        groups = (
            ("active_goals", inputs.goals, self.budget.goals_tokens),
            ("retrieved_memories", inputs.memories, self.budget.memories_tokens),
            ("applicable_skills", inputs.skills, self.budget.skills_tokens),
            ("recent_events", inputs.recent_events, self.budget.recent_events_tokens),
        )
        for name, items, limit in groups:
            selected, rejected, used = _select(items, min(limit, remaining))
            sections.append((name, selected))
            included.extend(item["id"] for item in selected)
            omitted.extend(rejected)
            remaining -= used

        sections.append(("response_contract", {
            "schema_version": 1,
            "actions": [{"name": "one of allowed_actions", "arguments": []}],
            "allowed_actions": list(inputs.allowed_actions),
            "action_argument_schemas": {
                name: ACTION_SCHEMAS[name]
                for name in inputs.allowed_actions if name in ACTION_SCHEMAS
            },
            "goal_updates": [], "memory_notes": [], "rationale": "brief",
        }))
        prompt = "Return exactly one JSON object.\n" + "\n".join(
            f"[{name}]\n{json.dumps(value, ensure_ascii=False, separators=(',', ':'))}"
            for name, value in sections
        )
        # The response contract is fixed overhead. Trim the observation once more if needed.
        if estimate_tokens(prompt) > self.budget.total_tokens:
            excess = estimate_tokens(prompt) - self.budget.total_tokens
            new_limit = max(1, estimate_tokens(observation) - excess - 2)
            observation, _ = _trim(observation, new_limit)
            sections[1] = ("observation", observation)
            prompt = "Return exactly one JSON object.\n" + "\n".join(
                f"[{name}]\n{json.dumps(value, ensure_ascii=False, separators=(',', ':'))}"
                for name, value in sections
            )
        digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        return WorkingContext(
            prompt=prompt,
            estimated_tokens=estimate_tokens(prompt),
            included_ids=tuple(included),
            omitted_ids=tuple(omitted),
            sha256=digest,
        )


def _trim(text: str, token_limit: int) -> tuple[str, int]:
    if token_limit <= 0:
        return "", 0
    raw = text.encode("utf-8")
    value = raw[: token_limit * 4].decode("utf-8", errors="ignore")
    return value, estimate_tokens(value)


def _select(items: tuple[ContextItem, ...], token_limit: int):
    selected: list[dict[str, str]] = []
    rejected: list[str] = []
    used = 0
    for item in items:
        cost = estimate_tokens(item.item_id) + estimate_tokens(item.text) + 4
        if used + cost <= token_limit:
            selected.append({"id": item.item_id, "text": item.text})
            used += cost
        else:
            rejected.append(item.item_id)
    return selected, rejected, used
