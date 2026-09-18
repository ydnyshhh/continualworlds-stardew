# E1 Long-Horizon Continual-Learning Ablation

## Status and scientific boundary

E1 is a study family, not an engineering milestone. It evaluates whether accumulated external
agent state improves future behavior during a persistent Stardew lifetime and isolates which
mechanism causes any improvement.

The current `e1-offline` runner validates contracts, event reconstruction, probe isolation, reset
semantics, and reporting. Its synthetic probe values are deliberately identical across conditions
and support no scientific conclusions. E1-Pilot begins only after authenticated seven-day smoke
runs and a live persistent-lifetime runner pass their gates.

## Research questions

1. Does persistent experience improve standardized probe AULC?
2. Does relevance retrieval improve use of the same memory-token budget?
3. Do learned skills reduce recurring-task deliberation without harming outcomes?
4. Does reflection produce useful downstream behavior after accounting for its cost?
5. Does Spring Year 1 knowledge survive the intervening seasons and return in Spring Year 2?
6. Does the full harness add measurable value beyond the standardized learning stack?

Final gold is reported as an environment outcome. It is not the definition of learning.

## Persistent objective

Every condition receives this unchanged objective:

> Manage and develop this farm as effectively as possible over the available lifetime. Increase
> its long-term economic value and productive capacity while improving useful tools,
> infrastructure, resource access, and capabilities. Balance immediate reward against future
> progress. Adapt your strategy based on what you learn from experience.
>
> You may choose your own priorities, intermediate goals, and daily strategy. Avoid unnecessary
> bankruptcy, wasted days, preventable failures, and investments that cannot reasonably pay off.
> Use only information and actions available through the permitted game interface.

Evaluator goals, probes, scoring weights, future events, and oracle strategies remain hidden.

## Study phases

| Phase | Conditions | Seeds | Horizon | Runs | Purpose |
|---|---:|---:|---:|---:|---|
| Contract validation | 6 | 2 | 28 days | 12 | Validate mechanics; no scientific claims |
| E1-Pilot | 6 | 2 paired | 28 days | 12 | Cost, reliability, variance, and probe calibration |
| E1-Season | 6 | 8 paired | 28 days | 48 | First preregistered primary study |
| E1-Year | Initially A/C/D/E/F | 5 paired | 119 days | 25 | Long-delay transfer and retention |

The year covers Spring, Summer, Fall, Winter, and Spring Year 2 Days 1–7. E1-Season will not run
until the pilot report is inspected and its versioned protocol is accepted.

## Immutable condition matrix

| Condition | Cross-day state | Memory exposure | Retrieval | Skills | Reflection | Full harness |
|---|---:|---|---:|---:|---:|---:|
| A Base | No | None | No | No | No | No |
| B Memory | Yes | Chronological | No | No | No | No |
| C Retrieval | Yes | Relevance-ranked | Yes | No | No | No |
| D Skills | Yes | Relevance-ranked | Yes | Yes | No | No |
| E Refine | Yes | Relevance-ranked | Yes | Yes | Yes | No |
| F Full | Yes | Relevance-ranked | Yes | Yes | Yes | Yes |

Conditions B and C share the same maximum memory-token budget. B receives a deterministic recent
active-memory window. C selects from the same eligible store by relevance. F currently adds the
immutable manifest below to E:

```yaml
persistent_goal_manager: true
adaptive_tool_selection: true
native_compaction: true
subagents: false
```

The authenticated pilot configuration must record the exact supported feature set again. A feature
that the Prime adapter cannot demonstrate will be disabled in the manifest rather than simulated.

## Fairness and inference accounting

Paired conditions share the canonical game-state hash, seed, world, model, provider, decoding,
objective, interfaces, pause policy, and decision budgets. The initial contract fixes an 8,000-token
decision context and a 1,500-token memory allowance. Pilot values remain subject to cost inspection
before preregistration.

Each decision records maximum and included context, memory, skills, recent events, and omitted
items. Calls are attributed separately to acting, reflection, skill proposal, memory management,
subagents, repair, and compute-matched replanning. Game time pauses during inference.

## Harness-neutral boundary

E1 depends on an `AgentHarness` protocol with `start`, `decide`, `end_day`, `checkpoint`, `restore`,
`export_learning_state`, and `reset_learning_state`. A harness exposes an exact capability manifest.
The benchmark rejects a runtime manifest that differs from the immutable condition configuration.

Prime is the first adapter. The dimensions `environment`, `condition`, `model`, `harness`, and
`seed` remain independent for future harness comparisons.

## Recurring competency records

Natural gameplay is segmented into farm maintenance, shopping, crop planning, mining, and
investment instances. Every instance records work units, model decisions, primitive actions, game
minutes, energy, success, failures, skills, memories, and domain fields. Efficiency is normalized
per ten work units so larger farms do not appear less efficient merely because they contain more
work.

