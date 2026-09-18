"""Validate M12 bounded memory management through authenticated Prime Agent RPC."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .agent import PrimeRpcProvider
from .experiments.config import (
    MemoryManagementConfig, MemoryManagementPolicy, ProviderConfig, load_run_config,
)
from .experiments.lifecycle import RunPhase
from .experiments.provenance import artifact_sha256
from .experiments.runner import ExperimentRunner
from .m8_gate import _provenance
from .memory import (
    AgentMemoryManagementPolicy, MemoryBudgetManager, MemoryQuery, MemoryStore,
    estimate_memory_tokens,
)
from .memory.research import synthetic_records
from .telemetry import EventStore


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/m12-live.yaml"))
    parser.add_argument("--provider", default="openrouter")
    parser.add_argument("--model", default="z-ai/glm-5.3")
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
    base = load_run_config(args.config)
    config = base.model_copy(update={
        "provider": ProviderConfig(
            provider=args.provider, model=args.model, route="prime-agent-rpc",
            decoding=base.provider.decoding,
        ),
    })
    runner = ExperimentRunner(root / "runs", config, _provenance())
    runner.start()
    environment = {
        "PRIME_AGENT_CODING_AGENT_DIR": str(args.prime_config_dir.resolve()),
        "PI_SKIP_VERSION_CHECK": "1",
    }
    if args.node_dir:
        environment["PATH"] = str(args.node_dir.resolve()) + os.pathsep + os.environ.get("PATH", "")
    provider = PrimeRpcProvider(
        executable=str(args.prime_executable.resolve()), provider=args.provider,
        model=args.model, session_dir=args.prime_session_dir, cwd=Path.cwd(),
        environment=environment, timeout=300,
        extra_args=("--no-tools", "--no-skills", "--no-context-files", "--no-session"),
    )
    agent_store = MemoryStore(root / "memory" / "agent.sqlite3", store_id="m12-live-agent")
    fifo_store = MemoryStore(root / "memory" / "fifo.sqlite3", store_id="m12-live-fifo")
    try:
        records = synthetic_records(config.seed)
        state_sha256 = artifact_sha256([record.model_dump(mode="json") for record in records])
        for record in records:
            agent_store.add(record)
            fifo_store.add(record)
            runner.events.append("memory_observed", {
                "candidate_state_sha256": state_sha256,
                "record": record.model_dump(mode="json"),
            })
        _prime_usage_signals(agent_store)
        _prime_usage_signals(fifo_store)

        fifo_config = MemoryManagementConfig(
            enabled=True, max_active_tokens=config.memory_management.max_active_tokens,
            policy=MemoryManagementPolicy.FIFO,
            overflow_fallback=MemoryManagementPolicy.FIFO,
        )
        fifo_budget = MemoryBudgetManager(fifo_store, fifo_config).enforce()
        decision = AgentMemoryManagementPolicy(runner, provider).decide(
            agent_store.records(), budget_tokens=config.memory_management.max_active_tokens,
        )
        agent_budget = MemoryBudgetManager(
            agent_store, config.memory_management, events=runner.events,
        ).enforce(decision)
        fifo_score = _score(fifo_store)
        agent_score = _score(agent_store)
        runner.events.append("m12_authenticated_scored", {
            "candidate_state_sha256": state_sha256,
            "decision": decision.model_dump(mode="json"),
            "agent_budget": agent_budget.model_dump(mode="json"),
            "fifo_budget": fifo_budget.model_dump(mode="json"),
            "agent_score": agent_score,
            "fifo_score": fifo_score,
        })
        runner.transition(
            RunPhase.COMPLETED, reason="M12 authenticated memory-management gate complete",
            operation_id="complete",
        )

        # Reopen and reconstruct only from the authenticated run's append-only event chain.
        replay = EventStore(runner.events.path, runner.run_id)
        raw_events = tuple(replay.iter_records())
        calls = [event.payload for event in raw_events if event.event_type == "model_call_completed"]
        scores = [event.payload for event in raw_events if event.event_type == "m12_authenticated_scored"]
        lifecycle = [event.payload for event in raw_events if event.event_type == "lifecycle_transition"]
        scored = scores[0] if len(scores) == 1 else {}
        passed = (
            len(calls) in {1, 2}
            and all(call["provider"] == args.provider and call["model"] == args.model for call in calls)
            and all(str(call.get("route", "")).strip() for call in calls)
            and len(scores) == 1
            and scored.get("agent_budget", {}).get("fallback_applied") is False
            and int(scored.get("agent_budget", {}).get("after_active_tokens", 10**9))
                <= config.memory_management.max_active_tokens
            and scored.get("agent_score", {}).get("held_out_utility", 0) >= 1
            and lifecycle[-1]["to"] == RunPhase.COMPLETED.value
        )
        report = {
            "schema_version": 1,
            "status": "passed" if passed else "failed",
            "scope": "authenticated GLM 5.3 bounded-memory management validation",
            "provider": args.provider,
            "model": args.model,
            "adapter": "prime-agent-rpc",
            "run_id": runner.run_id,
            "candidate_state_sha256": state_sha256,
            "decision": scored.get("decision"),
            "agent_budget": scored.get("agent_budget"),
            "fifo_budget": scored.get("fifo_budget"),
            "agent_score": scored.get("agent_score"),
            "fifo_score": scored.get("fifo_score"),
            "model_calls": len(calls),
            "actual_routes": sorted({str(call["route"]) for call in calls}),
            "input_tokens": sum(int(call["input_tokens"]) for call in calls),
            "output_tokens": sum(int(call["output_tokens"]) for call in calls),
            "cost_usd": sum(float(call["cost_usd"]) for call in calls),
            "event_count": replay.cursor().sequence,
            "event_last_hash": replay.cursor().event_hash,
            "raw_event_reconstruction": len(scores) == 1,
            "final_phase": lifecycle[-1]["to"] if lifecycle else None,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        if not passed:
            raise RuntimeError("M12 authenticated gate failed")
        print(json.dumps(report, indent=2))
    except Exception as exc:
        if runner.state.phase is RunPhase.RUNNING:
            runner.record_error(
                "m12-live", error_type=type(exc).__name__, message=str(exc), recoverable=False,
            )
            runner.transition(RunPhase.FAILED, reason=str(exc), operation_id="failed")
        raise
    finally:
        provider.close()
        agent_store.close()
        fifo_store.close()


def _prime_usage_signals(store: MemoryStore) -> None:
    store.retrieve(MemoryQuery(text="recent weather wind", limit=1, token_budget=100))
    store.retrieve(MemoryQuery(text="recent market color", limit=1, token_budget=100))
    store.retrieve(MemoryQuery(text="rain forecast clay", limit=1, token_budget=100))


def _score(store: MemoryStore) -> dict[str, object]:
    tasks = (
        ("cool waxy crops mineral mulch", "future-cool-rule"),
        ("dry rocky root crops compost", "future-root-rule"),
    )
    outcomes = []
    used_ids: set[str] = set()
    for query, expected_id in tasks:
        selected = store.retrieve(MemoryQuery(
            text=query, game_day=20, limit=1, token_budget=100,
        )).selected
        selected_ids = [record.memory_id for record in selected]
        used_ids.update(selected_ids)
        outcomes.append({
            "query": query, "expected_memory_id": expected_id,
            "selected_memory_ids": selected_ids, "success": expected_id in selected_ids,
        })
    active = store.records()
    tokens = sum(estimate_memory_tokens(record) for record in active)
    utility = sum(bool(outcome["success"]) for outcome in outcomes)
    return {
        "held_out_utility": utility,
        "active_tokens": tokens,
        "held_out_utility_per_active_token": utility / max(1, tokens),
        "active_memory_ids": [record.memory_id for record in active],
        "unused_memory_fraction": len({record.memory_id for record in active} - used_ids)
            / max(1, len(active)),
        "outcomes": outcomes,
    }


if __name__ == "__main__":
    main()
