# M15 Stardew-Shift Design

## Research question

Can the agent adapt when some mechanics change, preserve mechanics that remain stable, and recover
its original behavior when the earlier world returns?

M15 uses a controlled `World A -> World B -> World A` sequence. It separates three failure modes:

- **Rigidity:** retain A behavior throughout B.
- **Global overwrite:** adapt to B by replacing A beliefs, then relearn A after the return.
- **Contextual learning:** preserve A beliefs, create B-specific revisions only where evidence
  changes, and retrieve A beliefs immediately when its visible context returns.

## Counterfactual world family

Every seed creates a versioned and hashable `stardew-shift-v1` family with three opaque two-action
mechanics:

| Mechanic | A to B transformation |
|---|---|
| Crop growth | Optimal action swaps |
| Crop price | Optimal action swaps |
| Resource distribution | Remains stable |

Action labels, rewards, and visible context cues vary deterministically by seed. The decision
interface exposes mechanic IDs, action labels, the current context cue, and observed rewards. It
does not expose evaluator-optimal actions. Exactly two of three mechanics shift, preventing the
benchmark from rewarding indiscriminate forgetting.

Each phase contains six interactions per mechanic. Adaptation lag is the number of interactions
before a stable correct policy begins. Return retention is accuracy on the first interaction for
the two shifted mechanics when A returns.

## Conditions

1. `frozen_a`: learns A and refuses later revisions.
2. `global_overwrite`: keeps one belief per mechanic and overwrites it across contexts.
3. `contextual_beliefs`: preserves context-specific immutable beliefs and uses a reward drop to
   create a new contextual revision.
4. `fresh_at_b`: resets policy state at the B boundary, providing the flagship fresh-B baseline.

All conditions within a seed share world, observable-state, and canonical start-state hashes.
They use the same actions, observations, rewards, phase lengths, and interaction budget.

## Preregistered analysis

The immutable preregistration is
[`configs/m15-stardew-shift-study.yaml`](../configs/m15-stardew-shift-study.yaml). It fixes eight
seeds, four conditions, phase length, stable window, exclusions, stopping rule, primary metric,
bootstrap interval, exact sign test, and World B non-inferiority criterion before execution.

The primary paired effect is:

```text
contextual return-to-A retention - global-overwrite return-to-A retention
```

The hypothesis passes only if its bootstrap lower bound is positive, the exact two-sided sign-test
result is below 0.05, contextual B adaptation is no slower than `fresh_at_b`, contextual initial-B
regret is no higher than `fresh_at_b`, shifted mechanics are contextualized, the stable mechanic
is preserved, hashes match, and no hidden evaluator action enters a decision input.

All metrics are reconstructed by reopening the hash-chained event logs. Memory records are
immutable, superseded versions remain available for audit, and each revision cites its exact
interaction event.

## Authenticated external gate

The provider gate gives GLM 5.3 context-labeled A memories and the observed result of trying those
actions once in B. It asks for a typed `retain`, `contextualize`, or `retrieve` response and a next
action for every mechanic. It then presents both A and B memories under the returning A cue.

The model never receives the hidden optimum. Evaluation occurs after each strict decision. This
gate tests provider-level context selection; it does not claim live modification of Stardew's game
rules.
