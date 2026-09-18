"""Load and authenticate E1 study configurations."""

from __future__ import annotations

from pathlib import Path

import yaml

from prime_stardew.experiments.provenance import artifact_sha256

from .models import E1StudyConfig


def load_e1_config(path: Path) -> tuple[E1StudyConfig, str]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"E1 configuration must be a mapping: {path}")
    config = E1StudyConfig.model_validate(value)
    return config, artifact_sha256(config.model_dump(mode="json"))

