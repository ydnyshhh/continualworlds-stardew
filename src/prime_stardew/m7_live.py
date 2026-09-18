"""Run the M7 hidden crop-rule revision gate through authenticated Prime Agent."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .agent import PrimeRpcProvider
from .experiments.config import ProviderConfig, load_run_config
from .experiments.lifecycle import RunPhase
from .experiments.provenance import RunProvenance, RuntimeProvenance, capture_code_provenance
from .experiments.runner import ExperimentRunner
from .m7_gate import _evidence, _policy_checks
from .memory import (
    BeliefPrediction, MemoryKind, MemoryQuery, MemoryRecord, MemoryStatus,
    MemoryStore, PredictionOutcome,
)
from .reflection import MemoryConsolidator, ReflectionEngine, RefinementPolicy, RefinementTrigger


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/m7-reflection.yaml"))
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--prime-executable", type=Path, required=True)
    parser.add_argument("--node-dir", type=Path)
    parser.add_argument("--prime-config-dir", type=Path, required=True)
    parser.add_argument("--prime-session-dir", type=Path, required=True)
    parser.add_argument("--gate-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.gate_root.resolve()
    if root.exists():
        raise RuntimeError(f"Gate root already exists: {root}")
    root.mkdir(parents=True)
    loaded = load_run_config(args.config)
    config = loaded.model_copy(update={
        "provider": ProviderConfig(
            provider=args.provider, model=args.model, route="prime-agent-rpc",
            decoding=loaded.provider.decoding,
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
    runner = ExperimentRunner(root / "runs", config, provenance)
    runner.start()
    store = MemoryStore(root / "memory.sqlite3", store_id="m7-live")
    environment = {
        "PRIME_AGENT_CODING_AGENT_DIR": str(args.prime_config_dir.resolve()),
        "PI_SKIP_VERSION_CHECK": "1",
    }
    if args.node_dir:
        environment["PATH"] = str(args.node_dir.resolve()) + os.pathsep + os.environ.get("PATH", "")
    provider = PrimeRpcProvider(
        executable=str(args.prime_executable.resolve()), provider=args.provider,
        model=args.model, session_dir=args.prime_session_dir,
        cwd=Path.cwd(), environment=environment, timeout=90,
        # Avoid the daemon relay for this short-lived gate. The RPC process stays
        # alive across both reflections, so the two calls still share one agent
        # session while completion events come directly from the worker.
        extra_args=("--no-tools", "--no-skills", "--no-context-files", "--no-session"),
    )
    engine = ReflectionEngine(
        runner, provider, store,
        RefinementPolicy(trigger="nightly", evidence_token_budget=512),
    )
    first_evidence, second_evidence = _evidence()
    try:
        first = engine.reflect(
            trigger=RefinementTrigger.NIGHTLY, evidence=first_evidence,
            objective=(
                "Create exactly one supported belief stating that Moonroot matures after "
                "four watered nights. Cite crop-day-4 and crop-day-5, use confidence 0.8, "
                "and predict readiness after four watered nights with probability 0.8."
            ),
        )
        first_id = next(
            memory_id for memory_id in first.created_memory_ids
            if store.get(memory_id, include_inactive=True).kind is MemoryKind.BELIEF  # type: ignore[union-attr]
        )
        second = engine.reflect(
            trigger=RefinementTrigger.NIGHTLY, evidence=second_evidence,
            objective=(
                "Create exactly one revised belief stating that Moonroot currently matures "
                "after five watered nights. Supersede the current active four-night belief, "
                "cite crop-day-5-contradiction as contradictory and crop-day-6 as supporting, "
                "use confidence 0.9, and predict five-night readiness with probability 0.9."
            ),
        )
        revised_id = next(
            memory_id for memory_id in second.created_memory_ids
            if store.get(memory_id, include_inactive=True).kind is MemoryKind.BELIEF  # type: ignore[union-attr]
        )
        for memory_id, event_id, text in (
            ("episode-batch-1", "crop-day-5", "First batch matured after four watered nights."),
            ("episode-batch-2", "crop-day-6", "Second batch matured after five watered nights."),
        ):
            store.add(MemoryRecord(
                memory_id=memory_id, kind=MemoryKind.EPISODE, text=text,
                source_event_ids=(event_id,), task_domain="crop-rule",
            ))
        semantic = MemoryConsolidator(store).consolidate(
            rule_key="moonroot-watered-nights",
            episode_ids=("episode-batch-1", "episode-batch-2"),
            statement="Moonroot maturation must be tracked by watered nights and current evidence.",
        )
        _record_calibration(store, first_id, revised_id)
        calibration = store.calibration()
        store.add(MemoryRecord(
            memory_id="deleted-belief", kind=MemoryKind.BELIEF,
            text="Moonroot matures instantly.", source_event_ids=("bad-event",),
        ))
        store.delete("deleted-belief", reason="contradicted fixture belief")
        current = store.retrieve(MemoryQuery(
            text="Moonroot maturation watered nights", task_domain="crop-rule",
            token_budget=512, limit=20,
        ))
        current_ids = tuple(record.memory_id for record in current.selected)
        runner.transition(RunPhase.COMPLETED, reason="M7 live gate complete", operation_id="complete")
        calls = [event.payload for event in runner.events.iter_records()
                 if event.event_type == "model_call_completed"]
        revised = store.get(revised_id)
        policies = _policy_checks(first_evidence, second_evidence)
        passed = (
            store.status(first_id) is MemoryStatus.SUPERSEDED
            and store.status(revised_id) is MemoryStatus.ACTIVE
            and first_id not in current_ids and revised_id in current_ids
            and "deleted-belief" not in current_ids
            and bool(revised.payload["contradicting_event_ids"])  # type: ignore[union-attr]
            and semantic.payload["support_count"] == 2
            and calibration.scored_predictions == 2
            and all(policies.values())
            and all(call["provider"] == args.provider and call["model"] == args.model for call in calls)
        )
        report = {
            "status": "passed" if passed else "failed",
            "scope": "authenticated Prime Agent M7 reflection gate",
            "run_id": runner.run_id, "provider": args.provider, "model": args.model,
            "initial_belief_id": first_id, "revised_belief_id": revised_id,
            "initial_statement": store.get(first_id, include_inactive=True).text,  # type: ignore[union-attr]
            "revised_statement": revised.text,  # type: ignore[union-attr]
            "initial_status": store.status(first_id).value,
            "revised_status": store.status(revised_id).value,
            "revision_cited_contradiction": bool(
                revised.payload["contradicting_event_ids"]  # type: ignore[union-attr]
            ),
            "inactive_beliefs_excluded": (
                first_id not in current_ids and "deleted-belief" not in current_ids
            ),
            "current_memory_ids": current_ids,
            "semantic_memory_id": semantic.memory_id,
            "semantic_source_event_ids": semantic.source_event_ids,
            "calibration": calibration.model_dump(mode="json"),
            "policy_checks": policies,
            "model_calls": len(calls),
            "actual_routes": sorted({call["route"] for call in calls}),
            "input_tokens": sum(call["input_tokens"] for call in calls),
            "output_tokens": sum(call["output_tokens"] for call in calls),
            "cost_usd": sum(call["cost_usd"] for call in calls),
            "final_phase": runner.state.phase.value,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        if not passed:
            raise RuntimeError("Authenticated M7 gate failed")
        print(json.dumps(report, indent=2))
    finally:
        provider.close()
        store.close()


def _record_calibration(store: MemoryStore, first_id: str, revised_id: str) -> None:
    store.record_prediction(BeliefPrediction(
        prediction_id="prediction-v1", belief_id=first_id,
        predicted_event="Moonroot ready after four watered nights", probability=0.8,
        source_event_id="prediction-event-v1",
    ))
    store.record_outcome(PredictionOutcome(
        prediction_id="prediction-v1", occurred=False,
        source_event_id="crop-day-5-contradiction",
    ))
    store.record_prediction(BeliefPrediction(
        prediction_id="prediction-v2", belief_id=revised_id,
        predicted_event="Moonroot ready after five watered nights", probability=0.9,
        source_event_id="prediction-event-v2",
    ))
    store.record_outcome(PredictionOutcome(
        prediction_id="prediction-v2", occurred=True, source_event_id="crop-day-6",
    ))


if __name__ == "__main__":
    main()
