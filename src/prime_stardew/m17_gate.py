"""Run the preregistered deterministic M17 noisy-evidence curriculum study."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .curriculum import build_m17_report_from_events, load_m17_preregistration, run_m17_condition


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preregistration", type=Path, default=Path("configs/m17-noisy-curriculum-study.yaml"))
    parser.add_argument("--gate-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.gate_root.resolve()
    if root.exists():
        raise RuntimeError(f"Gate root already exists: {root}")
    root.mkdir(parents=True)
    registration, digest = load_m17_preregistration(args.preregistration)
    for seed in registration.seeds:
        for condition in registration.conditions:
            run_m17_condition(
                root, seed=seed, condition=condition,
                practice_budget=registration.practice_budget,
                heldout_tasks_per_regime=registration.heldout_tasks_per_regime,
                preregistration_sha256=digest,
            )
    report = build_m17_report_from_events(root, registration, digest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if report["status"] != "passed":
        raise RuntimeError("M17 deterministic gate failed")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
