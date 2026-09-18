# Events and run checkpoints

## Event log

Each experiment owns one JSON Lines file and one writer. `EventStore` validates the
complete file when it opens and immediately before every append. It refuses partial
lines, invalid schemas, run-ID changes, sequence gaps, broken predecessor links, and
payloads whose SHA-256 no longer matches the stored event hash.

Every record contains:

- schema version, run ID, monotonic sequence, UUID, and UTC timestamp;
- event type and JSON payload;
- the preceding event hash, or `null` for the first event;
- a hash over the canonical JSON representation of every preceding field.

Appending opens the file in binary append mode, emits one newline-terminated record,
flushes it, and calls `fsync`. The initial implementation has a single-writer
contract: the experiment runner pauses mutation while it makes a checkpoint.

An `EventCursor` stores the run ID, last included sequence, last event hash, and exact
byte offset. A cursor authenticates a prefix, so it remains valid after later events
are appended. `events_after(cursor)` exposes attempts made after the recovery point.
The runner can record their interruption and decide whether to retry; it must never
silently count them as committed work.

## Combined checkpoint

`RunCheckpointManager` publishes this directory layout:

```text
checkpoint/
  manifest.json
  game/
    manifest.json
    <Stardew save files>
  agent/state.json
  config/config.json
  events/cursor.json
```

Creation captures the event cursor, waits for the Stardew save files to stabilize,
and copies them into a temporary bundle. It writes agent state, frozen configuration,
and the cursor with file flushes. The top-level manifest includes the nested game
manifest plus the size and SHA-256 of every bundle file. It is written last, and the
temporary directory is atomically renamed to its final path.

Verification checks every declared hash, verifies the nested game checkpoint, checks
the duplicate cursor in the manifest and cursor file, and optionally authenticates
that cursor against the retained event log. Restore refuses an existing destination,
renames the Stardew primary and `_old` files to a new experiment save ID, and returns
the agent state, configuration, and event cursor needed by the runner.

## Recovery sequence

1. Stop environment and event mutation.
2. Select the latest verified completed-day bundle.
3. Verify the bundle and cursor against the append-only log.
4. Restore the game into a fresh experiment-owned save ID.
5. Restore the agent state and frozen configuration.
6. Inspect events after the cursor and mark unfinished attempts as interrupted.
7. Append a recovery event, load the restored save, validate farmer and date, then
   resume from the next decision step.

The live fixture proves that a sequence-3 cursor remains valid after an injected
sequence-4 interruption, and that its combined Spring 6 bundle loads through
StarDojo under `PrimeStardewCombined_406041616`.

## Retention

Combined checkpoints are classified as `base`, `day`, `milestone`, or `failure`.
Retention always protects base fixtures, milestones, and failure snapshots. For each
run, it keeps the configured number of newest distinct completed days and selects
older ordinary day bundles for deletion. Malformed or unverifiable directories are
reported as issues and never selected.

Applying a plan recomputes it first and rejects stale deletion sets. Every selected
bundle is verified again immediately before removal, including its checkpoint ID,
kind, hashes, nested game checkpoint, and cursor file. Paths must be immediate
children of the configured root.
