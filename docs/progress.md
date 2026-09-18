# Implementation progress

## Completed

### M0: Local environment proof

- Full Stardew Valley 1.6.15 plus SMAPI 4.5.2 and patched StarDojo 1.0.0.
- Isolated farmer, structured observation, RGBA screenshot, actions, pause/resume,
  day transition, save, restart, and checkpoint reload.
- Personal save was never loaded.

### M1: Reliable environment SDK

- Pydantic observation and date models with forward-compatible extra fields.
- Loopback-only TCP transport with request IDs, response limits, deadlines,
  framing checks, UTF-8 validation, and typed errors.
- Mutations execute once; retry is restricted to explicitly idempotent requests.
- Replay transport supports tests without launching the game.
- Lifecycle controller loads by save ID and polls farmer/date state instead of the
  racy `wait_game_start` event.
- Reversible live smoke and soak commands.
- 79 tests pass, including injected disconnect recovery and typed movement/tool
  command validation.
- Fifty live action/observation cycles passed in 23.846 seconds and restored the
  starting direction.

## Completed (continued)

### M2: Deterministic saves and checkpoints

Implemented:

- Stable-file polling before snapshot.
- Atomic checkpoint directory publication.
- Manifest written last with schema version, UUID, timestamp, farmer, date,
  environment fingerprint, sizes, and SHA-256 hashes.
- Verification detects missing, changed, and path-traversal entries.
- Restore refuses overwrite and publishes through an atomic directory rename.
- Primary save filenames are renamed for a new experiment-owned save ID.
- Live checkpoint `m2-spring06` restored as
  `PrimeStardewM2Restore_406041616` and loaded successfully through StarDojo.
- Append-only JSONL event store with monotonic sequences, UUIDs, UTC timestamps,
  canonical SHA-256 record hashes, predecessor links, durable appends, and complete
  validation on reopen and append.
- Authenticated event cursors record run ID, sequence, event hash, and byte boundary;
  they remain valid as later events are appended.
- Atomic combined bundles include the verified game snapshot, agent state, frozen
  configuration, event cursor, environment fingerprint, and hashes for every file.
- Combined checkpoint `combined-spring06` was restored as
  `PrimeStardewCombined_406041616` and loaded successfully through StarDojo.
- An injected event after the saved cursor was identified as post-checkpoint work,
  without duplicating the three committed events.
- Retention planning protects base, milestone, failure, malformed, and unverifiable
  bundles. It retains the newest distinct completed days per run and only prunes
  verified ordinary day bundles after a fresh-plan comparison.
- Checkpoint polling waits through locked and lingering Stardew temporary save files.
- The live Spring 6→7→8 interruption probe restored the pre-action state, detected
  the unfinished logical action, reissued it, and recorded exactly one completion.
- Final recovery milestone checkpoint `03122429-537b-4d2d-a13d-6f42a66ebe2e`
  verified against the retained event log.

M2 gate result:

1. Base/latest-day/milestone/failure retention: passed.
2. Real interruption after an action mutation and before completion: passed.
3. Restore last completed day with one committed logical action: passed.

### M3: Action model and deterministic task harness

- Absolute, relative, and one-tile movement passed on verified clear destinations.
- Axe selection, tree chopping, and debris pickup passed: five swings used 10 stamina
  and collected four Wood.
- Hoeing, watering, seed selection, planting, crop growth fixture setup, and normal
  harvesting passed with state and inventory evidence.
- StarDojo absolute movement can enter a tree-occupied destination while returning
  `True`.
- The environment controller now guards absolute, relative, and step movement. It
  preflights objects, blocking terrain, buildings, furniture, NPCs, and exits, then
  verifies the reported result, location, and exact final tile.
- A live regression rejected the tree at `(62,23)` without sending a movement
  mutation and kept the farmer at `(62,22)`.
- Five versioned, immutable fixtures cover turn/move, watering, debris clearing and
  pickup, harvesting, and chest transfer.
