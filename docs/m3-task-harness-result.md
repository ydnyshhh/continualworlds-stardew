# M3 deterministic task-harness result

## Result

M3 passed on the local full game through StarDojo. The scripted policy completed
five atomic fixtures from reset state three times each. All 15 trajectories scored
1.0, and none contained a privileged setup action.

| Fixture | Scored primitives | Repetitions | Result |
| --- | --- | ---: | --- |
| Turn and move | `turn`, guarded `move_step` | 3 | Passed |
| Water crop | `choose_item`, `turn`, `use` | 3 | Passed |
| Clear debris | `choose_item`, `turn`, `use`, guarded `move_step` | 3 | Passed |
| Harvest crop | `turn`, `interact` | 3 | Passed |
| Chest transfer | `put_to_chest` | 3 | Passed |

The debris task includes the collection step because breaking a twig creates a
world pickup before Wood enters the inventory. The chest scorer requires equal and
opposite player/chest quantity deltas, so an item cannot disappear or duplicate.

## Harness contract

- Fixture files are schema-versioned and loaded into frozen Pydantic models.
- Baseline and final snapshots normalize farmer identity, date, time, location,
  position, facing, stamina, money, inventory, target tile, crop, menu, and chest.
- Action sequences start at 1 and remain contiguous.
- Every action must be typed, allowlisted, within the action and game-time budgets,
  non-privileged, and successful.
- Debug fixture setup completes before `LiveTaskHarness.begin()`. The live harness
  provides no raw-command method after scoring begins.

## Live fixture

- Save: `PrimeStardewM3A_406041616`, restored from the verified Spring 8 M2
  milestone checkpoint.
- Farmer: `PrimeStardewSmoke`.
- Date: Spring 8, Year 1.
- Location: Farm.
- Evidence: `runtime/smoke/m3-atomic-3x.json`.
- Installed StarDojo DLL SHA-256:
  `6fffa01cdba2b8db5d3c1e008969f05bad9a2730bc2e93c1f8cc2c403d87506b`.

The personal save was never loaded or modified.
