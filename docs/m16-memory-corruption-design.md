# M16 Memory Corruption and Autonomous Repair Design

## Research question

Can the learning system detect that plausible stored knowledge is wrong, repair the immutable
memory graph from observed evidence, and improve its own repair policy before a held-out second
corruption episode?

## Hidden-corruption family

Each seeded family has two episodes. Each episode contains seven interleaved records:

- one false semantic memory;
- one false belief;
- one stale, formerly correct belief;
- one overconfident belief supported by one claimed observation;
- one incorrect procedural recommendation; and
- two correct decoys.

The decision input contains the record text, recommended action, alternatives, claimed confidence,
claimed support count, source description, and observed outcomes. It does not contain the hidden
correct action, corruption type, or a corrupted/clean flag. Evaluator truth is appended only after
the scenario's decisions are complete. Separate world and observable hashes make this separation
auditable.

## Conditions

The preregistered study pairs four conditions on eight seeds:

1. `blind_trust` continues using every retrieved record.
2. `fixed_repair` requires two contradictory observations before superseding a record.
3. `adaptive_repair` starts with the same two-observation policy, proposes a restricted patch after
   episode one, and applies it in episode two.
4. `oracle_repair` supplies a performance ceiling and is excluded from the primary causal contrast.

Adaptive and fixed repair have identical episode-one code, states, inputs, and outcomes. The only
episode-two difference is availability of the learned policy patch. The patch is a frozen, data-only
schema with a named signal, bounded comparison and threshold, response, rationale, and exact
evidence event IDs. It cannot contain executable code, commands, tools, or paths.

## Memory and event semantics

Injected records use the existing `MemoryStore`. A correction creates a new immutable
`MemoryRecord` whose `supersedes_id` points to the earlier record and whose `source_event_ids`
contains the exact contradictory interaction. The earlier record remains queryable for audit but is
removed from active retrieval by its append-only `superseded` status.

The hash-chained event log records injection, interaction, contradiction, selected response,
repair, truth reveal, episode completion, policy proposal, policy application, and study completion.
The report is reconstructed from those raw events.

## Preregistered outcomes

The primary metric is integer episode-two utility-retained percentage for adaptive minus fixed
repair, paired by seed. The study requires a positive 95% bootstrap lower bound and an exact
two-sided sign-test result below alpha 0.05. Integrity gates also require:

- matching world, observable, and start-state hashes;
- identical adaptive and fixed episode-one utility;
- no hidden-field exposure;
- no missing or invalid event chain;
- no repair of clean decoys beyond the zero-event margin; and
- exactly one applied adaptive patch per seed.

Secondary recovery measures are harmful actions before detection, time to first suspicion, time to
correction, evidence required, post-correction recurrence, utility lost, corrupt records repaired,
and clean false repairs.

## Authenticated transfer gate

`prime-stardew-m16-live` uses Prime Agent RPC with OpenRouter `z-ai/glm-5.3`. It presents one
unlabeled record per bounded call, asks for record-level repair decisions, asks the model to invent a data-only policy
patch without suggesting a desired mechanism, applies that patch to a second episode, and checks
five corrupted and two clean records. Provider/model/route attribution and token usage are recorded
through the existing experiment runner.
