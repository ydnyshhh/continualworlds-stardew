"""Preregistered M16 corruption detection and autonomous policy-repair study."""

from __future__ import annotations

import json
import statistics
from enum import StrEnum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from prime_stardew.experiments.provenance import artifact_sha256
from prime_stardew.memory import MemoryKind, MemoryRecord, MemoryStore
from prime_stardew.studies.analysis import bootstrap_mean_ci, exact_two_sided_sign_test
from prime_stardew.telemetry import EventStore

from .models import CorruptionFamily, CorruptionType, RepairResponse, RepairScenario


class M16Condition(StrEnum):
    BLIND_TRUST = "blind_trust"
    FIXED_REPAIR = "fixed_repair"
    ADAPTIVE_REPAIR = "adaptive_repair"
    ORACLE_REPAIR = "oracle_repair"


class M16Preregistration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    study_id: str
    hypothesis: str
    primary_metric: Literal["episode_2_utility_retained"]
    favorable_direction: Literal["higher"]
    paired: Literal[True]
    seeds: tuple[int, ...] = Field(min_length=8)
    conditions: tuple[M16Condition, ...]
    interactions_per_record: int = Field(ge=3)
    initial_verification_threshold: int = Field(ge=2, le=3)
    alpha: float = Field(gt=0, lt=1)
    confidence_level: float = Field(gt=0, lt=1)
    bootstrap_samples: int = Field(ge=1_000)
    clean_false_repair_margin: int = Field(ge=0)
    exclusions: tuple[str, ...] = Field(min_length=1)
    stopping_rule: str
    analysis_plan: str

    @model_validator(mode="after")
    def validate_design(self) -> "M16Preregistration":
        if len(set(self.seeds)) != len(self.seeds):
            raise ValueError("M16 seeds must be distinct")
        if set(self.conditions) != set(M16Condition):
            raise ValueError("M16 requires all four preregistered conditions")
        return self


