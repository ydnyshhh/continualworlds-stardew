# M14 active experimentation and scientific learning design

## Repository audit

M14 reuses the following exact components:

- Belief records, immutable versions, supersession, prediction fields, provenance, and calibration:
  `memory/models.py`, `memory/store.py`, `reflection/models.py`, and `reflection/engine.py`.
- Provider-neutral strict calls, repair, and attribution: `agent/models.py`, `agent/provider.py`,
  and `experiments/runner.py`.
- Immutable configuration and run identity: `experiments/config.py`.
- Append-only authenticated events: `telemetry/events.py`.
- Preregistration hashing and paired statistics: `experiments/provenance.py`,
  `studies/preregistration.py`, and `studies/analysis.py`.
- Deterministic seeded simulation patterns and raw-event reporting: `benchmarks/season.py` and
  the M9-M12 gates.

No parallel belief database, provider adapter, event store, or statistical implementation is
introduced.

## Controlled hidden-mechanics benchmark

Each seed contains three counterfactual crop-treatment scenarios. The safe treatment returns a
known value. The alternative has a hidden seeded value that is absent from decision input.

- Two long-horizon scenarios have identical visible priors and positive expected value of one
  test. One hidden result is high and one is low; their order changes by seed.
- One short, expensive scenario has negative expected value of testing. Its hidden result also
  changes by seed, so abstention cannot rely on a fixed scenario outcome.

An experiment consumes the current production cycle, charges a visible overhead, reveals the
alternative's value, and permits the learned best choice for remaining cycles. This creates a
real immediate opportunity cost when the candidate is poor.

## Conditions

- `no_explicit_uncertainty`: exploit the safe treatment without a belief record.
- `belief_tracking_only`: persist the prior belief but do not perform experiments.
- `agent_experimentation`: use only visible priors, possible outcomes, horizon, and cost to test
  when expected net value is positive.
- `oracle_experimentation`: evaluator upper bound that uses hidden mechanics.
- `random_experimentation`: fixed-seed random test choices.

All conditions receive the same hidden world, action space, scenario order, and reward accounting.

## Schemas and validation

- `ExperimentScenario`: visible hypothesis context with safe value, possible alternative values,
  prior probability, remaining cycles, and experiment overhead.
- `HiddenMechanicScenario`: evaluator-only extension containing the realized alternative value.
- `ExperimentChoice`: scenario ID, exploit/experiment action, predicted probability, expected
  information gain, expected net value, and rationale.
- `ExperimentDecision`: exactly one unique choice per supplied scenario.
- `ExperimentOutcome`: raw reward, counterfactual safe reward, experiment cost, information gain,
  learned value, belief IDs, and future reward attributable to information.

Unknown, duplicate, missing, and numerically invalid choices are rejected. Agent decisions receive
one existing-style schema repair call.

## Beliefs and provenance

Visible prior hypotheses are stored as `MemoryKind.BELIEF` records citing the
`hypothesis_created` event. A completed experiment creates a new immutable belief version whose
`supersedes_id` points to the prior and whose `source_event_ids` cite the experiment execution
event. The old belief remains in history with superseded status.

## Events

- `active_experiment_study_started`
- `hypothesis_created`
- `experiment_proposal`
- `experiment_execution`
- `hypothesis_revised`
- `active_experiment_scenario_completed`
- `active_experiment_study_completed`
- `m14_authenticated_scored`

Every outcome statistic is rebuilt from these authenticated events.

## Metrics

Primary: net total reward, comparing agent experimentation with belief tracking only.

Secondary: experiments proposed/executed, experiment overhead, information gain, belief accuracy,
time to identification, future reward attributable to experimentation, restraint on negative-value
cases, model calls/tokens/cost, and regret relative to the hidden-mechanic oracle.

## Study and gate

The preregistered study uses eight paired seeds and all five conditions. Analysis uses the paired
agent-minus-belief reward effect, deterministic paired bootstrap, and exact sign test. The gate
requires matched hidden-world hashes, full run coverage, correct restraint, no raw-event failure,
and no manual score edit.

After the offline gate, one authenticated Prime/OpenRouter GLM 5.3 decision receives the same
visible scenarios. The gate verifies strict coverage, attribution, cost, at least one experiment
and one abstention, belief-version provenance, and raw-event reconstruction. This is controlled
simulation/replay, not live Stardew gameplay.