- Normalization merges StarDojo's coordinate crop list into exact target tile state
  and handles the live `Vector2` string and nullable menu representations.
- Scorers enforce farmer, date, location, origin, action allowlist, action budget,
  game-time budget, successful responses, and task-specific state transitions.
- `LiveTaskHarness` exposes typed actions only after the baseline snapshot. Fixture
  setup commands cannot enter the scored action trace.
- The Spring 8 live gate completed all 15 runs: five tasks, three repetitions each,
  score 1.0 on every run, with zero privileged actions in scored trajectories.
- StarDojo now has a setup-only `place_chest` initializer so chest fixtures contain
  a real interactive `Chest` rather than a generic object named Chest.

M3 gate result:

1. Five deterministic atomic task definitions and scorers: passed.
2. Three clean repetitions per task without manual input: passed.
3. No privileged debug command in any scored run: passed.

### M4: Experiment runner and event store

- Strict YAML is validated into deeply frozen Pydantic configuration models.
- Canonical configuration SHA-256 values generate stable run IDs from suite,
  provider model, condition, seed, and content hash.
- Run creation records the complete configuration, code revision, dirty patch hash,
  game/SMAPI/StarDojo versions, installed DLL hash, and provider route.
- The lifecycle state machine validates created, running, day-complete,
  interrupted, recovering, completed, and failed transitions by reconstructing
  state from the event log.
- Stable idempotency keys cover task, observation, decision, primitive action,
  scoring, checkpoint, model-call, memory, skill, artifact, error, and recovery
  events. Replaying identical work returns its original event; conflicting reuse is
  rejected.
- Action, model-call, token, cost, game-day, and wall-time limits are represented in
  the immutable run budget. Action and model usage are enforced at append time.
- Checkpoint resume authenticates the event prefix and rejects a configuration hash
  mismatch before restoring state.
- Resume can discover the newest valid completed-day bundle for the current run;
  malformed, unverifiable, foreign-run, and non-day checkpoints are ignored.
- Two independent scripted runs produced normalized trajectory digest
  `3b067f930c672c233064094b6ecf6642ef84ac6ae1e1a21d648f7151172b79a1`.
- The injected interruption left four trailing events after the checkpoint. Resume
  completed the pending task with two committed actions, and replaying that task
  added zero events.

M4 gate result:

1. Immutable configuration and stable identity: passed.
2. Equivalent normalized trajectories for independent runs: passed.
3. Checkpoint-backed resume with no duplicated action or event: passed.

### M5: Prime and provider integration

- Prime Agent is isolated behind its official persistent JSONL RPC mode; experiments
  do not depend on terminal UI automation.
- Provider-neutral request, response, usage, context, decision, and session-state
  models keep the runner independent of Prime and the underlying model vendor.
- Context assembly assigns fixed budgets to the objective, current observation,
  active goals, retrieved memories, applicable skills, and recent events. It records
  the context hash plus included and omitted IDs.
- Decisions must be strict JSON and use the task action allowlist. One malformed or
  disallowed response gets one repair call; a second failure is recorded and raised.
- Each provider call records the actual provider, model, API route, decoding settings,
  request ID, input/output tokens, latency, and cost from the response.
- Game time is paused around every provider call and resumed in `finally`, including
  provider failures.
- Model-call, input/output token, cost, action, game-day, and wall-time limits stop
  work through the M4 runner. Knowable limits are checked before inference.
- Session checkpoints contain active goals, decision count, and the last context hash;
  they intentionally exclude the full model transcript.
- The offline contract gate produced five valid decisions over the five live-recorded
  M3 trajectories, attributed all five calls, restored goals into the fifth context,
  and completed the lifecycle.
- The authenticated live gate loaded and verified the isolated Spring 8 save, then
  scored all five tasks at 1.0 through OpenRouter `z-ai/glm-5.3`. Six model calls were
  fully attributed, including one repair, and no privileged action entered a scored
  trajectory.

M5 status:

