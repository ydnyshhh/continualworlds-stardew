"""Preregistered World A to B to A stability-plasticity study."""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from prime_stardew.experiments.provenance import artifact_sha256
from prime_stardew.memory import MemoryKind, MemoryRecord, MemoryStore
from prime_stardew.studies.analysis import bootstrap_mean_ci, exact_two_sided_sign_test
from prime_stardew.telemetry import EventStore

from .models import MechanicDefinition, ShiftWorldFamily


class ShiftCondition(StrEnum):
    FROZEN_A = "frozen_a"
    GLOBAL_OVERWRITE = "global_overwrite"
    CONTEXTUAL_BELIEFS = "contextual_beliefs"
    FRESH_AT_B = "fresh_at_b"


class M15Preregistration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    study_id: str
    hypothesis: str
    primary_metric: Literal["retention_after_return"]
    favorable_direction: Literal["higher"]
    paired: Literal[True]
    seeds: tuple[int, ...] = Field(min_length=8)
    conditions: tuple[ShiftCondition, ...]
    trials_per_phase: int = Field(ge=4)
    stable_window: int = Field(ge=2)
    alpha: float = Field(gt=0, lt=1)
    confidence_level: float = Field(gt=0, lt=1)
    bootstrap_samples: int = Field(ge=1_000)
    b_adaptation_noninferiority_margin: float = Field(ge=0)
    exclusions: tuple[str, ...] = Field(min_length=1)
    stopping_rule: str
    analysis_plan: str

    @model_validator(mode="after")
    def validate_design(self) -> "M15Preregistration":
        if len(set(self.seeds)) != len(self.seeds):
            raise ValueError("M15 seeds must be distinct")
        if set(self.conditions) != set(ShiftCondition):
            raise ValueError("M15 requires all four preregistered conditions")
        if self.stable_window > self.trials_per_phase:
            raise ValueError("Stable window cannot exceed phase length")
        return self


def load_m15_preregistration(path: Path) -> tuple[M15Preregistration, str]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("M15 preregistration must be a mapping")
    preregistration = M15Preregistration.model_validate(value)
    return preregistration, artifact_sha256(preregistration.model_dump(mode="json"))


def shift_world_family(seed: int) -> ShiftWorldFamily:
    first = "alpha" if seed % 2 == 0 else "beta"
    second = "beta" if first == "alpha" else "alpha"
    high = 10 + seed % 3
    low = 2 + seed % 2
    return ShiftWorldFamily(
        seed=seed,
        visible_context_cues={"A": f"amber-{seed % 5}", "B": f"cobalt-{seed % 7}"},
        mechanics=(
            MechanicDefinition(
                mechanic_id="crop_growth", optimal_in_a=first, optimal_in_b=second,
                high_reward=high, low_reward=low, shifted=True,
            ),
            MechanicDefinition(
                mechanic_id="crop_price", optimal_in_a=second, optimal_in_b=first,
                high_reward=high + 2, low_reward=low, shifted=True,
            ),
            MechanicDefinition(
                mechanic_id="resource_distribution", optimal_in_a=first, optimal_in_b=first,
                high_reward=high + 1, low_reward=low, shifted=False,
            ),
        ),
    )


@dataclass
class _Estimate:
    rewards: dict[str, int] = field(default_factory=dict)
    pending_probe: str | None = None
    published_best: str | None = None
    memory_id: str | None = None
    version: int = 0
    last_world: str | None = None


