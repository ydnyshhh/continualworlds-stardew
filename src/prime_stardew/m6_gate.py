"""Run the deterministic four-condition M6 memory/retrieval causal gate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .agent import ContextInputs, ContextItem, PrimeSession
from .agent.models import ProviderRequest, ProviderResponse, ProviderUsage
from .experiments.config import LearningConditionConfig, load_run_config
from .experiments.lifecycle import RunPhase
from .experiments.provenance import (
    RunProvenance, RuntimeProvenance, artifact_sha256, capture_code_provenance,
)
from .experiments.runner import ExperimentRunner
from .memory import MemoryQuery, MemoryStore


SECRET = "M6_SECRET_SLOT=5"
EXPECTED_SLOT = 5


class MemorySensitiveProvider:
    """One fixed deterministic model whose answer depends only on supplied context."""

    provider = "m6-contract"
    model = "fixed-memory-sensitive-model-v1"
    route = "deterministic-replay"

    def __init__(self) -> None:
        self.requests: list[ProviderRequest] = []

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        slot = EXPECTED_SLOT if SECRET in request.prompt else 0
        text = json.dumps({
            "schema_version": 1,
            "actions": [{"name": "choose_item", "arguments": [slot]}],
            "goal_updates": [], "memory_notes": [],
            "rationale": "Use only the supplied bounded context.",
        }, separators=(",", ":"))
        return ProviderResponse(
            text=text, request_id=f"m6-{len(self.requests)}",
            provider=self.provider, model=self.model, route=self.route,
            usage=ProviderUsage(
                input_tokens=request.estimated_input_tokens,
                output_tokens=max(1, len(text.encode("utf-8")) // 4),
                latency_ms=0, cost_usd=0,
            ),
        )

    def close(self) -> None:
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/m6-memory.yaml"))
    parser.add_argument("--gate-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.gate_root.resolve()
    if root.exists():
        raise RuntimeError(f"Gate root already exists: {root}")
    root.mkdir(parents=True)

    base_config = load_run_config(args.config)
    provenance = RunProvenance(
        code=capture_code_provenance(Path.cwd()),
        runtime=RuntimeProvenance(
            game_version="1.6.15.24356", smapi_version="4.5.2",
            stardojo_version="1.0.0-patched",
            stardojo_dll_sha256="6fffa01cdba2b8db5d3c1e008969f05bad9a2730bc2e93c1f8cc2c403d87506b",
        ),
    )
    game_state = {
        "save_id": base_config.fixture.save_id,
        "player": base_config.fixture.player,
        "date": base_config.fixture.starting_date.model_dump(mode="json"),
        "location": "Farm", "position": [62, 17], "seed": base_config.seed,
    }
    checkpoint_digest = artifact_sha256(game_state)

    seed_store = MemoryStore(root / "seed-memory.sqlite3", store_id="m6-seed")
    seed_store.add_text(
        f"For the hidden watering fixture, the correct inventory fact is {SECRET}.",
        memory_id="hidden-watering-slot", source_event_ids=("m5-live:task-2",),
        season="spring", map_name="Farm", task_domain="watering", confidence=1,
    )
    seed_store.add_text(
        "Unrelated market notes: " + "prices weather villagers festivals " * 10,
        memory_id="recent-distractor", task_domain="shopping", confidence=1,
    )
    export_path = root / "memory-export.json"
    seed_store.export(export_path, provenance={
        "source_run": "m5-prime-atomic-z-ai-glm-5-3",
        "checkpoint_sha256": checkpoint_digest,
    })
    seed_store.close()

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
    results = []
    for repetition, (name, learning, import_memory) in enumerate(conditions, 1):
        results.append(_run_condition(
            root, base_config, provenance, name, learning, repetition,
            export_path if import_memory else None, checkpoint_digest,
        ))
    removed = _run_condition(
        root, base_config, provenance, "D-memory-retrieval-memory-removed",
        conditions[-1][1], 5, None, checkpoint_digest,
    )

    by_name = {item["condition"]: item for item in results}
    passed = (
        len(results) == 4
        and all(item["model"] == MemorySensitiveProvider.model for item in results)
        and len({item["checkpoint_sha256"] for item in [*results, removed]}) == 1
        and not by_name["A-stateless"]["success"]
        and not by_name["B-context-only"]["success"]
        and not by_name["C-memory"]["success"]
        and by_name["D-memory-retrieval"]["success"]
        and not removed["success"]
    )
    report = {
        "status": "passed" if passed else "failed",
        "scope": "deterministic M6 storage and retrieval causal contract",
        "checkpoint_sha256": checkpoint_digest,
        "same_model_all_conditions": len({item["model"] for item in results}) == 1,
        "conditions": results,
        "memory_removal_control": removed,
        "retrieval_gain": int(by_name["D-memory-retrieval"]["success"])
        - int(by_name["C-memory"]["success"]),
        "memory_removal_eliminated_gain": (
            by_name["D-memory-retrieval"]["success"] and not removed["success"]
        ),
        "game_state_unchanged": len(
            {item["checkpoint_sha256"] for item in [*results, removed]}
        ) == 1,
        "memory_export": str(export_path),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if not passed:
        raise RuntimeError("M6 causal gate failed")
    print(json.dumps(report, indent=2))


def _run_condition(
    root: Path,
    base_config,
    provenance: RunProvenance,
    name: str,
    learning: LearningConditionConfig,
    repetition: int,
    memory_export: Path | None,
    checkpoint_digest: str,
) -> dict[str, object]:
    config = base_config.model_copy(update={
        "condition": name, "learning": learning, "repetition": repetition,
    })
    runner = ExperimentRunner(root / "runs", config, provenance)
    runner.start()
    store = MemoryStore(root / "memory" / f"{name}.sqlite3", store_id=name)
    if memory_export is not None:
        store.import_bundle(memory_export)
    provider = MemorySensitiveProvider()
    session = PrimeSession(runner, provider, memory_store=store)
    recent = (
        ContextItem(item_id="recent-1", text="The current tile is safe and clear."),
    )
    decision = session.decide(
        ContextInputs(
            objective="Select the correct inventory slot for the hidden watering fixture.",
            observation=json.dumps({
                "location": "Farm", "season": "spring", "inventory_slots": list(range(8)),
            }, sort_keys=True),
            allowed_actions=("choose_item",), recent_events=recent,
        ),
        memory_query=MemoryQuery(
            text="hidden watering fixture correct inventory slot",
            season="spring", map_name="Farm", task_domain="watering",
            game_day=8, limit=2, token_budget=config.context.memories_tokens,
        ),
    )
    selected_slot = int(decision.actions[0].arguments[0])
    runner.transition(RunPhase.COMPLETED, reason="M6 condition complete", operation_id="complete")
    retrieval_events = [
        event for event in runner.events.iter_records() if event.event_type == "memory_retrieval"
    ]
    result = {
        "condition": name,
        "run_id": runner.run_id,
        "model": provider.model,
        "provider": provider.provider,
        "route": provider.route,
        "checkpoint_sha256": checkpoint_digest,
        "selected_slot": selected_slot,
        "expected_slot": EXPECTED_SLOT,
        "success": selected_slot == EXPECTED_SLOT,
        "memory_count": len(store.records()),
        "retrieval_events": len(retrieval_events),
        "selected_memory_ids": (
            retrieval_events[0].payload["selected_ids"] if retrieval_events else []
        ),
        "context_sha256": session.state.last_context_sha256,
        "final_phase": runner.state.phase.value,
    }
    store.close()
    session.close()
    return result


if __name__ == "__main__":
    main()
