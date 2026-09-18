from prime_stardew.env.checkpoints import CheckpointError, CheckpointManager
from prime_stardew.env.models import GameDate
from prime_stardew.experiments.checkpoints import CheckpointKind, RunCheckpointManager
from prime_stardew.experiments.retention import CheckpointRetentionManager, RetentionPolicy
from prime_stardew.telemetry.events import EventStore


def _setup(tmp_path):
    saves = tmp_path / "saves"
    source = saves / "Fixture_1"
    source.mkdir(parents=True)
    (source / "Fixture_1").write_text("current", encoding="utf-8")
    (source / "Fixture_1_old").write_text("old", encoding="utf-8")
    manager = RunCheckpointManager(
        CheckpointManager(saves, stable_checks=2, stable_interval=0, stable_timeout=1)
    )
    root = tmp_path / "checkpoints"
    events = EventStore(tmp_path / "events.jsonl", "run-1")
    return manager, root, events


def _create(manager, root, events, name, day, kind=CheckpointKind.DAY):
    events.append("checkpoint_requested", {"name": name})
    return manager.create(
        run_id="run-1",
        destination=root / name,
        save_id="Fixture_1",
        player="Fixture",
        game_date=GameDate(year=1, season="spring", day=day),
        agent_state={"day": day},
        configuration={"seed": 1},
        event_store=events,
        kind=kind,
    )


def test_retention_protects_special_checkpoints_and_keeps_latest_distinct_days(tmp_path):
    manager, root, events = _setup(tmp_path)
    _create(manager, root, events, "base", 1, CheckpointKind.BASE)
    _create(manager, root, events, "day-1", 1)
    _create(manager, root, events, "day-2-old", 2)
    _create(manager, root, events, "day-2-new", 2)
    _create(manager, root, events, "day-3", 3)
    _create(manager, root, events, "milestone", 2, CheckpointKind.MILESTONE)
    _create(manager, root, events, "failure", 3, CheckpointKind.FAILURE)
    broken = root / "broken"
    broken.mkdir()
    (broken / "manifest.json").write_text("{}", encoding="utf-8")

    retention = CheckpointRetentionManager(root, manager)
    plan = retention.plan(RetentionPolicy(latest_completed_days=2))

    kept = {entry.path.name for entry in plan.keep}
    deleted = {entry.path.name for entry in plan.delete}
    assert kept == {"base", "day-2-new", "day-3", "milestone", "failure"}
    assert deleted == {"day-1", "day-2-old"}
    assert [issue.path.name for issue in plan.issues] == ["broken"]

    preview = retention.apply(plan)
    assert preview.dry_run is True
    assert (root / "day-1").exists()

    result = retention.apply(plan, dry_run=False)
    assert {path.name for path in result.deleted} == deleted
    assert not (root / "day-1").exists()
    assert (root / "base").exists()
    assert broken.exists()


def test_retention_rejects_stale_plan(tmp_path):
    manager, root, events = _setup(tmp_path)
    _create(manager, root, events, "day-1", 1)
    _create(manager, root, events, "day-2", 2)
    retention = CheckpointRetentionManager(root, manager)
    policy = RetentionPolicy(latest_completed_days=1)
    plan = retention.plan(policy)
    _create(manager, root, events, "day-3", 3)

    try:
        retention.apply(plan, dry_run=False)
    except CheckpointError as exc:
        assert "stale" in str(exc)
    else:
        raise AssertionError("Expected stale retention plan to be rejected")

    assert (root / "day-1").exists()
