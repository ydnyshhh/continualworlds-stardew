"""Run the M8 procedural-skill ablation over three proven watering trajectories."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .agent import ContextInputs, PrimeSession, ScriptedProvider
from .experiments.config import LearningConditionConfig, load_run_config
from .experiments.lifecycle import RunPhase
from .experiments.provenance import RunProvenance, RuntimeProvenance, capture_code_provenance
from .experiments.runner import ExperimentRunner
from .skills import (
    ParameterType, SkillArgument, SkillDefinition, SkillExecutor, SkillParameter,
    SkillProposalEngine, SkillStep, SkillStore, ValidationStage,
)
from .tasks import ActionCommand, PrimitiveAction, TaskTrajectory, score_trajectory


SKILL_ID = "water-adjacent-crop"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/m8-skills.yaml"))
    parser.add_argument("--m3-report", type=Path, required=True)
    parser.add_argument("--gate-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.gate_root.resolve()
    if root.exists():
        raise RuntimeError(f"Gate root already exists: {root}")
    root.mkdir(parents=True)
    trajectories = _water_trajectories(args.m3_report)
    base = load_run_config(args.config)
    provenance = _provenance()

    baseline_config = base.model_copy(update={
        "condition": "memory-retrieval",
        "learning": LearningConditionConfig(
            recent_context=True, persistent_memory=True, retrieval=True,
            skills=False, refinement=False,
        ),
    })
    baseline = ExperimentRunner(root / "baseline-runs", baseline_config, provenance)
    baseline.start()
    baseline_provider = ScriptedProvider(
        [_decision_json(trajectory) for trajectory in trajectories],
        provider="skill-contract", model="fixed-skill-model-v1",
    )
    session = PrimeSession(baseline, baseline_provider)
    for index, trajectory in enumerate(trajectories, 1):
        decision = session.decide(ContextInputs(
            objective=trajectory.fixture.description,
            observation=json.dumps(trajectory.before.model_dump(mode="json"), sort_keys=True),
            allowed_actions=trajectory.fixture.allowed_actions,
        ))
        _assert_commands(decision.actions, trajectory)
        baseline.record_task(f"baseline-water-{index}", trajectory, score_trajectory(trajectory))
    baseline.transition(RunPhase.COMPLETED, reason="M8 baseline complete", operation_id="complete")

    skills_runner = ExperimentRunner(root / "skill-runs", base, provenance)
    skills_runner.start()
    slot = int(trajectories[0].actions[0].arguments[0])
    scripted_skill = _skill(version=1, source_count=len(trajectories))
    proposal_provider = ScriptedProvider([
        json.dumps({
            "skill": scripted_skill.model_dump(mode="json"),
            "rationale": "Three successful watering trajectories share one safe procedure.",
        })
    ], provider="skill-contract", model="fixed-skill-model-v1")
    proposal = SkillProposalEngine(skills_runner, proposal_provider).propose(
        proposal_id="watering-skill-proposal", objective="Compile repeated watering into a skill",
        trajectories=trajectories,
    )
    store = SkillStore(root / "skills.sqlite3")
    executor = SkillExecutor(store, skills_runner)
    try:
        skill = store.add(proposal.skill)
        skills_runner.events.append_idempotent(
            "skill_version_created",
            {"skill_id": skill.skill_id, "version": skill.version,
             "content_sha256": skill.content_sha256(),
             "source_trajectory_ids": list(skill.source_trajectory_ids)},
            idempotency_key=f"skill-version:{skill.skill_id}:{skill.version}",
        )
        fixture = trajectories[0].fixture
        executor.validate(
            validation_id="watering-v1-replay", skill=skill, stage=ValidationStage.REPLAY,
            fixture=fixture, parameters={"tool_slot": slot},
            run=lambda commands: _replay(commands, trajectories[0]),
        )
        executor.validate(
            validation_id="watering-v1-disposable", skill=skill,
            stage=ValidationStage.DISPOSABLE, fixture=fixture,
            parameters={"tool_slot": slot},
            run=lambda commands: _disposable(commands, trajectories[0], "validation"),
        )
        store.activate(skill.skill_id, skill.version)

        # Exercise version retention and explicit rollback before the scored uses.
        second = store.add(_skill(
            version=2, source_count=len(trajectories),
            description="Water one adjacent crop with an explicitly bounded tool slot.",
        ))
        for stage in ValidationStage:
            executor.validate(
                validation_id=f"watering-v2-{stage.value}", skill=second, stage=stage,
                fixture=fixture, parameters={"tool_slot": slot},
                run=lambda commands, s=stage: _disposable(
                    commands, trajectories[0], f"v2-{s.value}"
                ),
            )
        store.activate(second.skill_id, second.version)
        store.rollback(SKILL_ID, 1, reason="controlled rollback verifies prior version recovery")
        skills_runner.events.append_idempotent(
            "skill_rolled_back", {"skill_id": SKILL_ID, "from_version": 2,
                                  "to_version": 1, "reason": "controlled gate"},
            idempotency_key="skill-rollback:water-adjacent-crop:2:1",
        )

        uses = []
        for index, source in enumerate(trajectories, 1):
            trajectory, score, metrics = executor.execute(
                use_id=f"watering-use-{index}", skill_id=SKILL_ID,
                fixture=source.fixture, parameters={"tool_slot": slot},
                run=lambda commands, item=source, i=index: _disposable(
                    commands, item, f"use-{i}"
                ),
                baseline_model_decisions=1,
                baseline_primitive_actions=len(source.actions),
            )
            skills_runner.record_task(f"skill-water-{index}", trajectory, score)
            uses.append(metrics)
        skills_runner.transition(
            RunPhase.COMPLETED, reason="M8 procedural-skill gate complete", operation_id="complete"
        )
        baseline_calls = baseline.state.model_call_count
        skill_calls = skills_runner.state.model_call_count
        baseline_actions = sum(len(item.actions) for item in trajectories)
        skill_actions = sum(item.actual_primitive_actions for item in uses)
        history = store.history(SKILL_ID)
        all_successful = all(item.success and item.score == 1 for item in uses)
        passed = (
            all_successful and len(uses) == 3 and baseline_calls == 3 and skill_calls == 1
            and store.active(SKILL_ID).version == 1  # type: ignore[union-attr]
            and any(item["event_type"] == "rolled_back" for item in history)
            and all(item.primitive_actions_saved == 0 for item in uses)
        )
        report = {
            "status": "passed" if passed else "failed",
            "scope": "deterministic M8 skill proposal, validation, rollback, and ablation",
            "source_m3_report": str(args.m3_report.resolve()),
            "skill_id": SKILL_ID, "active_version": store.active(SKILL_ID).version,  # type: ignore[union-attr]
            "retained_versions": [1, 2],
            "validation_stages": [stage.value for stage in ValidationStage],
            "rollback_verified": any(item["event_type"] == "rolled_back" for item in history),
            "executions": len(uses), "successful_executions": sum(item.success for item in uses),
            "scores": [item.score for item in uses],
            "baseline_model_decisions": baseline_calls,
            "skill_condition_model_calls_including_proposal": skill_calls,
            "net_model_decisions_saved": baseline_calls - skill_calls,
            "gross_execution_decisions_saved": sum(item.model_decisions_saved for item in uses),
            "baseline_primitive_actions": baseline_actions,
            "skill_primitive_actions": skill_actions,
            "primitive_actions_saved": baseline_actions - skill_actions,
            "restricted_actions": sorted({action.name for item in trajectories for action in item.actions}),
            "privileged_actions": sum(action.privileged for item in trajectories for action in item.actions),
            "conditions": {
                "D-memory-retrieval": baseline_config.learning.model_dump(mode="json"),
                "E-memory-retrieval-skills": base.learning.model_dump(mode="json"),
            },
            "baseline_run_id": baseline.run_id, "skill_run_id": skills_runner.run_id,
            "skill_event_count": skills_runner.events.cursor().sequence,
            "skill_event_last_hash": skills_runner.events.cursor().event_hash,
            "final_phases": [baseline.state.phase.value, skills_runner.state.phase.value],
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        if not passed:
            raise RuntimeError("M8 gate failed")
        print(json.dumps(report, indent=2))
    finally:
        store.close()


def _water_trajectories(path: Path) -> tuple[TaskTrajectory, ...]:
    report = json.loads(path.read_text(encoding="utf-8"))
    selected = [
        TaskTrajectory.model_validate(item["trajectory"])
        for item in report["results"] if item["kind"] == "water_crop"
    ]
    if len(selected) < 3 or any(not score_trajectory(item).success for item in selected[:3]):
        raise RuntimeError("M8 requires three successful M3 watering trajectories")
    return tuple(selected[:3])


def _skill(
    *, version: int, source_count: int, description: str = "Water one crop directly south."
) -> SkillDefinition:
    return SkillDefinition(
        skill_id=SKILL_ID, version=version, name="Water adjacent crop",
        description=description, task_kind="water_crop",
        parameters=(SkillParameter(
            name="tool_slot", type=ParameterType.INTEGER, minimum=0, maximum=11,
        ),),
        preconditions=("An unwatered crop is directly south of the farmer.",),
        postconditions=("The target crop is watered and preserved.",),
        steps=(
            SkillStep(action="choose_item", arguments=(SkillArgument(parameter="tool_slot"),)),
            SkillStep(action="turn", arguments=(SkillArgument(literal=2),)),
            SkillStep(action="use"),
        ),
        source_trajectory_ids=tuple(f"trajectory-{index}" for index in range(1, source_count + 1)),
    )


def _decision_json(trajectory: TaskTrajectory) -> str:
    return json.dumps({
        "schema_version": 1,
        "actions": [{"name": action.name, "arguments": list(action.arguments)}
                    for action in trajectory.actions],
        "goal_updates": [], "memory_notes": [], "rationale": "Plan watering primitives.",
    })


def _assert_commands(commands: tuple[ActionCommand, ...], trajectory: TaskTrajectory) -> None:
    actual = tuple((item.name, item.arguments) for item in commands)
    expected = tuple((item.name, item.arguments) for item in trajectory.actions)
    if actual != expected:
        raise RuntimeError(f"Baseline decision mismatch: {actual} != {expected}")


def _replay(commands: tuple[ActionCommand, ...], source: TaskTrajectory) -> TaskTrajectory:
    _assert_commands(commands, source)
    return source


def _disposable(
    commands: tuple[ActionCommand, ...], source: TaskTrajectory, suffix: str
) -> TaskTrajectory:
    _assert_commands(commands, source)
    return source.model_copy(update={
        "actions": tuple(PrimitiveAction(
            sequence=index, name=command.name, arguments=command.arguments,
            succeeded=True, privileged=False, request_id=f"m8-{suffix}-{index}",
        ) for index, command in enumerate(commands, 1)),
    })


def _provenance() -> RunProvenance:
    return RunProvenance(
        code=capture_code_provenance(Path.cwd()),
        runtime=RuntimeProvenance(
            game_version="1.6.15.24356", smapi_version="4.5.2",
            stardojo_version="1.0.0-patched",
            stardojo_dll_sha256="6fffa01cdba2b8db5d3c1e008969f05bad9a2730bc2e93c1f8cc2c403d87506b",
        ),
    )


if __name__ == "__main__":
    main()
