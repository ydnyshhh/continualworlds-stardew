"""Validate M17 corruption repair against live Stardew outcomes and an adversarial report."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from .agent import PrimeRpcProvider
from .corruption import RepairResponse
from .curriculum import LiveEvidenceRepairPolicy
from .env.checkpoints import CheckpointManager
from .env.client import StarDojoClient
from .env.lifecycle import EnvironmentController
from .env.models import GameDate
from .env.transport import SocketTransport
from .experiments.config import ProviderConfig, load_run_config
from .experiments.lifecycle import RunPhase
from .experiments.runner import ExperimentRunner
from .m14_stardew_live import (
    ORIGIN, TARGETS, _prepare_fixture, _quantity, _record_action, _record_movement, _slot,
)
from .m8_gate import _provenance
from .memory import MemoryKind, MemoryRecord, MemoryStatus, MemoryStore
from .telemetry import EventStore


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/m17-stardew-live.yaml"))
    parser.add_argument("--source-checkpoint", type=Path, required=True)
    parser.add_argument("--saves-root", type=Path, required=True)
    parser.add_argument("--save-id", default="PrimeStardewM17Corrupt_406041617")
    parser.add_argument("--player", default="PrimeStardewSmoke")
    parser.add_argument("--provider", default="openrouter")
    parser.add_argument("--model", default="z-ai/glm-5.3")
    parser.add_argument("--prime-executable", type=Path, required=True)
    parser.add_argument("--node-dir", type=Path)
    parser.add_argument("--prime-config-dir", type=Path, required=True)
    parser.add_argument("--prime-session-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--port", type=int, default=10783)
    parser.add_argument("--gate-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    root = args.gate_root.resolve()
    if root.exists():
        raise RuntimeError(f"Gate root already exists: {root}")
    root.mkdir(parents=True)
    manager = CheckpointManager(args.saves_root)
    manifest = manager.verify(args.source_checkpoint)
    save_path = manager.restore(args.source_checkpoint, args.save_id)
    base = load_run_config(args.config)
    config = base.model_copy(update={
        "fixture": base.fixture.model_copy(update={"save_id": args.save_id, "player": args.player}),
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
    client = StarDojoClient(SocketTransport(port=args.port, timeout=30))
    controller = EnvironmentController(client, args.player, timeout=60, poll_interval=.25)
    memory = MemoryStore(root / "memory" / "live.sqlite3", store_id="m17-live-stardew")
    expected_date = GameDate(year=1, season="spring", day=8)
    try:
        loaded = controller.load(args.save_id, expected_date=expected_date)
        _prepare_fixture(controller, branch="repair")
        before = controller.observe(radius=2)
        axe_slot = _slot(before, "Axe")
        _record_action(runner.events, "repair", "choose_item", (axe_slot,),
                       lambda: client.choose_item(axe_slot), controller)
        injected = runner.events.append("live_memory_corruption_injected", {
            "memory_id": "belief:m17-live-twig-yield:v1",
            "statement": "A farm twig yields 5 Wood.",
            "expected_yield": 5, "plausible_source": "legacy field guide",
            "corruption_label_exposed_to_agent": False,
        })
        prior_id = "belief:m17-live-twig-yield:v1"
        memory.add(MemoryRecord(
            memory_id=prior_id, kind=MemoryKind.BELIEF,
            text="A farm twig yields 5 Wood.", confidence=.9,
            source_event_ids=(str(injected.event_id),), task_domain="live-resource-yield",
            tags=("m17", "live", "twig-yield"), payload={"expected_yield": 5},
        ))
        current_x = ORIGIN[0]
        live_evidence: list[dict[str, object]] = []
        live_event_ids: list[str] = []
        sample_yields: list[int] = []
        for index in range(2):
            result, current_x = _clear_live_twig(
                controller, runner.events, branch="repair", target=TARGETS[index],
                current_x=current_x, label=f"corruption-sample-{index + 1}",
            )
            execution = runner.events.append("live_corruption_evidence_observed", {
                "sample_index": index + 1, "observed_wood_yield": result["wood_delta"],
                "source_evidence": result["request_ids"], "live_game": True,
                "authenticated_source": True,
            })
            event_id = str(execution.event_id)
            live_event_ids.append(event_id)
            sample_yields.append(int(result["wood_delta"]))
            live_evidence.append({
                "evidence_id": event_id, "claim_yield": result["wood_delta"],
                "source": f"live sample {index + 1}", "live_game": True,
                "authenticated": True,
            })
        attack = runner.events.append("adversarial_evidence_injected", {
            "claim_yield": 5, "source": "unverified copied guide",
            "live_game": False, "authenticated_source": False,
        })
        attack_id = str(attack.event_id)
        evidence = live_evidence + [{
            "evidence_id": attack_id, "claim_yield": 5,
            "source": "unverified copied guide", "live_game": False,
            "authenticated": False,
        }]
        client.pause()
        try:
            decision = LiveEvidenceRepairPolicy(runner, provider).decide(
                memory={
                    "memory_id": prior_id, "statement": "A farm twig yields 5 Wood.",
                    "confidence": .9, "source": "legacy field guide",
                }, evidence=evidence,
            )
        finally:
            client.resume()
        runner.events.append("live_corruption_repair_decided", decision.model_dump(mode="json"))
        revised_id = "belief:m17-live-twig-yield:v2"
        trusted_live = tuple(event_id for event_id in live_event_ids
                             if event_id in decision.trusted_evidence_ids)
        memory.add(MemoryRecord(
            memory_id=revised_id, kind=MemoryKind.BELIEF,
            text=f"A farm twig yields about {decision.estimated_heldout_yield} Wood.",
            confidence=.8, source_event_ids=trusted_live,
            task_domain="live-resource-yield", tags=("m17", "live", "repaired"),
            supersedes_id=prior_id, version=2,
            payload={
                "expected_yield": decision.estimated_heldout_yield,
                "response": decision.response.value,
                "rejected_evidence_ids": list(decision.rejected_evidence_ids),
            },
        ))
        heldout, _ = _clear_live_twig(
            controller, runner.events, branch="repair", target=TARGETS[2],
            current_x=current_x, label="corruption-heldout",
        )
        heldout_event = runner.events.append("live_corruption_heldout_validated", {
            "predicted_wood_yield": decision.estimated_heldout_yield,
            "observed_wood_yield": heldout["wood_delta"],
            "absolute_prediction_error": abs(decision.estimated_heldout_yield - int(heldout["wood_delta"])),
            "belief_id": revised_id, "live_game": True,
        })
        runner.events.append("m17_live_stardew_scored", {
            "source_checkpoint_id": manifest.checkpoint_id,
            "sample_yields": sample_yields,
            "adversarial_claim": 5,
            "heldout_yield": heldout["wood_delta"],
            "prediction": decision.estimated_heldout_yield,
            "original_prediction_error": abs(5 - int(heldout["wood_delta"])),
            "repaired_prediction_error": abs(decision.estimated_heldout_yield - int(heldout["wood_delta"])),
            "trusted_live_evidence": list(trusted_live),
            "adversarial_evidence_rejected": attack_id in decision.rejected_evidence_ids,
            "heldout_event_id": str(heldout_event.event_id),
        })
        runner.transition(
            RunPhase.COMPLETED, reason="M17 disposable live corruption repair complete",
            operation_id="complete",
        )
        replay = EventStore(runner.events.path, runner.run_id)
        records = tuple(replay.iter_records())
        calls = [x.payload for x in records if x.event_type == "model_call_completed"]
        primitive = [x.payload for x in records if x.event_type == "live_primitive_action_completed"]
        passed = (
            all(value == 1 for value in sample_yields)
            and int(heldout["wood_delta"]) == 1
            and decision.estimated_heldout_yield == 1
            and decision.response is not RepairResponse.BLINDLY_TRUST
            and set(trusted_live) == set(live_event_ids)
            and attack_id in decision.rejected_evidence_ids
            and memory.status(prior_id) is MemoryStatus.SUPERSEDED
            and set(memory.get(revised_id).source_event_ids) == set(live_event_ids)
            and not any(bool(item["privileged"]) for item in primitive)
            and len(primitive) <= config.budget.max_actions
            and loaded.player.name == args.player and loaded.game_state.date == expected_date
            and len(calls) in {1, 2}
        )
        report = {
            "schema_version": 1, "status": "passed" if passed else "failed",
            "scope": "authenticated disposable live Stardew memory-corruption repair gate",
            "provider": args.provider, "model": args.model, "adapter": "prime-agent-rpc",
            "source_checkpoint_id": manifest.checkpoint_id,
            "save_path": str(save_path), "date": expected_date.model_dump(mode="json"),
            "injected_false_yield": 5, "sample_yields": sample_yields,
            "adversarial_claim": 5, "decision": decision.model_dump(mode="json"),
            "heldout_yield": heldout["wood_delta"],
            "original_prediction_error": abs(5 - int(heldout["wood_delta"])),
            "repaired_prediction_error": abs(decision.estimated_heldout_yield - int(heldout["wood_delta"])),
            "belief_provenance_valid": set(memory.get(revised_id).source_event_ids) == set(live_event_ids),
            "adversarial_evidence_rejected": attack_id in decision.rejected_evidence_ids,
            "primitive_actions": len(primitive),
            "privileged_actions_in_scored_phase": sum(bool(item["privileged"]) for item in primitive),
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
            raise RuntimeError("M17 live Stardew corruption gate failed")
        print(json.dumps(report, indent=2))
    except Exception as exc:
        if runner.state.phase is RunPhase.RUNNING:
            runner.record_error("m17-stardew-live", error_type=type(exc).__name__, message=str(exc), recoverable=False)
            runner.transition(RunPhase.FAILED, reason=str(exc), operation_id="failed")
        raise
    finally:
        provider.close()
        memory.close()
        try:
            client.raw("exit_menu", idempotent=False)
        except Exception:
            pass


def _clear_live_twig(
    controller: EnvironmentController, events: EventStore, *, branch: str,
    target: tuple[int, int], current_x: int, label: str,
) -> tuple[dict[str, object], int]:
    """Clear one placed twig while tolerating live animation/tool-hit variance."""
    action_count = 0
    request_ids: list[str] = []
    while current_x < target[0]:
        movement = _record_movement(
            events, branch, "move_step", (2,), lambda: controller.move_step(2), controller,
        )
        request_ids.append(movement.action.request_id)
        current_x += 1
        action_count += 1
    before = controller.observe()
    wood_before = _quantity(before, "Wood")
    stamina_before = before.player.stamina
    turn = _record_action(events, branch, "turn", (2,), lambda: controller.client.turn(2), controller)
    request_ids.append(turn.request_id)
    action_count += 1
    for _ in range(4):
        use = _record_action(events, branch, "use", (), controller.client.use, controller)
        request_ids.append(use.request_id)
        action_count += 1
        time.sleep(.4)
        if not controller.client.get_tile_info(*target).object_at_tile:
            break
    else:
        raise RuntimeError(f"Live twig did not clear after four ordinary tool uses: {label}")
    down = _record_movement(
        events, branch, "move_step", (3,), lambda: controller.move_step(3), controller,
    )
    request_ids.append(down.action.request_id)
    action_count += 1
    collected = controller.wait_until(
        lambda observation: _quantity(observation, "Wood") > wood_before,
        description=f"{branch} {label} Wood pickup",
    )
    up = _record_movement(
        events, branch, "move_step", (1,), lambda: controller.move_step(1), controller,
    )
    request_ids.append(up.action.request_id)
    action_count += 1
    after = controller.observe()
    return ({
        "wood_delta": _quantity(collected, "Wood") - wood_before,
        "stamina_cost": stamina_before - after.player.stamina,
        "primitive_actions": action_count,
        "request_ids": request_ids,
    }, current_x)


if __name__ == "__main__":
    main()
