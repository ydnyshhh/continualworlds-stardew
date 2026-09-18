"""Load and authenticate E1 study configurations."""

from __future__ import annotations

from pathlib import Path

import yaml

from prime_stardew.experiments.config import MetadataEntry, RunConfig
from prime_stardew.experiments.provenance import artifact_sha256

from .conditions import condition_manifest, learning_config
from .models import E1Condition, E1StudyConfig


def load_e1_config(path: Path) -> tuple[E1StudyConfig, str]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"E1 configuration must be a mapping: {path}")
    config = E1StudyConfig.model_validate(value)
    return config, artifact_sha256(config.model_dump(mode="json"))


def materialize_run_config(
    study: E1StudyConfig, condition: E1Condition, seed: int,
) -> RunConfig:
    if condition not in study.conditions:
        raise ValueError(f"Condition {condition.value} is absent from study configuration")
    if seed not in study.seeds:
        raise ValueError(f"Seed {seed} is absent from study configuration")
    manifest = condition_manifest(
        condition, full_harness_features=study.full_harness_features,
    )
    context = study.base_run.context.model_copy(update={
        "memories_tokens": study.memory_token_budget,
    })
    budget = study.base_run.budget.model_copy(update={
        "max_game_days": study.horizon_days,
    })
    metadata = tuple(item for item in study.base_run.metadata if item.key not in {
        "study_id", "study_phase", "e1_condition",
    }) + (
        MetadataEntry(key="study_id", value=study.study_id),
        MetadataEntry(key="study_phase", value=study.phase.value),
        MetadataEntry(key="e1_condition", value=condition.value),
    )
    return study.base_run.model_copy(update={
        "condition": f"E1-{condition.value}",
        "seed": seed,
        "learning": learning_config(manifest),
        "context": context,
        "budget": budget,
        "metadata": metadata,
    })
