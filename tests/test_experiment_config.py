from pathlib import Path

import pytest
from pydantic import ValidationError

from prime_stardew.experiments.config import RunConfig, load_run_config


YAML = """
schema_version: 1
suite: m4-scripted-gate
condition: deterministic
seed: 406041616
repetition: 1
observation_mode: replay
fixture:
  save_id: Fixture_1
  player: PrimeStardewSmoke
  starting_date: {year: 1, season: spring, day: 8}
tasks: [m3-turn-move-v1]
provider:
  provider: scripted
  model: scripted-v1
  route: local
budget:
  max_actions: 10
  max_game_days: 1
  max_model_calls: 0
  max_input_tokens: 0
  max_output_tokens: 0
  max_cost_usd: 0
  max_wall_seconds: 60
tags: [m4, gate]
metadata:
  - {key: purpose, value: deterministic-runner-test}
"""


def test_yaml_config_is_strict_frozen_and_has_stable_identity(tmp_path: Path) -> None:
    path = tmp_path / "run.yaml"
    path.write_text(YAML, encoding="utf-8")

    first = load_run_config(path)
    second = RunConfig.model_validate(first.model_dump(mode="json"))

    assert first.config_hash() == second.config_hash()
    assert first.run_id() == second.run_id()
    assert first.run_id().startswith("m4-scripted-gate-scripted-v1-deterministic-s406041616-")
    with pytest.raises(ValidationError, match="frozen"):
        first.seed = 2  # type: ignore[misc]


def test_config_hash_changes_with_repetition_and_unknown_fields_fail(tmp_path: Path) -> None:
    path = tmp_path / "run.yaml"
    path.write_text(YAML, encoding="utf-8")
    first = load_run_config(path)
    second = first.model_copy(update={"repetition": 2})
    assert first.config_hash() != second.config_hash()
    assert first.run_id() != second.run_id()

    bad = tmp_path / "bad.yaml"
    bad.write_text(YAML + "unknown: true\n", encoding="utf-8")
    with pytest.raises(ValidationError, match="Extra inputs"):
        load_run_config(bad)
