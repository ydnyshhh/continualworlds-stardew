import io
from collections import deque

import pytest

from prime_stardew.agent.provider import PrimeRpcProvider, _assistant_text, _attribution, _usage


def test_prime_rpc_agent_end_parsing_uses_actual_route_and_aggregates_usage() -> None:
    event = {
        "type": "agent_end",
        "messages": [
            {
                "role": "assistant", "content": [{"type": "text", "text": "thinking turn"}],
                "provider": "openai", "model": "gpt-a", "api": "responses",
                "usage": {"input": 10, "output": 2, "cost": {"total": 0.01}},
            },
            {"role": "toolResult", "content": []},
            {
                "role": "assistant", "content": [{"type": "text", "text": '{"actions":[]}'}],
                "provider": "openai", "model": "gpt-b", "api": "responses",
                "usage": {"input": 12, "output": 3, "cost": {"total": 0.02}},
            },
        ],
    }

    assert _assistant_text(event) == '{"actions":[]}'
    assert _usage(event, {"input": 0, "output": 0, "cost": 0}) == {
        "input": 22, "output": 5, "cost": pytest.approx(0.03),
    }
    assert _attribution(event, {"provider": "x", "model": "y", "route": "z"}) == {
        "provider": "openai", "model": "gpt-b", "route": "responses",
    }


def test_prime_rpc_stderr_reader_drains_and_bounds_diagnostics() -> None:
    provider = object.__new__(PrimeRpcProvider)
    provider._stderr_lines = deque(maxlen=2)
    provider._read_stderr(io.StringIO("first\nsecond\nthird\n"))
    assert tuple(provider._stderr_lines) == ("second", "third")
