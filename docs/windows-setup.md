# Windows integration and validation

## Validated local stack

- Stardew Valley: `E:\GAMES\Stardew-Valley-AnkerGames\Stardew Valley`
- Game version: `1.6.15.24356`
- SMAPI: `4.5.2`
- StarDojo mod: `1.0.0`, patched from source commit recorded in `dependencies.json`
- StarDojo endpoint: `127.0.0.1:10783`
- Local build SDK: .NET `8.0.425` under `runtime/dotnet`

The full local game is the simulator. StarDojo is the SMAPI control and observation
layer. This gives the project real Stardew mechanics and saves while retaining a
programmatic interface suitable for experiments.

## Installed artifacts

The user-supplied archive is
`E:\GAMES\StarDojoMod-34175-1-0-1747938194.zip`. Its SHA-256 matches the hash
published for the Nexus release:
`35dca5a1579163cfdba913ab833b73b19a33528cc29bb578fbb52eeb1ae23946`.

The archive was installed under the game's `Mods\StardojoMod` directory. The
installed `StardojoMod.dll` is the locally rebuilt compatibility version with
SHA-256:
`6fffa01cdba2b8db5d3c1e008969f05bad9a2730bc2e93c1f8cc2c403d87506b`.

The original official DLL is backed up under
`runtime/backups/stardojo-20260915-173927`. Runtime data is deliberately ignored
by Git.

## Compatibility patch

The upstream source required five changes for this machine and game version:

1. Resolve game and SMAPI assemblies through the explicit `GamePath` MSBuild
   property instead of macOS paths.
2. Add the `testUtils.TestUtils` source expected by the repository but absent from
   its checkout. Referencing the legacy `ModProject.dll` was rejected because it
   contains calls removed from current Stardew Valley.
3. Remove `Game1.fadeToBlack` from the general action readiness gate. Stardew
   1.6.15 can leave that flag set after the loaded fixture is observable and can
   accept actions, which otherwise deadlocks the command queue.
4. Expose `sleep` and implement the current 1.6.15 sleep dialogue path so a run can
   advance, save, and checkpoint at day boundaries.
5. Add a setup-only `place_chest` initializer that creates the real interactive
   `Chest` class for deterministic inventory-transfer fixtures.

The complete source delta is preserved in
`patches/stardojo-windows-build.patch`.

Build without automatic deployment:

```powershell
$env:DOTNET_CLI_HOME = (Resolve-Path '.\runtime\dotnet-home').Path
.\runtime\dotnet\dotnet.exe build `
  .\vendor\stardojo\StardojoMod\StardojoMod.csproj `
  --configuration Release `
  '-p:GamePath=E:\GAMES\Stardew-Valley-AnkerGames\Stardew Valley' `
  -p:EnableModDeploy=false `
  -p:EnableModZip=false
```

The build completes with zero errors. The upstream project contains a duplicate
MessagePack reference and pins MessagePack 3.1.3, for which current NuGet emits
moderate and high-severity advisory warnings. Dependency cleanup belongs in the
environment-hardening milestone before running untrusted network inputs.

## Graphics startup fix

SMAPI initially crashed inside SDL window creation. Setting the Windows
per-application GPU preference for `StardewModdingAPI.exe` to high performance
(`GpuPreference=2;`) resolved it. The value is under
`HKCU\Software\Microsoft\DirectX\UserGpuPreferences`, named with the executable's
full path. The prior value is recorded in
`runtime/smoke/gpu-preference-backup.json`.

The installed SDL2 2.30.9 library matched the official x64 release; no DLL
replacement was needed.

## Save isolation

All validation used the disposable save
`PrimeStardewSmoke_406041616` with farmer `PrimeStardewSmoke`. The personal farmer
`Nova` was never loaded. The fixture starts on Spring 5, Year 1 with 50,000g and
was advanced to Spring 6 by the test.

The completed-day checkpoint is under
`runtime/checkpoints/spring-06-smoke`. Its hashes and the full validation record
are in `docs/smoke-test-result.json`.

## Verified behavior

- SMAPI loads StarDojo and opens TCP port 10783.
- `observe_v2%1` returns valid structured state and a 1920x1080 RGBA frame.
- `turn%1` returns and changes facing direction from 2 to 1.
- Pause and resume preserve game time while paused.
- `sleep` advances Spring 5 to Spring 6 and writes the save.
- A clean restart reloads the checkpoint at 06:00 on Spring 6.
- The launcher only checks the configured port and never terminates an unrelated
  process.

## Run commands

Start the game and StarDojo:

```powershell
.\scripts\start-stardojo.ps1
```

Send diagnostic commands from another terminal:

```powershell
.\.venv\Scripts\prime-stardew-command.exe 'load_game_record%PrimeStardewSmoke_406041616'
.\.venv\Scripts\prime-stardew-command.exe 'observe_v2%1' --output .\runtime\observation.json
.\.venv\Scripts\prime-stardew-command.exe 'turn%1'
.\.venv\Scripts\prime-stardew-command.exe 'sleep'
```

`observe` is intentionally rejected by the diagnostic client because its binary
payload is unsafe to print as text; use `observe_v2`.

## Known adapter work

- Replace `wait_game_start` with state polling keyed by save ID and expected day.
  The upstream event waiter can subscribe after `DayStarted` has fired.
- Ignore screenshot buffer mismatches during the initial window-size transition,
  then require stable dimensions before declaring the visual channel ready.
- Add request IDs, typed errors, response-size limits, timeouts, and reconnect logic
  around the raw TCP protocol.
- Pin a supported MessagePack version after compatibility tests.

## Python SDK validation

Install the locked environment with `uv sync --extra dev --link-mode copy`.
Copy mode is required in this OneDrive workspace to prevent package metadata from
being installed as reparse points that cannot be refreshed.

The SDK currently provides bounded loopback transport, typed observations,
request IDs, safe retry rules, replay transport, farmer identity checks, date
polling, reversible smoke and soak commands, and atomic save checkpoints.

Validation completed:

- 70 automated tests pass.
- The live SDK validates the Spring 6 fixture and its 1920x1080 RGBA frame.
- A 50-cycle observation/action soak passes and restores initial state.
- An idempotent observation recovers after an injected disconnect.
- A checkpoint restored as `PrimeStardewM2Restore_406041616` loads successfully
  through StarDojo with the expected farmer and date.
- A live Spring 6→7→8 run survived an injected post-action crash by restoring the
  Spring 7 combined checkpoint and recording one completion for the logical action.
- Live control probes validate clear-tile pathfinding, relative and step movement,
  inventory selection, axe use, tree removal, Wood pickup, hoeing, watering,
  planting, and harvesting. Upstream pathfinding entered a tree tile while returning
  success; the controller occupancy guard now rejects that movement before mutation
  and verifies the observed post-move tile.
- The live M3 gate completes five atomic tasks three times each with score 1.0 and
  no privileged action in any scored trajectory.
