from pathlib import Path

import pytest

from prime_stardew.env.checkpoints import CheckpointManager
from prime_stardew.env.models import GameDate
from prime_stardew.experiments.checkpoints import RunCheckpointManager
from prime_stardew.experiments.config import (
    ExperimentBudget,
    FixtureConfig,
    RunConfig,
)
from prime_stardew.experiments.lifecycle import RunPhase
from prime_stardew.experiments.provenance import (
    CodeProvenance,
    RunProvenance,
    RuntimeProvenance,
)
from prime_stardew.experiments.runner import (
    ExperimentRunner,
    ModelCallUsage,
    RunnerError,
)
from prime_stardew.tasks import (
    ItemCount,
    NormalizedState,
    PrimitiveAction,
    TaskKind,
    TaskTrajectory,
    load_atomic_fixtures,
    score_trajectory,
)


PROVENANCE = RunProvenance(
    code=CodeProvenance(revision="abc123", dirty=False, dirty_patch_sha256="0" * 64),
    runtime=RuntimeProvenance(
        game_version="1.6.15",
        smapi_version="4.5.2",
        stardojo_version="1.0.0-patched",
        stardojo_dll_sha256="1" * 64,
        python_version="3.13",
        platform="test",
    ),
)


def _config(*, repetition: int = 0, save_id: str = "Fixture_1") -> RunConfig:
    return RunConfig(
        suite="m4-gate",
        condition="scripted",
        seed=7,
        repetition=repetition,
        observation_mode="replay",
        fixture=FixtureConfig(
            save_id=save_id,
            player="PrimeStardewSmoke",
            starting_date=GameDate(year=1, season="spring", day=8),
        ),
        tasks=("m3-turn-move-v1",),
        budget=ExperimentBudget(max_actions=20, max_game_days=1, max_wall_seconds=60),
    )


def _trajectory(request_suffix: str = "one") -> tuple[TaskTrajectory, object]:
    fixture = next(item for item in load_atomic_fixtures() if item.kind is TaskKind.TURN_MOVE)
    common = dict(
        player=fixture.expected_player,
        date=fixture.expected_date,
        location=fixture.location,
        stamina=270,
        money=50000,
        inventory=(ItemCount(name="Axe", quantity=1),),
    )
    before = NormalizedState(
        **common, time=600, position=fixture.origin, facing=2
    )
    after = NormalizedState(
        **common, time=600, position=fixture.target, facing=fixture.expected_facing
    )
    trajectory = TaskTrajectory(
        fixture=fixture,
        before=before,
        after=after,
        actions=(
            PrimitiveAction(sequence=1, name="turn", arguments=(1,),
                            request_id=f"turn-{request_suffix}"),
            PrimitiveAction(sequence=2, name="move_step", arguments=(2,),
                            request_id=f"move-{request_suffix}"),
        ),
    )
    return trajectory, score_trajectory(trajectory)


def _make_save(root: Path, save_id: str) -> None:
    save = root / save_id
    save.mkdir(parents=True)
    (save / save_id).write_text("current", encoding="utf-8")
    (save / f"{save_id}_old").write_text("old", encoding="utf-8")


def test_two_runs_have_equivalent_normalized_trajectories(tmp_path: Path) -> None:
    trajectory_one, score_one = _trajectory("one")
    trajectory_two, score_two = _trajectory("two")
    first = ExperimentRunner(tmp_path / "runs", _config(repetition=1), PROVENANCE)
    second = ExperimentRunner(tmp_path / "runs", _config(repetition=2), PROVENANCE)
    first.start()
    second.start()

    first.record_task("turn-move", trajectory_one, score_one)  # type: ignore[arg-type]
    second.record_task("turn-move", trajectory_two, score_two)  # type: ignore[arg-type]

    assert first.run_id != second.run_id
    assert first.equivalence_digest() == second.equivalence_digest()
    assert first.state.action_count == second.state.action_count == 2


def test_partial_task_event_replay_adds_only_missing_records(tmp_path: Path) -> None:
    runner = ExperimentRunner(tmp_path / "runs", _config(), PROVENANCE)
    runner.start()
    trajectory, score = _trajectory()
    runner.events.append_idempotent(
        "task_started",
        {"task_id": "task-1", "fixture_id": trajectory.fixture.fixture_id,
         "kind": trajectory.fixture.kind.value},
        idempotency_key="task:task-1:started",
    )

    runner.record_task("task-1", trajectory, score)  # type: ignore[arg-type]
    count = len(list(runner.events.iter_records()))
    runner.record_task("task-1", trajectory, score)  # type: ignore[arg-type]

    assert len(list(runner.events.iter_records())) == count
    assert runner.state.completed_task_ids == ("task-1",)
    assert runner.state.action_count == 2


