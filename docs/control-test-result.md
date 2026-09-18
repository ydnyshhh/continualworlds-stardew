# Live control test result

Date: 2026-09-15

All tests used disposable restores of the Spring 6 checkpoint. The personal save was
never loaded. Tree testing used `PrimeStardewControls_406041616`; farming testing used
`PrimeStardewFarming_406041616`.

| Control | Result | Observed evidence |
|---|---|---|
| Absolute pathfinding | Pass on clear targets | Moved `(64,15)` to `(62,22)` and later `(64,15)` to `(55,10)` |
| Relative movement | Pass on verified clear target | Moved `(55,10)` to `(54,10)` |
| One-tile movement | Pass on verified clear target | Moved `(55,10)` to `(55,11)` |
| Raw occupied-tile collision | Upstream fail | `move(54,11)` returned `True` and entered a tile containing a tree |
| Guarded occupied-tile movement | Pass | Adapter rejected tree at `(62,23)`, sent no mutation, and remained at `(62,22)` |
| Inventory selection | Pass | Selected Axe, Hoe, Watering Can, and Parsnip Seeds by slot |
| Axe/tree removal | Pass | Removed terrain tree at `(62,23)` in five swings |
| Wood collection | Pass | Wood increased from 0 to 4 |
| Hoe | Pass | Empty tile `(54,10)` became `HoeDirt`; stamina decreased by 2 |
| Watering can | Pass | Tool action completed on `HoeDirt`; stamina decreased by 2 |
| Planting | Pass | Seed ID 472 appeared on the tilled tile; seed count decreased from 2 to 1 |
| Harvesting | Pass | Crop disappeared and Parsnip inventory increased by 1 |

The farming fixture provisioned two Parsnip Seeds and advanced the planted crop with
StarDojo task-initialization commands. Those commands only prepared deterministic
test state. Planting and harvesting themselves used the normal `choose_item`,
`interact`, and observation controls.

The raw collision result means agents must not trust the Boolean result of `move` as
proof that a destination was safe. `EnvironmentController.move`, `move_relative`,
and `move_step` now inspect structured tile occupancy before movement. They reject
objects, blocking terrain, buildings, furniture, NPCs, and exits. After movement,
they require `True`, the same location, and the exact requested tile. Scored runners
must use these controller methods instead of the raw client movement methods.

Machine-readable evidence:

- `runtime/smoke/controls-tree-live.json`
- `runtime/smoke/controls-farming-live.json`
- `runtime/smoke/movement-guard-live.json`