1. Prime RPC adapter and provider-neutral schemas: passed.
2. Bounded context, structured decisions, pause/resume, budgets, and state restore: passed.
3. Five-task deterministic contract gate: passed.
4. Prime Agent 0.9.5 local installation and no-model RPC framing probe: passed.
5. Five-task live run through an authenticated Prime provider: passed.

### M6: Persistent memory and retrieval ablations

- SQLite stores immutable episode, semantic, and belief records independently from
  derived status history. Supersession creates a new version and keeps the old record.
- Deterministic retrieval ranks keyword overlap, metadata matches, and confidence,
  while filtering season, map, task domain, game-day validity, and minimum confidence.
- Every retrieval event records the query, all candidates, rank features, selection,
  and selected token cost. Agent-authored memory notes cite their source decision event.
- Hashed JSON transfer bundles preserve source IDs and provenance; imports reject
  changed content. Combined checkpoints can snapshot and restore the SQLite database.
- Independent flags implement stateless, context-only, persistent-memory, and
  memory-plus-retrieval conditions without changing the provider or game fixture.
- The deterministic four-condition gate passed from checkpoint digest
  `87efe51bcc4ea5fe407d5b71f83cc1684c0407319446409f00824d2a94ff663a`.
- The authenticated paired gate also passed through fresh Prime Agent 0.9.5 sessions
  using OpenRouter `z-ai/glm-5.3` and the actual `openai-completions` route. Conditions
  A, B, and C selected fallback slot 0; D retrieved `hidden-watering-slot` and selected
  slot 5; deleting memory from D returned it to slot 0.
- Authenticated usage: 9 calls, 6,593 input tokens, 718 output tokens, and $0.013151.

M6 gate result:

1. Same model and immutable checkpoint across four conditions: passed.
2. Retrieval improves the controlled hidden-fact task: passed, gain +1.
3. Removing memory eliminates the gain without changing game state: passed.
4. Export/import provenance and memory checkpoint restore: passed.
5. Automated verification: 84 tests passed.

### M7: Reflection, beliefs, and consolidation

- Reflection selects failures, surprises, and outcomes deterministically within a fixed
  token budget, then records selected and omitted source event IDs.
- Strict structured outputs capture lessons, failed assumptions, counterfactuals, goals,
  confidence-rated beliefs, and optional calibrated predictions. Invalid JSON or schema
  gets at most one repair attempt.
- Every belief citation must belong to the selected evidence. A revision must name an
  active parent belief and cite contradictory evidence before superseding it.
- Belief edits create immutable versions. Current retrieval excludes deleted and
  superseded records.
- Consolidation merges repeated episodes into a semantic rule with deterministic identity,
  complete source-event provenance, and support count.
- Nightly, failure-triggered, surprise-triggered, and agent-selected policies are covered.
- Prediction outcomes produce aggregate Brier score, mean confidence, and observed rate.
- The deterministic hidden crop-rule gate formed the four-night rule, revised it to five
  nights after contradiction, and passed all current-belief and provenance checks.
- The authenticated gate repeated the two reflections in one direct Prime Agent 0.9.5 RPC
  process using OpenRouter `z-ai/glm-5.3` and the `openai-completions` route.
- Authenticated usage: 2 calls, 1,240 input tokens, 449 output tokens, and $0.00397144.
- Its append-only log contains 11 verified hash-chained events ending in `completed`.

M7 gate result:

1. Supported hidden rule formation and contradiction-driven revision: passed.
2. Deleted and superseded beliefs excluded from current retrieval: passed.
3. Episode-to-semantic consolidation preserves provenance: passed.
4. Four refinement policies and prediction calibration: passed.
5. Automated verification: 90 tests passed.

### M8: Procedural skills

- Skill definitions are immutable, versioned, parameter typed, and linked to every source
  trajectory used to propose them.
- The proposal engine accepts only repeated successful trajectories, validates strict JSON,
  and permits one schema repair.
- Compilation resolves exact parameters and rejects raw, debug, privileged, unknown, or
  fixture-disallowed actions before execution.
