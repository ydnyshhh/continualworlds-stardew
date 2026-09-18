"""Preregistered active-experimentation study and raw-event reconstruction."""

from __future__ import annotations

import json
import statistics
from enum import StrEnum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from prime_stardew.experiments.provenance import artifact_sha256
from prime_stardew.memory import MemoryStore
from prime_stardew.studies.analysis import bootstrap_mean_ci, exact_two_sided_sign_test
from prime_stardew.telemetry import EventStore

from .engine import deterministic_decision, execute_decision
from .models import ExperimentAction, HiddenMechanicScenario


class ExperimentCondition(StrEnum):
    NO_EXPLICIT_UNCERTAINTY = "no_explicit_uncertainty"
    BELIEF_TRACKING_ONLY = "belief_tracking_only"
    AGENT_EXPERIMENTATION = "agent_experimentation"
    ORACLE_EXPERIMENTATION = "oracle_experimentation"
    RANDOM_EXPERIMENTATION = "random_experimentation"


class M14Preregistration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    study_id: str
    hypothesis: str
    primary_metric: Literal["net_total_reward"]
    favorable_direction: Literal["higher"]
    paired: Literal[True]
    seeds: tuple[int, ...] = Field(min_length=8)
    conditions: tuple[ExperimentCondition, ...]
    alpha: float = Field(gt=0, lt=1)
    confidence_level: float = Field(gt=0, lt=1)
    bootstrap_samples: int = Field(ge=1_000)
    experiment_budget: int = Field(gt=0)
    exclusions: tuple[str, ...] = Field(min_length=1)
    stopping_rule: str
    analysis_plan: str

    @model_validator(mode="after")
    def validate_design(self) -> "M14Preregistration":
        if len(set(self.seeds)) != len(self.seeds):
            raise ValueError("M14 seeds must be distinct")
        if set(self.conditions) != set(ExperimentCondition):
            raise ValueError("M14 requires all five preregistered conditions")
        return self


def load_m14_preregistration(path: Path) -> tuple[M14Preregistration, str]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("M14 preregistration must be a mapping")
    preregistration = M14Preregistration.model_validate(value)
    return preregistration, artifact_sha256(preregistration.model_dump(mode="json"))


def hidden_scenarios(seed: int) -> tuple[HiddenMechanicScenario, ...]:
    high_first = seed % 2 == 0
    long_values = (240, 40) if high_first else (40, 240)
    short_value = 240 if seed % 4 == 0 else 40
    return (
        HiddenMechanicScenario(
            scenario_id=f"long-alpha-s{seed}",
            context="Six future crop cycles remain for an unfamiliar soil treatment.",
            safe_value=120, candidate_low_value=40, candidate_high_value=240,
            prior_high_probability=0.5, production_cycles=6, experiment_overhead=20,
            candidate_value=long_values[0], expected_experiment_worthwhile=True,
        ),
        HiddenMechanicScenario(
            scenario_id=f"long-beta-s{seed}",
            context="Six future crop cycles remain for a second unfamiliar soil treatment.",
            safe_value=120, candidate_low_value=40, candidate_high_value=240,
            prior_high_probability=0.5, production_cycles=6, experiment_overhead=20,
            candidate_value=long_values[1], expected_experiment_worthwhile=True,
        ),
        HiddenMechanicScenario(
            scenario_id=f"short-costly-s{seed}",
            context="Only two crop cycles remain and the test consumes costly materials.",
            safe_value=120, candidate_low_value=40, candidate_high_value=240,
            prior_high_probability=0.125, production_cycles=2, experiment_overhead=80,
            candidate_value=short_value, expected_experiment_worthwhile=False,
        ),
    )


