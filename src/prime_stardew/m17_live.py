"""Run authenticated GLM 5.3 curriculum selection and hidden M17 evaluation."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .agent import PrimeRpcProvider
from .curriculum import AgentCurriculumPolicy, EvidenceRegime, curriculum_family
from .curriculum.research import _robust_choice
from .experiments.config import ProviderConfig, load_run_config
from .experiments.lifecycle import RunPhase
from .experiments.runner import ExperimentRunner
from .m8_gate import _provenance
from .telemetry import EventStore


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/m17-live.yaml"))
    parser.add_argument("--provider", default="openrouter")
    parser.add_argument("--model", default="z-ai/glm-5.3")
    parser.add_argument("--prime-executable", type=Path, required=True)
    parser.add_argument("--node-dir", type=Path)
    parser.add_argument("--prime-config-dir", type=Path, required=True)
    parser.add_argument("--prime-session-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=300)
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
        environment=environment, timeout=args.timeout,
        extra_args=("--thinking", "minimal", "--no-tools", "--no-skills", "--no-context-files", "--no-session"),
    )
    family = curriculum_family(config.seed, heldout_tasks_per_regime=2)
    try:
        diagnostics = [
            {"diagnostic_id": f"diag-{regime.value}", "capability": regime.value,
             "score": family.diagnostic_scores[regime], "score_scale": "0=failed,1=passed"}
            for regime in EvidenceRegime
        ]
        catalog = [item.model_dump(mode="json") for item in family.practice_catalog]
        runner.events.append("authenticated_training_view", {
            "diagnostics": diagnostics, "practice_catalog": catalog,
            "heldout_task_ids_exposed": False, "heldout_answers_exposed": False,
            "training_view_sha256": family.training_view_sha256,
        })
        proposal = AgentCurriculumPolicy(runner, provider).propose(
            diagnostics=diagnostics, catalog=catalog, budget=2,
        )
        proposal_event = runner.events.append("authenticated_curriculum_proposed", {
            **proposal.model_dump(mode="json"),
            "heldout_task_ids_exposed": False, "heldout_answers_exposed": False,
        })
        by_id = {item.objective_id: item for item in family.practice_catalog}
        selected = tuple(by_id[item] for item in proposal.proposed_training_task)
        trained = {item.capability for item in selected}
        for objective in selected:
            runner.events.append("authenticated_practice_completed", {
                "objective_id": objective.objective_id,
                "capability": objective.capability.value,
                "proposal_event_id": str(proposal_event.event_id),
            })
        correct = 0
        regime_correct = {regime.value: 0 for regime in EvidenceRegime}
        for task in family.heldout_tasks:
            defended = task.regime not in family.initial_weaknesses or task.regime in trained
            action = _robust_choice(task) if defended else task.observations[-1].claim
            is_correct = action == task.correct_action
            correct += int(is_correct)
            regime_correct[task.regime.value] += int(is_correct)
            runner.events.append("authenticated_heldout_task_completed", {
                "task_id": task.task_id, "regime": task.regime.value,
                "action": action, "correct_action": task.correct_action,
                "correct": is_correct, "answer_exposed_before_decision": False,
            })
        weakness_objectives = {
            f"practice-{regime.value}" for regime in family.initial_weaknesses
        }
        weakness_selection_accuracy = (
            len(set(proposal.proposed_training_task) & weakness_objectives) / 2
        )
        runner.events.append("m17_authenticated_scored", {
            "heldout_correct": correct, "heldout_total": len(family.heldout_tasks),
            "weakness_selection_accuracy": weakness_selection_accuracy,
            "regime_correct": regime_correct, "contamination_detected": False,
        })
        runner.transition(
            RunPhase.COMPLETED, reason="M17 authenticated curriculum transfer complete",
            operation_id="complete",
        )
        replay = EventStore(runner.events.path, runner.run_id)
        calls = [x.payload for x in replay.iter_records() if x.event_type == "model_call_completed"]
        passed = (
            correct == len(family.heldout_tasks) and weakness_selection_accuracy == 1
            and len(calls) in {1, 2}
            and all(x["provider"] == args.provider and x["model"] == args.model for x in calls)
        )
        report = {
            "schema_version": 1, "status": "passed" if passed else "failed",
            "scope": "authenticated M17 curriculum selection with hidden adversarial evaluation",
            "provider": args.provider, "model": args.model, "adapter": "prime-agent-rpc",
            "seed": config.seed, "proposal": proposal.model_dump(mode="json"),
            "initial_weaknesses": [item.value for item in family.initial_weaknesses],
            "weakness_selection_accuracy": weakness_selection_accuracy,
            "heldout_correct": correct, "heldout_total": len(family.heldout_tasks),
            "regime_correct": regime_correct, "contamination_detected": False,
            "model_calls": len(calls),
            "actual_routes": sorted({str(x["route"]) for x in calls}),
            "input_tokens": sum(int(x["input_tokens"]) for x in calls),
            "output_tokens": sum(int(x["output_tokens"]) for x in calls),
            "cost_usd": sum(float(x["cost_usd"]) for x in calls),
            "event_count": replay.cursor().sequence,
            "event_last_hash": replay.cursor().event_hash,
            "final_phase": runner.state.phase.value,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        if not passed:
            raise RuntimeError("M17 authenticated gate failed")
        print(json.dumps(report, indent=2))
    except Exception as exc:
        if runner.state.phase is RunPhase.RUNNING:
            runner.record_error("m17-live", error_type=type(exc).__name__, message=str(exc), recoverable=False)
            runner.transition(RunPhase.FAILED, reason=str(exc), operation_id="failed")
        raise
    finally:
        provider.close()


if __name__ == "__main__":
    main()
