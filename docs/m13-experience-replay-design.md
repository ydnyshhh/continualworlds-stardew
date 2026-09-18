# M13 Experience-Replay Prioritization Design

## Research question

Can an agent choose which past experiences to review so that a fixed replay budget improves
performance on related held-out tasks?

M13 sits between M12 selective memory retention and M14 active experimentation. M12 determines
which records remain active. M13 determines which active experiences deserve deliberate review
before a known task family.

## Causal design

Each seed provides six matched, agent-visible training experiences and three held-out evaluation
tasks. Three old failures contain lessons that transfer to the evaluation capabilities. One recent,
high-error failure is irrelevant to evaluation, and two recent successful notes are low-value
distractors. The policy sees task identifiers, capability names, and prompts, but never sees held-out
correct actions.

All replay conditions share the same candidate and evaluation hashes and a budget of three:

1. no replay;
2. most recent experiences;
3. fixed-seed random replay;
4. largest prediction errors;
5. agent-prioritized expected transfer.

The primary preregistered comparison is held-out accuracy for agent-prioritized replay minus
error-priority replay across eight paired seeds.

## Contracts and safety properties

- Every decision must cover every candidate ID exactly once.
- At most three candidates may have `replay=true`.
- Unknown, missing, duplicate, and over-budget decisions are rejected.
- An authenticated provider receives only observable evaluation fields.
- Every learning update cites the exact `experience_replayed` event that produced it.
- Reports are reconstructed from verified hash-chained event logs.
- Candidate and hidden-evaluation hashes must match across conditions within each seed.

This is a controlled deterministic transfer benchmark. It tests prioritization and provenance; it
does not yet establish that replay has the same effect during long autonomous live-game play.