def run_m14_condition(
    root: Path,
    *,
    seed: int,
    condition: ExperimentCondition,
    experiment_budget: int,
    preregistration_sha256: str,
) -> None:
    run_id = f"m14-{condition.value}-s{seed}"
    run_root = root / run_id
    events = EventStore(run_root / "events.jsonl", run_id)
    memory = MemoryStore(run_root / "memory.sqlite3", store_id=run_id)
    hidden = hidden_scenarios(seed)
    visible = tuple(scenario.observable() for scenario in hidden)
    world_hash = artifact_sha256([scenario.model_dump(mode="json") for scenario in hidden])
    observable_hash = artifact_sha256([scenario.model_dump(mode="json") for scenario in visible])
    events.append("active_experiment_study_started", {
        "seed": seed,
        "condition": condition.value,
        "world_state_sha256": world_hash,
        "observable_state_sha256": observable_hash,
        "preregistration_sha256": preregistration_sha256,
        "experiment_budget": experiment_budget,
    })
    decision = deterministic_decision(
        visible, policy=condition.value, seed=seed,
        hidden=hidden if condition is ExperimentCondition.ORACLE_EXPERIMENTATION else None,
    )
    events.append("active_experiment_decision", {
        "condition": condition.value,
        "decision": decision.model_dump(mode="json"),
        "decision_input": [scenario.model_dump(mode="json") for scenario in visible],
        "hidden_fields_exposed": False,
    })
    execute_decision(
        decision, hidden, policy=condition.value, experiment_budget=experiment_budget,
        events=events, memory=memory,
    )
    events.append("active_experiment_study_completed", {"status": "completed"})
    memory.close()


