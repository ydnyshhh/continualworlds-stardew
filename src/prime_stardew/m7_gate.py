"""Run the deterministic M7 hidden crop-rule reflection and revision gate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .agent import ScriptedProvider
from .experiments.config import load_run_config
from .experiments.lifecycle import RunPhase
from .experiments.provenance import RunProvenance, RuntimeProvenance, capture_code_provenance
from .experiments.runner import ExperimentRunner
from .memory import (
    BeliefPrediction, MemoryKind, MemoryQuery, MemoryRecord, MemoryStatus,
    MemoryStore, PredictionOutcome,
)
from .reflection import (
    EvidenceItem, MemoryConsolidator, ReflectionEngine, RefinementPolicy,
    RefinementTrigger,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/m7-reflection.yaml"))
    parser.add_argument("--gate-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.gate_root.resolve()
    if root.exists():
        raise RuntimeError(f"Gate root already exists: {root}")
    root.mkdir(parents=True)
    config = load_run_config(args.config)
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
    store = MemoryStore(root / "memory.sqlite3", store_id="m7-gate")
    first_id = f"{runner.run_id}:reflection-1:belief-1-v1"
    first_response, second_response = _responses(first_id)
    provider = ScriptedProvider(
        [first_response, second_response],
        provider="reflection-contract", model="fixed-reflection-model-v1",
    )
    engine = ReflectionEngine(
        runner, provider, store,
        RefinementPolicy(trigger="nightly", evidence_token_budget=512),
    )
    first_evidence, second_evidence = _evidence()
    first = engine.reflect(
        trigger=RefinementTrigger.NIGHTLY, evidence=first_evidence,
        objective="Infer one supported Moonroot maturation rule.",
    )
    second = engine.reflect(
        trigger=RefinementTrigger.NIGHTLY, evidence=second_evidence,
        objective="Revise the contradicted Moonroot rule and cite contrary evidence.",
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
    policies = _policy_checks(first_evidence, second_evidence)
    runner.transition(RunPhase.COMPLETED, reason="M7 gate complete", operation_id="complete")
    calls = [event.payload for event in runner.events.iter_records()
             if event.event_type == "model_call_completed"]
    passed = (
        first_id in first.created_memory_ids
        and store.status(first_id) is MemoryStatus.SUPERSEDED
        and store.status(revised_id) is MemoryStatus.ACTIVE
        and revised_id in current_ids and first_id not in current_ids
        and "deleted-belief" not in current_ids
        and semantic.payload["support_count"] == 2
        and calibration.scored_predictions == 2
        and all(policies.values())
        and all(call["provider"] == "reflection-contract" for call in calls)
    )
    report = {
        "status": "passed" if passed else "failed",
        "scope": "deterministic M7 reflection contract",
        "run_id": runner.run_id,
        "initial_belief_id": first_id, "revised_belief_id": revised_id,
        "initial_status": store.status(first_id).value,
        "revised_status": store.status(revised_id).value,
        "current_memory_ids": current_ids,
        "inactive_beliefs_excluded": (
            first_id not in current_ids and "deleted-belief" not in current_ids
        ),
        "revision_cited_contradiction": bool(
            store.get(revised_id).payload["contradicting_event_ids"]  # type: ignore[union-attr]
        ),
        "semantic_memory_id": semantic.memory_id,
        "semantic_support_count": semantic.payload["support_count"],
        "semantic_source_event_ids": semantic.source_event_ids,
        "calibration": calibration.model_dump(mode="json"),
        "policy_checks": policies, "model_calls": len(calls),
        "all_model_calls_attributable": all(
            all(call.get(field) for field in ("request_id", "provider", "model", "route"))
            for call in calls
        ),
        "final_phase": runner.state.phase.value,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    store.close()
    provider.close()
    if not passed:
        raise RuntimeError("M7 deterministic gate failed")
    print(json.dumps(report, indent=2))


def _evidence() -> tuple[tuple[EvidenceItem, ...], tuple[EvidenceItem, ...]]:
    return (
        (
            EvidenceItem(
                event_id="crop-day-4", kind="observation", game_day=4,
                text="After three watered nights, Moonroot is not mature.",
            ),
            EvidenceItem(
                event_id="crop-day-5", kind="outcome", game_day=5,
                text="After four watered nights, Moonroot is mature and harvestable.",
                success=True, surprise=0.4,
            ),
        ),
        (
            EvidenceItem(
                event_id="crop-day-5-contradiction", kind="outcome", game_day=9,
                text="A new Moonroot batch was not mature after four watered nights.",
                success=False, surprise=1,
            ),
            EvidenceItem(
                event_id="crop-day-6", kind="outcome", game_day=10,
                text="The new Moonroot batch matured after five watered nights.", success=True,
            ),
        ),
    )


def _responses(first_id: str) -> tuple[str, str]:
    first = {
        "schema_version": 1,
        "lessons": ["Track crop age in watered nights."],
        "failed_assumptions": [], "counterfactuals": [],
        "goals": ["Test another Moonroot batch."],
        "beliefs": [{
            "statement": "Moonroot matures after four watered nights.",
            "confidence": 0.8,
            "source_event_ids": ["crop-day-4", "crop-day-5"],
            "supporting_event_ids": ["crop-day-4", "crop-day-5"],
            "contradicting_event_ids": [], "supersedes_id": None,
            "predicted_event": "Moonroot is ready after four watered nights.",
            "predicted_probability": 0.8,
        }],
    }
    second = {
        "schema_version": 1, "lessons": [],
        "failed_assumptions": ["The four-night rule remained valid."],
        "counterfactuals": ["Wait for a fifth watered night."],
        "goals": ["Retest the revised rule."],
        "beliefs": [{
            "statement": "Moonroot currently matures after five watered nights.",
            "confidence": 0.9,
            "source_event_ids": ["crop-day-5-contradiction", "crop-day-6"],
            "supporting_event_ids": ["crop-day-6"],
            "contradicting_event_ids": ["crop-day-5-contradiction"],
            "supersedes_id": first_id,
            "predicted_event": "Moonroot is ready after five watered nights.",
            "predicted_probability": 0.9,
        }],
    }
    return json.dumps(first), json.dumps(second)


def _policy_checks(
    first: tuple[EvidenceItem, ...], second: tuple[EvidenceItem, ...],
) -> dict[str, bool]:
    return {
        "nightly": RefinementPolicy(trigger="nightly").allows(
            RefinementTrigger.NIGHTLY, first,
        ),
        "failure": RefinementPolicy(trigger="failure").allows(
            RefinementTrigger.FAILURE, second,
        ),
        "surprise": RefinementPolicy(trigger="surprise", surprise_threshold=0.8).allows(
            RefinementTrigger.SURPRISE, second,
        ),
        "agent_selected": RefinementPolicy(trigger="agent_selected").allows(
            RefinementTrigger.AGENT_SELECTED, first,
        ),
    }


if __name__ == "__main__":
    main()

