"""Model-provider protocol and Prime Agent JSONL RPC adapter."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Protocol, TextIO
from uuid import uuid4

from .models import ProviderRequest, ProviderResponse, ProviderUsage


class ProviderError(RuntimeError):
    pass


class AgentProvider(Protocol):
    def complete(self, request: ProviderRequest) -> ProviderResponse: ...
    def close(self) -> None: ...


class ScriptedProvider:
    """Deterministic provider for gates, replay, and failure injection."""

    def __init__(self, responses: list[str], *, provider: str = "scripted", model: str = "scripted-v1") -> None:
        self.responses = deque(responses)
        self.provider = provider
        self.model = model
        self.requests: list[ProviderRequest] = []

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        if not self.responses:
            raise ProviderError("Scripted provider has no response remaining")
        self.requests.append(request)
        text = self.responses.popleft()
        return ProviderResponse(
            text=text,
            request_id=f"scripted-{len(self.requests)}",
            provider=self.provider,
            model=self.model,
            route="deterministic-replay",
            usage=ProviderUsage(
                input_tokens=request.estimated_input_tokens,
                output_tokens=max(1, len(text.encode("utf-8")) // 4),
                latency_ms=0,
                cost_usd=0,
            ),
        )

    def close(self) -> None:
        return None


class PrimeRpcProvider:
    """Persistent subprocess client for Prime Agent's public RPC mode."""

    def __init__(
        self,
        *,
        executable: str = "prime-agent",
        provider: str,
        model: str,
        session_dir: Path,
        cwd: Path,
        timeout: float = 300,
        extra_args: tuple[str, ...] = ("--no-tools", "--no-skills", "--no-context-files"),
        environment: dict[str, str] | None = None,
    ) -> None:
        args = [executable, "--mode", "rpc", "--provider", provider, "--model", model,
                "--session-dir", str(session_dir.resolve()), *extra_args]
        self.provider, self.model, self.timeout = provider, model, timeout
        self._process = subprocess.Popen(
            args, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1,
            env={**os.environ, **(environment or {})},
        )
        if self._process.stdin is None or self._process.stdout is None:
            raise ProviderError("Prime Agent RPC pipes were not created")
        self._stdin: TextIO = self._process.stdin
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._stderr_lines: deque[str] = deque(maxlen=200)
        threading.Thread(target=self._read_stdout, args=(self._process.stdout,), daemon=True).start()
        if self._process.stderr is not None:
            threading.Thread(
                target=self._read_stderr, args=(self._process.stderr,), daemon=True,
            ).start()

    def _read_stdout(self, stream: TextIO) -> None:
        for line in stream:
            self._lines.put(line.rstrip("\n").rstrip("\r"))
        self._lines.put(None)

    def _read_stderr(self, stream: TextIO) -> None:
        # A PIPE must be consumed continuously: otherwise a verbose child can fill the OS pipe
        # buffer and block before emitting the terminal RPC event on stdout.
        for line in stream:
            self._stderr_lines.append(line.rstrip("\n").rstrip("\r"))

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        started = time.perf_counter()
        command_id = request.call_id or str(uuid4())
        self._send({"id": command_id, "type": "prompt", "message": request.prompt})
        accepted = False
        assistant = None
        usage = {"input": request.estimated_input_tokens, "output": 0, "cost": 0.0}
        actual = {"provider": self.provider, "model": self.model, "route": "prime-agent-rpc"}
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            try:
                line = self._lines.get(timeout=min(0.25, max(0.01, deadline - time.monotonic())))
            except queue.Empty:
                continue
            if line is None:
                raise ProviderError("Prime Agent RPC exited before agent_end")
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ProviderError("Prime Agent emitted non-JSON stdout") from exc
            if event.get("type") == "response" and event.get("id") == command_id:
                if not event.get("success"):
                    raise ProviderError(str(event.get("error", "Prime prompt rejected")))
                accepted = True
            if event.get("type") == "agent_end":
                assistant = _assistant_text(event)
                usage = _usage(event, usage)
                actual = _attribution(event, actual)
                break
        if not accepted or assistant is None:
            raise ProviderError("Timed out waiting for Prime Agent RPC completion")
        return ProviderResponse(
            text=assistant,
            request_id=command_id,
            provider=str(actual["provider"]),
            model=str(actual["model"]),
            route=str(actual["route"]),
            usage=ProviderUsage(
                input_tokens=int(usage["input"]), output_tokens=int(usage["output"]),
                latency_ms=(time.perf_counter() - started) * 1000,
                cost_usd=float(usage["cost"]),
            ),
        )

    def _send(self, value: dict[str, object]) -> None:
        self._stdin.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
        self._stdin.flush()

    def close(self) -> None:
        if self._process.poll() is None:
            self._stdin.close()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.terminate()


def _assistant_text(event: dict[str, object]) -> str | None:
    messages = event.get("messages")
    if not isinstance(messages, list):
        return None
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                str(item.get("text", "")) for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            )
    return None


def _usage(event: dict[str, object], fallback: dict[str, int | float]):
    messages = event.get("messages")
    if not isinstance(messages, list):
        return fallback
    totals: dict[str, int | float] = {"input": 0, "output": 0, "cost": 0.0}
    found = False
    for message in messages:
        if isinstance(message, dict) and message.get("role") == "assistant":
            usage = message.get("usage")
            if isinstance(usage, dict):
                found = True
                totals["input"] += int(usage.get("input", usage.get("inputTokens", 0)))
                totals["output"] += int(usage.get("output", usage.get("outputTokens", 0)))
                cost = usage.get("cost", usage.get("totalCost", 0))
                if isinstance(cost, dict):
                    cost = cost.get("total", 0)
                totals["cost"] += float(cost)
    return totals if found else fallback


def _attribution(event: dict[str, object], fallback: dict[str, str]) -> dict[str, str]:
    messages = event.get("messages")
    if not isinstance(messages, list):
        return fallback
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "assistant":
            return {
                "provider": str(message.get("provider") or fallback["provider"]),
                "model": str(message.get("model") or fallback["model"]),
                "route": str(message.get("api") or fallback["route"]),
            }
    return fallback
