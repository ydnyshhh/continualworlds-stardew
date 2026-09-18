"""Run the M6 paired memory ablation through authenticated Prime Agent sessions."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .agent import ContextInputs, ContextItem, PrimeRpcProvider, PrimeSession
from .experiments.config import (
    LearningConditionConfig, ProviderConfig, load_run_config,
)
from .experiments.lifecycle import RunPhase
from .experiments.provenance import (
    RunProvenance, RuntimeProvenance, artifact_sha256, capture_code_provenance,
)
from .experiments.runner import ExperimentRunner
from .memory import MemoryQuery, MemoryStore
from .m6_gate import EXPECTED_SLOT, SECRET


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/m6-memory.yaml"))
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--prime-executable", type=Path, required=True)
    parser.add_argument("--node-dir", type=Path)
    parser.add_argument("--prime-config-dir", type=Path, required=True)
    parser.add_argument("--prime-session-root", type=Path, required=True)
    parser.add_argument("--gate-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.gate_root.resolve()
    if root.exists():
        raise RuntimeError(f"Gate root already exists: {root}")
    root.mkdir(parents=True)

    base = load_run_config(args.config).model_copy(update={
        "provider": ProviderConfig(
            provider=args.provider, model=args.model, route="prime-agent-rpc",
            decoding=load_run_config(args.config).provider.decoding,
        ),
    })
    provenance = RunProvenance(
        code=capture_code_provenance(Path.cwd()),
        runtime=RuntimeProvenance(
            game_version="1.6.15.24356", smapi_version="4.5.2",
            stardojo_version="1.0.0-patched",
            stardojo_dll_sha256="6fffa01cdba2b8db5d3c1e008969f05bad9a2730bc2e93c1f8cc2c403d87506b",
        ),
    )
    state = {
        "save_id": base.fixture.save_id, "player": base.fixture.player,
        "date": base.fixture.starting_date.model_dump(mode="json"),
        "location": "Farm", "position": [62, 17], "seed": base.seed,
    }
    checkpoint_digest = artifact_sha256(state)
    seed = MemoryStore(root / "seed.sqlite3", store_id="m6-live-seed")
    seed.add_text(
        f"The explicit hidden-fixture rule is {SECRET}.",
        memory_id="hidden-watering-slot", source_event_ids=("m5-live:task-2",),
        season="spring", map_name="Farm", task_domain="watering", confidence=1,
    )
    seed.add_text(
        "Unrelated market notes: " + "prices weather villagers festivals " * 10,
        memory_id="recent-distractor", task_domain="shopping", confidence=1,
    )
    export_path = root / "memory-export.json"
    seed.export(export_path, provenance={"checkpoint_sha256": checkpoint_digest})
    seed.close()

    conditions = (
        ("A-stateless", LearningConditionConfig(
            recent_context=False, persistent_memory=False, retrieval=False,
            skills=False, refinement=False,
        ), False),
        ("B-context-only", LearningConditionConfig(
            recent_context=True, persistent_memory=False, retrieval=False,
            skills=False, refinement=False,
        ), False),
        ("C-memory", LearningConditionConfig(
            recent_context=True, persistent_memory=True, retrieval=False,
            skills=False, refinement=False,
        ), True),
        ("D-memory-retrieval", LearningConditionConfig(
            recent_context=True, persistent_memory=True, retrieval=True,
            skills=False, refinement=False,
        ), True),
    )
    environment = {
        "PRIME_AGENT_CODING_AGENT_DIR": str(args.prime_config_dir.resolve()),
        "PI_SKIP_VERSION_CHECK": "1",
    }
    if args.node_dir:
        environment["PATH"] = str(args.node_dir.resolve()) + os.pathsep + os.environ.get("PATH", "")
    results = [
        _run(
            root, base, provenance, name, learning, index,
            export_path if import_memory else None, checkpoint_digest,
            args, environment,
        )
        for index, (name, learning, import_memory) in enumerate(conditions, 1)
    ]
    removed = _run(
        root, base, provenance, "D-memory-retrieval-memory-removed",
        conditions[-1][1], 5, None, checkpoint_digest, args, environment,
    )
    by_name = {item["condition"]: item for item in results}
    all_items = [*results, removed]
    passed = (
        len({item["actual_model"] for item in all_items}) == 1
        and len({item["checkpoint_sha256"] for item in all_items}) == 1
        and not by_name["A-stateless"]["success"]
        and not by_name["B-context-only"]["success"]
        and not by_name["C-memory"]["success"]
        and by_name["D-memory-retrieval"]["success"]
        and not removed["success"]
    )
    report = {
        "status": "passed" if passed else "failed",
        "scope": "authenticated Prime Agent M6 paired memory ablation",
        "provider": args.provider, "model": args.model,
        "checkpoint_sha256": checkpoint_digest,
        "conditions": results, "memory_removal_control": removed,
        "retrieval_gain": int(by_name["D-memory-retrieval"]["success"])
        - int(by_name["C-memory"]["success"]),
        "memory_removal_eliminated_gain": (
            by_name["D-memory-retrieval"]["success"] and not removed["success"]
        ),
        "game_state_unchanged": len({item["checkpoint_sha256"] for item in all_items}) == 1,
        "total_model_calls": sum(int(item["model_calls"]) for item in all_items),
        "total_input_tokens": sum(int(item["input_tokens"]) for item in all_items),
        "total_output_tokens": sum(int(item["output_tokens"]) for item in all_items),
        "total_cost_usd": sum(float(item["cost_usd"]) for item in all_items),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if not passed:
        raise RuntimeError("Authenticated M6 paired ablation failed")
    print(json.dumps(report, indent=2))


def _run(
    root: Path, base, provenance: RunProvenance, name: str,
    learning: LearningConditionConfig, repetition: int,
    memory_export: Path | None, checkpoint_digest: str,
    args: argparse.Namespace, environment: dict[str, str],
) -> dict[str, object]:
    config = base.model_copy(update={
        "condition": name, "learning": learning, "repetition": repetition,
    })
    runner = ExperimentRunner(root / "runs", config, provenance)
    runner.start()
    store = MemoryStore(root / "memory" / f"{name}.sqlite3", store_id=name)
    if memory_export is not None:
        store.import_bundle(memory_export)
    provider = PrimeRpcProvider(
        executable=str(args.prime_executable.resolve()), provider=args.provider,
        model=args.model, session_dir=args.prime_session_root / name,
        cwd=Path.cwd(), environment=environment,
    )
    session = PrimeSession(runner, provider, memory_store=store)
    try:
        decision = session.decide(
            ContextInputs(
                objective=(
                    "Select one inventory slot using only explicit facts in this context. "
                    "If no context fact states a value for M6_SECRET_SLOT, choose slot 0. "
                    "If a fact supplies its value, choose that integer slot."
                ),
                observation=json.dumps({
                    "location": "Farm", "season": "spring",
                    "inventory_slots": list(range(8)), "hidden_fixture": True,
                }, sort_keys=True),
                allowed_actions=("choose_item",),
                recent_events=(ContextItem(
                    item_id="recent-1", text="The current tile is safe and clear.",
                ),),
            ),
            memory_query=MemoryQuery(
                text="M6 secret slot hidden watering fixture inventory",
                season="spring", map_name="Farm", task_domain="watering",
                game_day=8, limit=2, token_budget=config.context.memories_tokens,
            ),
        )
        selected_slot = int(decision.actions[0].arguments[0])
        runner.transition(RunPhase.COMPLETED, reason="M6 live condition complete", operation_id="complete")
        calls = [e.payload for e in runner.events.iter_records()
                 if e.event_type == "model_call_completed"]
        retrievals = [e for e in runner.events.iter_records()
                      if e.event_type == "memory_retrieval"]
        return {
            "condition": name, "run_id": runner.run_id,
            "checkpoint_sha256": checkpoint_digest,
            "selected_slot": selected_slot, "expected_slot": EXPECTED_SLOT,
            "success": selected_slot == EXPECTED_SLOT,
            "memory_count": len(store.records()),
            "selected_memory_ids": retrievals[0].payload["selected_ids"] if retrievals else [],
            "actual_provider": calls[-1]["provider"],
            "actual_model": calls[-1]["model"], "actual_route": calls[-1]["route"],
            "model_calls": len(calls),
            "input_tokens": sum(call["input_tokens"] for call in calls),
            "output_tokens": sum(call["output_tokens"] for call in calls),
            "cost_usd": sum(call["cost_usd"] for call in calls),
            "final_phase": runner.state.phase.value,
        }
    finally:
        session.close()
        store.close()


if __name__ == "__main__":
    main()

