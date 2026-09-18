"""Load and authenticate a study preregistration before any study run starts."""

from __future__ import annotations

from pathlib import Path

import yaml

from prime_stardew.experiments.provenance import artifact_sha256

from .models import StudyPreregistration


def load_preregistration(path: Path) -> tuple[StudyPreregistration, str]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Preregistration must be a mapping: {path}")
    preregistration = StudyPreregistration.model_validate(value)
    digest = artifact_sha256(preregistration.model_dump(mode="json"))
    return preregistration, digest