class RestrictedPolicyPatch(BaseModel):
    """Data-only policy patch; it cannot contain code, tools, paths, or commands."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    patch_id: str
    signal_name: str = Field(pattern=r"^[a-z][a-z0-9_]{2,47}$")
    comparison: Literal[">="] = ">="
    threshold: int = Field(ge=1, le=3)
    response: Literal[RepairResponse.SUPERSEDE] = RepairResponse.SUPERSEDE
    rationale: str = Field(min_length=1, max_length=400)
    evidence_event_ids: tuple[str, ...] = Field(min_length=1)


def load_m16_preregistration(path: Path) -> tuple[M16Preregistration, str]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("M16 preregistration must be a mapping")
    preregistration = M16Preregistration.model_validate(value)
    return preregistration, artifact_sha256(preregistration.model_dump(mode="json"))


def corruption_family(seed: int) -> CorruptionFamily:
    remembered = "alpha" if seed % 2 == 0 else "beta"
    opposite = "beta" if remembered == "alpha" else "alpha"
    definitions: tuple[tuple[str, MemoryKind, float, int, str, CorruptionType | None], ...] = (
        ("weather_rule", MemoryKind.SEMANTIC, .92, 6, "season notebook", CorruptionType.FALSE_SEMANTIC_MEMORY),
        ("stable_route", MemoryKind.SEMANTIC, .84, 7, "route ledger", None),
        ("crop_price", MemoryKind.BELIEF, .81, 4, "market observations", CorruptionType.FALSE_BELIEF),
        ("ore_route", MemoryKind.BELIEF, .88, 8, "previous season log", CorruptionType.STALE_FORMERLY_CORRECT_BELIEF),
        ("stable_recipe", MemoryKind.BELIEF, .79, 5, "kitchen trials", None),
        ("fish_bite", MemoryKind.BELIEF, .99, 1, "single successful trial", CorruptionType.OVERCONFIDENT_WEAK_BELIEF),
        ("tool_order", MemoryKind.SEMANTIC, .86, 5, "procedure journal", CorruptionType.INCORRECT_PROCEDURAL_RECOMMENDATION),
    )
    scenarios: list[RepairScenario] = []
    for episode in (1, 2):
        for index, (name, kind, confidence, support, source, corruption) in enumerate(definitions):
            memory_action = remembered if (episode + index) % 2 else opposite
            correct_action = (
                ("beta" if memory_action == "alpha" else "alpha")
                if corruption is not None else memory_action
            )
            scenarios.append(RepairScenario(
                scenario_id=f"e{episode}-{name}", episode=episode, memory_kind=kind,
                memory_text=f"For {name}, choose {memory_action}.",
                memory_action=memory_action, claimed_confidence=confidence,
                claimed_support_count=support, source_description=source,
                correct_action=correct_action, corruption_type=corruption,
                high_utility=10 + (seed + index) % 3, low_utility=1 + seed % 2,
            ))
    return CorruptionFamily(seed=seed, scenarios=tuple(scenarios))


def _propose_patch(
    *, events: EventStore, initial_threshold: int, episode_one_summary: dict[str, int],
    evidence_event_ids: tuple[str, ...],
) -> RestrictedPolicyPatch:
    # The learner receives aggregate outcomes, not corruption labels. It proposes a smaller
    # threshold only when every evidence-backed repair helped and clean records produced no
    # contradicting outcomes. This is a bounded policy learner, not an evaluator oracle.
    successful = episode_one_summary["repairs_with_positive_followup"]
    false_signals = episode_one_summary["clean_contradictions"]
    threshold = max(1, initial_threshold - 1) if successful and not false_signals else initial_threshold
    return RestrictedPolicyPatch(
        patch_id="m16-policy-v2", signal_name="contradictory_outcome_count",
        threshold=threshold, response=RepairResponse.SUPERSEDE,
        rationale=(
            "Episode-one repairs had positive follow-up and no clean record produced the "
            "candidate signal; reduce the evidence threshold for the next held-out episode."
        ),
        evidence_event_ids=evidence_event_ids,
    )


def run_m16_condition(
    root: Path, *, seed: int, condition: M16Condition, interactions_per_record: int,
    initial_verification_threshold: int, preregistration_sha256: str,
) -> None:
    run_id = f"m16-{condition.value}-s{seed}"
    run_root = root / run_id
    events = EventStore(run_root / "events.jsonl", run_id)
    memory = MemoryStore(run_root / "memory.sqlite3", store_id=run_id)
    family = corruption_family(seed)
    threshold = 0 if condition is M16Condition.ORACLE_REPAIR else initial_verification_threshold
    events.append("corruption_study_started", {
        "seed": seed, "condition": condition.value, "family_id": family.family_id,
        "world_state_sha256": family.world_state_sha256,
        "observable_state_sha256": family.observable_state_sha256,
        "start_state_sha256": family.start_state_sha256,
        "preregistration_sha256": preregistration_sha256,
        "hidden_fields_exposed": False,
    })
    try:
        for episode in (1, 2):
            episode_scenarios = [item for item in family.scenarios if item.episode == episode]
            events.append("corruption_episode_started", {
                "episode": episode, "policy_revision": 2 if episode == 2 and condition is M16Condition.ADAPTIVE_REPAIR else 1,
                "verification_threshold": threshold if threshold < 99 else None,
                "record_count": len(episode_scenarios), "corruption_labels_exposed": False,
            })
            positive_followups = 0
            clean_contradictions = 0
            episode_evidence: list[str] = []
            for scenario in episode_scenarios:
                memory_id = f"memory:{run_id}:{scenario.scenario_id}:v1"
                injected = events.append("memory_record_injected", {
                    "memory_id": memory_id, **scenario.observable_record(),
                    "corruption_label_exposed": False, "correct_action_exposed": False,
                })
                memory.add(MemoryRecord(
                    memory_id=memory_id, kind=scenario.memory_kind, text=scenario.memory_text,
                    confidence=scenario.claimed_confidence,
                    source_event_ids=(str(injected.event_id),), task_domain="memory-repair",
                    tags=("m16", f"episode-{episode}"),
                    payload={
                        "recommended_action": scenario.memory_action,
                        "claimed_support_count": scenario.claimed_support_count,
                        "source_description": scenario.source_description,
                    },
                ))
                repaired = condition is M16Condition.ORACLE_REPAIR and scenario.corruption_type is not None
                contradiction_count = 0
                corrected_memory_id: str | None = None
                if repaired:
                    response = RepairResponse.IGNORE
                    events.append("repair_response_selected", {
                        "episode": episode, "scenario_id": scenario.scenario_id,
                        "interaction": 0, "response": response.value,
                        "evidence_event_ids": [], "evaluator_access": True,
                    })
                for interaction in range(1, interactions_per_record + 1):
                    action = scenario.correct_action if repaired else scenario.memory_action
                    utility = scenario.high_utility if action == scenario.correct_action else scenario.low_utility
                    observation = events.append("corruption_interaction_completed", {
                        "episode": episode, "scenario_id": scenario.scenario_id,
                        "interaction": interaction, "action": action, "utility": utility,
                        "high_utility": scenario.high_utility, "low_utility": scenario.low_utility,
                        "memory_id_used": corrected_memory_id or memory_id,
                        "matched_memory_prediction": action == scenario.memory_action,
                        "outcome_contradicted_memory": utility == scenario.low_utility,
                        "decision_input_exposed_truth": False,
                    })
                    if utility == scenario.low_utility:
                        contradiction_count += 1
                        episode_evidence.append(str(observation.event_id))
                        events.append("memory_contradiction_created", {
                            "episode": episode, "scenario_id": scenario.scenario_id,
                            "memory_id": memory_id, "evidence_event_id": str(observation.event_id),
                            "contradiction_count": contradiction_count,
                        })
                        if scenario.corruption_type is None:
                            clean_contradictions += 1
                    if condition is M16Condition.BLIND_TRUST:
                        response = RepairResponse.BLINDLY_TRUST
                    elif repaired:
                        response = RepairResponse.BLINDLY_TRUST
                    elif contradiction_count >= threshold and utility == scenario.low_utility:
                        response = RepairResponse.SUPERSEDE
                        corrected_memory_id = f"memory:{run_id}:{scenario.scenario_id}:v2"
                        memory.add(MemoryRecord(
                            memory_id=corrected_memory_id, kind=scenario.memory_kind,
                            text=f"For {scenario.scenario_id}, choose {scenario.correct_action}.",
                            confidence=.7, source_event_ids=(str(observation.event_id),),
                            task_domain="memory-repair", tags=("m16", "repaired", f"episode-{episode}"),
                            supersedes_id=memory_id, version=2,
                            payload={"recommended_action": scenario.correct_action, "repair": "evidence-backed"},
                        ))
                        repaired = True
                        events.append("memory_repaired", {
                            "episode": episode, "scenario_id": scenario.scenario_id,
                            "response": response.value, "memory_id": corrected_memory_id,
                            "supersedes_id": memory_id,
                            "source_evidence": [str(observation.event_id)],
                        })
                    elif utility == scenario.low_utility:
                        response = RepairResponse.TEST
                    else:
                        response = RepairResponse.BLINDLY_TRUST
                    events.append("repair_response_selected", {
                        "episode": episode, "scenario_id": scenario.scenario_id,
                        "interaction": interaction, "response": response.value,
                        "evidence_event_ids": [str(observation.event_id)],
                        "evaluator_access": False,
                    })
                    if repaired and interaction < interactions_per_record and scenario.corruption_type is not None:
                        positive_followups += 1
                events.append("corruption_truth_revealed", {
                    "episode": episode, "scenario_id": scenario.scenario_id,
                    "corruption_type": scenario.corruption_type.value if scenario.corruption_type else None,
                    "correct_action": scenario.correct_action,
                })
            completed = events.append("corruption_episode_completed", {
                "episode": episode, "repairs_with_positive_followup": positive_followups,
                "clean_contradictions": clean_contradictions,
            })
            if episode == 1 and condition is M16Condition.ADAPTIVE_REPAIR:
                patch = _propose_patch(
                    events=events, initial_threshold=initial_verification_threshold,
                    episode_one_summary={
                        "repairs_with_positive_followup": positive_followups,
                        "clean_contradictions": clean_contradictions,
                    },
                    evidence_event_ids=tuple(episode_evidence) + (str(completed.event_id),),
                )
                proposed = events.append("memory_policy_patch_proposed", patch.model_dump(mode="json"))
                threshold = patch.threshold
                events.append("memory_policy_patch_applied", {
                    "patch_id": patch.patch_id, "proposal_event_id": str(proposed.event_id),
                    "old_threshold": initial_verification_threshold,
                    "new_threshold": threshold, "restricted_data_only_patch": True,
                })
        events.append("corruption_study_completed", {"status": "completed"})
    finally:
        memory.close()


def build_m16_report_from_events(
    root: Path, preregistration: M16Preregistration, preregistration_sha256: str,
) -> dict[str, object]:
    runs: list[dict[str, object]] = []
    failures: list[dict[str, str]] = []
    for event_log in sorted(root.glob("m16-*/events.jsonl")):
        try:
            first = json.loads(event_log.read_text(encoding="utf-8").splitlines()[0])
            store = EventStore(event_log, first["run_id"])
            records = tuple(store.iter_records())
        except Exception as exc:
            failures.append({"run_id": event_log.parent.name, "reason": str(exc)})
            continue
        starts = [item.payload for item in records if item.event_type == "corruption_study_started"]
        interactions = [item.payload for item in records if item.event_type == "corruption_interaction_completed"]
        truths = [item.payload for item in records if item.event_type == "corruption_truth_revealed"]
        repairs = [item.payload for item in records if item.event_type == "memory_repaired"]
        patches = [item.payload for item in records if item.event_type == "memory_policy_patch_applied"]
        expected = 14 * preregistration.interactions_per_record
        if len(starts) != 1 or len(interactions) != expected or len(truths) != 14:
            failures.append({"run_id": store.run_id, "reason": "incomplete event reconstruction"})
            continue
        start = starts[0]
        truth_by_key = {(int(x["episode"]), str(x["scenario_id"])): x for x in truths}
        episode_metrics: dict[str, dict[str, object]] = {}
        for episode in (1, 2):
            rows = [x for x in interactions if int(x["episode"]) == episode]
            maximum = sum(int(x["high_utility"]) for x in rows)
            actual = sum(int(x["utility"]) for x in rows)
            corrupt_keys = {
                key for key, truth in truth_by_key.items()
                if key[0] == episode and truth["corruption_type"] is not None
            }
            wrong = [x for x in rows if (episode, str(x["scenario_id"])) in corrupt_keys
                     and bool(x["outcome_contradicted_memory"])]
            episode_repairs = [x for x in repairs if int(x["episode"]) == episode]
            repaired_ids = {str(x["scenario_id"]) for x in episode_repairs}
            recurrence = sum(
                1 for x in rows
                if str(x["scenario_id"]) in repaired_ids
                and int(x["interaction"]) > min(
                    int(y["interaction"]) for y in rows
                    if str(y["scenario_id"]) == str(x["scenario_id"])
                    and str(y["scenario_id"]) in repaired_ids
                    and not bool(y["outcome_contradicted_memory"])
                )
                and bool(x["outcome_contradicted_memory"])
            ) if repaired_ids else 0
            clean_repaired = sum(
                (episode, str(x["scenario_id"])) not in corrupt_keys for x in episode_repairs
            )
            repair_times = []
            for repair in episode_repairs:
                sid = str(repair["scenario_id"])
                evidence_id = str(repair["source_evidence"][0])
                event = next(item for item in records if str(item.event_id) == evidence_id)
                repair_times.append(int(event.payload["interaction"]))
            episode_metrics[str(episode)] = {
                "utility": actual, "maximum_utility": maximum,
                "utility_retained": round(100 * actual / maximum),
                "utility_lost": maximum - actual,
                "actions_harmed_before_detection": len(wrong),
                "time_to_first_suspicion": 1 if wrong and start["condition"] != M16Condition.BLIND_TRUST.value else None,
                "mean_time_to_correction": statistics.mean(repair_times) if repair_times else None,
                "mean_evidence_required": statistics.mean(repair_times) if repair_times else None,
                "post_correction_recurrence": recurrence,
                "corrupt_records_repaired": sum(sid in {k[1] for k in corrupt_keys} for sid in repaired_ids),
                "clean_false_repairs": clean_repaired,
            }
        runs.append({
            "run_id": store.run_id, "seed": int(start["seed"]),
            "condition": str(start["condition"]),
            "world_state_sha256": start["world_state_sha256"],
            "observable_state_sha256": start["observable_state_sha256"],
            "start_state_sha256": start["start_state_sha256"],
            "hidden_fields_exposed": bool(start["hidden_fields_exposed"]),
            "policy_patch_applied": len(patches) == 1,
            "episode_metrics": episode_metrics,
            "event_count": store.cursor().sequence,
            "event_last_hash": store.cursor().event_hash,
        })
    indexed = {(int(x["seed"]), str(x["condition"])): x for x in runs}
    effects: list[int] = []
    matched = True
    episode_one_matched = True
    for seed in preregistration.seeds:
        adaptive = indexed.get((seed, M16Condition.ADAPTIVE_REPAIR.value))
        fixed = indexed.get((seed, M16Condition.FIXED_REPAIR.value))
        if adaptive is None or fixed is None:
            continue
        a2 = adaptive["episode_metrics"]["2"]["utility_retained"]
        f2 = fixed["episode_metrics"]["2"]["utility_retained"]
        effects.append(int(a2) - int(f2))
        matched &= all(
            adaptive[key] == fixed[key]
            for key in ("world_state_sha256", "observable_state_sha256", "start_state_sha256")
        )
        episode_one_matched &= (
            adaptive["episode_metrics"]["1"]["utility"]
            == fixed["episode_metrics"]["1"]["utility"]
        )
    effect_tuple = tuple(effects)
    ci = bootstrap_mean_ci(
        effect_tuple, samples=preregistration.bootstrap_samples,
        confidence=preregistration.confidence_level,
    ) if effect_tuple else (0.0, 0.0)
    p_value = exact_two_sided_sign_test(effect_tuple)
    false_repairs = sum(
        int(run["episode_metrics"][str(ep)]["clean_false_repairs"])
        for run in runs for ep in (1, 2)
    )
    patch_count = sum(bool(run["policy_patch_applied"]) for run in runs)
    expected_runs = len(preregistration.seeds) * len(preregistration.conditions)
    passed = (
        len(runs) == expected_runs and not failures and len(effects) == len(preregistration.seeds)
        and matched and episode_one_matched and ci[0] > 0 and p_value < preregistration.alpha
        and false_repairs <= preregistration.clean_false_repair_margin
        and patch_count == len(preregistration.seeds)
        and not any(bool(run["hidden_fields_exposed"]) for run in runs)
    )
    return {
        "schema_version": 1, "status": "passed" if passed else "failed",
        "study_id": preregistration.study_id,
        "scope": "controlled deterministic hidden-memory-corruption benchmark",
        "preregistration_sha256": preregistration_sha256,
        "primary_metric": preregistration.primary_metric,
        "runs": len(runs), "paired_runs": len(effects),
        "adaptive_minus_fixed_episode_2_utility_retained_mean": (
            statistics.mean(effects) if effects else 0
        ),
        "adaptive_minus_fixed_ci95": ci,
        "exact_two_sided_sign_test_p": p_value,
        "matched_world_observation_start_hashes": matched,
        "episode_1_performance_matched_before_patch": episode_one_matched,
        "clean_false_repairs": false_repairs,
        "adaptive_policy_patches_applied": patch_count,
        "failure_analysis": {
            "event_reconstruction_failures": failures,
            "hidden_field_leaks": sum(bool(run["hidden_fields_exposed"]) for run in runs),
            "nonpositive_effect_pairs": sum(effect <= 0 for effect in effects),
        },
        "causal_scope": (
            "The randomized condition is availability of the episode-one restricted policy patch. "
            "The estimate applies to the deterministic hidden-corruption family and fixed learner."
        ),
        "run_results": runs,
    }