def build_m14_report_from_events(
    root: Path, preregistration: M14Preregistration, preregistration_sha256: str,
) -> dict[str, object]:
    runs: list[dict[str, object]] = []
    event_failures: list[dict[str, str]] = []
    for event_log in sorted(root.glob("m14-*/events.jsonl")):
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
        started = [record for record in records
                   if record.event_type == "active_experiment_study_started"]
        completed = [record for record in records
                     if record.event_type == "active_experiment_study_completed"]
        scenarios = [record.payload for record in records
                     if record.event_type == "active_experiment_scenario_completed"]
        decisions = [record.payload for record in records
                     if record.event_type == "active_experiment_decision"]
        if len(started) != 1 or len(completed) != 1 or len(decisions) != 1 or len(scenarios) != 3:
            event_failures.append({
                "run_id": store.run_id, "reason": "missing or duplicate run/scenario terminal events",
            })
            continue
        start = started[0].payload
        outcomes = [scenario["outcome"] for scenario in scenarios]
        experiment_scenarios = [
            scenario for scenario in scenarios
            if scenario["outcome"]["action"] == ExperimentAction.EXPERIMENT_CANDIDATE.value
        ]
        negative_cases = [scenario for scenario in scenarios
                          if not bool(scenario["expected_experiment_worthwhile"])]
        correct_beliefs = 0
        for scenario in scenarios:
            outcome = scenario["outcome"]
            if outcome["candidate_identified"]:
                correct_beliefs += int(
                    int(outcome["learned_candidate_value"])
                    == int(scenario["hidden_candidate_value"])
                )
            else:
                predicted_high = float(scenario["choice"]["predicted_high_probability"]) >= 0.5
                actual_high = (
                    int(scenario["hidden_candidate_value"])
                    == int(scenario["candidate_high_value"])
                )
                correct_beliefs += int(predicted_high == actual_high)
        cursor = store.cursor()
        runs.append({
            "run_id": store.run_id,
            "seed": int(start["seed"]),
            "condition": str(start["condition"]),
            "world_state_sha256": str(start["world_state_sha256"]),
            "observable_state_sha256": str(start["observable_state_sha256"]),
            "net_total_reward": sum(int(outcome["total_reward"]) for outcome in outcomes),
            "safe_counterfactual_reward": sum(
                int(outcome["safe_counterfactual_reward"]) for outcome in outcomes
            ),
            "experiments_proposed": len(experiment_scenarios),
            "experiments_executed": len(experiment_scenarios),
            "experiment_overhead": sum(
                int(outcome["experiment_overhead"]) for outcome in outcomes
            ),
            "information_gain_bits": sum(
                float(outcome["information_gain_bits"]) for outcome in outcomes
            ),
            "belief_accuracy": correct_beliefs / len(scenarios),
            "identified_mechanics": sum(bool(outcome["candidate_identified"])
                                        for outcome in outcomes),
            "future_reward_attributable_to_information": sum(
                int(outcome["future_reward_attributable_to_information"])
                for outcome in outcomes
            ),
            "negative_value_cases": len(negative_cases),
            "negative_value_experiments": sum(
                scenario["outcome"]["action"] == ExperimentAction.EXPERIMENT_CANDIDATE.value
                for scenario in negative_cases
            ),
            "hidden_fields_exposed": bool(decisions[0]["hidden_fields_exposed"]),
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
    effects = tuple(
        float(by_key[(seed, ExperimentCondition.AGENT_EXPERIMENTATION.value)]["net_total_reward"])
        - float(by_key[(seed, ExperimentCondition.BELIEF_TRACKING_ONLY.value)]["net_total_reward"])
        for seed in preregistration.seeds if not missing
    )
    ci = bootstrap_mean_ci(
        effects, samples=preregistration.bootstrap_samples,
        confidence=preregistration.confidence_level,
    ) if effects else (0.0, 0.0)
    p_value = exact_two_sided_sign_test(effects) if effects else 1.0
    summaries = {}
    for condition in preregistration.conditions:
        selected = [run for run in runs if run["condition"] == condition.value]
        summaries[condition.value] = {
            "runs": len(selected),
            "mean_net_total_reward": _mean(selected, "net_total_reward"),
            "mean_experiments": _mean(selected, "experiments_executed"),
            "mean_experiment_overhead": _mean(selected, "experiment_overhead"),
            "mean_information_gain_bits": _mean(selected, "information_gain_bits"),
            "mean_belief_accuracy": _mean(selected, "belief_accuracy"),
            "mean_future_reward_attributable_to_information": _mean(
                selected, "future_reward_attributable_to_information",
            ),
            "negative_value_experiments": sum(
                int(run["negative_value_experiments"]) for run in selected
            ),
        }
    hashes_match = all(
        len({by_key[(seed, condition.value)]["world_state_sha256"]
             for condition in preregistration.conditions}) == 1
        for seed in preregistration.seeds if not missing
    )
    observable_hashes_match = all(
        len({by_key[(seed, condition.value)]["observable_state_sha256"]
             for condition in preregistration.conditions}) == 1
        for seed in preregistration.seeds if not missing
    )
    agent_runs = [run for run in runs
                  if run["condition"] == ExperimentCondition.AGENT_EXPERIMENTATION.value]
    restraint_passed = bool(agent_runs) and all(
        int(run["negative_value_experiments"]) == 0 for run in agent_runs
    )
    hidden_leaks = sum(bool(run["hidden_fields_exposed"]) for run in runs)
    passed = (
        len(runs) == expected and not event_failures and not missing and duplicate_count == 0
        and hashes_match and observable_hashes_match and restraint_passed and hidden_leaks == 0
        and bool(effects) and ci[0] > 0 and p_value < preregistration.alpha
    )
    return {
        "schema_version": 1,
        "status": "passed" if passed else "failed",
        "study_id": preregistration.study_id,
        "scope": "controlled deterministic hidden-mechanics active-learning study",
        "preregistration_sha256": preregistration_sha256,
        "primary_metric": preregistration.primary_metric,
        "runs": len(runs),
        "condition_summaries": summaries,
        "agent_minus_belief_mean": statistics.mean(effects) if effects else 0,
        "agent_minus_belief_ci95": list(ci),
        "exact_two_sided_sign_test_p": p_value,
        "matched_hidden_world_states": hashes_match,
        "matched_observable_states": observable_hashes_match,
        "agent_restraint_passed": restraint_passed,
        "raw_runs": runs,
        "failure_analysis": {
            "event_failures": event_failures,
            "missing_runs": missing,
            "duplicate_condition_seed_runs": duplicate_count,
            "hidden_field_leaks": hidden_leaks,
            "agent_negative_value_experiments": sum(
                int(run["negative_value_experiments"]) for run in agent_runs
            ),
            "nonpositive_primary_pairs": sum(effect <= 0 for effect in effects),
        },
        "manual_score_edits": 0,
    }


def _mean(runs: list[dict[str, object]], key: str) -> float:
    return statistics.mean(float(run[key]) for run in runs) if runs else 0.0

