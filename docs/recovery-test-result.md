# Live interruption and recovery result

Date: 2026-09-15

The M2 recovery gate used only disposable saves and the hash-chained event log
`m2-recovery-live-v2`.

## Sequence

1. Loaded `PrimeStardewRecoverySource2_406041616` on Spring 6.
2. Slept through the day and verified the saved Spring 7 state.
3. Published combined checkpoint `f6f6d8e6-d747-4200-bec0-6deae810a433`
   with event cursor sequence 4.
4. Appended `action_started` for logical action `day-7-move-to-62-17`.
5. Moved the farmer from `(64,15)` to `(62,17)` and deliberately terminated the
   owned game process before appending `action_completed` or saving the action.
6. Restored the checkpoint as `PrimeStardewRecoveryResume_406041616`.
7. Verified that the game rolled back to `(64,15)` and that the event suffix exposed
   the unfinished action.
8. Reissued the same logical action as attempt 2, reached `(62,17)`, and appended its
   only completion event.
9. Finished Spring 7, loaded Spring 8, and published protected milestone checkpoint
   `03122429-537b-4d2d-a13d-6f42a66ebe2e`.

The raw log intentionally contains two start attempts and one completion. Reports
and scorers identify logical work by stable `action_id`, so the recovered trajectory
contains one committed action. The first mutation cannot survive because the game is
restored from the pre-action checkpoint.

The first phase-one trial also found a normal Stardew save race: a locked
`_STARDEWVALLEYSAVETMP` file briefly existed after the day transition. Checkpoint
polling now waits for temporary files to disappear and retries transient read locks.

Evidence:

- `runtime/smoke/m2-recovery-v2-phase1.json`
- `runtime/smoke/m2-recovery-v2-final.json`
- `runtime/runs/m2-recovery-live-v2/events.jsonl`
- `runtime/checkpoints/recovery-v2-day07`
- `runtime/checkpoints/recovery-v2-day08-final`