class _ShiftLearner:
    def __init__(self, condition: ShiftCondition) -> None:
        self.condition = condition
        self.estimates: dict[str, _Estimate] = {}

    def start_phase(self, phase: str) -> bool:
        if self.condition is ShiftCondition.FRESH_AT_B and phase == "B":
            self.estimates = {}
            return True
        return False

    def _key(self, mechanic_id: str, world: str, phase: str) -> str:
        if self.condition is ShiftCondition.CONTEXTUAL_BELIEFS:
            return f"{mechanic_id}:{world}"
        if self.condition is ShiftCondition.FRESH_AT_B:
            epoch = "A1" if phase == "A1" else "post-reset"
            return f"{mechanic_id}:{epoch}"
        return f"{mechanic_id}:global"

    def decide(
        self, mechanic: MechanicDefinition, *, world: str, phase: str,
    ) -> tuple[str, str, bool]:
        key = self._key(mechanic.mechanic_id, world, phase)
        estimate = self.estimates.get(key)
        used_prior_context = False
        if estimate is None and self.condition is ShiftCondition.CONTEXTUAL_BELIEFS and world == "B":
            prior_key = f"{mechanic.mechanic_id}:A"
            estimate = self.estimates.get(prior_key)
            if estimate is not None:
                key = prior_key
                used_prior_context = True
        if estimate is None:
            estimate = self.estimates.setdefault(key, _Estimate())
        used_prior_context = used_prior_context or (
            estimate.last_world is not None and estimate.last_world != world
        )
        if estimate.pending_probe is not None:
            action = estimate.pending_probe
            estimate.pending_probe = None
            return action, key, used_prior_context
        for action in mechanic.choices:
            if action not in estimate.rewards:
                return action, key, used_prior_context
        action = max(mechanic.choices, key=lambda item: (estimate.rewards[item], -mechanic.choices.index(item)))
        return action, key, used_prior_context

    def observe(
        self,
        mechanic: MechanicDefinition,
        *,
        world: str,
        phase: str,
        action: str,
        reward: int,
        decision_key: str,
        used_prior_context: bool,
    ) -> tuple[str, _Estimate, bool]:
        if self.condition is ShiftCondition.FROZEN_A and phase != "A1":
            return decision_key, self.estimates[decision_key], False
        key = self._key(mechanic.mechanic_id, world, phase)
        if used_prior_context and reward != self.estimates[decision_key].rewards.get(action):
            estimate = self.estimates.setdefault(key, _Estimate())
            estimate.rewards[action] = reward
            estimate.pending_probe = next(item for item in mechanic.choices if item != action)
        else:
            estimate = self.estimates.setdefault(key, self.estimates.get(decision_key, _Estimate()))
            previous = estimate.rewards.get(action)
            estimate.rewards[action] = reward
            if previous is not None and reward < previous:
                estimate.pending_probe = next(item for item in mechanic.choices if item != action)
        estimate.last_world = world
        best = max(
            estimate.rewards,
            key=lambda item: (estimate.rewards[item], -mechanic.choices.index(item)),
        )
        changed = best != estimate.published_best
        return key, estimate, changed