The evaluator detects activity families from game events. Agents are not asked to label benchmark
tasks.

## Standardized probes

Probe days for a 119-day lifetime are 7, 14, 28, 42, 56, 70, 84, 98, 112, and 119. A 28-day run
uses Days 7, 14, and 28. Each probe set includes:

- familiar tasks for retention and reuse;
- structurally related transfer tasks;
- conflict tasks requiring stale-rule rejection and contextualization;
- optional preregistered novel tasks.

The parent is checkpointed before a probe. Game and learning state are copied into a branch with a
distinct ID and parent hashes. Probe events remain on the branch, the branch is discarded, and the
parent hashes are verified unchanged before the lifetime resumes.

## Primary metric

Each task score is normalized to `[0, 1]`. A weighted mean gives `ProbeScore(day)`. The primary
metric is trapezoidal area under this curve, divided by the elapsed span between the first and last
probe. Probe definitions, maximum scores, and weights are immutable before outcome runs.

Key secondary metrics are within-season gain, time to criterion, recurring-task efficiency,
cross-season transfer, negative transfer, memory precision, stale retrieval, skill reuse,
refinement usefulness, Spring Year 2 retention, and absolute and cost-normalized outcomes.

## Learning-state resets and Spring Year 2

A reset deactivates selected branch state without deleting historical provenance or modifying game
state. Supported branches are full state, memory and belief reset, skill reset, all-learning reset,
and refinement-product reset. Every reset records before and after hashes and preserved historical
artifact IDs.

At Day 112 all Spring Year 2 branches share the exact save, inventory, farm, model, provider, and
objective. The main comparison is full state versus all-learning reset; memory and skill resets
provide mechanism tests.

## Memory, skill, and refinement analysis

Memory curves include count, active tokens, types, retrieval precision, distinct-day reuse, dead
memory after age thresholds, stale retrieval, contradictions, and supersessions. Evaluable beliefs
are classified as universal, seasonal, location-specific, temporary, or task-specific. Unsupported
beliefs remain unscored.

Skill records include creation, validation, activation, successes, failures, days and seasons used,
last use, dormancy, action count, and decision/token/cost savings. Outcome non-inferiority is checked
alongside proceduralization gain.

Refinement attribution follows source experience to reflection artifact, future retrieval or use,
decision, and outcome. Outcomes are unused, no behavioral effect, harmful, neutral, beneficial, or
unknown. High-value causal claims require preregistered checkpoint forks.

## Statistical plan

Seeds are paired. E1 reports within-seed AULC differences, paired bootstrap intervals, exact sign
tests, paired permutation tests, effect sizes, time to criterion, and complete raw condition means.
Endpoint-only inference is insufficient. Negative transfer and null results are retained.

Failures are classified as agent, environment, provider, infrastructure, budget, invalid output,
or checkpoint failures. Exclusions, retry policy, and stopping rules will be frozen before
E1-Season. Infrastructure failures resume from authenticated checkpoints where possible.

## Repository reuse and gaps

E1 reuses M3 actions, M4 event/config/checkpoint infrastructure, M5 inference and pause controls,
M6 memory, M7 reflection, M8 skills, M9 season recovery, M10 statistics, M11 lifetime transfer,
M12 memory budgets, M13 replay primitives where explicitly configured, M15 shift measures, and M16
belief-quality machinery.

The new E1 package supplies the condition matrix, harness contract, branch identity, reset schema,
competency events, probe scoring, AULC, raw-event contract validation, and condition-faithful B/C
memory exposure. Combined checkpoints now capture and restore named learning SQLite databases in
addition to the game, agent, configuration, event cursor, and memory database. Remaining work before
the real pilot is the live broad-objective runner, Prime adapter capability enforcement, physical
probe lifecycle orchestration around those combined checkpoints, evaluator-side activity
segmentation from live events, authenticated seven-day smoke runs, cost review, and pilot
preregistration.

## Reporting and interpretation

Reports are reconstructed from hash-chained events and retain provenance, config and checkpoint
hashes, seed pairing, failures, costs, and curve data. The contract gate emits study summary, run
metrics, probe scores, learning curves, competency metrics, per-category inference accounting,
memory exposure, skill events, refinement events, and failure analysis. Live phases will add outcome
attribution and Spring Year 2 reset tables.

Higher reward alone is not interpreted as learning. Equal reward with fewer decisions can show
procedural improvement; stronger performance at four times the cost exposes a compute tradeoff;
unused reflections show low refinement efficiency; worse post-shift behavior shows interference;
and equality between E and F shows no measurable full-harness benefit.
