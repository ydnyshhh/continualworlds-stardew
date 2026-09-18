from pathlib import Path

import pytest

from prime_stardew.benchmarks import (
    SeasonSimulator, benchmark_policy, load_project_fixtures, score_projects,
)
from prime_stardew.studies import (
    StudyRunResult, analyze_study, bootstrap_mean_ci, exact_two_sided_sign_test,
    load_preregistration,
)


PREREGISTRATION = Path("configs/m10-proceduralization-study.yaml")


def test_preregistration_is_hashed_and_isolates_skill_availability() -> None:
    study, digest = load_preregistration(PREREGISTRATION)
    assert len(study.seeds) == 8
    assert [condition.learning.skills for condition in study.conditions] == [False, True]
    assert len(digest) == 64
    with pytest.raises(Exception):
        study.title = "changed"  # type: ignore[misc]


def test_procedural_skill_reduces_decisions_without_changing_outcome() -> None:
    study, _ = load_preregistration(PREREGISTRATION)
    fixtures = load_project_fixtures()
    decisions = []
    states = []
    for condition in study.conditions:
        simulator = SeasonSimulator(
            study.seeds[0], condition.learning,
            procedural_skills_reduce_decisions=condition.learning.skills,
        )
        for day in range(1, 29):
            simulator.step(benchmark_policy(day), fixtures)
        decisions.append(simulator.state.decisions)
        states.append(simulator.state)
    assert decisions == [28, 23]
    assert all(all(score.success for score in score_projects(state, fixtures)) for state in states)
    assert states[0].primitive_actions == states[1].primitive_actions == 592


def test_declared_statistics_are_exact_and_deterministic() -> None:
    effects = (5,) * 8
    assert exact_two_sided_sign_test(effects) == pytest.approx(0.0078125)
    assert bootstrap_mean_ci(effects, samples=1_000, confidence=0.95) == (5.0, 5.0)


def test_analysis_pairs_seeds_and_requires_outcome_noninferiority() -> None:
    study, digest = load_preregistration(PREREGISTRATION)
    runs = []
    for condition in study.conditions:
        for seed in study.seeds:
            runs.append(StudyRunResult(
                seed=seed, condition=condition.name, run_id=f"{condition.name}-{seed}",
                start_state_sha256="a" * 64,
                model_decisions=23 if condition.learning.skills else 28,
                primitive_actions=592, invalid_actions=0, project_successes=3,
                mean_outcome_score=1, event_count=110, event_last_hash="b" * 64,
            ))
    report = analyze_study(study, digest, tuple(runs))
    assert report.status == "passed"
    assert report.paired_runs == 8
    assert report.decision_reduction_ci95 == (5, 5)
    assert report.outcome_noninferiority_passed


def test_analysis_failure_report_identifies_missing_pair() -> None:
    study, digest = load_preregistration(PREREGISTRATION)
    report = analyze_study(study, digest, ())
    assert report.status == "failed"
    assert report.failure_analysis["missing_pair_seeds"] == [str(seed) for seed in study.seeds]
