from pathlib import Path

import pytest

from prime_stardew.benchmarks import (
    BenchmarkCheckpointError, BenchmarkCheckpointManager, ProjectKind,
    SeasonSimulator, append_day_events, benchmark_policy, build_report_from_events,
    load_project_fixtures, score_projects,
)
from prime_stardew.experiments.config import LearningConditionConfig
from prime_stardew.telemetry import EventStore


LEARNING = LearningConditionConfig(
    recent_context=True, persistent_memory=True, retrieval=True,
    skills=True, refinement=True,
)


def _run_days(store: EventStore, simulator: SeasonSimulator, count: int) -> None:
    fixtures = load_project_fixtures()
    for _ in range(count):
        result = simulator.step(benchmark_policy(simulator.state.completed_days + 1), fixtures)
        append_day_events(store, result)


def test_project_fixture_catalog_is_versioned_and_complete() -> None:
    fixtures = load_project_fixtures()
    assert len(fixtures) == 3
    assert {fixture.kind for fixture in fixtures} == set(ProjectKind)
    assert all(fixture.schema_version == 1 and fixture.horizon_days == 28 for fixture in fixtures)


def test_policy_completes_all_projects_with_intermediate_progress() -> None:
    fixtures = load_project_fixtures()
    simulator = SeasonSimulator(406041616, LEARNING)
    progress = []
    for day in range(1, 29):
        result = simulator.step(benchmark_policy(day), fixtures)
        progress.append(tuple(score.progress for score in result.project_scores))
    scores = score_projects(simulator.state, fixtures)
    assert all(score.success and score.progress == 1 for score in scores)
    assert progress[0] != progress[-1]
    assert simulator.state.cauliflower_harvested >= 10
    assert simulator.state.gold >= 1500
    assert simulator.state.mine_level >= 40
    assert simulator.state.pickaxe_tier >= 1
    assert "Coop" in simulator.state.buildings
    assert simulator.state.chickens >= 1


def test_benchmark_checkpoint_authenticates_state_config_and_event_prefix(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.jsonl", "benchmark-run")
    simulator = SeasonSimulator(1, LEARNING)
    _run_days(store, simulator, 14)
    manager = BenchmarkCheckpointManager()
    checkpoint = tmp_path / "checkpoint"
    manifest = manager.create(
        checkpoint, run_id=store.run_id, config_sha256="a" * 64,
        state=simulator.state, event_store=store,
    )
    store.append("benchmark_interrupted", {"after_day": 14})
    restored = manager.restore(
        checkpoint, event_store=store, expected_config_sha256="a" * 64,
    )
    assert restored.completed_days == 14
    assert manifest.event_cursor.sequence < store.cursor().sequence
    with pytest.raises(BenchmarkCheckpointError, match="configuration hash"):
        manager.restore(
            checkpoint, event_store=store, expected_config_sha256="b" * 64,
        )


def test_benchmark_checkpoint_detects_state_tamper(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.jsonl", "benchmark-run")
    simulator = SeasonSimulator(1, LEARNING)
    _run_days(store, simulator, 1)
    checkpoint = tmp_path / "checkpoint"
    manager = BenchmarkCheckpointManager()
    manager.create(
        checkpoint, run_id=store.run_id, config_sha256="a" * 64,
        state=simulator.state, event_store=store,
    )
    (checkpoint / "state.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(BenchmarkCheckpointError, match="state hash"):
        manager.verify(
            checkpoint, event_store=store, expected_config_sha256="a" * 64,
        )


def test_report_is_rebuilt_from_28_unique_day_events(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.jsonl", "benchmark-run")
    simulator = SeasonSimulator(7, LEARNING)
    _run_days(store, simulator, 28)
    report = build_report_from_events(
        store, load_project_fixtures(), seed=7, condition="full",
    )
    assert report.completed_days == 28
    assert all(score.success and score.criterion_day for score in report.project_scores)
    assert report.metrics.learning["observations_to_all_criteria"] == 20
    assert report.metrics.systems["events"] == store.cursor().sequence
    assert all(len(curve) == 28 for curve in report.progress_curves.values())