def run_m15_condition(
    root: Path,
    *,
    seed: int,
    condition: ShiftCondition,
    trials_per_phase: int,
    preregistration_sha256: str,
) -> None:
    run_id = f"m15-{condition.value}-s{seed}"
    run_root = root / run_id
    events = EventStore(run_root / "events.jsonl", run_id)
    memory = MemoryStore(run_root / "memory.sqlite3", store_id=run_id)
    family = shift_world_family(seed)
    learner = _ShiftLearner(condition)
    events.append("shift_study_started", {
        "seed": seed,
        "condition": condition.value,
        "family_id": family.family_id,
        "world_state_sha256": family.world_state_sha256,
        "observable_state_sha256": family.observable_state_sha256,
        "start_state_sha256": family.start_state_sha256,
        "preregistration_sha256": preregistration_sha256,
        "sequence": list(family.sequence),
        "hidden_fields_exposed": False,
    })
    phases = (("A1", "A"), ("B", "B"), ("A2", "A"))
    try:
        for phase, world in phases:
            reset = learner.start_phase(phase)
            events.append("shift_phase_started", {
                "phase": phase, "world": world,
                "visible_context_cue": family.visible_context_cues[world],
                "learner_reset": reset,
            })
            for trial in range(1, trials_per_phase + 1):
                for mechanic in family.mechanics:
                    action, decision_key, used_prior = learner.decide(
                        mechanic, world=world, phase=phase,
                    )
                    reward = mechanic.reward(world, action)
                    optimal = mechanic.optimal_action(world)
                    prior_world = "A" if phase == "B" else "B" if phase == "A2" else None
                    stale = bool(
                        used_prior and prior_world and action == mechanic.optimal_action(prior_world)
                        and action != optimal
                    )
                    interaction = events.append("shift_interaction_completed", {
                        "phase": phase, "world": world, "trial": trial,
                        "mechanic_id": mechanic.mechanic_id,
                        "shifted": mechanic.shifted,
                        "action": action, "reward": reward,
                        "high_reward": mechanic.high_reward,
                        "low_reward": mechanic.low_reward,
                        "optimal_action": optimal,
                        "correct": action == optimal,
                        "decision_context_key": decision_key,
                        "used_prior_context": used_prior,
                        "stale_belief_use": stale,
                        "decision_input_exposed_optimal": False,
                    })
                    belief_key, estimate, changed = learner.observe(
                        mechanic, world=world, phase=phase, action=action, reward=reward,
                        decision_key=decision_key, used_prior_context=used_prior,
                    )
                    if changed:
                        best = max(
                            estimate.rewards,
                            key=lambda item: (
                                estimate.rewards[item], -mechanic.choices.index(item),
                            ),
                        )
                        estimate.version += 1
                        memory_id = (
                            f"belief:{condition.value}:{belief_key}:v{estimate.version}"
                        )
                        record = MemoryRecord(
                            memory_id=memory_id,
                            kind=MemoryKind.BELIEF,
                            text=f"In {belief_key}, prefer {best} for {mechanic.mechanic_id}.",
                            confidence=0.5 if len(estimate.rewards) == 1 else 1.0,
                            source_event_ids=(str(interaction.event_id),),
                            task_domain="stardew-shift",
                            tags=("m15", phase, world, mechanic.mechanic_id),
                            supersedes_id=estimate.memory_id,
                            version=estimate.version,
                            payload={
                                "belief_context_key": belief_key,
                                "preferred_action": best,
                                "observed_rewards": dict(estimate.rewards),
                            },
                        )
                        memory.add(record)
                        estimate.memory_id = memory_id
                        estimate.published_best = best
                        events.append("shift_belief_revised", {
                            "phase": phase, "world": world,
                            "mechanic_id": mechanic.mechanic_id,
                            "memory_id": memory_id,
                            "supersedes_id": record.supersedes_id,
                            "belief_context_key": belief_key,
                            "preferred_action": best,
                            "source_evidence": [str(interaction.event_id)],
                            "contextualized": (
                                condition is ShiftCondition.CONTEXTUAL_BELIEFS
                                and world == "B" and mechanic.shifted
                            ),
                        })
            events.append("shift_phase_completed", {"phase": phase, "world": world})
        events.append("shift_study_completed", {
            "status": "completed", "active_beliefs": len(memory.records()),
        })
    finally:
        memory.close()


