"""Run the preregistered M10 proceduralization study and paired analysis."""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

from .benchmarks import (
    SeasonSimulator, SeasonState, append_day_events, benchmark_policy,
    build_report_from_events, load_project_fixtures,
)
from .experiments.config import MetadataEntry, load_run_config
from .experiments.lifecycle import RunPhase
from .experiments.provenance import (
    RunProvenance, RuntimeProvenance, artifact_sha256, capture_code_provenance,
)
from .experiments.runner import ExperimentRunner
from .studies import StudyRunResult, analyze_study, load_preregistration


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--preregistration", type=Path,
        default=Path("configs/m10-proceduralization-study.yaml"),
    )
    parser.add_argument("--season-config", type=Path, default=Path("configs/m9-season.yaml"))
    parser.add_argument("--gate-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    root = args.gate_root.resolve()
    if root.exists():
        raise RuntimeError(f"Gate root already exists: {root}")
    root.mkdir(parents=True)
    preregistration, preregistration_sha256 = load_preregistration(args.preregistration)
    base = load_run_config(args.season_config)
    fixtures = load_project_fixtures()
    if preregistration.environment != "deterministic-season-replay-v1":
        raise RuntimeError("M10 gate supports only deterministic-season-replay-v1")
    if preregistration.policy != base.provider.model:
        raise RuntimeError("Preregistered policy does not match the season configuration")
    if tuple(fixture.fixture_id for fixture in fixtures) != base.tasks:
        raise RuntimeError("Season configuration tasks do not match the project catalog")

    start_state = SeasonState()
    start_state_payload = start_state.model_dump(mode="json")
    start_state_sha256 = artifact_sha256(start_state_payload)
    paired_checkpoint = root / "paired-start-checkpoint.json"
    paired_checkpoint.write_text(json.dumps({
        "schema_version": 1,
        "study_id": preregistration.study_id,
        "preregistration_sha256": preregistration_sha256,
        "state_sha256": start_state_sha256,
        "state": start_state_payload,
    }, indent=2) + "\n", encoding="utf-8")
    provenance = _provenance()
    runs: list[StudyRunResult] = []
    repetition = 0
    for condition in preregistration.conditions:
        for seed in preregistration.seeds:
            repetition += 1
            checkpoint_value = json.loads(paired_checkpoint.read_text(encoding="utf-8"))
            if checkpoint_value["preregistration_sha256"] != preregistration_sha256:
                raise RuntimeError("Paired checkpoint preregistration hash mismatch")
            if artifact_sha256(checkpoint_value["state"]) != checkpoint_value["state_sha256"]:
                raise RuntimeError("Paired checkpoint state hash mismatch")
            restored_start = SeasonState.model_validate(checkpoint_value["state"])
            metadata = (*base.metadata,
                MetadataEntry(key="study_id", value=preregistration.study_id),
                MetadataEntry(key="preregistration_sha256", value=preregistration_sha256),
                MetadataEntry(key="paired_start_state_sha256", value=start_state_sha256),
            )
            config = base.model_copy(update={
                "suite": preregistration.study_id,
                "condition": condition.name,
                "seed": seed,
                "repetition": repetition,
                "learning": condition.learning,
                "metadata": metadata,
            })
            runner = ExperimentRunner(root / "runs", config, provenance)
            runner.start()
            runner.events.append_idempotent(
                "study_preregistered",
                {
                    "study_id": preregistration.study_id,
                    "preregistration_sha256": preregistration_sha256,
                    "primary_metric": preregistration.primary_metric,
                    "condition": condition.name,
                    "seed": seed,
                    "start_state_sha256": start_state_sha256,
                },
                idempotency_key="study:preregistered",
            )
            simulator = SeasonSimulator(
                seed,
                condition.learning,
                restored_start,
                procedural_skills_reduce_decisions=condition.learning.skills,
            )
            for day in range(1, preregistration.max_days + 1):
                result = simulator.step(benchmark_policy(day), fixtures)
                append_day_events(runner.events, result)
            runner.transition(
                RunPhase.COMPLETED,
                reason="M10 paired season run complete",
                operation_id="complete",
            )
            report = build_report_from_events(
                runner.events, fixtures, seed=seed, condition=condition.name,
            )
            mean_outcome = statistics.mean(score.progress for score in report.project_scores)
            result = StudyRunResult(
                seed=seed,
                condition=condition.name,
                run_id=runner.run_id,
                start_state_sha256=start_state_sha256,
                model_decisions=int(report.metrics.efficiency["decisions"]),
                primitive_actions=int(report.metrics.efficiency["primitive_actions"]),
                invalid_actions=int(report.metrics.efficiency["invalid_actions"]),
                project_successes=sum(score.success for score in report.project_scores),
                mean_outcome_score=mean_outcome,
                event_count=report.source_event_count,
                event_last_hash=report.source_event_last_hash,
                excluded_reason=(
                    "One or more invalid project actions."
                    if report.metrics.efficiency["invalid_actions"] else None
                ),
            )
            result_path = root / "run-results" / f"{condition.name}-seed-{seed}.json"
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
            runs.append(result)

    report = analyze_study(preregistration, preregistration_sha256, tuple(runs))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    if report.status != "passed":
        raise RuntimeError("M10 gate failed")
    print(json.dumps(report.model_dump(mode="json"), indent=2))


def _provenance() -> RunProvenance:
    return RunProvenance(
        code=capture_code_provenance(Path.cwd()),
        runtime=RuntimeProvenance(
            game_version="1.6.15.24356",
            smapi_version="4.5.2",
            stardojo_version="1.0.0-patched",
            stardojo_dll_sha256=(
                "6fffa01cdba2b8db5d3c1e008969f05bad9a2730bc2e93c1f8cc2c403d87506b"
            ),
        ),
    )


if __name__ == "__main__":
    main()
