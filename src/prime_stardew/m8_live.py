"""Authenticate, validate, and execute an M8 watering skill in disposable Stardew fixtures."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .agent import PrimeRpcProvider
from .env.client import StarDojoClient
from .env.lifecycle import EnvironmentController
from .env.transport import SocketTransport
from .experiments.config import ProviderConfig, load_run_config
from .experiments.lifecycle import RunPhase
from .experiments.runner import ExperimentRunner
from .m3_atomic_probe import _setup
from .m8_gate import SKILL_ID, _provenance, _replay, _water_trajectories
from .skills import SkillExecutor, SkillProposalEngine, SkillStore, ValidationStage
from .tasks import ActionCommand, LiveTaskHarness, TaskKind, TaskTrajectory, load_atomic_fixtures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/m8-skills.yaml"))
    parser.add_argument("--m3-report", type=Path, required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--prime-executable", type=Path, required=True)
    parser.add_argument("--node-dir", type=Path)
    parser.add_argument("--prime-config-dir", type=Path, required=True)
    parser.add_argument("--prime-session-dir", type=Path, required=True)
    parser.add_argument("--save-id", default="PrimeStardewM3A_406041616")
    parser.add_argument("--player", default="PrimeStardewSmoke")
    parser.add_argument("--port", type=int, default=10783)
    parser.add_argument("--gate-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.gate_root.resolve()
    if root.exists():
        raise RuntimeError(f"Gate root already exists: {root}")
    root.mkdir(parents=True)
    sources = _water_trajectories(args.m3_report)
    config = load_run_config(args.config)
    config = config.model_copy(update={
        "fixture": config.fixture.model_copy(update={
            "save_id": args.save_id, "player": args.player,
        }),
        "provider": ProviderConfig(
            provider=args.provider, model=args.model, route="prime-agent-rpc",
            decoding=config.provider.decoding,
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
        environment=environment, timeout=90,
        extra_args=("--no-tools", "--no-skills", "--no-context-files", "--no-session"),
    )
    store = SkillStore(root / "skills.sqlite3")
    client = StarDojoClient(SocketTransport(port=args.port, timeout=30))
    controller = EnvironmentController(client, args.player, timeout=45, poll_interval=0.2)
    executor = SkillExecutor(store, runner)
    try:
        proposal = SkillProposalEngine(runner, provider).propose(
            proposal_id="authenticated-watering-skill",
            objective=(
                "Create water-adjacent-crop version 1. Use integer tool_slot parameter "
                "bounded 0 through 11 and exactly these steps: choose_item(tool_slot), "
                "turn(2), use(). Cite all three trajectories."
            ),
            trajectories=sources,
        )
        skill = store.add(proposal.skill)
        runner.events.append_idempotent(
            "skill_version_created",
            {"skill_id": skill.skill_id, "version": skill.version,
             "content_sha256": skill.content_sha256(),
             "source_trajectory_ids": list(skill.source_trajectory_ids)},
            idempotency_key=f"skill-version:{skill.skill_id}:{skill.version}",
        )
        recorded_slot = int(sources[0].actions[0].arguments[0])
        executor.validate(
            validation_id="authenticated-v1-replay", skill=skill,
            stage=ValidationStage.REPLAY, fixture=sources[0].fixture,
            parameters={"tool_slot": recorded_slot},
            run=lambda commands: _replay(commands, sources[0]),
        )

        controller.load(args.save_id, expected_date=config.fixture.starting_date)
        fixture = next(
            item for item in load_atomic_fixtures() if item.kind is TaskKind.WATER_CROP
        )
        live_slot = _prepare(controller, fixture)
        executor.validate(
            validation_id="authenticated-v1-disposable", skill=skill,
            stage=ValidationStage.DISPOSABLE, fixture=fixture,
            parameters={"tool_slot": live_slot},
            run=lambda commands: _live_run(controller, fixture, commands),
        )
        store.activate(skill.skill_id, skill.version)

        uses = []
        scores = []
        live_trajectories = []
        for index in range(1, 4):
            live_slot = _prepare(controller, fixture)
            trajectory, score, metrics = executor.execute(
                use_id=f"live-watering-{index}", skill_id=skill.skill_id,
                fixture=fixture, parameters={"tool_slot": live_slot},
                run=lambda commands: _live_run(controller, fixture, commands),
                baseline_model_decisions=1,
                baseline_primitive_actions=len(sources[index - 1].actions),
            )
            runner.record_task(f"live-skill-water-{index}", trajectory, score)
            uses.append(metrics)
            scores.append(score)
            live_trajectories.append(trajectory)
        runner.transition(RunPhase.COMPLETED, reason="M8 live skill gate complete", operation_id="complete")
        calls = [event.payload for event in runner.events.iter_records()
                 if event.event_type == "model_call_completed"]
        passed = (
            skill.skill_id == SKILL_ID and store.active(SKILL_ID).version == 1  # type: ignore[union-attr]
            and len(uses) == 3 and all(score.success and score.score == 1 for score in scores)
            and all(metric.actual_model_decisions == 0 for metric in uses)
            and all(call["provider"] == args.provider and call["model"] == args.model for call in calls)
            and not any(action.privileged for item in live_trajectories for action in item.actions)
        )
        report = {
            "status": "passed" if passed else "failed",
            "scope": "authenticated proposal plus disposable live Stardew skill validation",
            "run_id": runner.run_id, "provider": args.provider, "model": args.model,
            "skill_id": skill.skill_id, "active_version": store.active(SKILL_ID).version,  # type: ignore[union-attr]
            "replay_validation": "passed", "disposable_live_validation": "passed",
            "live_executions": len(uses),
            "successful_live_executions": sum(metric.success for metric in uses),
            "scores": [score.score for score in scores],
            "gross_execution_decisions_saved": sum(metric.model_decisions_saved for metric in uses),
            "proposal_model_calls": len(calls),
            "net_model_decisions_saved_after_proposal": 3 - len(calls),
            "primitive_actions_saved": sum(metric.primitive_actions_saved for metric in uses),
            "actual_routes": sorted({call["route"] for call in calls}),
            "input_tokens": sum(call["input_tokens"] for call in calls),
            "output_tokens": sum(call["output_tokens"] for call in calls),
            "cost_usd": sum(call["cost_usd"] for call in calls),
            "event_count": runner.events.cursor().sequence,
            "event_last_hash": runner.events.cursor().event_hash,
            "final_phase": runner.state.phase.value,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        if not passed:
            raise RuntimeError("Authenticated M8 live gate failed")
        print(json.dumps(report, indent=2))
    except Exception as exc:
        if runner.state.phase is RunPhase.RUNNING:
            runner.record_error(
                "m8-live", error_type=type(exc).__name__, message=str(exc), recoverable=False,
            )
            runner.transition(RunPhase.FAILED, reason=str(exc), operation_id="failed")
        raise
    finally:
        provider.close()
        store.close()
        try:
            client.raw("exit_menu", idempotent=False)
        except Exception:
            pass


def _prepare(controller: EnvironmentController, fixture) -> int:
    expected = _setup(controller, fixture)
    if not expected or expected[0].name != "choose_item":
        raise RuntimeError("Watering setup did not return a tool selection")
    return int(expected[0].arguments[0])


def _live_run(
    controller: EnvironmentController, fixture, commands: tuple[ActionCommand, ...]
) -> TaskTrajectory:
    harness = LiveTaskHarness(controller, fixture, settle_seconds=1.0)
    harness.begin()
    for command in commands:
        harness.perform(command)
    controller.wait_until(
        lambda observation: any(
            crop.position is not None
            and (crop.position.x, crop.position.y) == fixture.target
            and crop.is_watered
            for crop in observation.crops
        ),
        description=f"skill postcondition: watered crop at {fixture.target}",
    )
    return harness.finish()


if __name__ == "__main__":
    main()