- Activation requires both replay and disposable-fixture validation. Validation records retain
  trajectory hashes, scores, and failure reasons.
- SQLite history is append-only across version creation, validation, activation, execution,
  and rollback. Earlier versions remain available and rollback selects an already retained version.
- Every use records baseline and actual model decisions, primitive actions, score, and success.
- The deterministic D memory-plus-retrieval versus E memory-plus-retrieval-plus-skills ablation
  reused the same three live-recorded M3 watering trajectories.
- The deterministic learned skill scored 1.0 three times, used no privileged action, reduced
  three baseline decisions to one proposal call, and retained the same nine primitives.
- The authenticated gate used OpenRouter `z-ai/glm-5.3` to propose the skill, passed replay and
  disposable live Stardew validation, then scored 1.0 on three further live executions.
- Authenticated usage: 1 call, 3,309 input tokens, 394 output tokens, and $0.0063662.
- The deterministic baseline, deterministic skill, and authenticated live logs reopened as
  valid 48-, 49-, and 46-event hash chains ending in `completed`.

M8 gate result:

1. Versioned proposal from repeated successful trajectories: passed.
2. Restricted typed execution and two-stage activation: passed.
3. Prior-version retention and rollback: passed.
4. Three score-1.0 executions with net model-decision savings: passed.
5. Disposable live validation and three live executions: passed.
6. Automated verification: 96 tests passed.

### M9: Project and season benchmarks

- Three immutable project fixtures cover harvesting 10 cauliflower with at least 1,500g,
  reaching mine level 40 with a pickaxe upgrade, and building a coop with the first chicken.
- Typed season state records gold, crops, mine depth, tool tier, buildings, animals, decisions,
  primitive actions, retries, invalid actions, energy, game time, memory reads, and skill uses.
- Every simulated day records its starting state, typed project actions, final state, and all
  three intermediate project scores in the append-only experiment log.
- The 28-day deterministic Spring environment varies income reproducibly by seed while applying
  explicit costs and preconditions for planting, harvesting, mining, upgrades, construction,
  and animal purchase.
- Replay checkpoints atomically bind season state, immutable configuration hash, and authenticated
  event cursor. Verification rejects changed state, configuration, cursor, run identity, or day.
- The first seed was interrupted after Day 14. Recovery restored the checkpoint with seven
  trailing recovery events and continued at Day 15 without duplicating Day 14.
- Reports are reconstructed solely from raw events and reject a missing, repeated, or out-of-order
  day completion. No manual score field is accepted by the report path.
- Three seeds completed all 28 days. Every project passed on every seed; criterion days were 13
  for cauliflower, 15 for mine/pickaxe, and 20 for coop/chicken.
- Final gold ranged from 12,322g to 12,458g, and every run used 592 modeled primitive actions.
- Event logs contained 117 records for the interrupted run and 109 for each uninterrupted run.
- The standalone command rebuilt the interrupted run report and reproduced its final event hash.

M9 gate result:

1. Three project fixtures and intermediate progress curves: passed.
2. Three unattended 28-day seeded runs: passed.
3. Day-14 authenticated checkpoint and injected recovery: passed.
4. Exactly one completion for every day 1 through 28: passed.
5. Raw-event report reconstruction with zero manual edits: passed.
6. Automated verification: 101 tests passed.

### M10: Preregistered proceduralization study

- The versioned YAML preregistration fixes the hypothesis, environment, policy, two conditions,
  eight seeds, primary metric, direction, exclusions, non-inferiority margin, alpha, bootstrap
  confidence interval, and exact sign test before execution.
- Conditions D and E differ only in procedural-skill availability. Every pair starts from the
  same `SeasonState` hash and retains immutable configuration plus complete code/runtime provenance.
- The validated watering procedure becomes available after three successful uses and handles five
  later routine watering decisions. It executes the same underlying typed primitive actions.
- All 16 unattended 28-day runs completed. Every run passed all three projects, used 592 primitive
  actions, and recorded zero invalid actions.
