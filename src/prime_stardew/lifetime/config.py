"""Immutable M11 study configuration loading."""

from __future__ import annotations

from pathlib import Path

import yaml

from prime_stardew.experiments.provenance import artifact_sha256

from .models import LifetimeStudyConfig


def load_lifetime_config(path: Path) -> tuple[LifetimeStudyConfig, str]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Lifetime study configuration must be a mapping: {path}")
    config = LifetimeStudyConfig.model_validate(value)
    return config, artifact_sha256(config.model_dump(mode="json"))
