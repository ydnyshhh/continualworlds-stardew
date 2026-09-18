"""Preregistered paired continual-learning studies."""

from .analysis import analyze_study, bootstrap_mean_ci, exact_two_sided_sign_test
from .models import (
    PairedResult, StudyCondition, StudyPreregistration, StudyReport, StudyRunResult,
)
from .preregistration import load_preregistration

__all__ = [
    "PairedResult", "StudyCondition", "StudyPreregistration", "StudyReport",
    "StudyRunResult", "analyze_study", "bootstrap_mean_ci",
    "exact_two_sided_sign_test", "load_preregistration",
]
