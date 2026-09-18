"""Versioned held-out transfer fixtures."""

from __future__ import annotations

import json
from pathlib import Path

from .models import TransferTask


CATALOG = Path(__file__).with_name("transfer-fixtures-v1.json")


def load_transfer_tasks(path: Path = CATALOG) -> tuple[TransferTask, ...]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise ValueError("Transfer fixture catalog must be a list")
    tasks = tuple(TransferTask.model_validate(item) for item in value)
    if len({task.task_id for task in tasks}) != len(tasks):
        raise ValueError("Transfer fixture IDs must be unique")
    return tasks
