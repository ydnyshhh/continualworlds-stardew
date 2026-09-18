"""M9 multi-day project fixtures, simulation, checkpointing, and reporting."""

from .checkpoints import (
    BenchmarkCheckpointError, BenchmarkCheckpointManager, BenchmarkCheckpointManifest,
)
from .fixtures import load_project_fixtures
from .models import (
    ProjectAction, ProjectActionKind, ProjectFixture, ProjectKind, ProjectScore,
    SeasonDayResult, SeasonMetrics, SeasonReport, SeasonState,
)
from .season import (
    SeasonSimulator, append_day_events, benchmark_policy, build_report_from_events,
    score_projects,
)

__all__ = [
    "BenchmarkCheckpointError", "BenchmarkCheckpointManager",
    "BenchmarkCheckpointManifest", "ProjectAction", "ProjectActionKind",
    "ProjectFixture", "ProjectKind", "ProjectScore", "SeasonDayResult",
    "SeasonMetrics", "SeasonReport", "SeasonSimulator", "SeasonState",
    "append_day_events", "benchmark_policy", "build_report_from_events",
    "load_project_fixtures", "score_projects",
]
