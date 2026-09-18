"""Run the preregistered deterministic M15 Stardew-Shift study."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .shifting import (
    build_m15_report_from_events, load_m15_preregistration, run_m15_condition,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--preregistration", type=Path,
        default=Path("configs/m15-stardew-shift-study.yaml"),
    )
    parser.add_argument("--gate-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.gate_root.resolve()
    if root.exists():
        raise RuntimeError(f"Gate root already exists: {root}")
    root.mkdir(parents=True)
    preregistration, digest = load_m15_preregistration(args.preregistration)
    for seed in preregistration.seeds:
        for condition in preregistration.conditions:
            run_m15_condition(
                root, seed=seed, condition=condition,
                trials_per_phase=preregistration.trials_per_phase,
                preregistration_sha256=digest,
            )
    report = build_m15_report_from_events(root, preregistration, digest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if report["status"] != "passed":
        raise RuntimeError("M15 deterministic gate failed")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
