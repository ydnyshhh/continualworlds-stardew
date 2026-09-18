"""Preview or apply verified combined-checkpoint retention."""

from __future__ import annotations

import argparse
from pathlib import Path

from .checkpoint_cli import default_saves_root
from .env.checkpoints import CheckpointManager
from .experiments.checkpoints import RunCheckpointManager
from .experiments.retention import CheckpointRetentionManager, RetentionPolicy


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--saves-root", type=Path, default=default_saves_root())
    parser.add_argument("--latest-days", type=int, default=2)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Delete the verified day checkpoints listed by a freshly generated plan",
    )
    args = parser.parse_args()

    retention = CheckpointRetentionManager(
        args.root,
        RunCheckpointManager(CheckpointManager(args.saves_root)),
    )
    plan = retention.plan(RetentionPolicy(latest_completed_days=args.latest_days))
    if args.apply:
        print(retention.apply(plan, dry_run=False).model_dump_json(indent=2))
    else:
        print(plan.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