- D used 28 model decisions and E used 23 for every seed. Mean paired reduction was 5 decisions,
  with deterministic paired-bootstrap 95% CI [5, 5] and exact two-sided sign-test p=0.0078125.
- Mean outcome difference was 0.0 and the zero-margin non-inferiority requirement passed.
- Failure analysis found zero exclusions, missing pairs, duplicate pairs, start-state mismatches,
  invalid actions, project failures, or nonpositive-effect pairs.
- The causal interpretation is restricted to procedural-skill availability under the checked-in
  deterministic policy and season-replay environment.

M10 gate result:

1. Immutable preregistration and complete provenance: passed.
2. Eight matched multi-seed pairs from an identical starting state: passed.
3. Positive 95% confidence bound and exact test below alpha: passed.
4. Project outcome non-inferiority and unchanged primitive actions: passed.
5. Machine-readable failure analysis and one-command reproduction: passed.
6. Automated verification: 106 tests passed.

### M11: Lifetime, held-out transfer, and learned routing

- The immutable configuration fixes the 112-day horizon, four ordered seasons, distinct scripted
  donor and recipient identities, 84-day dormant-skill gap, transfer conditions, and $0.012 budget.
- The donor completed 112 daily events. State/configuration/event-cursor checkpoints passed at
  Days 28, 56, 84, and 112.
- The row-watering skill succeeded in Spring and again after 84 dormant days in Winter. Semantic
  memory retrieval succeeded at every season boundary.
- Memory and skill exports are independent, content hashed, and provenance tagged. Skill exports
  retain immutable definitions, replay/disposable validation, and activation state.
- Four held-out tasks cover knowledge and procedural transfer. Donor bundles contain none of their
  IDs or unique names.
- Raw and fresh baselines scored 0.5 with 8 decisions. Memories only scored 1.0 with 8 decisions;
  skills only scored 0.5 with 4; full inheritance scored 1.0 with 4.
- Under the same $0.012 budget, economy-only completed 4/8 tasks, capable-only completed 6/8,
  and learned routing completed 8/8. Utility per dollar rose from 500 to 666.67.
- The donor log contains 124 verified events. Failure analysis found no contamination, transfer
  failure, retention failure, missing checkpoint, or routing budget violation.
- An authenticated OpenRouter `z-ai/glm-5.3` external-validity gate used fresh provider sessions
  and the same transferred semantic bundle. Fresh context scored 0/2; transferred context scored
  2/2. One schema repair produced five attributable calls total.
- A cloned disposable save advanced from Spring 8 through 20 observed transitions to Spring 28.
  The imported M8 watering skill then scored 1.0 using three ordinary actions, saved one model
  decision, and used no privileged action in the scored trajectory.
- Live authenticated usage was 3,746 input tokens, 424 output tokens, and $0.00783576. The event
  log contains 33 authenticated events ending in `completed`.

M11 controlled gate result:

1. Full 112-day lifetime and four authenticated checkpoints: passed.
2. Seasonal retrieval and 84-day dormant-skill retention: passed.
3. Independent memory and skill transfer bundles: passed.
4. Held-out transfer gain with contamination and raw baselines: passed.
5. Fixed-budget learned routing gain: passed.
6. Automated verification: 112 tests passed.
7. Authenticated GLM 5.3 held-out transfer: passed.
8. Disposable live Spring 28 transferred-skill validation: passed.

### M12: Bounded selective memory management

- Added frozen active-memory token budgets and six policies: none, FIFO, LRU, least retrieved,
  fixed-seed random, and agent selected.
- Added strict complete-coverage retain/deactivate decisions, one repair attempt, deterministic
  over-budget fallback, retrieval access history, and append-only deactivation state.
- Upgraded authenticated memory bundles to schema v2 with full status history and v1 import
  compatibility. Checkpoint restore reproduces active, deactivated, and superseded eligibility.
