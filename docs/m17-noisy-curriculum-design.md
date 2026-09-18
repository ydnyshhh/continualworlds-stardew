# M17 Self-Generated Curriculum for Noisy and Adversarial Evidence

## Research question

Can the agent identify weaknesses in how it handles evidence, choose a fixed-cost practice
curriculum, and improve performance on hidden noisy and adversarial tasks?

M17 combines the roadmap's self-generated curriculum milestone with the next external-validity
targets from M16: noisy evidence, adversarial observations, and live Stardew corruption.

## Training and hidden evaluation

Each seeded family contains a training view and a separately hashed hidden evaluation manifest.
The training view exposes four broad diagnostic scores and four practice objectives:

- independent noise aggregation;
- burst noise aggregation;
- duplicate-source resistance; and
- spoofed-source resistance.

Each seed has two evidence-handling weaknesses. The curriculum may select exactly two objectives at
equal unit cost. It cannot inspect hidden task IDs, observation streams, correct actions, or
evaluator scores. Hidden tasks are revealed only after practice is complete.

## Conditions

The preregistered study pairs four conditions over eight seeds:

1. random curriculum with a fixed seed;
2. fixed human curriculum;
3. weakness-targeted scripted curriculum; and
4. agent-generated curriculum.

All conditions receive the same diagnostics, catalog, objective budget, learner state, and hidden
evaluation. The primary contrast is agent-generated minus fixed-human held-out accuracy. The agent
condition must also be non-inferior to the scripted weakness-targeted upper baseline.

## Attack construction

Independent and burst-noise tasks contain three correct claims followed by two incorrect claims.
Duplicate-source tasks contain two independent correct sources and three repeated claims from one
attacker identity. Spoofing tasks contain two authenticated correct sources and one final
unauthenticated impostor claim. A learner without the relevant practiced defense follows the final
claim. A defended learner uses majority aggregation, per-source vote caps, or authentication
filtering as appropriate.

## Metrics and integrity

The primary metric is held-out transfer accuracy after curriculum training. Secondary measures are
training cost, practice diversity, weakness-detection accuracy, regime-level accuracy, utility, and
attack successes. Reports are reconstructed from hash-chained events.

Integrity gates require matching world, training-view, evaluation-manifest, and start-state hashes;
equal practice cost; complete task coverage; zero held-out contamination; a positive bootstrap
lower bound; an exact sign-test result below alpha; and non-inferiority to scripted selection.

## Authenticated and live gates

The authenticated GLM gate supplies only diagnostic IDs, scores, and the practice catalog. It checks
the structured roadmap fields: observed weakness, evidence, proposed training task, expected
transfer, estimated cost, and success criterion.

The disposable Stardew gate injects an immutable false belief that a farm twig yields 5 Wood. It
then supplies two authenticated live-game samples and one unauthenticated adversarial claim. GLM
must select evidence, repair through immutable supersession, and predict a third held-out live twig.
Fixture placement is outside scoring; movement and Axe actions are ordinary scored primitives.
