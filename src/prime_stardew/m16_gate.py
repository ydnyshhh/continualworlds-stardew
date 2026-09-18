"""Run the preregistered deterministic M16 memory-corruption study."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .corruption import (
    build_m16_report_from_events, load_m16_preregistration, run_m16_condition,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--preregistration", type=Path, default=Path("configs/m16-memory-corruption-study.yaml"),
    )
    parser.add_argument("--gate-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.gate_root.resolve()
    if root.exists():
        raise RuntimeError(f"Gate root already exists: {root}")
    root.mkdir(parents=True)
    preregistration, digest = load_m16_preregistration(args.preregistration)
    for seed in preregistration.seeds:
        for condition in preregistration.conditions:
            run_m16_condition(
                root, seed=seed, condition=condition,
                interactions_per_record=preregistration.interactions_per_record,
                initial_verification_threshold=preregistration.initial_verification_threshold,
                preregistration_sha256=digest,
            )
    report = build_m16_report_from_events(root, preregistration, digest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if report["status"] != "passed":
        raise RuntimeError("M16 deterministic gate failed")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
