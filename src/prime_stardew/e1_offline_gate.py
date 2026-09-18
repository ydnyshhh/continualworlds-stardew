"""Run E1 deterministic contract validation without making scientific claims."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .e1.config import load_e1_config
from .e1.offline import run_offline_matrix


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/e1-offline-validation.yaml"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    if root.exists():
        raise RuntimeError(f"E1 validation root already exists: {root}")
    root.mkdir(parents=True)
    config, digest = load_e1_config(args.config)
    report = run_offline_matrix(root, config, digest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if report["status"] != "passed":
        raise RuntimeError("E1 offline contract validation failed")
    detail_keys = {
        "raw_runs", "competency_rows", "inference_rows", "memory_rows",
        "skill_rows", "refinement_rows",
    }
    print(json.dumps({key: value for key, value in report.items() if key not in detail_keys}, indent=2))


if __name__ == "__main__":
    main()
