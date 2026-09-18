# M6 persistent memory and retrieval result

## Result

M6 passed its deterministic contract gate and its authenticated paired ablation. The
authenticated run used fresh Prime Agent 0.9.5 sessions and OpenRouter
`z-ai/glm-5.3` for every condition. All calls reported the `openai-completions` route.

The controlled task withheld one inventory-slot fact from the observation. Stateless,
context-only, and storage-only conditions selected the specified fallback slot 0. The
memory-plus-retrieval condition selected `hidden-watering-slot` and chose the correct
slot 5. Repeating that condition with an empty memory store returned the model to slot
0. Every condition used checkpoint digest
`87efe51bcc4ea5fe407d5b71f83cc1684c0407319446409f00824d2a94ff663a`.

| Condition | Persisted memory | Retrieval | Selected memory | Result |
| --- | ---: | ---: | --- | --- |
| A stateless | No | No | None | Fallback slot 0 |
| B context only | No | No | None | Fallback slot 0 |
| C memory | Yes | No | `recent-distractor` | Fallback slot 0 |
| D memory plus retrieval | Yes | Yes | `hidden-watering-slot` | Correct slot 5 |
| D with memory removed | Empty | Yes | None | Fallback slot 0 |

The authenticated comparison used 9 calls, 6,593 input tokens, 718 output tokens, and
$0.013151. Repair calls are included. Each condition ended in the completed lifecycle
phase, and all five append-only event chains validated.

## Implemented boundary

- `MemoryStore` uses SQLite for immutable episode, semantic, and belief records.
- Status changes are append-only. A revised belief creates a new version and marks its
  predecessor superseded without deleting the original evidence.
- Retrieval filters by season, map, task domain, validity interval, and confidence,
  then ranks keyword overlap, metadata matches, and confidence deterministically.
- Retrieval events contain every candidate, rank features, selected IDs, and token
  cost. Memory writes cite the agent-decision event that produced the note.
- Learning flags independently control recent context, persistent memory, retrieval,
  skills, and refinement.
- Exports include a content hash and provenance. Imports reject tampering and retain
  memory IDs and source-event citations.
- Combined checkpoints inventory, hash, copy, verify, and restore `store.sqlite3`
  alongside game, agent, configuration, and event-cursor state.

## Evidence

- Configuration: `configs/m6-memory.yaml`
- Deterministic gate: `runtime/smoke/m6-memory-gate.json`
- Authenticated gate: `runtime/smoke/m6-live-glm-5.3.json`
- Memory package: `src/prime_stardew/memory`
- Deterministic runner: `src/prime_stardew/m6_gate.py`
- Authenticated runner: `src/prime_stardew/m6_live.py`
- Automated verification: 84 tests passed.

This gate isolates whether external memory and retrieval transmit a hidden fact under
controlled context budgets. It does not claim a long-horizon Stardew performance gain;
those learning curves require the multi-day M9 and M10 benchmark suites.
