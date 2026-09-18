import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from prime_stardew.agent import ScriptedProvider
from prime_stardew.env.models import GameDate
from prime_stardew.experiments.config import LearningConditionConfig, RunConfig
from prime_stardew.experiments.provenance import RunProvenance, RuntimeProvenance
from prime_stardew.experiments.runner import ExperimentRunner
from prime_stardew.skills import (
    ParameterType, SkillArgument, SkillDefinition, SkillError, SkillExecutor,
    SkillParameter, SkillProposalEngine, SkillStep, SkillStore, SkillStoreError,
    SkillValidation, ValidationStage, compile_skill,
)
from prime_stardew.tasks import ActionCommand, load_atomic_fixtures
from prime_stardew.tasks.models import (
    NormalizedCrop, NormalizedState, NormalizedTile, PrimitiveAction, TaskKind, TaskTrajectory,
)


WATER = next(fixture for fixture in load_atomic_fixtures() if fixture.kind is TaskKind.WATER_CROP)
PROVENANCE = RunProvenance(
    code={"revision": "test", "dirty": False, "dirty_patch_sha256": "a" * 64},
    runtime=RuntimeProvenance(
        platform="test", python_version="3", game_version="1", smapi_version="1",
        stardojo_version="1", stardojo_dll_sha256="a" * 64,
    ),
)


def _trajectory(suffix: str = "1") -> TaskTrajectory:
    crop = NormalizedCrop(seed_id="472", harvest_id="24", watered=False, phase=1)
    tile = NormalizedTile(position=WATER.target, terrain="HoeDirt", crop=crop)
    before = NormalizedState(
        player=WATER.expected_player, date=WATER.expected_date, time=600,
        location=WATER.location, position=WATER.origin, facing=2, stamina=270, money=50000,
        target=tile,
    )
    after = before.model_copy(update={
        "time": 610, "stamina": 268,
        "target": tile.model_copy(update={"crop": crop.model_copy(update={"watered": True})}),
    })
    return TaskTrajectory(
        fixture=WATER, before=before, after=after,
        actions=(
            PrimitiveAction(sequence=1, name="choose_item", arguments=(1,), request_id=f"{suffix}-1"),
            PrimitiveAction(sequence=2, name="turn", arguments=(2,), request_id=f"{suffix}-2"),
            PrimitiveAction(sequence=3, name="use", request_id=f"{suffix}-3"),
        ),
    )


def _skill(version: int = 1, *, description: str = "Water an adjacent crop") -> SkillDefinition:
    return SkillDefinition(
        skill_id="water-adjacent-crop", version=version, name="Water adjacent crop",
        description=description, task_kind="water_crop",
        parameters=(SkillParameter(
            name="tool_slot", type=ParameterType.INTEGER, minimum=0, maximum=11,
        ),),
        preconditions=("An unwatered crop is directly south of the farmer.",),
        postconditions=("The crop is watered.",),
        steps=(
            SkillStep(action="choose_item", arguments=(SkillArgument(parameter="tool_slot"),)),
            SkillStep(action="turn", arguments=(SkillArgument(literal=2),)),
            SkillStep(action="use"),
        ),
        source_trajectory_ids=("trajectory-1", "trajectory-2", "trajectory-3"),
    )


def _runner(tmp_path: Path) -> ExperimentRunner:
    config = RunConfig(
        suite="m8-test", condition="skills", seed=1,
        fixture={
            "save_id": "fixture", "player": WATER.expected_player,
            "starting_date": GameDate(year=1, season="spring", day=8),
        },
        tasks=(WATER.fixture_id,),
        learning=LearningConditionConfig(
            recent_context=True, persistent_memory=True, retrieval=True,
            skills=True, refinement=False,
        ),
        budget={
            "max_actions": 20, "max_game_days": 1, "max_model_calls": 3,
            "max_input_tokens": 20000, "max_output_tokens": 5000,
            "max_cost_usd": 5, "max_wall_seconds": 300,
        },
    )
    runner = ExperimentRunner(tmp_path / "runs", config, PROVENANCE)
    runner.start()
    return runner


def _proposal_json() -> str:
    return json.dumps({
        "skill": _skill().model_dump(mode="json"),
        "rationale": "Three successful trajectories use the same safe watering procedure.",
    })


def test_skill_schema_is_immutable_and_requires_sources() -> None:
    skill = _skill()
    with pytest.raises(ValidationError, match="frozen"):
        skill.version = 2  # type: ignore[misc]
    with pytest.raises(ValidationError):
        SkillDefinition.model_validate({
            **skill.model_dump(mode="json"), "source_trajectory_ids": ["only-one"],
        })


