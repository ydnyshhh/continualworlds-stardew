"""Validate, initialize, inspect, and transition M4 experiment runs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .experiments.config import load_run_config
from .experiments.lifecycle import RunPhase
from .experiments.provenance import (
    RunProvenance,
    RuntimeProvenance,
    capture_code_provenance,
)
from .experiments.runner import ExperimentRunner


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--runs-root", type=Path, default=Path("runtime/runs"))
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    subparsers = parser.add_subparsers(dest="operation", required=True)
    subparsers.add_parser("validate")
    subparsers.add_parser("init")
    subparsers.add_parser("start")
    subparsers.add_parser("status")
    transition = subparsers.add_parser("transition")
    transition.add_argument("phase", choices=[phase.value for phase in RunPhase])
    transition.add_argument("--operation-id", required=True)
    transition.add_argument("--reason", required=True)
    args = parser.parse_args()

    config = load_run_config(args.config)
    identity = {
        "run_id": config.run_id(),
        "config_hash": config.config_hash(),
        "configuration": config.model_dump(mode="json"),
    }
    if args.operation == "validate":
        print(json.dumps(identity, indent=2))
        return

    provenance = RunProvenance(
        code=capture_code_provenance(args.workspace),
        runtime=RuntimeProvenance(),
    )
    runner = ExperimentRunner(args.runs_root, config, provenance)
    if args.operation == "start":
        runner.start()
    elif args.operation == "transition":
        runner.transition(
            RunPhase(args.phase),
            reason=args.reason,
            operation_id=args.operation_id,
        )
    state = runner.state
    print(json.dumps({
        **identity,
        "run_directory": str(runner.run_dir),
        "state": state.model_dump(mode="json"),
        "event_cursor": runner.events.cursor().model_dump(mode="json"),
        "equivalence_digest": runner.equivalence_digest(),
    }, indent=2))


if __name__ == "__main__":
    main()
