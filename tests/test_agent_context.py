from prime_stardew.agent import ContextInputs, ContextItem, WorkingContextAssembler
from prime_stardew.experiments.config import ContextBudgetConfig


def test_context_is_deterministic_bounded_and_reports_omissions() -> None:
    assembler = WorkingContextAssembler(ContextBudgetConfig(
        total_tokens=256, objective_tokens=32, observation_tokens=64,
        goals_tokens=32, memories_tokens=24, skills_tokens=24, recent_events_tokens=24,
    ))
    inputs = ContextInputs(
        objective="Complete the task safely.",
        observation="player at Farm 62,17; crop south" * 8,
        goals=(ContextItem(item_id="g1", text="water crop"),),
        memories=(
            ContextItem(item_id="m1", text="watering can is slot 1"),
            ContextItem(item_id="m2", text="x" * 300),
        ),
        skills=(ContextItem(item_id="s1", text="select, turn, use"),),
        recent_events=(ContextItem(item_id="e1", text="observed crop"),),
    )

    first = assembler.assemble(inputs)
    second = assembler.assemble(inputs)

    assert first == second
    assert first.estimated_tokens <= 256
    assert "Complete the task safely" in first.prompt
    assert "player at Farm" in first.prompt
    assert "m1" in first.included_ids
    assert "m2" in first.omitted_ids

