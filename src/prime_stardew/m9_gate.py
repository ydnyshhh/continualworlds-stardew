"""Run the three-seed M9 28-day benchmark with injected checkpoint recovery."""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

from .benchmarks import (
    BenchmarkCheckpointManager, SeasonReport, SeasonSimulator, append_day_events,
    benchmark_policy, build_report_from_events, load_project_fixtures,
)
from .experiments.config import load_run_config
from .experiments.lifecycle import RunPhase
from .experiments.provenance import RunProvenance, RuntimeProvenance, capture_code_provenance
from .experiments.runner import ExperimentRunner


DEFAULT_SEEDS = (406041616, 406041617, 406041618)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/m9-season.yaml"))
    parser.add_argument("--gate-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seeds", type=int, nargs="+", default=DEFAULT_SEEDS)
    args = parser.parse_args()
    if len(args.seeds) < 3 or len(set(args.seeds)) != len(args.seeds):
        parser.error("--seeds requires at least three distinct values")
    root = args.gate_root.resolve()
    if root.exists():
        raise RuntimeError(f"Gate root already exists: {root}")
    root.mkdir(parents=True)
    base = load_run_config(args.config)
    fixtures = load_project_fixtures()
    if tuple(fixture.fixture_id for fixture in fixtures) != base.tasks:
        raise RuntimeError("M9 configuration tasks do not match the project catalog")
    provenance = _provenance()
    reports: list[SeasonReport] = []
    recovery: dict[str, object] | None = None
    for repetition, seed in enumerate(args.seeds, 1):
        config = base.model_copy(update={"seed": seed, "repetition": repetition})
        runner = ExperimentRunner(root / "runs", config, provenance)
        runner.start()
        simulator = SeasonSimulator(seed, config.learning)
        checkpoint = root / "checkpoints" / f"seed-{seed}-day-14"
        manager = BenchmarkCheckpointManager()
        for day in range(1, 29):
            result = simulator.step(benchmark_policy(day), fixtures)
            append_day_events(runner.events, result)
            if repetition == 1 and day == 14:
                runner.transition(
                    RunPhase.DAY_COMPLETE, reason="M9 day 14 boundary",
                    operation_id="day-14-complete",
                )
                manifest = manager.create(
                    checkpoint, run_id=runner.run_id,
                    config_sha256=config.config_hash(), state=simulator.state,
                    event_store=runner.events,
                )
                runner.events.append_idempotent(
                    "benchmark_checkpoint_published",
                    {"checkpoint_id": manifest.checkpoint_id, "completed_day": 14,
                     "event_cursor": manifest.event_cursor.model_dump(mode="json")},
                    idempotency_key="benchmark-checkpoint:day-14",
                )
                runner.transition(
                    RunPhase.RUNNING, reason="continue after day checkpoint",
                    operation_id="day-14-continue",
                )
                runner.transition(
                    RunPhase.INTERRUPTED, reason="injected M9 crash",
                    operation_id="injected-crash",
                )
                runner.events.append_idempotent(
                    "benchmark_interrupted", {"after_day": 14, "injected": True},
                    idempotency_key="benchmark-interruption:day-14",
                )
                runner.transition(
                    RunPhase.RECOVERING, reason="restore M9 day 14 checkpoint",
                    operation_id="recovery-start",
                )
                restored = manager.restore(
                    checkpoint, event_store=runner.events,
                    expected_config_sha256=config.config_hash(),
                )
                simulator = SeasonSimulator(seed, config.learning, restored)
                runner.events.append_idempotent(
                    "benchmark_checkpoint_restored",
                    {"checkpoint_id": manifest.checkpoint_id,
                     "completed_day": restored.completed_days,
                     "trailing_events": len(tuple(runner.events.events_after(manifest.event_cursor)))},
                    idempotency_key="benchmark-checkpoint:day-14:restored",
                )
                runner.transition(
                    RunPhase.RUNNING, reason="M9 checkpoint restored",
                    operation_id="recovery-complete",
                )
                recovery = {
                    "seed": seed, "checkpoint_id": manifest.checkpoint_id,
                    "completed_day": restored.completed_days,
                    "cursor_sequence": manifest.event_cursor.sequence,
                    "trailing_events_before_resume": len(tuple(
                        runner.events.events_after(manifest.event_cursor)
                    )),
                }
        runner.transition(RunPhase.COMPLETED, reason="M9 season complete", operation_id="complete")
        report = build_report_from_events(
            runner.events, fixtures, seed=seed, condition=config.condition,
        )
        report_path = root / "reports" / f"seed-{seed}.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
        reports.append(report)

    all_scores = [score for report in reports for score in report.project_scores]
    gold = [int(report.metrics.environment["gold"]) for report in reports]
    actions = [int(report.metrics.efficiency["primitive_actions"]) for report in reports]
    passed = (
        len(reports) >= 3 and all(report.completed_days == 28 for report in reports)
        and all(score.success and score.progress == 1 for score in all_scores)
        and recovery is not None and recovery["completed_day"] == 14
        and all(report.metrics.efficiency["invalid_actions"] == 0 for report in reports)
    )
    aggregate = {
        "status": "passed" if passed else "failed",
        "scope": "M9 deterministic 28-day project benchmark and injected recovery gate",
        "condition": base.condition, "seeds": list(args.seeds),
        "runs": len(reports), "completed_days_per_run": [report.completed_days for report in reports],
        "project_successes": {
            fixture.fixture_id: sum(
                score.success for report in reports for score in report.project_scores
                if score.fixture_id == fixture.fixture_id
            )
            for fixture in fixtures
        },
        "criterion_days": {
            fixture.fixture_id: [
                score.criterion_day for report in reports for score in report.project_scores
                if score.fixture_id == fixture.fixture_id
            ]
            for fixture in fixtures
        },
        "gold": {"mean": statistics.mean(gold), "min": min(gold), "max": max(gold)},
        "primitive_actions": {
            "mean": statistics.mean(actions), "min": min(actions), "max": max(actions),
        },
        "recovery": recovery,
        "raw_event_reports": [
            {"run_id": report.run_id, "event_count": report.source_event_count,
             "last_hash": report.source_event_last_hash}
            for report in reports
        ],
        "manual_score_edits": 0,
        "causal_conclusions_drawn": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(aggregate, indent=2) + "\n", encoding="utf-8")
    if not passed:
        raise RuntimeError("M9 gate failed")
    print(json.dumps(aggregate, indent=2))


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
