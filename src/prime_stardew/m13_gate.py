"""Run the preregistered deterministic M13 experience-replay study."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .experience_replay.research import (
    build_m13_report_from_events, load_m13_preregistration, run_m13_condition,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--preregistration", type=Path,
        default=Path("configs/m13-experience-replay-study.yaml"),
    )
    parser.add_argument("--gate-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.gate_root.resolve()
    if root.exists():
        raise RuntimeError(f"Gate root already exists: {root}")
    root.mkdir(parents=True)
    registration, digest = load_m13_preregistration(args.preregistration)
    for seed in registration.seeds:
        for condition in registration.conditions:
            run_m13_condition(
                root, seed=seed, condition=condition,
                replay_budget=registration.replay_budget,
                preregistration_sha256=digest,
            )
    report = build_m13_report_from_events(root, registration, digest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if report["status"] != "passed":
        raise RuntimeError("M13 deterministic gate failed")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