- Added explicit budget, request, decision, status, and consolidation-selection events.
- Preregistered eight seeds and five study conditions before executing outcomes.
- Completed 40 matched controlled runs with no missing run, state mismatch, fallback, budget
  violation, exclusion, or manual score edit.
- Agent selection achieved 2.0 held-out utility in 17 tokens (0.11765/token); FIFO, LRU, and
  least-retrieved achieved 1.0 in 18 tokens (0.05556/token); unbounded achieved 2.0 in 58 tokens
  (0.03448/token).
- Agent minus FIFO density was 0.06209 with deterministic 95% CI [0.06209, 0.06209] and exact
  two-sided sign-test p=0.0078125.
- Authenticated Prime Agent/OpenRouter GLM 5.3 selected the two useful memories in one call with
  no repair or fallback, scored 2/2, and produced an 18-event reconstructible report.
- The first live-gate artifact is retained: its model result was valid, while a local route-label
  assertion was incorrect. The corrected v2 gate passed.

M12 gate result:

1. Immutable records and provenance retained: passed.
2. Explicit active token capacity and deterministic accounting: passed.
3. At least three deterministic baselines: passed.
4. Strict authenticated agent retention: passed.
5. Exact checkpoint active-state recovery: passed.
6. Status-provenance-preserving export/import: passed.
7. Deterministic mechanics benchmark: passed.
8. Preregistration before outcomes: passed.
9. Eight-seed paired study: passed.
10. Raw-event report reconstruction: passed.
11. Machine-readable failure analysis: passed.
12. Authenticated provider run: passed.
13. One-command reproduction: passed.

### M14: Active experimentation and scientific learning

- Added evaluator-only hidden mechanics and a separate observable scenario schema.
- Added strict experiment/exploit choices, complete scenario coverage, budget validation, one
  repair attempt, and bounded provider batching.
- Reused immutable belief records: experiments supersede priors with exact execution-event
  provenance while leaving historical beliefs intact.
- Added hypothesis, proposal, execution, revision, scenario-completion, and study events.
- Preregistered eight seeds and five conditions before outcome execution.
- Completed 40 matched runs with no missing run, hash mismatch, hidden-field leak, exclusion,
  duplicate, event-chain failure, or manual score edit.
- Agent experimentation raised mean reward from 1,680 to 2,280. Paired effect was +600 with
  deterministic 95% CI [600, 600] and exact two-sided sign-test p=0.0078125.
- The agent ran both positive-value tests, gained 2 information bits, attributed 600 future reward
  to information, and declined all eight negative-value cases.
- Fixed-seed random testing averaged 1,847.5 reward and entered three negative-value cases.
- Authenticated Prime/OpenRouter GLM 5.3 selected both worthwhile tests and declined the costly
  test using two bounded calls. It produced two evidence-linked belief revisions and a 22-event
  reconstructible report for $0.0054756.
- Four earlier authenticated attempts timed out before decisions or scores. A health probe isolated
  the issue to long structured turns; compact bounded calls then passed. Failed logs are retained.
- Prime RPC now drains stderr continuously to prevent child-process pipe backpressure.
- Added a disposable live Stardew sampling gate with two checkpoint-identical branches, held-out
  validation, ordinary scored primitives, immutable evidence-linked belief revision, and strict
  separation between fixture setup and scoring.
- Authenticated GLM 5.3 selected two samples without a supplied numeric prior, derived expected
  value, hidden object ID, or outcome. Live yields were 1, 1, and held-out 1 Wood; the prediction
  error was 0. The matched control also yielded 1 Wood.
- The live active/control branches used 15/7 primitive actions, consumed 6/2 stamina, contained
  zero privileged scored actions, and completed a 34-event verified chain.

M14 gate result:

