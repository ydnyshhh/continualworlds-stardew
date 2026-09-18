# M5 Prime RPC integration result

## Result

M5 passed both its offline contract gate and its authenticated live gate. Prime Agent
0.9.5 controlled the disposable Spring 8 save through OpenRouter `z-ai/glm-5.3` and
completed all five atomic tasks with a score of 1.0. The scored trajectories contained
zero privileged setup actions.

The successful live run used six model calls, including one bounded repair. Every call
records the actual `openrouter` provider, `z-ai/glm-5.3` model, and
`openai-completions` route reported by Prime, plus its request ID, decoding settings,
token counts, latency, and cost. Total usage was 4,117 input tokens, 793 output tokens,
and $0.01143924.

| Check | Result |
| --- | --- |
| Five atomic decisions | Passed, deterministic RPC-contract provider |
| Five M3 task outcomes | Passed, live-recorded trajectories |
| Provider/model/route attribution | 5 of 5 |
| Structured action allowlist | Passed |
| One malformed-output repair | Passed |
| Pause/resume on success and failure | Passed |
| Goal restore into resumed context | Passed |
| Full transcript stored in checkpoint | No |
| Installed Prime RPC framing probe | Passed, Prime Agent 0.9.5 |
| Live authenticated Prime run | Passed, OpenRouter `z-ai/glm-5.3` |
| Live task scores | 5 of 5 at 1.0 |
| Privileged actions in scored trajectories | 0 |
| Live model-call attribution | 6 of 6 |
| Append-only event-chain validation | Passed, 67 records |

## Implemented boundary

- `PrimeRpcProvider` starts one persistent Prime RPC subprocess and uses correlated
  prompt responses plus `agent_end` events.
- The adapter reads final assistant text, aggregates usage across assistant turns,
  and records the actual provider, model, and API reported by Prime.
- `PrimeSession` assembles bounded context, pauses the game, calls the provider,
  validates the decision, records usage, and resumes the game in a `finally` block.
- Invalid JSON, schema violations, and disallowed action names receive one repair
  request. A second failure writes an error event and stops the decision.
- The checkpoint state contains active goals, decision count, and the last context
  hash. Provider transcripts stay in Prime's own session storage and are not replayed
  into the working context.
- `prime_stardew.m5_live` performs privileged fixture setup before scoring, asks
  Prime for a decision, executes only typed allowlisted actions through
  `LiveTaskHarness`, scores the result, and writes the complete M4 event trail.
- A fresh live run loads and verifies its configured save and date before fixture
  setup. Explicit repetition IDs preserve failed attempts as separate immutable runs.
- Action schemas document StarDojo direction semantics and validate argument count,
  type, and range before execution. Fixture skills capture observed mechanics such as
  stepping onto a cleared twig tile to collect its Wood drop.

## Evidence

- Configuration: `configs/m5-prime.yaml`
- Offline machine-readable report: `runtime/smoke/m5-gate.json`
- Live machine-readable report: `runtime/smoke/m5-live-glm-5.3.json`
- Successful run ID:
  `m5-prime-atomic-z-ai-glm-5-3-bounded-context-s406041616-93fa537b4c7d`
- Gate workspace: `runtime/smoke/m5-contract-gate`
- Automated verification: 79 tests passed.
- Prime RPC specification: https://github.com/PrimeIntellect-ai/prime-agent/blob/main/packages/coding-agent/docs/rpc.md

The official stable installer currently documents Linux and macOS. Prime's Windows
notes require Bash, so this project-local install uses Git Bash and keeps the newer
Node runtime isolated from the machine's Node 18 installation.
