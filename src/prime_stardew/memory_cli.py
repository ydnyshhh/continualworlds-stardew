"""Inspect, query, transfer, and add PrimeStardew external memories."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .memory import MemoryKind, MemoryQuery, MemoryStore


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--store-id", default="prime-stardew-memory")
    commands = parser.add_subparsers(dest="operation", required=True)

    add = commands.add_parser("add")
    add.add_argument("--text", required=True)
    add.add_argument("--kind", choices=[kind.value for kind in MemoryKind], default="semantic")
    add.add_argument("--memory-id")
    add.add_argument("--source-event", action="append", default=[])
    add.add_argument("--season")
    add.add_argument("--map-name")
    add.add_argument("--task-domain")
    add.add_argument("--confidence", type=float, default=1)
    add.add_argument("--tag", action="append", default=[])

    retrieve = commands.add_parser("retrieve")
    retrieve.add_argument("query")
    retrieve.add_argument("--season")
    retrieve.add_argument("--map-name")
    retrieve.add_argument("--task-domain")
    retrieve.add_argument("--game-day", type=int)
    retrieve.add_argument("--min-confidence", type=float, default=0)
    retrieve.add_argument("--limit", type=int, default=5)
    retrieve.add_argument("--token-budget", type=int, default=512)

    commands.add_parser("list")
    export = commands.add_parser("export")
    export.add_argument("destination", type=Path)
    export.add_argument("--provenance-json", default="{}")
    import_command = commands.add_parser("import")
    import_command.add_argument("bundle", type=Path)

    args = parser.parse_args()
    with MemoryStore(args.database, store_id=args.store_id) as store:
        if args.operation == "add":
            result = store.add_text(
                args.text, kind=MemoryKind(args.kind), memory_id=args.memory_id,
                source_event_ids=tuple(args.source_event), season=args.season,
                map_name=args.map_name, task_domain=args.task_domain,
                confidence=args.confidence, tags=tuple(args.tag),
            )
            print(result.model_dump_json(indent=2))
        elif args.operation == "retrieve":
            result = store.retrieve(MemoryQuery(
                text=args.query, season=args.season, map_name=args.map_name,
                task_domain=args.task_domain, game_day=args.game_day,
                min_confidence=args.min_confidence, limit=args.limit,
                token_budget=args.token_budget,
            ))
            print(result.model_dump_json(indent=2))
        elif args.operation == "list":
            print(json.dumps(
                [record.model_dump(mode="json") for record in store.records(active_only=False)],
                indent=2,
            ))
        elif args.operation == "export":
            provenance = json.loads(args.provenance_json)
            if not isinstance(provenance, dict) or not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in provenance.items()
            ):
                parser.error("--provenance-json must be an object of string keys and values")
            print(store.export(args.destination, provenance=provenance).model_dump_json(indent=2))
        else:
            print(json.dumps({"imported_memory_ids": store.import_bundle(args.bundle)}, indent=2))


if __name__ == "__main__":
    main()

