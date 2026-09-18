# M9 project and 28-day season benchmark

## Result

M9 passed its three-seed 28-day benchmark and injected-recovery gate. Each seeded run completed
all three project objectives, and the interrupted run resumed from its authenticated Day 14
checkpoint without repeating a completed day.

This result validates benchmark mechanics, scoring, checkpoint recovery, aggregation, and raw-event
reporting. The policy is deterministic, so these results do not establish a causal learning gain.

## Project fixtures

| Project | Completion rule | Criterion day in all seeds |
|---|---|---:|
| Cauliflower reserve | Harvest at least 10 cauliflower and retain at least 1,500g | 13 |
| Mine and pickaxe | Reach mine level 40 and pickaxe tier 1 or higher | 15 |
| Coop and chicken | Own a Coop and at least one chicken | 20 |

Each fixture is immutable and versioned. Daily scores retain component outcomes and continuous
progress, allowing partial project performance to be compared before final completion.

## Three-seed gate

The aggregate report is `runtime/smoke/m9-season-gate.json`.

| Measure | Result |
|---|---:|
| Seeds | 406041616, 406041617, 406041618 |
| Completed days | 28, 28, 28 |
| Successful project outcomes | 9/9 |
| Final gold | 12,322g to 12,458g |
| Mean final gold | 12,411.33g |
| Modeled primitive actions per run | 592 |
| Invalid actions | 0 |
| Manual score edits | 0 |

Each per-seed report contains environment, efficiency, learning, memory, skill, and system metrics,
plus a 28-point progress curve for every project.

## Injected recovery

Seed `406041616` published an atomic checkpoint after Day 14. The checkpoint bound:

- the complete season state;
- the immutable run configuration SHA-256;
- the event run ID and authenticated cursor;
- independent hashes for the state and cursor files.

The injected crash left seven later lifecycle and recovery events after cursor sequence 64.
Recovery authenticated the retained prefix, restored `completed_days = 14`, and continued with
Day 15. Raw-event reconstruction later found exactly one completion for each day 1 through 28.

## Raw-event reporting

The interrupted event log contains 117 records and ends at hash
`981f0ddab27f84f9450989b5eb1c43affb7f8ab1d3dbc59bf43c3e39ce2c2086`.
The two uninterrupted logs contain 109 records each.

`prime_stardew.m9_report` reopens the event store, revalidates its hash chain, reconstructs all
daily states and project scores, calculates criterion days and metric groups, and writes a new
report. It refuses logs whose completed-day sequence is not exactly 1 through 28.

## Scope and next study

The deterministic replay world makes benchmark and recovery behavior fast and reproducible. It
does not substitute for a paired continual-learning study against the full game. M10 uses these
fixtures, metric schemas, and report paths for preregistered multi-condition experiments with
confidence intervals and failure analysis.
