"""Model proposal, restricted compilation, validation, and execution of skills."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping

from pydantic import ValidationError

from prime_stardew.agent.models import ProviderRequest
from prime_stardew.agent.provider import AgentProvider
from prime_stardew.experiments.provenance import artifact_sha256
from prime_stardew.experiments.runner import ExperimentRunner, ModelCallUsage
from prime_stardew.tasks import ActionCommand, TaskFixture, TaskScore, TaskTrajectory, score_trajectory

from .models import (
    Scalar, SkillDefinition, SkillProposal, SkillUseMetrics, SkillValidation,
    ValidationStage, normalize_skill_json, validate_parameter_value,
)
from .store import SkillStore, SkillStoreError


SAFE_ACTIONS = frozenset({
    "move", "move_relative", "move_step", "turn", "choose_item", "use",
    "interact", "take_from_chest", "put_to_chest",
})


class SkillError(RuntimeError):
    pass


class SkillProposalEngine:
    def __init__(self, runner: ExperimentRunner, provider: AgentProvider) -> None:
        self.runner = runner
        self.provider = provider

    def propose(
        self,
        *,
        proposal_id: str,
        objective: str,
        trajectories: tuple[TaskTrajectory, ...],
    ) -> SkillProposal:
        if not self.runner.config.learning.skills:
            raise SkillError("Skills are disabled by the run configuration")
        if len(trajectories) < 2:
            raise SkillError("Skill proposal requires at least two trajectories")
        if any(not score_trajectory(trajectory).success for trajectory in trajectories):
            raise SkillError("Skill proposal sources must all be successful")
        prompt = _proposal_prompt(objective, trajectories)
        response = self._call(proposal_id, prompt, repair=False)
        try:
            proposal = SkillProposal.model_validate_json(normalize_skill_json(response.text))
            _validate_sources(proposal.skill, trajectories)
            return proposal
        except (ValidationError, ValueError, SkillError) as first_error:
            repair_prompt = (
                "Return exactly one corrected JSON object matching the original skill schema. "
                "Skill arguments permit only {\"parameter\": \"name\"} or "
                "{\"literal\": scalar}; never use a field named value. "
                f"Error: {first_error}. Candidate: {response.text}. "
                f"Original request: {prompt}"
            )
            repaired = self._call(f"{proposal_id}-repair", repair_prompt, repair=True)
            try:
                proposal = SkillProposal.model_validate_json(normalize_skill_json(repaired.text))
                _validate_sources(proposal.skill, trajectories)
                return proposal
            except (ValidationError, ValueError, SkillError) as final_error:
                raise SkillError("Invalid skill proposal after one repair") from final_error

    def _call(self, call_id: str, prompt: str, *, repair: bool):
        estimated = max(1, (len(prompt.encode("utf-8")) + 3) // 4)
        self.runner.assert_model_call_allowed(input_tokens=estimated)
        request = ProviderRequest(
            call_id=call_id, prompt=prompt, estimated_input_tokens=estimated,
            decoding=self.runner.config.provider.decoding.model_dump(mode="json"),
            repair=repair,
        )
        response = self.provider.complete(request)
        self.runner.record_model_call(
            call_id,
            ModelCallUsage(
                request_id=response.request_id, input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens, latency_ms=response.usage.latency_ms,
                cost_usd=response.usage.cost_usd,
            ),
            provider=response.provider, model=response.model, route=response.route,
            decoding=request.decoding,
        )
        return response


def compile_skill(
    skill: SkillDefinition,
    parameters: Mapping[str, Scalar],
    *,
    allowed_actions: tuple[str, ...],
) -> tuple[ActionCommand, ...]:
    expected = {parameter.name for parameter in skill.parameters}
    if set(parameters) != expected:
        raise SkillError(
            f"Skill parameters must be exactly {sorted(expected)}; got {sorted(parameters)}"
        )
    for parameter in skill.parameters:
        validate_parameter_value(parameter, parameters[parameter.name])
    fixture_allowlist = set(allowed_actions)
    commands = []
    for step in skill.steps:
        if step.action not in SAFE_ACTIONS or step.action not in fixture_allowlist:
            raise SkillError(f"Skill action {step.action!r} is outside the typed allowlist")
        arguments = tuple(
            argument.literal if argument.parameter is None else parameters[argument.parameter]
            for argument in step.arguments
        )
        commands.append(ActionCommand(name=step.action, arguments=arguments))
    return tuple(commands)


class SkillExecutor:
    def __init__(self, store: SkillStore, runner: ExperimentRunner | None = None) -> None:
        self.store = store
        self.runner = runner

    def validate(
        self,
        *,
        validation_id: str,
        skill: SkillDefinition,
        stage: ValidationStage,
        fixture: TaskFixture,
        parameters: Mapping[str, Scalar],
        run: Callable[[tuple[ActionCommand, ...]], TaskTrajectory],
    ) -> SkillValidation:
        commands = compile_skill(skill, parameters, allowed_actions=fixture.allowed_actions)
        trajectory = run(commands)
        if trajectory.fixture.fixture_id != fixture.fixture_id:
            raise SkillError("Validation returned a trajectory for a different fixture")
        score = score_trajectory(trajectory)
        validation = SkillValidation(
            validation_id=validation_id, skill_id=skill.skill_id, version=skill.version,
            stage=stage, fixture_id=fixture.fixture_id, success=score.success,
            score=score.score, trajectory_sha256=artifact_sha256(trajectory.model_dump(mode="json")),
            failure_reasons=score.failure_reasons,
        )
        self.store.record_validation(validation)
        if self.runner:
            self.runner.events.append_idempotent(
                "skill_validated", validation.model_dump(mode="json"),
                idempotency_key=f"skill-validation:{validation_id}",
            )
        return validation

    def execute(
        self,
        *,
        use_id: str,
        skill_id: str,
        fixture: TaskFixture,
        parameters: Mapping[str, Scalar],
        run: Callable[[tuple[ActionCommand, ...]], TaskTrajectory],
        baseline_model_decisions: int = 1,
        baseline_primitive_actions: int,
    ) -> tuple[TaskTrajectory, TaskScore, SkillUseMetrics]:
        skill = self.store.active(skill_id)
        if skill is None:
            raise SkillStoreError(f"No active version for skill {skill_id!r}")
        commands = compile_skill(skill, parameters, allowed_actions=fixture.allowed_actions)
        trajectory = run(commands)
        score = score_trajectory(trajectory)
        metrics = SkillUseMetrics(
            use_id=use_id, skill_id=skill.skill_id, version=skill.version,
            success=score.success, score=score.score,
            baseline_model_decisions=baseline_model_decisions, actual_model_decisions=0,
            baseline_primitive_actions=baseline_primitive_actions,
            actual_primitive_actions=len(trajectory.actions),
        )
        self.store.record_use(metrics)
        if self.runner:
            self.runner.record_skill_use(
                use_id, skill=skill.skill_id, version=str(skill.version),
                success=metrics.success, score=metrics.score,
                model_decisions_saved=metrics.model_decisions_saved,
                primitive_actions_saved=metrics.primitive_actions_saved,
            )
        return trajectory, score, metrics


def _validate_sources(skill: SkillDefinition, trajectories: tuple[TaskTrajectory, ...]) -> None:
    expected = {f"trajectory-{index}" for index in range(1, len(trajectories) + 1)}
    if set(skill.source_trajectory_ids) != expected:
        raise SkillError("Skill must cite every supplied successful trajectory exactly once")
    kinds = {trajectory.fixture.kind.value for trajectory in trajectories}
    if kinds != {skill.task_kind}:
        raise SkillError("Skill task kind does not match its source trajectories")
    allowed = set.intersection(*(set(trajectory.fixture.allowed_actions) for trajectory in trajectories))
    if any(step.action not in allowed or step.action not in SAFE_ACTIONS for step in skill.steps):
        raise SkillError("Proposed skill contains an action outside its source fixture allowlist")


def _proposal_prompt(objective: str, trajectories: tuple[TaskTrajectory, ...]) -> str:
    evidence = [{
        "trajectory_id": f"trajectory-{index}",
        "fixture": trajectory.fixture.model_dump(mode="json"),
        "actions": [
            {"name": action.name, "arguments": list(action.arguments)}
            for action in trajectory.actions
        ],
        "score": score_trajectory(trajectory).model_dump(mode="json"),
    } for index, trajectory in enumerate(trajectories, 1)]
    schema = {
        "skill": {
            "schema_version": 1, "skill_id": "lowercase-hyphen-id", "version": 1,
            "name": "string", "description": "string", "task_kind": "water_crop",
            "parameters": [{"name": "tool_slot", "type": "integer", "minimum": 0, "maximum": 11}],
            "preconditions": ["string"], "postconditions": ["string"],
            "steps": [
                {"action": "choose_item", "arguments": [{"parameter": "tool_slot"}]},
                {"action": "turn", "arguments": [{"literal": 2}]},
                {"action": "use", "arguments": []},
            ],
            "source_trajectory_ids": ["trajectory-1", "trajectory-2"],
        },
        "rationale": "string",
    }
    return (
        "Return exactly one JSON object. Propose one reusable procedural skill from all "
        "successful trajectories. Use only fixture allowed actions. Parameterize the tool "
        "inventory slot as integer tool_slot from 0 through 11; other stable arguments may "
        "be literals. Do not include setup or debug actions.\n"
        f"[objective]\n{json.dumps(objective)}\n"
        f"[successful_trajectories]\n{json.dumps(evidence, sort_keys=True)}\n"
        f"[response_schema]\n{json.dumps(schema, sort_keys=True)}"
    )
