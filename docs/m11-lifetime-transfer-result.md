# M11 lifetime, transfer, and routing result

## Result

The controlled M11 gate passed on 2026-09-17. A donor policy completed a 112-day four-season
lifetime with authenticated checkpoints at every season boundary. A procedure used in Spring
still succeeded after an 84-day dormant interval in Winter, and semantic memory was retrieved at
all four season boundaries.

The immutable configuration is
[`configs/m11-lifetime-transfer.yaml`](../configs/m11-lifetime-transfer.yaml), SHA-256
`7b1db62a1eec6f1454027927587d4a21c24091e4652da905f935bbe925819e85`.

## Held-out transfer

Two semantic rules and one validated procedural skill were exported independently. Both bundles
bind their content and donor provenance with SHA-256. Skill transfer also carries replay and
disposable validation evidence plus activation state.

| Recipient condition | Held-out success | Model decisions | Imported state |
|---|---:|---:|---|
| Raw-model baseline | 0.5 | 8 | None |
| Fresh recipient | 0.5 | 8 | None |
| Memories only | 1.0 | 8 | Two semantic rules |
| Skills only | 0.5 | 4 | One validated procedure |
| Full inheritance | 1.0 | 4 | Memories and skill |

Full inheritance produced a +0.5 held-out transfer gain and halved model decisions. Memory
transfer changed knowledge-task outcomes; skill transfer changed procedural decision cost.
Scanning both donor bundles found none of the held-out task identifiers or unique crop names.

## Learned model routing

All policies received the same $0.012 inference budget over four easy and four difficult tasks.

| Policy | Attempted | Successful | Cost | Utility per dollar |
|---|---:|---:|---:|---:|
| Economy only | 8 | 4 | $0.008 | 500.00 |
| Capable only | 6 | 6 | $0.012 | 500.00 |
| Learned complexity router | 8 | 8 | $0.012 | 666.67 |

## Provenance and failure analysis

The donor event cursor authenticates 124 events. Four manifests bind serialized lifetime state,
configuration hash, and event cursor. The final report is
`runtime/smoke/m11-lifetime-transfer-v2.json`.

Failure analysis reported no contamination, full-transfer task failure, dormant-skill failure,
missing checkpoint, raw-baseline inversion, or routing budget violation. The project suite passed
112 tests.

## Scope

The controlled gate validates storage, retention, transfer, contamination, evaluation, and routing
under deterministic replay. Its full-year donor and recipient identities are scripted policies.
The authenticated external-validity gate below establishes transfer into GLM 5.3 and one live
season-boundary skill execution. It does not yet establish a full year of autonomous live play or
effects across multiple deployed recipient model families.

## Authenticated GLM and live-game validation

The external-validity gate used OpenRouter `z-ai/glm-5.3` through Prime Agent. On two synthetic
held-out crop decisions, fresh context selected the required no-rule default and scored 0/2.
After importing the semantic bundle, the same model selected Mineral Mulch and Compost correctly,
scoring 2/2. The model made five attributable calls because one fresh response required the single
allowed schema repair.

For the game validation, a hash-verified copy of the disposable Spring 8 M3 save was restored as
`PrimeStardewM11Boundary_406041616`. The harness observed and verified 20 sleep/save transitions
to Spring 28. It exported the previously GLM-proposed and live-validated M8 watering procedure,
imported it into a fresh skill registry, and executed it on a newly prepared crop fixture.

The transferred skill scored 1.0, used the same three primitive actions, saved one model decision,
and contained zero privileged actions in its scored trajectory. Provider attribution was
`openrouter / z-ai/glm-5.3 / openai-completions`. Usage was 3,746 input tokens, 424 output tokens,
and $0.00783576. The 33-event hash chain ends in `completed`; the report is
`runtime/smoke/m11-live-glm-5.3-v2.json`.

## Reproduce

Use a new output directory from the repository root:

```powershell
.\.venv\Scripts\python.exe -m prime_stardew.m11_gate `
  --gate-root .\runtime\smoke\m11-reproduction `
  --output .\runtime\smoke\m11-reproduction.json
```
