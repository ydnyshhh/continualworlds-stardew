"""Validate M11 transfer with GLM 5.3 and a disposable live season-boundary fixture."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .agent import ContextInputs, PrimeRpcProvider, PrimeSession
from .env.client import StarDojoClient
from .env.lifecycle import EnvironmentController
from .env.models import GameDate
from .env.transport import SocketTransport
from .experiments.config import ProviderConfig, load_run_config
from .experiments.lifecycle import RunPhase
from .experiments.runner import ExperimentRunner
from .m3_atomic_probe import _setup
from .m8_gate import _provenance
from .m8_live import _live_run
from .memory import MemoryQuery, MemoryStore
from .skills import SkillExecutor, SkillStore
from .tasks import TaskKind, load_atomic_fixtures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/m8-skills.yaml"))
    parser.add_argument(
        "--memory-bundle", type=Path,
        default=Path("runtime/smoke/m11-lifetime-transfer-v2/transfers/memories.json"),
    )
    parser.add_argument(
        "--source-skill-database", type=Path,
        default=Path("runtime/smoke/m8-live-glm-5.3-v3/skills.sqlite3"),
    )
    parser.add_argument("--provider", default="openrouter")
    parser.add_argument("--model", default="z-ai/glm-5.3")
    parser.add_argument("--prime-executable", type=Path, required=True)
    parser.add_argument("--node-dir", type=Path)
    parser.add_argument("--prime-config-dir", type=Path, required=True)
    parser.add_argument("--prime-session-dir", type=Path, required=True)
    parser.add_argument("--save-id", required=True)
    parser.add_argument("--player", default="PrimeStardewSmoke")
    parser.add_argument("--port", type=int, default=10783)
    parser.add_argument("--gate-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    root = args.gate_root.resolve()
    if root.exists():
        raise RuntimeError(f"Gate root already exists: {root}")
    root.mkdir(parents=True)
    boundary_date = GameDate(year=1, season="spring", day=28)
    base = load_run_config(args.config)
    config = base.model_copy(update={
        "suite": "m11-live-external-validity",
        "condition": "glm-transfer-and-live-boundary",
        "fixture": base.fixture.model_copy(update={
            "save_id": args.save_id, "player": args.player,
            "starting_date": boundary_date,
        }),
        "provider": ProviderConfig(
            provider=args.provider, model=args.model, route="prime-agent-rpc",
            decoding=base.provider.decoding,
        ),
        "tags": (*base.tags, "m11", "live", "transfer", "season-boundary"),
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
    fresh_memory = MemoryStore(root / "memory" / "fresh.sqlite3", store_id="m11-live-fresh")
    transferred_memory = MemoryStore(
        root / "memory" / "transferred.sqlite3", store_id="m11-live-transferred",
    )
    transferred_memory.import_bundle(args.memory_bundle)
    session = PrimeSession(runner, provider, memory_store=fresh_memory)
    client = StarDojoClient(SocketTransport(port=args.port, timeout=30))
    controller = EnvironmentController(client, args.player, timeout=60, poll_interval=0.2)
    recipient_skills = SkillStore(root / "skills" / "recipient.sqlite3", store_id="m11-live-recipient")
    source_skills = SkillStore(args.source_skill_database, store_id="m8-live-donor")
    skill_bundle_path = root / "transfers" / "watering-skill.json"
    source_bundle = source_skills.export(skill_bundle_path, provenance={
        "source": "m8-live-glm-5.3-v3", "recipient_model": args.model,
    })
    source_skills.close()
    recipient_skills.import_bundle(skill_bundle_path)
    try:
        fresh_results = _knowledge_tasks(session, condition="fresh")
        session.memory_store = transferred_memory
        transferred_results = _knowledge_tasks(session, condition="transferred")

        loaded = controller.load(args.save_id, expected_date=GameDate(
            year=1, season="spring", day=8,
        ))
        day_transitions = []
        while loaded.game_state.date != boundary_date:
            transition = controller.finish_day()
            day_transitions.append({
                "before": transition.before.model_dump(mode="json"),
                "after": transition.after.model_dump(mode="json"),
            })
            loaded = transition.observation
            if loaded.game_state.date.ordinal() > boundary_date.ordinal():
                raise RuntimeError("Disposable save advanced beyond the target boundary")
        fixture = next(item for item in load_atomic_fixtures() if item.kind is TaskKind.WATER_CROP)
        boundary_fixture = fixture.model_copy(update={"expected_date": boundary_date})
        commands = _setup(controller, boundary_fixture)
        tool_slot = int(commands[0].arguments[0])
        executor = SkillExecutor(recipient_skills, runner)
        trajectory, score, metrics = executor.execute(
            use_id="m11-live-boundary-watering",
            skill_id="water-adjacent-crop",
            fixture=boundary_fixture,
            parameters={"tool_slot": tool_slot},
            run=lambda compiled: _live_run(controller, boundary_fixture, compiled),
            baseline_model_decisions=1,
            baseline_primitive_actions=3,
        )
        runner.record_task("m11-live-boundary-watering", trajectory, score)
        runner.transition(
            RunPhase.COMPLETED, reason="M11 GLM transfer and live boundary gate complete",
            operation_id="complete",
        )
        calls = [event.payload for event in runner.events.iter_records()
                 if event.event_type == "model_call_completed"]
        fresh_successes = sum(item["success"] for item in fresh_results)
        transferred_successes = sum(item["success"] for item in transferred_results)
        passed = (
            transferred_successes == 2 and transferred_successes > fresh_successes
            and score.success and score.score == 1
            and metrics.actual_model_decisions == 0
            and loaded.game_state.date == boundary_date
            and len(day_transitions) == 20
            and source_bundle.active_versions.get("water-adjacent-crop") == 1
            and all(call["provider"] == args.provider and call["model"] == args.model for call in calls)
            and not any(action.privileged for action in trajectory.actions)
        )
        report = {
            "status": "passed" if passed else "failed",
            "scope": "authenticated GLM transfer plus disposable live Stardew boundary validation",
            "provider": args.provider,
            "model": args.model,
            "run_id": runner.run_id,
            "fresh_results": fresh_results,
            "transferred_results": transferred_results,
            "fresh_successes": fresh_successes,
            "transferred_successes": transferred_successes,
            "live_save_id": args.save_id,
            "boundary_date": boundary_date.model_dump(mode="json"),
            "verified_day_transitions": len(day_transitions),
            "transferred_skill_id": "water-adjacent-crop",
            "transferred_skill_bundle_sha256": source_bundle.content_sha256,
            "live_skill_score": score.score,
            "live_skill_decisions_saved": metrics.model_decisions_saved,
            "live_primitive_actions": metrics.actual_primitive_actions,
            "privileged_actions_in_scored_trajectory": sum(
                action.privileged for action in trajectory.actions
            ),
            "model_calls": len(calls),
            "actual_routes": sorted({call["route"] for call in calls}),
            "input_tokens": sum(int(call["input_tokens"]) for call in calls),
            "output_tokens": sum(int(call["output_tokens"]) for call in calls),
            "cost_usd": sum(float(call["cost_usd"]) for call in calls),
            "event_count": runner.events.cursor().sequence,
            "event_last_hash": runner.events.cursor().event_hash,
            "final_phase": runner.state.phase.value,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        if not passed:
            raise RuntimeError("M11 live external-validity gate failed")
        print(json.dumps(report, indent=2))
    except Exception as exc:
        if runner.state.phase is RunPhase.RUNNING:
            runner.record_error(
                "m11-live", error_type=type(exc).__name__, message=str(exc), recoverable=False,
            )
            runner.transition(RunPhase.FAILED, reason=str(exc), operation_id="failed")
        raise
    finally:
        provider.close()
        fresh_memory.close()
        transferred_memory.close()
        recipient_skills.close()
        try:
            client.raw("exit_menu", idempotent=False)
        except Exception:
            pass


def _knowledge_tasks(session: PrimeSession, *, condition: str) -> list[dict[str, object]]:
    tasks = (
        ("cool-crop", "A cool colored crop with waxy leaves needs treatment.", 2,
         "cool colored waxy crop mulch"),
        ("rocky-root", "A root crop is growing in dry rocky soil.", 3,
         "dry rocky root crop soil treatment"),
    )
    results = []
    for task_id, description, expected_slot, query in tasks:
        decision = session.decide(
            ContextInputs(
                objective=(
                    "Choose exactly one inventory slot using only explicit rules in the supplied "
                    "context. Slot 2 is Mineral Mulch; slot 3 is Compost; slot 0 means no explicit "
                    "rule. If no retrieved rule explicitly supports a treatment, choose slot 0."
                ),
                observation=json.dumps({
                    "task": task_id, "description": description,
                    "inventory": {"0": "No selection", "2": "Mineral Mulch", "3": "Compost"},
                }, sort_keys=True),
                allowed_actions=("choose_item",),
            ),
            memory_query=MemoryQuery(text=query, limit=2, token_budget=256),
        )
        selected = int(decision.actions[0].arguments[0])
        results.append({
            "condition": condition, "task_id": task_id,
            "selected_slot": selected, "expected_slot": expected_slot,
            "success": selected == expected_slot,
        })
    return results


if __name__ == "__main__":
    main()
