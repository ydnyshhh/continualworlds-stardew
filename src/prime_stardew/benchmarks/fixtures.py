"""Load the checked-in M9 project catalog."""

from __future__ import annotations

import json
from importlib.resources import files

from .models import ProjectFixture


def load_project_fixtures() -> tuple[ProjectFixture, ...]:
    path = files("prime_stardew.benchmarks").joinpath("project-fixtures-v1.json")
    values = json.loads(path.read_text(encoding="utf-8"))
    fixtures = tuple(ProjectFixture.model_validate(value) for value in values)
    ids = [fixture.fixture_id for fixture in fixtures]
    if len(set(ids)) != len(ids):
        raise ValueError("Project fixture IDs must be unique")
    return fixtures