def test_checkpoint_resume_completes_pending_task_without_duplicates(tmp_path: Path) -> None:
    saves = tmp_path / "saves"
    _make_save(saves, "Fixture_1")
    checkpoints = RunCheckpointManager(
        CheckpointManager(saves, stable_checks=2, stable_interval=0, stable_timeout=1)
    )
    runner = ExperimentRunner(tmp_path / "runs", _config(), PROVENANCE)
    runner.start()
    runner.transition(
        RunPhase.DAY_COMPLETE, reason="scripted boundary", operation_id="day-8-complete"
    )
    bundle = tmp_path / "checkpoint"
    runner.publish_checkpoint(checkpoints, destination=bundle)
    runner.transition(RunPhase.RUNNING, reason="next work", operation_id="day-9-start")
    trajectory, score = _trajectory()
    runner.events.append_idempotent(
        "task_started",
        {"task_id": "pending", "fixture_id": trajectory.fixture.fixture_id,
         "kind": trajectory.fixture.kind.value},
        idempotency_key="task:pending:started",
    )
    runner.transition(
        RunPhase.INTERRUPTED, reason="injected crash", operation_id="injected-crash"
    )

    state = runner.resume_from_checkpoint(checkpoints, bundle, "Restored_1")
    assert state.phase is RunPhase.RUNNING
    runner.record_task("pending", trajectory, score)  # type: ignore[arg-type]
    before_replay = len(list(runner.events.iter_records()))
    runner.record_task("pending", trajectory, score)  # type: ignore[arg-type]

    assert len(list(runner.events.iter_records())) == before_replay
    assert runner.state.completed_task_ids == ("pending",)
    assert runner.state.action_count == 2
    keys = [record.payload.get("idempotency_key") for record in runner.events.iter_records()]
    assert len(keys) == len(set(keys))
    assert (saves / "Restored_1" / "Restored_1").read_text(encoding="utf-8") == "current"


def test_lifecycle_rejects_invalid_terminal_transition(tmp_path: Path) -> None:
    runner = ExperimentRunner(tmp_path / "runs", _config(), PROVENANCE)
    runner.start()
    runner.transition(RunPhase.COMPLETED, reason="done", operation_id="complete")
    with pytest.raises(RunnerError, match="Invalid lifecycle"):
        runner.transition(RunPhase.RUNNING, reason="illegal", operation_id="restart")


def test_latest_completed_day_checkpoint_ignores_invalid_and_selects_newest(
    tmp_path: Path,
) -> None:
    saves = tmp_path / "saves"
    _make_save(saves, "Fixture_1")
    manager = RunCheckpointManager(
        CheckpointManager(saves, stable_checks=2, stable_interval=0, stable_timeout=1)
    )
    runner = ExperimentRunner(tmp_path / "runs", _config(), PROVENANCE)
    runner.start()
    root = tmp_path / "checkpoints"
    runner.transition(RunPhase.DAY_COMPLETE, reason="day 8", operation_id="day-8")
    runner.publish_checkpoint(
        manager,
        destination=root / "day-8",
        game_date=GameDate(year=1, season="spring", day=8),
    )
    runner.transition(RunPhase.RUNNING, reason="day 9", operation_id="day-9-start")
    runner.transition(RunPhase.DAY_COMPLETE, reason="day 9", operation_id="day-9")
    latest = runner.publish_checkpoint(
        manager,
        destination=root / "day-9",
        game_date=GameDate(year=1, season="spring", day=9),
    )
    (root / "invalid").mkdir()

    path, manifest = runner.latest_completed_day_checkpoint(manager, root)

    assert path == root / "day-9"
    assert manifest.checkpoint_id == latest.checkpoint_id


def test_provider_usage_budget_and_auxiliary_telemetry_are_attributed(tmp_path: Path) -> None:
    config = _config().model_copy(update={
        "budget": ExperimentBudget(
            max_actions=20,
            max_game_days=1,
            max_model_calls=1,
            max_input_tokens=10,
            max_output_tokens=5,
            max_cost_usd=0.1,
            max_wall_seconds=60,
        )
    })
    runner = ExperimentRunner(tmp_path / "runs", config, PROVENANCE)
    runner.start()
    usage = ModelCallUsage(
        request_id="provider-request-1",
        input_tokens=8,
        output_tokens=4,
        latency_ms=125,
        cost_usd=0.05,
    )
    first = runner.record_model_call("call-1", usage)
    replay = runner.record_model_call("call-1", usage)
    runner.record_memory_access("memory-1", operation="read", memory_ids=("m1", "m2"))
    runner.record_skill_use("skill-1", skill="water-crop", version="1")
    runner.record_artifact("artifact-1", kind="trajectory", path="trace.json", sha256="a" * 64)
    runner.record_error(
        "error-1", error_type="InjectedError", message="test", recoverable=True
    )

    assert replay == first
    assert runner.state.model_call_count == 1
    assert runner.state.input_tokens == 8
    assert runner.state.output_tokens == 4
    assert runner.state.cost_usd == pytest.approx(0.05)
    with pytest.raises(RunnerError, match="Model-call budget"):
        runner.record_model_call(
            "call-2",
            usage.model_copy(update={"request_id": "provider-request-2"}),
        )