def test_compiler_resolves_parameters_and_blocks_unsafe_actions() -> None:
    commands = compile_skill(_skill(), {"tool_slot": 4}, allowed_actions=WATER.allowed_actions)
    assert commands == (
        ActionCommand(name="choose_item", arguments=(4,)),
        ActionCommand(name="turn", arguments=(2,)),
        ActionCommand(name="use"),
    )
    unsafe = _skill().model_copy(update={
        "steps": (SkillStep(action="debug_warp", arguments=(SkillArgument(literal=1),)),),
    })
    with pytest.raises(SkillError, match="typed allowlist"):
        compile_skill(unsafe, {"tool_slot": 4}, allowed_actions=("debug_warp",))


def test_model_proposal_requires_all_successful_sources(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    provider = ScriptedProvider([_proposal_json()])
    proposal = SkillProposalEngine(runner, provider).propose(
        proposal_id="proposal-1", objective="Learn crop watering",
        trajectories=(_trajectory("1"), _trajectory("2"), _trajectory("3")),
    )
    assert proposal.skill.skill_id == "water-adjacent-crop"
    assert runner.state.model_call_count == 1


def test_model_proposal_repairs_wrong_literal_field_once(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    invalid = json.loads(_proposal_json())
    invalid["skill"]["steps"][1]["arguments"][0] = {"value": 2}
    provider = ScriptedProvider([json.dumps(invalid), _proposal_json()])
    proposal = SkillProposalEngine(runner, provider).propose(
        proposal_id="proposal-repair", objective="Learn crop watering",
        trajectories=(_trajectory("1"), _trajectory("2"), _trajectory("3")),
    )
    assert proposal.skill.steps[1].arguments[0].literal == 2
    assert runner.state.model_call_count == 2
    assert provider.requests[1].repair
    assert '"literal": scalar' in provider.requests[1].prompt


def test_activation_requires_both_validation_stages_and_records_savings(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    store = SkillStore(tmp_path / "skills.sqlite3")
    skill = store.add(_skill())
    executor = SkillExecutor(store, runner)
    replay = _trajectory()

    def run(commands: tuple[ActionCommand, ...]) -> TaskTrajectory:
        assert tuple((item.name, item.arguments) for item in commands) == tuple(
            (item.name, item.arguments) for item in replay.actions
        )
        return replay

    executor.validate(
        validation_id="replay-1", skill=skill, stage=ValidationStage.REPLAY,
        fixture=WATER, parameters={"tool_slot": 1}, run=run,
    )
    with pytest.raises(SkillStoreError, match="replay and disposable"):
        store.activate(skill.skill_id, skill.version)
    executor.validate(
        validation_id="disposable-1", skill=skill, stage=ValidationStage.DISPOSABLE,
        fixture=WATER, parameters={"tool_slot": 1}, run=run,
    )
    store.activate(skill.skill_id, skill.version)
    _, score, metrics = executor.execute(
        use_id="use-1", skill_id=skill.skill_id, fixture=WATER,
        parameters={"tool_slot": 1}, run=run,
        baseline_model_decisions=1, baseline_primitive_actions=3,
    )
    assert score.success
    assert metrics.model_decisions_saved == 1
    assert metrics.primitive_actions_saved == 0
    event = next(item for item in runner.events.iter_records() if item.event_type == "skill_used")
    assert event.payload["model_decisions_saved"] == 1
    store.close()


def test_new_versions_are_immutable_and_rollback_selects_prior_version(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills.sqlite3")
    first = store.add(_skill())
    second = store.add(_skill(2, description="Water adjacent crops with explicit slot bounds"))
    for skill in (first, second):
        for stage in ValidationStage:
            store.record_validation(SkillValidation(
                validation_id=f"{skill.version}-{stage.value}", skill_id=skill.skill_id,
                version=skill.version, stage=stage, fixture_id=WATER.fixture_id,
                success=True, score=1, trajectory_sha256="b" * 64,
            ))
        store.activate(skill.skill_id, skill.version)
    assert store.active(first.skill_id).version == 2  # type: ignore[union-attr]
    store.rollback(first.skill_id, 1, reason="held-out regression")
    assert store.active(first.skill_id).version == 1  # type: ignore[union-attr]
    assert [event["event_type"] for event in store.history(first.skill_id)][-1] == "rolled_back"
    store.close()
