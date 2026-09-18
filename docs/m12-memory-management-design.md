# M12 self-improving memory management design

## Repository audit

M12 extends these existing components:

- Memory schemas: `src/prime_stardew/memory/models.py` (`MemoryRecord`, `MemoryQuery`,
  `MemoryExport`, `MemoryStatus`, retrieval feature/result models).
- SQLite storage and retrieval: `src/prime_stardew/memory/store.py` (`MemoryStore`,
  deterministic `_eligible`, `_rank`, and token estimation).
- Consolidation and belief versioning: `src/prime_stardew/reflection/engine.py`
  (`MemoryConsolidator`, `ReflectionEngine`) and `memory/store.py` supersession events.
- Immutable experiment configuration: `src/prime_stardew/experiments/config.py`
  (`RunConfig`, `LearningConditionConfig`, canonical configuration hashing).
- Append-only telemetry: `src/prime_stardew/telemetry/events.py` (`EventStore`,
  `EventRecord`, `EventCursor`).
- Memory-aware combined checkpoints: `src/prime_stardew/experiments/checkpoints.py`
  (`RunCheckpointManager.create`, `verify`, and `restore`).
- Study preregistration: `src/prime_stardew/studies/models.py` and
  `src/prime_stardew/studies/preregistration.py`.
- Existing reports: raw-event builders in `benchmarks/season.py`, `studies/analysis.py`,
  and the M9–M11 gates.

No parallel memory, checkpoint, event, or inference subsystem will be introduced.

## Existing behavior

Records are immutable and current status is derived from append-only status rows. Retrieval
filters current records and deterministically ranks them. Export/import authenticates records and
final status, but M12 must preserve the complete status history. Selected retrievals previously
had no persistent access counter, so LRU and least-retrieved policies were not possible.

## Required changes

1. Add an optional frozen `memory_management` block to `RunConfig`.
2. Count active capacity in the existing conservative text token estimate.
3. Add append-only access events and expose last-access/count statistics.
4. Add a non-destructive `deactivated` status and preserve exact status history in version-2
   transfer bundles while retaining version-1 import compatibility.
5. Implement deterministic FIFO, LRU, least-retrieved, fixed-seed random, no-deletion, and
   validated agent-selected policies.
6. Log budget evaluation, request, decision, status changes, and consolidation selection through
   the existing hash-chained `EventStore`.

## Schemas

- `MemoryManagementConfig`: enabled, token-estimate budget, maximum active tokens, policy, seed,
  and deterministic overflow fallback.
- `MemoryManagementDecision`: unique retain/deactivate actions, optional consolidation groups,
  reasoning summary, and predicted future value by memory ID.
- `MemoryBudgetReport`: before/after token counts, retained/deactivated IDs, overflow, and policy.
- `MemoryStatusEvent` and `MemoryAccessStats`: exportable status provenance and policy statistics.

## Migration and compatibility

SQLite migration uses `CREATE TABLE IF NOT EXISTS` for access events; existing databases remain
valid. Version-1 memory bundles continue to authenticate with their original content shape.
Version-2 exports add full status history. Imports restore that history exactly in a fresh store.
Historical records are never deleted or rewritten.

## Policies

- `none`: measure without deletion; used as the unbounded reference.
- `fifo`: retain newest records that fit.
- `least_recently_used`: retain most recently retrieved records, with deterministic creation/ID
  tie-breaking.
- `least_retrieved`: retain highest retrieval counts with deterministic tie-breaking.
- `random_fixed_seed`: seeded ordering, recorded for reproducibility.
- `agent_selected`: strict structured decision; invalid IDs, duplicates, inactive records, or
  overflow are rejected. The configured deterministic fallback is logged and applied explicitly.

## Metrics

Primary: held-out task utility divided by active memory tokens.

Secondary: task utility, retrieval precision, stale retrieval rate, unused-memory fraction,
memory churn, compression ratio, active tokens, model calls/tokens/cost, exclusions, and failures.

## Deterministic experiment

Eight paired seeds share the same synthetic observation stream, terminal state hash, held-out
queries, and budget. Early records include future-useful rules; later records include distractors
and stale rules. Conditions are FIFO, LRU, least-retrieved, agent-selected, and unbounded. The
agent policy sees only observable provenance, confidence, support, retrieval history, and predicted
future-value metadata; hidden evaluator labels and task IDs are excluded.

## Tests

Unit coverage includes accounting, each deterministic policy, strict decision validation,
unknown/duplicate/inactive IDs, overflow fallback, superseded records, status-history transfer,
SQLite checkpoint/snapshot recovery, and compatibility with version-1 bundles. Integration tests
reconstruct study reports solely from authenticated raw events.

## Authenticated gate

After the offline paired study passes, one small Prime/OpenRouter call selects memories under the
same fixed capacity. The gate verifies attribution, strict validation, enforced budget, cost,
event reconstruction, and comparison with FIFO from an identical candidate set. This is a
mechanism/external-validity gate, not a large model-powered outcome study.
