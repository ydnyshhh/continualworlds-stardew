"""Load the checked-in, versioned M3 atomic fixture catalog."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import TypeAdapter

from .models import TaskFixture


CATALOG = Path(__file__).with_name("atomic-fixtures-v1.json")


def load_atomic_fixtures(path: Path = CATALOG) -> tuple[TaskFixture, ...]:
    data = json.loads(path.read_text(encoding="utf-8"))
    fixtures = TypeAdapter(tuple[TaskFixture, ...]).validate_python(data)
    ids = tuple(fixture.fixture_id for fixture in fixtures)
    if len(ids) != len(set(ids)):
        raise ValueError("Fixture IDs must be unique")
    return fixtures