1. Seeded, hashable hidden mechanics: passed.
2. Hidden outcomes absent from decision input: passed.
3. Explicit hypotheses and immutable revisions: passed.
4. Costly experimental actions and later exploitation: passed.
5. Worthwhile and negative-value experiment cases: passed.
6. Five causal baseline/treatment conditions: passed.
7. Eight-seed preregistered paired study: passed.
8. Positive paired confidence bound and exact test below alpha: passed.
9. Raw-event reconstruction and machine-readable failures: passed.
10. Authenticated GLM 5.3 selection and attribution: passed.
11. Disposable live Stardew sampling and held-out validation: passed.
12. Live belief-revision provenance and branch action budgets: passed.

### M15: Stardew-Shift and stability-plasticity

- Added seeded, versioned, hashable counterfactual world families with two shifted mechanics and
  one stable mechanic across an `A -> B -> A` sequence.
- Added frozen-A, global-overwrite, contextual-belief, and fresh-at-B conditions with matched world,
  observation, and canonical start-state hashes.
- Reused immutable belief records and exact interaction-event provenance. Context-specific B
  revisions leave the earlier A records active and auditable.
- Implemented adaptation lag, return retention, negative transfer, stale-belief use, belief
  revisions, contextualization rate, stable-mechanic preservation, and relearning ratio.
- Preregistered eight seeds, four conditions, exclusions, stopping, bootstrap interval, exact sign
  test, and B-adaptation non-inferiority before the final outcome run.
- Completed 32 runs. Contextual minus global return retention was +1.0 with deterministic 95% CI
  [1.0, 1.0] and exact two-sided sign-test p=0.0078125.
- Contextual B adaptation lag was 1.0 versus 1.5 fresh-at-B, negative transfer was zero, return lag
  was zero, both shifted mechanics were contextualized, and stable-mechanic B accuracy was 1.0.
- Authenticated OpenRouter GLM 5.3 used two attributable calls: 3/3 B actions, 2/2 shifted mechanics
  contextualized, stable mechanic retained, and 3/3 return-A actions. Cost was $0.0068324.

M15 gate result:

1. Versioned seeded shiftable mechanics: passed.
2. Strict subset shift with stable-mechanic control: passed.
3. Matched world, observation, and start hashes: passed.
4. A-to-B-to-A sequence and four causal conditions: passed.
5. Immutable contextual beliefs with exact provenance: passed.
6. Adaptation, retention, transfer, staleness, and relearning metrics: passed.
7. Eight-seed preregistered study and raw-event reconstruction: passed.
8. Positive paired confidence bound and exact test below alpha: passed.
9. B adaptation non-inferiority and zero negative transfer: passed.
10. Authenticated GLM 5.3 context selection and recovery: passed.

### M16: Memory corruption and autonomous repair

- Added false semantic, false belief, stale belief, overconfident weak belief, and incorrect
  procedural-recommendation corruption types with two interleaved clean decoys per episode.
- Kept correct actions and corruption labels out of all decision inputs and maintained separate
  world and observable hashes.
- Added blind-trust, fixed-repair, adaptive-repair, and oracle-ceiling conditions over eight seeds.
- Reused immutable memory records, append-only statuses, exact evidence provenance, and hash-chained
  experiment events.
- Added a restricted data-only policy-patch schema and a held-out second corruption episode.
- Reconstructed harmful actions, suspicion and correction times, evidence required, recurrence,
  utility loss, corrupt repairs, and clean false repairs from raw events.
- Completed 32 runs. Adaptive minus fixed episode-two utility retention was +15.625 percentage
  points, 95% CI [15.0, 16.25], exact two-sided sign-test p=0.0078125.
- Adaptive repair cut harmful actions from 10 to 5 and evidence required from 2 to 1, with zero
  clean false repairs, recurrence, hidden-field leaks, or event reconstruction failures.
- The full project suite passed 143 tests.
- Authenticated GLM 5.3 proposed an expected-utility mismatch policy with threshold 1 and improved
  from 6/7 before the patch to 7/7 on the held-out episode. It corrected all five corrupt records,
  retained both clean records, and completed a verified 37-event chain for $0.02778402.
- Earlier timeout and development failures remain preserved as hash-chained runs.

M16 controlled gate result:

