# M17 Noisy Evidence, Adversarial Observations, and Live Corruption Result

## Controlled study

The preregistered controlled gate passed all 32 condition-seed runs. Under the identical budget of
two practice objectives, the agent-generated curriculum improved hidden evaluation accuracy over
the fixed human curriculum by **28.125 percentage points**, with deterministic 95% bootstrap CI
**[25.0, 34.375]** and exact two-sided sign-test **p = 0.0078125**.

Agent-generated curricula achieved 100% accuracy in every seed across independent noise, burst
noise, source duplication, and source spoofing. They were non-inferior to the scripted
weakness-targeted baseline. World, training, evaluation, and start hashes matched; contamination,
event reconstruction failures, and nonpositive paired effects were all zero.

## Authenticated GLM 5.3 curriculum

GLM 5.3 received four diagnostic results and the broad practice catalog. In one attributable call,
it identified source duplication and source spoofing as the two failed capabilities, cited the exact
diagnostic IDs, and selected the corresponding two objectives. It then scored **8/8** on hidden
evaluation, including **2/2** in each attack regime, with no held-out task or answer leakage.

The run used 1,239 input tokens, 116 output tokens, and $0.002245. Its verified 17-event chain ends
with hash `8924cb8c53e373e109e0fcab70f779826f5dc965bdecc9ed6c7fa463644a4578`.

## Disposable live Stardew corruption

The live gate restored checkpoint `86efa2c1-8bce-4f88-8231-00f3c12361ea` into an isolated Spring 8
save. It injected the false immutable belief `twig yield = 5 Wood`, then collected two authenticated
live outcomes of **1 Wood** each. An unauthenticated adversarial report repeated the false value 5.

GLM 5.3 chose `supersede`, trusted both exact live event IDs, rejected the adversarial event ID, and
predicted the held-out yield as 1. The third live twig yielded **1 Wood**, reducing prediction error
from **4 to 0**. The repaired memory superseded the corrupt record and cited exactly the two live
execution events.

The scored phase used 15 ordinary primitive actions and zero privileged actions. The model used one
call, 1,213 input tokens, 203 output tokens, and $0.0025914. The verified 26-event chain ends with
hash `df3b11ed65bbb0883f6ec878a17e4c2b49739c83e8a44608412727891c6e9cbe`.

One earlier disposable attempt is retained as a valid failed run. A placed twig remained after one
successful Axe request, so the run stopped before model inference or scoring. The live primitive was
then bounded to four recorded ordinary uses with tile verification; the clean retry passed using 15
actions.

## Validation

The complete project suite passed **148 tests**.

## Reproduction

```powershell
.\.venv\Scripts\python.exe -m prime_stardew.m17_gate `
  --gate-root runtime\gates\m17-noisy-curriculum-reproduction `
  --output runtime\smoke\m17-noisy-curriculum-reproduction.json
```

With StarDojo listening on port 10783:

```powershell
.\.venv\Scripts\python.exe -m prime_stardew.m17_stardew_live `
  --source-checkpoint runtime\checkpoints\m11-live-source-spring08 `
  --saves-root "$env:APPDATA\StardewValley\Saves" `
  --save-id PrimeStardewM17Reproduction `
  --prime-executable runtime\prime-agent\prime-agent.cmd `
  --node-dir runtime\prime-bootstrap\node-v22.23.2-win-x64 `
  --prime-config-dir runtime\prime-config `
  --prime-session-dir runtime\sessions\m17-reproduction `
  --gate-root runtime\gates\m17-stardew-reproduction `
  --output runtime\smoke\m17-stardew-reproduction.json
```

## Claim boundary

The causal curriculum estimate applies to the deterministic four-regime evidence family and fixed
learner. The authenticated gate validates one GLM-selected curriculum. The live gate validates one
resource-yield belief, one duplicated false claim, and three twigs in a disposable Spring 8 save.
Broader multi-day adversaries, compromised authenticated sources, and adaptive attackers remain
outside this result.
