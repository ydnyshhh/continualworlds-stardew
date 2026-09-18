# M16 Memory Corruption and Autonomous Repair Result

## Result

The preregistered controlled gate passed all 32 condition-seed runs. Adaptive repair improved
episode-two utility retention over fixed repair by **15.625 percentage points**, with deterministic
95% bootstrap CI **[15.0, 16.25]** and exact two-sided sign-test **p = 0.0078125**.

Before a patch was available, adaptive and fixed repair had identical episode-one utility. In the
held-out second episode, the learned restricted policy reduced the verification threshold from two
contradictory observations to one. That reduced harmful actions across the five corrupted records
from 10 to 5 and mean evidence required for correction from 2 to 1. All five corrupt records were
repaired in every adaptive episode.

There were **zero clean-memory false repairs**, **zero post-correction recurrences**, **zero hidden
field leaks**, no event reconstruction failures, and no nonpositive paired effects. All eight
adaptive runs proposed and applied one evidence-linked, data-only policy patch.

## What was implemented

- Five explicit corruption types and eight explicit repair-response categories.
- Interleaved clean decoys and evaluator-only truth.
- Seeded, immutable, separately hashed world and observable state.
- Four matched conditions over eight preregistered seeds.
- Immutable superseding repairs with exact interaction-event provenance.
- A restricted schema-level patch mechanism between episodes.
- Raw-event reconstruction of every primary and recovery metric.
- A strict Prime/GLM policy adapter with one JSON repair attempt and provider attribution.
- Five focused M16 tests and integration into the complete project test suite.

The complete suite passed **143 tests**.

## Authenticated GLM 5.3 result

The authenticated OpenRouter `z-ai/glm-5.3` gate passed on 18 September 2026. The starting policy
scored 6/7 records correctly from one observation per record. Without being given a desired
mechanism, GLM proposed an `expected_utility_mismatch_validation` policy. It tracked per-record
expected-utility mismatches, selected a threshold of 1, and chose `test` as its repair response.

With that restricted data-only patch active, GLM scored 7/7 on the held-out second episode: all five
corrupted records were corrected and both clean records retained their original recommendation.
No correct action or corruption label was exposed. The run used 17 attributable model calls, 7,149
input tokens, 1,907 output tokens, and $0.02778402. It completed a verified 37-event chain with final
hash `d00bd6a5a46b4a12847a20d48acc57bb2abe7092dc9776d3d510529babdca15f`.

Earlier multi-record attempts are retained. Two timed out before their first response, and one
single-record development run completed but exposed an overly strict score rule that required
perfect episode-one performance before policy adaptation. The final gate scores the intended M16
criterion: improvement on a held-out episode after the policy patch.

## Reproduction

Controlled study:

```powershell
.\.venv\Scripts\python.exe -m prime_stardew.m16_gate `
  --gate-root runtime\gates\m16-memory-corruption-reproduction `
  --output runtime\smoke\m16-memory-corruption-reproduction.json
```

Authenticated retry:

```powershell
.\.venv\Scripts\python.exe -m prime_stardew.m16_live `
  --prime-executable runtime\prime-agent\prime-agent.cmd `
  --node-dir runtime\prime-bootstrap\node-v22.23.2-win-x64 `
  --prime-config-dir runtime\prime-config `
  --prime-session-dir runtime\sessions\m16-reproduction `
  --gate-root runtime\gates\m16-live-reproduction `
  --output runtime\smoke\m16-live-reproduction.json `
  --timeout 600
```

## Claim boundary

The causal estimate applies to the seeded deterministic corruption family and fixed repair learner.
The authenticated gate demonstrates GLM 5.3 policy transfer on a controlled replay. Neither result
establishes behavior in live Stardew, noisy evidence, or open-ended corruption outside this family.