1. Five unlabeled corruption types and clean decoys: passed.
2. Hidden evaluator truth absent from decision inputs: passed.
3. Immutable superseding repair with exact evidence: passed.
4. Restricted schema-level policy patch: passed.
5. Matched episode-one performance before patch: passed.
6. Held-out episode-two adaptive improvement: passed.
7. Eight-seed preregistration, positive CI, and exact test below alpha: passed.
8. Zero clean false repairs and post-correction recurrence: passed.
9. Raw-event reconstruction and failure analysis: passed.
10. Authenticated GLM 5.3 policy proposal and held-out transfer: passed.

### M17: Self-generated curriculum under noisy and adversarial evidence

- Added a training window and separately hashed hidden evaluation period.
- Added random, fixed-human, weakness-targeted scripted, and agent-generated curricula with an
  identical two-objective budget.
- Added independent noise, burst noise, duplicate-source, and spoofed-source evaluation regimes.
- Added structured weakness, evidence, practice, transfer, cost, and success-criterion proposals.
- Added held-out task and answer contamination audits.
- Completed 32 runs. Agent-generated minus fixed-human held-out accuracy was +28.125 percentage
  points, 95% CI [25.0, 34.375], exact two-sided sign-test p=0.0078125.
- Agent-generated curricula achieved 100% hidden accuracy and were non-inferior to the scripted
  weakness-targeted condition with equal training cost.
- Authenticated GLM 5.3 correctly selected source-duplication and source-spoofing practice and
  scored 8/8 on hidden evaluation in one call.
- A disposable live Stardew gate injected a false 5-Wood twig belief, observed two authenticated
  1-Wood samples plus one unauthenticated false claim, and asked GLM to repair it.
- GLM superseded the corrupt record, rejected the adversarial event, cited both live evidence IDs,
  and predicted a third live 1-Wood yield exactly. The run used 15 ordinary actions and zero
  privileged scored actions.
- The full project suite passed 148 tests.

M17 gate result:

1. Training-to-hidden-evaluation separation: passed.
2. Four equal-cost curriculum conditions: passed.
3. Structured agent curriculum proposal: passed.
4. Four noisy/adversarial evidence regimes: passed.
5. Zero held-out contamination: passed.
6. Eight-seed paired study, positive CI, and exact test below alpha: passed.
7. Non-inferiority to weakness-targeted scripted curriculum: passed.
8. Authenticated GLM curriculum selection and hidden transfer: passed.
9. Disposable live Stardew corruption and held-out validation: passed.
10. Immutable live repair provenance and adversarial rejection: passed.

### M13: Experience-replay prioritization

- Added typed replay experiences, decisions, actions, evaluation tasks, and run scores.
- Added strict exact-coverage validation, replay-budget enforcement, and one provider repair attempt.
- Added no-replay, recency, fixed-seed random, error-priority, and agent-priority conditions.
- Isolated held-out correct actions from policy inputs and hash-matched candidates and evaluations.
- Linked every learned lesson to its exact replay event and rebuilt reports from verified raw logs.
- Completed 40 runs. Agent priority scored 1.0 versus error priority's 0.6667, a paired +0.3333
  effect with 95% CI [0.3333, 0.3333] and exact p=0.0078125.
- Authenticated GLM 5.3 selected all three transferable failures in one call, scored 3/3 versus
  2/3, preserved exact replay provenance, and cost $0.00169476. One intermediate provider attempt
  timed out before inference; the subsequent bounded retry passed.

M13 gate result:

1. Immutable typed replay candidates: passed.
2. Strict exact-coverage and budget validation: passed.
3. Held-out answer isolation: passed.
4. Five matched causal conditions: passed.
5. Exact replay-to-learning provenance: passed.
6. Eight-seed preregistration and raw-event reconstruction: passed.
7. Positive paired confidence bound and exact test below alpha: passed.
8. Authenticated GLM 5.3 prioritization: passed.

## Next implementation target

M18 should study autonomous tool and procedure invention beyond existing fixed skill templates.