def build_m15_report_from_events(
    root: Path, preregistration: M15Preregistration, preregistration_sha256: str,
) -> dict[str, object]:
    runs: list[dict[str, object]] = []
    event_failures: list[dict[str, str]] = []
    for event_log in sorted(root.glob("m15-*/events.jsonl")):
        lines = event_log.read_text(encoding="utf-8").splitlines()
        if not lines:
            event_failures.append({"run_id": event_log.parent.name, "reason": "empty event log"})
            continue
        first = json.loads(lines[0])
        try:
            store = EventStore(event_log, first["run_id"])
            records = tuple(store.iter_records())
        except Exception as exc:
            event_failures.append({"run_id": first.get("run_id", "unknown"), "reason": str(exc)})
            continue
        starts = [item.payload for item in records if item.event_type == "shift_study_started"]
        completed = [item for item in records if item.event_type == "shift_study_completed"]
        interactions = [item.payload for item in records
                        if item.event_type == "shift_interaction_completed"]
        revisions = [item.payload for item in records if item.event_type == "shift_belief_revised"]
        expected_interactions = preregistration.trials_per_phase * 3 * 3
        if len(starts) != 1 or len(completed) != 1 or len(interactions) != expected_interactions:
            event_failures.append({
                "run_id": store.run_id, "reason": "missing or duplicate study/interaction events",
            })
            continue
        start = starts[0]
        shifted = [item for item in interactions if bool(item["shifted"])]
        stable = [item for item in interactions if not bool(item["shifted"])]
        initial_lag = _mean_lag(
            shifted, "A1", preregistration.trials_per_phase, preregistration.stable_window,
        )
        b_lag = _mean_lag(
            shifted, "B", preregistration.trials_per_phase, preregistration.stable_window,
        )
        return_lag = _mean_lag(
            shifted, "A2", preregistration.trials_per_phase, preregistration.stable_window,
        )
        first_return = [item for item in shifted if item["phase"] == "A2" and item["trial"] == 1]
        b_first_two = [item for item in shifted if item["phase"] == "B" and item["trial"] <= 2]
        contextualized_mechanics = {
            str(item["mechanic_id"]) for item in revisions if bool(item["contextualized"])
        }
        cursor = store.cursor()
        runs.append({
            "run_id": store.run_id,
            "seed": int(start["seed"]),
            "condition": str(start["condition"]),
            "world_state_sha256": str(start["world_state_sha256"]),
            "observable_state_sha256": str(start["observable_state_sha256"]),
            "start_state_sha256": str(start["start_state_sha256"]),
            "adaptation_lag_b": b_lag,
            "initial_learning_lag_a": initial_lag,
            "return_adaptation_lag_a": return_lag,
            "retention_after_return": _accuracy(first_return),
            "relearning_ratio": return_lag / initial_lag if initial_lag else 0.0,
            "b_initial_regret": statistics.mean(
                int(item["high_reward"]) - int(item["reward"]) for item in b_first_two
            ),
            "stale_belief_uses": sum(bool(item["stale_belief_use"]) for item in shifted),
            "belief_revision_count": len(revisions),
            "contextualization_rate": len(contextualized_mechanics) / 2,
            "b_shifted_accuracy": _accuracy([item for item in shifted if item["phase"] == "B"]),
            "b_stable_accuracy": _accuracy([item for item in stable if item["phase"] == "B"]),
            "return_shifted_accuracy": _accuracy(
                [item for item in shifted if item["phase"] == "A2"]
            ),
            "hidden_fields_exposed": bool(start["hidden_fields_exposed"])
                or any(bool(item["decision_input_exposed_optimal"]) for item in interactions),
            "event_count": cursor.sequence,
            "event_last_hash": cursor.event_hash,
        })

    by_key = {(int(run["seed"]), str(run["condition"])): run for run in runs}
    expected = len(preregistration.seeds) * len(preregistration.conditions)
    missing = [
        f"{seed}:{condition.value}"
        for seed in preregistration.seeds for condition in preregistration.conditions
        if (seed, condition.value) not in by_key
    ]
    duplicate_count = len(runs) - len(by_key)
    eligible_seeds = [seed for seed in preregistration.seeds if not missing]
    retention_effects = tuple(
        float(by_key[(seed, ShiftCondition.CONTEXTUAL_BELIEFS.value)]["retention_after_return"])
        - float(by_key[(seed, ShiftCondition.GLOBAL_OVERWRITE.value)]["retention_after_return"])
        for seed in eligible_seeds
    )
    ci = bootstrap_mean_ci(
        retention_effects, samples=preregistration.bootstrap_samples,
        confidence=preregistration.confidence_level,
    ) if retention_effects else (0.0, 0.0)
    p_value = exact_two_sided_sign_test(retention_effects) if retention_effects else 1.0
    b_lag_differences = tuple(
        float(by_key[(seed, ShiftCondition.CONTEXTUAL_BELIEFS.value)]["adaptation_lag_b"])
        - float(by_key[(seed, ShiftCondition.FRESH_AT_B.value)]["adaptation_lag_b"])
        for seed in eligible_seeds
    )
    negative_transfer = tuple(
        float(by_key[(seed, ShiftCondition.CONTEXTUAL_BELIEFS.value)]["b_initial_regret"])
        - float(by_key[(seed, ShiftCondition.FRESH_AT_B.value)]["b_initial_regret"])
        for seed in eligible_seeds
    )
    summaries: dict[str, object] = {}
    for condition in preregistration.conditions:
        selected = [run for run in runs if run["condition"] == condition.value]
        summaries[condition.value] = {
            "runs": len(selected),
            "mean_adaptation_lag_b": _mean(selected, "adaptation_lag_b"),
            "mean_retention_after_return": _mean(selected, "retention_after_return"),
            "mean_return_adaptation_lag_a": _mean(selected, "return_adaptation_lag_a"),
            "mean_relearning_ratio": _mean(selected, "relearning_ratio"),
            "mean_stale_belief_uses": _mean(selected, "stale_belief_uses"),
            "mean_belief_revision_count": _mean(selected, "belief_revision_count"),
            "mean_contextualization_rate": _mean(selected, "contextualization_rate"),
            "mean_b_shifted_accuracy": _mean(selected, "b_shifted_accuracy"),
            "mean_b_stable_accuracy": _mean(selected, "b_stable_accuracy"),
        }
    hashes_match = all(
        len({by_key[(seed, condition.value)]["world_state_sha256"]
             for condition in preregistration.conditions}) == 1
        and len({by_key[(seed, condition.value)]["observable_state_sha256"]
                 for condition in preregistration.conditions}) == 1
        and len({by_key[(seed, condition.value)]["start_state_sha256"]
                 for condition in preregistration.conditions}) == 1
        for seed in eligible_seeds
    )
    contextual_runs = [run for run in runs
                       if run["condition"] == ShiftCondition.CONTEXTUAL_BELIEFS.value]
    noninferior = bool(b_lag_differences) and all(
        difference <= preregistration.b_adaptation_noninferiority_margin
        for difference in b_lag_differences
    )
    selective_context = bool(contextual_runs) and all(
        float(run["contextualization_rate"]) == 1.0
        and float(run["b_stable_accuracy"]) == 1.0
        for run in contextual_runs
    )
    hidden_leaks = sum(bool(run["hidden_fields_exposed"]) for run in runs)
    passed = (
        len(runs) == expected and not event_failures and not missing and duplicate_count == 0
        and hashes_match and bool(retention_effects) and ci[0] > 0
        and p_value < preregistration.alpha and noninferior
        and all(effect <= 0 for effect in negative_transfer)
        and selective_context and hidden_leaks == 0
    )
    return {
        "schema_version": 1,
        "status": "passed" if passed else "failed",
        "study_id": preregistration.study_id,
        "scope": "controlled seeded counterfactual A-to-B-to-A stability-plasticity study",
        "preregistration_sha256": preregistration_sha256,
        "primary_metric": preregistration.primary_metric,
        "runs": len(runs),
        "condition_summaries": summaries,
        "contextual_minus_global_retention_mean": (
            statistics.mean(retention_effects) if retention_effects else 0.0
        ),
        "contextual_minus_global_retention_ci95": list(ci),
        "exact_two_sided_sign_test_p": p_value,
        "contextual_minus_fresh_b_lag_mean": (
            statistics.mean(b_lag_differences) if b_lag_differences else 0.0
        ),
        "b_adaptation_noninferiority_passed": noninferior,
        "contextual_negative_transfer_mean": (
            statistics.mean(negative_transfer) if negative_transfer else 0.0
        ),
        "selective_contextualization_passed": selective_context,
        "matched_world_observation_start_hashes": hashes_match,
        "raw_runs": runs,
        "failure_analysis": {
            "event_failures": event_failures,
            "missing_runs": missing,
            "duplicate_condition_seed_runs": duplicate_count,
            "hidden_field_leaks": hidden_leaks,
            "nonpositive_retention_effect_pairs": sum(effect <= 0 for effect in retention_effects),
            "b_adaptation_margin_failures": sum(
                value > preregistration.b_adaptation_noninferiority_margin
                for value in b_lag_differences
            ),
            "positive_negative_transfer_pairs": sum(value > 0 for value in negative_transfer),
        },
        "manual_score_edits": 0,
    }


def _mean_lag(
    interactions: list[dict[str, object]], phase: str, trials: int, stable_window: int,
) -> float:
    mechanics = sorted({str(item["mechanic_id"]) for item in interactions})
    lags = []
    for mechanic in mechanics:
        selected = sorted(
            (item for item in interactions
             if item["phase"] == phase and item["mechanic_id"] == mechanic),
            key=lambda item: int(item["trial"]),
        )
        onset = next(
            (index for index, _ in enumerate(selected)
             if len(selected[index:index + stable_window]) == stable_window
             and all(bool(item["correct"]) for item in selected[index:index + stable_window])
             and all(bool(item["correct"]) for item in selected[index:])),
            trials,
        )
        lags.append(onset)
    return statistics.mean(lags) if lags else float(trials)


def _accuracy(interactions: list[dict[str, object]]) -> float:
    return statistics.mean(bool(item["correct"]) for item in interactions) if interactions else 0.0


def _mean(runs: list[dict[str, object]], key: str) -> float:
    return statistics.mean(float(run[key]) for run in runs) if runs else 0.0
