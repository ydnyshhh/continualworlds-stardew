"""Run M14 noisy sampling and held-out belief validation in disposable live Stardew saves."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from statistics import mean

from .agent import PrimeRpcProvider
from .env.checkpoints import CheckpointManager
from .env.client import ActionResult, StarDojoClient
from .env.lifecycle import EnvironmentController, MovementResult
from .env.models import GameDate, Observation
from .env.transport import SocketTransport
from .experimentation import LiveSamplingPolicy
from .experiments.config import ProviderConfig, load_run_config
from .experiments.lifecycle import RunPhase
from .experiments.runner import ExperimentRunner
from .m8_gate import _provenance
from .memory import MemoryKind, MemoryRecord, MemoryStatus, MemoryStore
from .telemetry import EventStore


ORIGIN = (62, 17)
TARGETS = ((62, 18), (63, 18), (64, 18))
TWIG_OBJECT_ID = "(O)294"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/m14-stardew-live.yaml"))
    parser.add_argument("--source-checkpoint", type=Path, required=True)
    parser.add_argument("--saves-root", type=Path, required=True)
    parser.add_argument("--active-save-id", default="PrimeStardewM14Active_406041616")
    parser.add_argument("--control-save-id", default="PrimeStardewM14Control_406041616")
    parser.add_argument("--player", default="PrimeStardewSmoke")
    parser.add_argument("--provider", default="openrouter")
    parser.add_argument("--model", default="z-ai/glm-5.3")
    parser.add_argument("--prime-executable", type=Path, required=True)
    parser.add_argument("--node-dir", type=Path)
    parser.add_argument("--prime-config-dir", type=Path, required=True)
    parser.add_argument("--prime-session-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--port", type=int, default=10783)
    parser.add_argument("--gate-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    root = args.gate_root.resolve()
    if root.exists():
        raise RuntimeError(f"Gate root already exists: {root}")
    root.mkdir(parents=True)
    checkpoint_manager = CheckpointManager(args.saves_root)
    manifest = checkpoint_manager.verify(args.source_checkpoint)
    active_path = checkpoint_manager.restore(args.source_checkpoint, args.active_save_id)
    control_path = checkpoint_manager.restore(args.source_checkpoint, args.control_save_id)

    base = load_run_config(args.config)
    config = base.model_copy(update={
        "fixture": base.fixture.model_copy(update={
            "save_id": args.active_save_id, "player": args.player,
        }),
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
        extra_args=(
            "--thinking", "minimal", "--no-tools", "--no-skills",
            "--no-context-files", "--no-session",
        ),
    )
    client = StarDojoClient(SocketTransport(port=args.port, timeout=30))
    controller = EnvironmentController(client, args.player, timeout=60, poll_interval=0.25)
    memory = MemoryStore(root / "memory" / "live.sqlite3", store_id="m14-live-stardew")
    expected_date = GameDate(year=1, season="spring", day=8)
    try:
        active_loaded = controller.load(args.active_save_id, expected_date=expected_date)
        _prepare_fixture(controller, branch="active")
        active_before = controller.observe(radius=2)
        client.pause()
        try:
            plan = LiveSamplingPolicy(runner, provider).decide()
        finally:
            client.resume()
        plan_event = runner.events.append("live_experiment_proposal", {
            "branch": "active", "plan": plan.model_dump(mode="json"),
            "numeric_prior_supplied": False,
            "derived_expected_value_supplied": False,
            "hidden_object_id_supplied": False,
            "candidate_count": 2,
            "held_out_count": 1,
        })
        prior_id = "belief:live-twig-yield:v1"
        prior_event = runner.events.append("hypothesis_created", {
            "branch": "active", "memory_id": prior_id,
            "statement": "Live twig Wood yield is unknown and may vary between samples.",
            "confidence": 0.0, "source_evidence": [str(plan_event.event_id)],
        })
        memory.add(MemoryRecord(
            memory_id=prior_id, kind=MemoryKind.BELIEF,
            text="Live twig Wood yield is unknown and may vary between samples.",
            confidence=0, source_event_ids=(str(prior_event.event_id),),
            task_domain="live-resource-yield", tags=("m14", "live", "twig-yield"),
            payload={"numeric_prior_supplied": False, "sample_count_planned": plan.sample_count},
        ))
        axe_slot = _slot(active_before, "Axe")
        _record_action(runner.events, "active", "choose_item", (axe_slot,),
                       lambda: client.choose_item(axe_slot), controller)
        sample_yields = []
        evidence_ids = []
        current_x = ORIGIN[0]
        for sample_index in range(plan.sample_count):
            target = TARGETS[sample_index]
            result, current_x = _clear_twig(
                controller, runner.events, branch="active", target=target,
                current_x=current_x, label=f"sample-{sample_index + 1}",
            )
            sample_yields.append(result["wood_delta"])
            execution = runner.events.append("experiment_execution", {
                "branch": "active", "sample_index": sample_index + 1,
                "target": list(target), "observed_wood_yield": result["wood_delta"],
                "stamina_cost": result["stamina_cost"],
                "primitive_actions": result["primitive_actions"],
                "source_evidence": result["request_ids"],
                "live_game": True,
            })
            evidence_ids.append(str(execution.event_id))
        predicted_yield = mean(sample_yields)
        revised_id = "belief:live-twig-yield:v2"
        revised_text = (
            f"Observed mean live twig yield {predicted_yield:.3f} Wood from "
            f"{len(sample_yields)} sample(s)."
        )
        memory.add(MemoryRecord(
            memory_id=revised_id, kind=MemoryKind.BELIEF, text=revised_text,
            confidence=min(1.0, len(sample_yields) / 2), source_event_ids=tuple(evidence_ids),
            task_domain="live-resource-yield", tags=("m14", "live", "twig-yield"),
            supersedes_id=prior_id, version=2,
            payload={
                "observed_yields": sample_yields, "predicted_held_out_yield": predicted_yield,
                "numeric_prior_supplied": False,
            },
        ))
        runner.events.append("hypothesis_revised", {
            "branch": "active", "memory_id": revised_id, "supersedes_id": prior_id,
            "source_evidence": evidence_ids, "statement": revised_text,
            "confidence": min(1.0, len(sample_yields) / 2),
        })
        heldout_result, current_x = _clear_twig(
            controller, runner.events, branch="active", target=TARGETS[2],
            current_x=current_x, label="held-out",
        )
        heldout_event = runner.events.append("live_heldout_validation", {
            "branch": "active", "target": list(TARGETS[2]),
            "predicted_wood_yield": predicted_yield,
            "observed_wood_yield": heldout_result["wood_delta"],
            "absolute_prediction_error": abs(predicted_yield - heldout_result["wood_delta"]),
            "stamina_cost": heldout_result["stamina_cost"],
            "primitive_actions": heldout_result["primitive_actions"],
            "belief_id": revised_id, "live_game": True,
        })
        active_after = controller.observe(radius=2)

        control_loaded = controller.load(args.control_save_id, expected_date=expected_date)
        _prepare_fixture(controller, branch="control")
        control_before = controller.observe(radius=2)
        control_axe_slot = _slot(control_before, "Axe")
        _record_action(runner.events, "control", "choose_item", (control_axe_slot,),
                       lambda: client.choose_item(control_axe_slot), controller)
        control_result, _ = _clear_twig(
            controller, runner.events, branch="control", target=TARGETS[2],
            current_x=ORIGIN[0], label="exploit-only-held-out",
        )
        control_after = controller.observe(radius=2)
        runner.events.append("live_control_completed", {
            "branch": "control", "observations": 1,
            "wood_yield": control_result["wood_delta"],
            "stamina_cost": control_result["stamina_cost"],
            "primitive_actions": control_result["primitive_actions"],
            "live_game": True,
        })
        runner.events.append("m14_live_stardew_scored", {
            "source_checkpoint_id": manifest.checkpoint_id,
            "active_save_id": args.active_save_id,
            "control_save_id": args.control_save_id,
            "sample_count": plan.sample_count,
            "sample_yields": sample_yields,
            "predicted_held_out_yield": predicted_yield,
            "active_held_out_yield": heldout_result["wood_delta"],
            "control_held_out_yield": control_result["wood_delta"],
            "active_total_wood_gain": _quantity(active_after, "Wood") - _quantity(active_before, "Wood"),
            "control_total_wood_gain": _quantity(control_after, "Wood") - _quantity(control_before, "Wood"),
            "active_stamina_cost": active_before.player.stamina - active_after.player.stamina,
            "control_stamina_cost": control_before.player.stamina - control_after.player.stamina,
            "absolute_prediction_error": abs(predicted_yield - heldout_result["wood_delta"]),
            "belief_id": revised_id,
            "belief_source_event_ids": list(memory.get(revised_id).source_event_ids),
            "heldout_event_id": str(heldout_event.event_id),
        })
        runner.transition(
            RunPhase.COMPLETED, reason="M14 disposable live Stardew sampling gate complete",
            operation_id="complete",
        )

        replay = EventStore(runner.events.path, runner.run_id)
        records = tuple(replay.iter_records())
        calls = [event.payload for event in records if event.event_type == "model_call_completed"]
        scored = [event.payload for event in records if event.event_type == "m14_live_stardew_scored"]
        primitive = [event.payload for event in records
                     if event.event_type == "live_primitive_action_completed"]
        active_primitive = [action for action in primitive if action["branch"] == "active"]
        control_primitive = [action for action in primitive if action["branch"] == "control"]
        executions = [event for event in records if event.event_type == "experiment_execution"]
        score = scored[0] if len(scored) == 1 else {}
        passed = (
            len(calls) in {1, 2}
            and all(call["provider"] == args.provider and call["model"] == args.model for call in calls)
            and len(scored) == 1 and len(executions) == plan.sample_count
            and all(value >= 1 for value in sample_yields)
            and heldout_result["wood_delta"] >= 1 and control_result["wood_delta"] >= 1
            and memory.status(prior_id) is MemoryStatus.SUPERSEDED
            and set(memory.get(revised_id).source_event_ids) == {str(event.event_id) for event in executions}
            and not any(bool(action["privileged"]) for action in primitive)
            and len(active_primitive) <= config.budget.max_actions
            and len(control_primitive) <= config.budget.max_actions
            and active_loaded.player.name == args.player and control_loaded.player.name == args.player
            and active_loaded.game_state.date == expected_date and control_loaded.game_state.date == expected_date
        )
        report = {
            "schema_version": 1,
            "status": "passed" if passed else "failed",
            "scope": "authenticated disposable live Stardew noisy sampling external-validity gate",
            "provider": args.provider, "model": args.model, "adapter": "prime-agent-rpc",
            "run_id": runner.run_id,
            "source_checkpoint_id": manifest.checkpoint_id,
            "source_checkpoint_files": len(manifest.files),
            "active_save_path": str(active_path), "control_save_path": str(control_path),
            "date": expected_date.model_dump(mode="json"),
            "plan": plan.model_dump(mode="json"),
            "numeric_prior_supplied": False,
            "derived_expected_value_supplied": False,
            "sample_yields": sample_yields,
            "observed_yield_variation": len(set(sample_yields + [heldout_result["wood_delta"]])) > 1,
            "predicted_held_out_yield": predicted_yield,
            "active_held_out_yield": heldout_result["wood_delta"],
            "control_held_out_yield": control_result["wood_delta"],
            "absolute_prediction_error": score.get("absolute_prediction_error"),
            "active_total_wood_gain": score.get("active_total_wood_gain"),
            "control_total_wood_gain": score.get("control_total_wood_gain"),
            "active_stamina_cost": score.get("active_stamina_cost"),
            "control_stamina_cost": score.get("control_stamina_cost"),
            "belief_id": revised_id,
            "belief_provenance_valid": set(memory.get(revised_id).source_event_ids)
                == {str(event.event_id) for event in executions},
            "primitive_actions": len(primitive),
            "active_primitive_actions": len(active_primitive),
            "control_primitive_actions": len(control_primitive),
            "per_branch_action_budget": config.budget.max_actions,
            "privileged_actions_in_scored_phase": sum(bool(action["privileged"]) for action in primitive),
            "model_calls": len(calls),
            "actual_routes": sorted({str(call["route"]) for call in calls}),
            "input_tokens": sum(int(call["input_tokens"]) for call in calls),
            "output_tokens": sum(int(call["output_tokens"]) for call in calls),
            "cost_usd": sum(float(call["cost_usd"]) for call in calls),
            "event_count": replay.cursor().sequence,
            "event_last_hash": replay.cursor().event_hash,
            "final_phase": runner.state.phase.value,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        if not passed:
            raise RuntimeError("M14 live Stardew gate failed")
        print(json.dumps(report, indent=2))
    except Exception as exc:
        if runner.state.phase is RunPhase.RUNNING:
            runner.record_error(
                "m14-stardew-live", error_type=type(exc).__name__, message=str(exc),
                recoverable=False,
            )
            runner.transition(RunPhase.FAILED, reason=str(exc), operation_id="failed")
        raise
    finally:
        provider.close()
        memory.close()
        try:
            client.raw("exit_menu", idempotent=False)
        except Exception:
            pass


def _prepare_fixture(controller: EnvironmentController, *, branch: str) -> None:
    client = controller.client
    client.raw("exit_menu", idempotent=False)
    client.raw("warp", "Farm", *ORIGIN, idempotent=False)
    controller.wait_until(
        lambda observation: observation.player.location == "Farm"
        and _position(observation) == ORIGIN,
        description=f"{branch} fixture origin",
    )
    for target in TARGETS:
        client.raw("remove_item", *target, idempotent=False)
        client.raw("place_item", TWIG_OBJECT_ID, "object", *target, idempotent=False)
    controller.wait_until(
        lambda _: all(controller.client.get_tile_info(*target).object_at_tile for target in TARGETS),
        description=f"{branch} three-twig fixture",
    )


def _clear_twig(
    controller: EnvironmentController,
    events: EventStore,
    *,
    branch: str,
    target: tuple[int, int],
    current_x: int,
    label: str,
) -> tuple[dict[str, object], int]:
    action_count = 0
    request_ids = []
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
    use = _record_action(events, branch, "use", (), controller.client.use, controller)
    request_ids.extend((turn.request_id, use.request_id))
    action_count += 2
    controller.wait_until(
        lambda _: not controller.client.get_tile_info(*target).object_at_tile,
        description=f"{branch} {label} cleared",
    )
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


def _record_action(
    events: EventStore,
    branch: str,
    name: str,
    arguments: tuple[int, ...],
    operation,
    controller: EnvironmentController,
) -> ActionResult:
    before = controller.observe()
    result = operation()
    after = controller.observe()
    events.append("live_primitive_action_completed", {
        "branch": branch, "name": name, "arguments": list(arguments),
        "request_id": result.request_id, "succeeded": result.payload != "False",
        "privileged": False, "before_time": before.game_state.time,
        "after_time": after.game_state.time,
    })
    return result


def _record_movement(
    events: EventStore,
    branch: str,
    name: str,
    arguments: tuple[int, ...],
    operation,
    controller: EnvironmentController,
) -> MovementResult:
    result = operation()
    events.append("live_primitive_action_completed", {
        "branch": branch, "name": name, "arguments": list(arguments),
        "request_id": result.action.request_id, "succeeded": result.action.payload != "False",
        "privileged": False, "source": list(result.source),
        "destination": list(result.destination),
    })
    return result


def _slot(observation: Observation, name: str) -> int:
    for index, item in enumerate(observation.player.inventory):
        if item.name == name:
            return index
    raise RuntimeError(f"Inventory item not found: {name}")


def _quantity(observation: Observation, name: str) -> int:
    return sum(item.quantity or 0 for item in observation.player.inventory if item.name == name)


def _position(observation: Observation) -> tuple[int, int]:
    return observation.player.position.x, observation.player.position.y


if __name__ == "__main__":
    main()
