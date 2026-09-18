"""Run one authenticated seven-day E1 smoke condition against a disposable live save."""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path

from .agent import PrimeRpcProvider, PrimeSession
from .e1.activity import segment_live_competencies
from .e1.conditions import condition_manifest
from .e1.config import load_e1_config, materialize_run_config
from .e1.live import execute_guarded_action, run_live_days
from .e1.learning import E1LearningLifecycle
from .e1.models import E1Condition, E1Phase, PERSISTENT_OBJECTIVE
from .e1.prime_adapter import PrimeHarnessAdapter
from .env.checkpoints import CheckpointManager
from .env.client import StarDojoClient
from .env.lifecycle import EnvironmentController
from .env.transport import SocketTransport
from .experiments.checkpoints import CheckpointKind, RunCheckpointManager
from .experiments.config import ProviderConfig
from .experiments.lifecycle import RunPhase
from .experiments.provenance import artifact_sha256
from .experiments.runner import ExperimentRunner
from .m8_gate import _provenance
from .memory import MemoryStore
from .skills import SkillStore, compile_skill


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/e1-live-smoke.yaml"))
    parser.add_argument("--condition", type=E1Condition, required=True, choices=list(E1Condition))
    parser.add_argument("--seed", type=int)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--port", type=int, default=10770)
    parser.add_argument("--provider", default="openrouter")
    parser.add_argument("--model", default="z-ai/glm-5.3")
    parser.add_argument("--prime-executable", type=Path, default=Path("runtime/prime-agent/prime-agent.cmd"))
    parser.add_argument("--prime-config-dir", type=Path, default=Path("runtime/prime-config"))
    parser.add_argument("--prime-session-dir", type=Path, default=Path("runtime/prime-sessions/e1-smoke"))
    parser.add_argument("--node-dir", type=Path, default=Path("runtime/prime-bootstrap/node-v22.23.2-win-x64"))
    parser.add_argument(
        "--saves-root", type=Path,
        default=Path(os.environ.get("APPDATA", ".")) / "StardewValley" / "Saves",
    )
    parser.add_argument(
        "--canonical-checkpoint", type=Path,
        default=Path("runtime/checkpoints/m11-live-source-spring08"),
    )
    parser.add_argument("--max-decisions-per-day", type=int, default=8)
    args = parser.parse_args()

    study, study_digest = load_e1_config(args.config)
    if study.phase is not E1Phase.SMOKE:
        raise RuntimeError("The live smoke command requires an E1 smoke configuration")
    seed = args.seed if args.seed is not None else study.seeds[0]
    base = materialize_run_config(study, args.condition, seed)
    manifest = condition_manifest(
        args.condition, full_harness_features=study.full_harness_features,
    )
    game_checkpoints = CheckpointManager(args.saves_root)
    canonical = game_checkpoints.verify(args.canonical_checkpoint)
    if canonical.player != base.fixture.player or canonical.game_date != base.fixture.starting_date:
        raise RuntimeError(
            "Canonical checkpoint player/date differs from the immutable smoke fixture: "
            f"checkpoint={canonical.player}@{canonical.game_date}, "
            f"fixture={base.fixture.player}@{base.fixture.starting_date}"
        )
    destination_save_id = f"PrimeStardewE1{args.condition.value}S{seed}"
    game_checkpoints.restore(args.canonical_checkpoint, destination_save_id)
    fixture = base.fixture.model_copy(update={"save_id": destination_save_id})
    config = base.model_copy(update={
        "fixture": fixture,
        "provider": ProviderConfig(
            provider=args.provider, model=args.model, route="prime-agent-rpc",
            decoding=base.provider.decoding,
        ),
    })
    root = args.root.resolve()
    runner = ExperimentRunner(root / "runs", config, _provenance())
    environment = {
        "PRIME_AGENT_CODING_AGENT_DIR": str(args.prime_config_dir.resolve()),
        "PI_SKIP_VERSION_CHECK": "1",
        "PATH": str(args.node_dir.resolve()) + os.pathsep + os.environ.get("PATH", ""),
    }
    provider = PrimeRpcProvider(
        executable=str(args.prime_executable.resolve()), provider=args.provider, model=args.model,
        session_dir=args.prime_session_dir / runner.run_id, cwd=Path.cwd(), environment=environment,
    )
    client = StarDojoClient(SocketTransport(port=args.port, timeout=30))
    controller = EnvironmentController(
        client, config.fixture.player, timeout=60, poll_interval=.25,
    )
    memory = (
        MemoryStore(root / "state" / runner.run_id / "memory.sqlite3")
        if config.learning.persistent_memory else None
    )
    skills = (
        SkillStore(root / "state" / runner.run_id / "skills.sqlite3")
        if manifest.capabilities.procedural_skills else None
    )
    session = PrimeSession(runner, provider, pause_controller=client, memory_store=memory)
    harness = PrimeHarnessAdapter(
        session, manifest, memory_token_budget=study.memory_token_budget,
        skill_store=skills, runtime_capabilities=manifest.capabilities,
    )

    def validate_skill_disposable(skill) -> bool:
        date = controller.observe().game_state.date
        validation_root = root / "skill-validation" / runner.run_id
        checkpoint_path = validation_root / f"{skill.skill_id}-v{skill.version}"
        checkpoint = game_checkpoints.create(
            config.fixture.save_id, checkpoint_path,
            player=config.fixture.player, game_date=date,
            environment={
                "purpose": "E1 disposable skill validation",
                "study_config_sha256": study_digest,
                "condition": args.condition.value,
            },
        )
        suffix = skill.content_sha256()[:8]
        branch_save_id = f"PrimeStardewE1Val{args.condition.value}{skill.version}{suffix}"
        branch_path = game_checkpoints.restore(checkpoint_path, branch_save_id)
        succeeded = False
        try:
            controller.load(branch_save_id, expected_date=date)
            commands = compile_skill(skill, {}, allowed_actions=tuple({
                "move", "move_relative", "move_step", "turn", "choose_item", "use",
                "interact", "take_from_chest", "put_to_chest",
            }))
            outcomes = [
                execute_guarded_action(
                    controller, runner, command.model_dump(mode="json"),
                    action_id=f"skill-validation-{skill.skill_id}-v{skill.version}-a{index}",
                    task_id="e1-skill-validation",
                )
                for index, command in enumerate(commands, 1)
            ]
            succeeded = bool(outcomes) and all(item["succeeded"] for item in outcomes)
        finally:
            controller.load(config.fixture.save_id, expected_date=date)
            resolved = branch_path.resolve()
            saves_root = game_checkpoints.saves_root.resolve()
            if not resolved.is_relative_to(saves_root) or resolved == saves_root:
                raise RuntimeError("Disposable skill branch escaped the saves root")
            if resolved.exists():
                shutil.rmtree(resolved)
            game_checkpoints.verify(checkpoint_path)
        runner.events.append("e1_skill_disposable_validation", {
            "skill_id": skill.skill_id, "version": skill.version,
            "success": succeeded, "checkpoint_id": checkpoint.checkpoint_id,
            "checkpoint_sha256": artifact_sha256(checkpoint.model_dump(mode="json")),
            "parent_save_id": config.fixture.save_id,
        })
        return succeeded

    learning_lifecycle = E1LearningLifecycle(
        harness=harness, provider=provider, skill_store=skills, pause_controller=client,
        disposable_skill_validator=(
            validate_skill_disposable if manifest.capabilities.procedural_skills else None
        ),
    ) if (manifest.capabilities.procedural_skills or manifest.capabilities.reflection) else None
    checkpoints = RunCheckpointManager(game_checkpoints)

    def checkpoint_day(summary) -> None:
        runner.publish_checkpoint(
            checkpoints,
            destination=root / "checkpoints" / runner.run_id / f"day-{summary.elapsed_day:03d}",
            kind=CheckpointKind.DAY,
            labels=("e1-smoke", f"condition-{args.condition.value}",
                    f"elapsed-day-{summary.elapsed_day}"),
            environment={
                "study_config_sha256": study_digest,
                "canonical_checkpoint_id": canonical.checkpoint_id,
                "canonical_checkpoint_sha256": artifact_sha256(
                    canonical.model_dump(mode="json")
                ),
                "live_smoke": True,
            },
            game_date=controller.observe().game_state.date,
            agent_state={
                "prime_session": session.checkpoint_state(),
                "learning_state": harness.export_learning_state().model_dump(mode="json"),
                "condition_manifest": manifest.model_dump(mode="json"),
            },
            memory_database=memory.path if memory is not None else None,
            learning_databases={"skills": skills.path} if skills is not None else None,
        )

    try:
        controller.load(config.fixture.save_id, expected_date=config.fixture.starting_date)
        harness.start(objective=PERSISTENT_OBJECTIVE, run_id=runner.run_id)
        result = run_live_days(
            harness, controller, days=study.horizon_days,
            max_decisions_per_day=args.max_decisions_per_day,
            on_day_complete=checkpoint_day,
            on_learning_boundary=(
                learning_lifecycle.end_day if learning_lifecycle is not None else None
            ),
        )
        competencies = segment_live_competencies(runner.events.iter_records())
        runner.transition(
            RunPhase.COMPLETED, reason="authenticated E1 smoke complete",
            operation_id="e1-smoke-complete",
        )
        report = {
            "schema_version": 1,
            "status": "passed",
            "scope": "authenticated seven-day E1 mechanics smoke; not an E1-Pilot result",
            "scientific_claims_permitted": False,
            "study_id": study.study_id,
            "study_config_sha256": study_digest,
            "canonical_checkpoint_id": canonical.checkpoint_id,
            "canonical_checkpoint_sha256": artifact_sha256(canonical.model_dump(mode="json")),
            "run_id": runner.run_id,
            "condition": args.condition.value,
            "seed": seed,
            "provider": args.provider,
            "model": args.model,
            "result": result.model_dump(mode="json"),
            "competencies": [item.model_dump(mode="json") for item in competencies],
            "event_cursor": runner.events.cursor().model_dump(mode="json"),
        }
    except Exception as exc:
        runner.record_error(
            "e1-live-smoke", error_type=type(exc).__name__, message=str(exc), recoverable=False,
        )
        if runner.state.phase in {RunPhase.CREATED, RunPhase.RUNNING, RunPhase.DAY_COMPLETE}:
            runner.transition(RunPhase.FAILED, reason=str(exc), operation_id="e1-smoke-failed")
        raise
    finally:
        session.close()
        if memory is not None:
            memory.close()
        if skills is not None:
            skills.close()
        try:
            client.raw("exit_menu", idempotent=False)
        except Exception:
            pass

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"], "run_id": runner.run_id,
        "condition": args.condition.value, "days": study.horizon_days,
    }))


if __name__ == "__main__":
    main()
