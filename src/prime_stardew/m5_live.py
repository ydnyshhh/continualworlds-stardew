"""Run the five atomic fixtures through a live Prime Agent RPC session."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .agent import ContextInputs, ContextItem, PrimeRpcProvider, PrimeSession
from .env.client import StarDojoClient
from .env.lifecycle import EnvironmentController
from .env.transport import SocketTransport
from .experiments.config import ProviderConfig, load_run_config
from .experiments.lifecycle import RunPhase
from .experiments.provenance import RunProvenance, RuntimeProvenance, capture_code_provenance
from .experiments.runner import ExperimentRunner
from .m3_atomic_probe import _setup
from .tasks import LiveTaskHarness, load_atomic_fixtures, score_trajectory


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/m5-prime.yaml"))
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--prime-executable", type=Path, required=True)
    parser.add_argument("--node-dir", type=Path)
    parser.add_argument("--prime-config-dir", type=Path, required=True)
    parser.add_argument("--prime-session-dir", type=Path, required=True)
    parser.add_argument("--save-id", default="PrimeStardewSmoke_406041616")
    parser.add_argument("--player", default="PrimeStardewSmoke")
    parser.add_argument("--repetition", type=int)
    parser.add_argument("--port", type=int, default=10783)
    parser.add_argument("--runs-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    config = load_run_config(args.config)
    config = config.model_copy(update={
        "fixture": config.fixture.model_copy(update={
            "save_id": args.save_id, "player": args.player,
        }),
        "provider": ProviderConfig(
            provider=args.provider, model=args.model, route="prime-agent-rpc",
            decoding=config.provider.decoding,
        ),
        **({"repetition": args.repetition} if args.repetition is not None else {}),
    })
    provenance = RunProvenance(
        code=capture_code_provenance(Path.cwd()),
        runtime=RuntimeProvenance(
            game_version="1.6.15.24356", smapi_version="4.5.2",
            stardojo_version="1.0.0-patched",
            stardojo_dll_sha256="6fffa01cdba2b8db5d3c1e008969f05bad9a2730bc2e93c1f8cc2c403d87506b",
        ),
    )
    runner = ExperimentRunner(args.runs_root, config, provenance)
    runner.start()
    environment = {
        "PRIME_AGENT_CODING_AGENT_DIR": str(args.prime_config_dir.resolve()),
        "PI_SKIP_VERSION_CHECK": "1",
    }
    if args.node_dir:
        environment["PATH"] = str(args.node_dir.resolve()) + os.pathsep + os.environ.get("PATH", "")
    provider = PrimeRpcProvider(
        executable=str(args.prime_executable.resolve()),
        provider=args.provider,
        model=args.model,
        session_dir=args.prime_session_dir,
        cwd=Path.cwd(),
        environment=environment,
    )
    client = StarDojoClient(SocketTransport(port=args.port, timeout=30))
    controller = EnvironmentController(client, args.player, timeout=45, poll_interval=0.2)
    session = PrimeSession(runner, provider, pause_controller=client)
    results = []
    try:
        # A freshly launched game is still on the title screen. Load and verify
        # the isolated experiment save before any fixture setup or model call.
        controller.load(args.save_id, expected_date=config.fixture.starting_date)
        for index, fixture in enumerate(load_atomic_fixtures(), 1):
            _setup(controller, fixture)
            harness = LiveTaskHarness(controller, fixture, settle_seconds=0.5)
            harness.begin()
            live_observation = controller.observe(radius=2)
            decision = session.decide(ContextInputs(
                objective=fixture.description,
                observation=json.dumps({
                    "state": harness.before.model_dump(mode="json"),
                    "fixture_target": fixture.target,
                    "inventory_slots": [
                        {"slot": slot, "name": item.name, "quantity": item.quantity}
                        for slot, item in enumerate(live_observation.player.inventory)
                        if item.name
                    ],
                }, sort_keys=True),
                allowed_actions=fixture.allowed_actions,
                skills=_task_skills(fixture.fixture_id),
            ))
            for action in decision.actions:
                harness.perform(action)
            trajectory = harness.finish()
            score = score_trajectory(trajectory)
            runner.record_task(f"atomic-{index}", trajectory, score)
            results.append({
                "fixture_id": fixture.fixture_id,
                "decision": decision.model_dump(mode="json"),
                "score": score.model_dump(mode="json"),
                "trajectory": trajectory.model_dump(mode="json"),
            })
            if not score.success:
                raise RuntimeError(f"Prime failed {fixture.fixture_id}: {score.failure_reasons}")
        runner.transition(RunPhase.COMPLETED, reason="live M5 gate complete", operation_id="complete")
    except Exception as exc:
        runner.record_error(
            "m5-live", error_type=type(exc).__name__, message=str(exc), recoverable=False,
        )
        if runner.state.phase is RunPhase.RUNNING:
            runner.transition(RunPhase.FAILED, reason=str(exc), operation_id="failed")
        raise
    finally:
        session.close()
        try:
            client.raw("exit_menu", idempotent=False)
        except Exception:
            pass

    calls = [record.payload for record in runner.events.iter_records()
             if record.event_type == "model_call_completed"]
    report = {
        "status": "passed", "run_id": runner.run_id, "prime_version": "0.9.5",
        "provider": args.provider, "model": args.model, "tasks": results,
        "model_calls": calls, "final_phase": runner.state.phase.value,
        "session_state": session.checkpoint_state(),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"], "run_id": report["run_id"],
        "tasks": len(results), "model_calls": len(calls),
    }))


def _task_skills(fixture_id: str) -> tuple[ContextItem, ...]:
    if fixture_id == "m3-turn-move-v1":
        return (ContextItem(
            item_id="stardojo-guarded-absolute-move-v1",
            text=(
                "For an exact clear destination, prefer move(x, y), which uses the occupancy "
                "guard and does not depend on current facing. Then use turn(direction) if the "
                "required final facing differs."
            ),
        ),)
    if fixture_id == "m3-clear-debris-v1":
        return (ContextItem(
            item_id="stardojo-debris-collection-v1",
            text=(
                "Breaking an adjacent twig leaves its Wood drop on that tile; it is not "
                "collected automatically. After use succeeds, move_step toward the cleared "
                "target tile to collect the drop."
            ),
        ),)
    return ()


if __name__ == "__main__":
    main()
