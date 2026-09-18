"""Rebuild one M9 season report exclusively from its append-only event log."""

from __future__ import annotations

import argparse
from pathlib import Path

from .benchmarks import build_report_from_events, load_project_fixtures
from .telemetry import EventStore


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-log", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--condition", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    store = EventStore(args.event_log, args.run_id)
    report = build_report_from_events(
        store, load_project_fixtures(), seed=args.seed, condition=args.condition,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(report.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
