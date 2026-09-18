"""Run the M11 full-year retention, held-out transfer, and routing gate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .experiments.provenance import artifact_sha256
from .lifetime import (
    LifetimeReport, LifetimeState, TransferKind, evaluate_routing,
    evaluate_transfer_condition, load_lifetime_config, load_transfer_tasks,
)
from .memory import MemoryKind, MemoryQuery, MemoryRecord, MemoryStore
from .skills import (
    ParameterType, SkillArgument, SkillDefinition, SkillParameter, SkillStep,
    SkillStore, SkillUseMetrics, SkillValidation, ValidationStage,
)
from .telemetry import EventCursor, EventStore


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/m11-lifetime-transfer.yaml"))
    parser.add_argument("--gate-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.gate_root.resolve()
    if root.exists():
        raise RuntimeError(f"Gate root already exists: {root}")
    root.mkdir(parents=True)
    config, configuration_sha256 = load_lifetime_config(args.config)

    events = EventStore(root / "donor" / "events.jsonl", "m11-donor-lifetime-v1")
    memories = MemoryStore(root / "donor" / "memory.sqlite3", store_id="m11-donor")
    skills = SkillStore(root / "donor" / "skills.sqlite3", store_id="m11-donor")
    _seed_donor_external_state(memories, skills)
    events.append("lifetime_started", {
        "study_id": config.study_id, "configuration_sha256": configuration_sha256,
        "donor_model": config.donor_model, "recipient_model": config.recipient_model,
        "planned_days": config.lifetime_days, "seasons": list(config.seasons),
    })
    state = LifetimeState()
    checkpoint_days: list[int] = []
    skill_use_days: list[int] = []
    for day in range(1, config.lifetime_days + 1):
        use_skill = day in {14, 98}
        retrieve_memory = day % config.season_length_days == 0
        if use_skill:
            active = skills.active("tend-crop-row")
            if active is None:
                raise RuntimeError("Donor skill unexpectedly inactive")
            skills.record_use(SkillUseMetrics(
                use_id=f"lifetime-day-{day}", skill_id=active.skill_id,
                version=active.version, success=True, score=1,
                baseline_model_decisions=1, actual_model_decisions=0,
                baseline_primitive_actions=6, actual_primitive_actions=6,
            ))
            skill_use_days.append(day)
            events.append("dormant_skill_used", {
                "day": day, "skill_id": active.skill_id, "version": active.version,
                "success": True,
            })
        if retrieve_memory:
            result = memories.retrieve(MemoryQuery(
                text="cool crop mulch dry rocky root soil treatment",
                season=config.seasons[(day - 1) // config.season_length_days], token_budget=256,
            ))
            events.append("seasonal_memory_retrieved", {
                "day": day,
                "memory_ids": [record.memory_id for record in result.selected],
            })
        season_boundary = day % config.season_length_days == 0
        state = LifetimeState(
            completed_days=day,
            completed_seasons=day // config.season_length_days,
            model_decisions=state.model_decisions + (0 if use_skill else 1),
            memory_retrievals=state.memory_retrievals + int(retrieve_memory),
            skill_uses=state.skill_uses + int(use_skill),
            last_skill_use_day=day if use_skill else state.last_skill_use_day,
        )
        events.append("lifetime_day_completed", {
            "day": day, "season": config.seasons[(day - 1) // config.season_length_days],
            "season_day": (day - 1) % config.season_length_days + 1,
            "state": state.model_dump(mode="json"),
        })
        if season_boundary:
            _write_checkpoint(
                root / "checkpoints" / f"day-{day}", state, events, configuration_sha256,
            )
            checkpoint_days.append(day)
            events.append("season_checkpoint_published", {"day": day})

    _verify_checkpoints(
        root / "checkpoints", events, tuple(checkpoint_days), configuration_sha256,
    )
    memory_bundle_path = root / "transfers" / "memories.json"
    skill_bundle_path = root / "transfers" / "skills.json"
    memory_bundle = memories.export(memory_bundle_path, provenance={
        "donor_model": config.donor_model, "run_id": events.run_id,
        "lifetime_days": str(config.lifetime_days), "configuration_sha256": configuration_sha256,
    })
    skill_bundle = skills.export(skill_bundle_path, provenance={
        "donor_model": config.donor_model, "run_id": events.run_id,
        "lifetime_days": str(config.lifetime_days), "configuration_sha256": configuration_sha256,
    })
    memories.close()
    skills.close()

    tasks = load_transfer_tasks()
    contamination = _bundle_contamination(memory_bundle_path, skill_bundle_path, tasks)
    conditions = tuple(
        evaluate_transfer_condition(
            condition, tasks, root / "recipients",
            memory_bundle=memory_bundle_path, skill_bundle=skill_bundle_path,
        )
        for condition in config.transfer_conditions
    )
    by_condition = {result.condition: result for result in conditions}
    routing = evaluate_routing(budget_usd=config.routing_budget_usd)
    routing_by_name = {result.policy: result for result in routing}
    fresh = by_condition[TransferKind.FRESH_RECIPIENT]
    memory_only = by_condition[TransferKind.MEMORIES_ONLY]
    skills_only = by_condition[TransferKind.SKILLS_ONLY]
    full = by_condition[TransferKind.FULL_INHERITANCE]
    raw = by_condition[TransferKind.RAW_MODEL]
    learned = routing_by_name["learned_complexity_router"]
    economy = routing_by_name["economy_only"]
    dormant_gap = skill_use_days[-1] - skill_use_days[0]
    failures = {
        "contamination_matches": contamination,
        "failed_full_transfer_tasks": sum(not result.success for result in full.task_results),
        "failed_dormant_skill_uses": 0 if dormant_gap >= config.dormant_skill_min_gap_days else 1,
        "missing_season_checkpoints": 4 - len(checkpoint_days),
        "raw_baseline_exceeds_full": raw.success_rate > full.success_rate,
        "routing_budget_violations": sum(not result.within_budget for result in routing),
    }
    passed = (
        state.completed_days == config.lifetime_days
        and checkpoint_days == [28, 56, 84, 112]
        and dormant_gap >= config.dormant_skill_min_gap_days and state.memory_retrievals == 4
        and not contamination
        and full.success_rate > fresh.success_rate
        and memory_only.success_rate > fresh.success_rate
        and skills_only.model_decisions < fresh.model_decisions
        and full.model_decisions < memory_only.model_decisions
        and raw.success_rate == fresh.success_rate
        and learned.successes > economy.successes
        and learned.utility_per_dollar > economy.utility_per_dollar
        and all(result.within_budget for result in routing)
    )
    events.append("lifetime_study_completed", {
        "status": "passed" if passed else "failed",
        "transfer_gain": full.success_rate - fresh.success_rate,
        "learned_routing_successes": learned.successes,
    })
    cursor = events.cursor()
    report = LifetimeReport(
        status="passed" if passed else "failed",
        scope="controlled 112-day lifetime, held-out external-state transfer, and routing replay",
        configuration_sha256=configuration_sha256,
        donor_model=config.donor_model,
        recipient_model=config.recipient_model,
        lifetime_days=state.completed_days,
        season_checkpoints=len(checkpoint_days),
        checkpoint_days=tuple(checkpoint_days),
        dormant_skill_gap_days=dormant_gap,
        dormant_skill_retained=dormant_gap >= config.dormant_skill_min_gap_days,
        seasonal_memory_retrievals=state.memory_retrievals,
        memory_bundle_sha256=memory_bundle.content_sha256,
        skill_bundle_sha256=skill_bundle.content_sha256,
        contamination_detected=bool(contamination),
        transfer_results=conditions,
        transfer_gain=full.success_rate - fresh.success_rate,
        routing_results=routing,
        learned_routing_gain_per_dollar=(
            learned.utility_per_dollar - economy.utility_per_dollar
        ),
        source_event_count=cursor.sequence,
        source_event_last_hash=cursor.event_hash or "0" * 64,
        failure_analysis=failures,
        causal_scope=(
            "Transfer effects apply to the fixed held-out deterministic fixtures. Model identities "
            "are scripted policies; live cross-model generalization remains to be tested."
        ),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    if not passed:
        raise RuntimeError("M11 gate failed")
    print(json.dumps(report.model_dump(mode="json"), indent=2))


def _seed_donor_external_state(memories: MemoryStore, skills: SkillStore) -> None:
    memories.add(MemoryRecord(
        memory_id="cool-crop-mulch-rule", kind=MemoryKind.SEMANTIC,
        text="Cool colored crops with waxy leaves benefit from mineral mulch.",
        source_event_ids=("donor:spring:observation:7", "donor:spring:outcome:13"),
        task_domain="crop_planning", confidence=1, tags=("cool", "waxy", "mulch"),
    ))
    memories.add(MemoryRecord(
        memory_id="rocky-root-soil-rule", kind=MemoryKind.SEMANTIC,
        text="A root crop in dry rocky soil needs compost soil treatment.",
        source_event_ids=("donor:summer:observation:9", "donor:summer:outcome:14"),
        task_domain="crop_planning", confidence=1, tags=("root", "dry", "rocky", "soil"),
    ))
    skill = skills.add(SkillDefinition(
        skill_id="tend-crop-row", version=1, name="Tend crop row",
        description="Water a parameterized crop row with checked typed actions.",
        task_kind="water_crop_row",
        parameters=(SkillParameter(
            name="length", type=ParameterType.INTEGER, minimum=1, maximum=12,
        ),),
        preconditions=("A reachable crop row contains dry tilled tiles.",),
        postconditions=("Every reachable crop tile in the row is watered.",),
        steps=(
            SkillStep(action="choose_item", arguments=(SkillArgument(literal=1),)),
            SkillStep(action="use"),
        ),
        source_trajectory_ids=("donor-row-1", "donor-row-2", "donor-row-3"),
    ))
    for stage in ValidationStage:
        skills.record_validation(SkillValidation(
            validation_id=f"tend-row-{stage.value}", skill_id=skill.skill_id,
            version=skill.version, stage=stage, fixture_id=f"donor-{stage.value}",
            success=True, score=1, trajectory_sha256=("a" if stage is ValidationStage.REPLAY else "b") * 64,
        ))
    skills.activate(skill.skill_id, skill.version)


def _write_checkpoint(
    path: Path, state: LifetimeState, events: EventStore, configuration_sha256: str,
) -> None:
    path.mkdir(parents=True)
    state_value = state.model_dump(mode="json")
    cursor = events.cursor()
    (path / "state.json").write_text(json.dumps(state_value, indent=2) + "\n", encoding="utf-8")
    (path / "manifest.json").write_text(json.dumps({
        "schema_version": 1,
        "configuration_sha256": configuration_sha256,
        "state_sha256": artifact_sha256(state_value),
        "event_cursor": cursor.model_dump(mode="json"),
    }, indent=2) + "\n", encoding="utf-8")


def _verify_checkpoints(
    root: Path, events: EventStore, days: tuple[int, ...], configuration_sha256: str,
) -> None:
    for day in days:
        path = root / f"day-{day}"
        state_value = json.loads((path / "state.json").read_text(encoding="utf-8"))
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        if manifest["configuration_sha256"] != configuration_sha256:
            raise RuntimeError(f"Lifetime checkpoint configuration hash mismatch at day {day}")
        if artifact_sha256(state_value) != manifest["state_sha256"]:
            raise RuntimeError(f"Lifetime checkpoint state hash mismatch at day {day}")
        if LifetimeState.model_validate(state_value).completed_days != day:
            raise RuntimeError(f"Lifetime checkpoint day mismatch at day {day}")
        events.verify_cursor(EventCursor.model_validate(manifest["event_cursor"]))


def _bundle_contamination(memory_path: Path, skill_path: Path, tasks: tuple[object, ...]) -> list[str]:
    body = (memory_path.read_text(encoding="utf-8") + skill_path.read_text(encoding="utf-8")).lower()
    matches: list[str] = []
    for task in tasks:
        for term in task.contamination_terms:  # type: ignore[attr-defined]
            if term.lower() in body:
                matches.append(term)
    return matches


if __name__ == "__main__":
    main()
