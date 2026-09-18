"""Bounded transports for live StarDojo and deterministic replay."""

from __future__ import annotations

import ipaddress
import socket
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

from .errors import (
    ConfigurationError,
    ConnectionFailed,
    ProtocolError,
    ResponseTimeout,
    ResponseTooLarge,
)

TERMINATOR = b"<EOF>"


@dataclass(frozen=True, slots=True)
class TransportResponse:
    request_id: str
    command: str
    payload: str
    elapsed_ms: float
    bytes_received: int
    attempt: int


class Transport(Protocol):
    def request(self, command: str, *, idempotent: bool = False) -> TransportResponse: ...


class SocketTransport:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 10783,
        timeout: float = 30.0,
        max_response_bytes: int = 32 * 1024 * 1024,
        idempotent_attempts: int = 2,
    ) -> None:
        try:
            address = ipaddress.ip_address(host)
        except ValueError as exc:
            raise ConfigurationError("StarDojo host must be a literal loopback IP") from exc
        if not address.is_loopback:
            raise ConfigurationError("StarDojo may only connect over loopback")
        if not 1 <= port <= 65535:
            raise ConfigurationError("Port must be between 1 and 65535")
        if timeout <= 0 or max_response_bytes <= 0 or idempotent_attempts < 1:
            raise ConfigurationError("Timeout, response limit, and attempts must be positive")
        self.host = host
        self.port = port
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self.idempotent_attempts = idempotent_attempts

    def request(self, command: str, *, idempotent: bool = False) -> TransportResponse:
        _validate_command(command)
        request_id = str(uuid4())
        attempts = self.idempotent_attempts if idempotent else 1
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                return self._request_once(command, request_id, attempt)
            except (ConnectionFailed, ResponseTimeout, ProtocolError) as exc:
                last_error = exc
                if attempt == attempts:
                    raise
        raise AssertionError("request attempts exhausted") from last_error

    def _request_once(self, command: str, request_id: str, attempt: int) -> TransportResponse:
        started = time.monotonic()
        deadline = started + self.timeout
        response = bytearray()
        try:
            client = socket.create_connection((self.host, self.port), timeout=self.timeout)
        except OSError as exc:
            raise ConnectionFailed(f"Unable to connect to {self.host}:{self.port}") from exc

        with client:
            try:
                client.sendall(command.encode("utf-8"))
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ResponseTimeout(
                            f"Request {request_id} timed out; mutation outcome may be unknown"
                        )
                    client.settimeout(remaining)
                    try:
                        chunk = client.recv(65536)
                    except TimeoutError as exc:
                        raise ResponseTimeout(
                            f"Request {request_id} timed out; mutation outcome may be unknown"
                        ) from exc
                    if not chunk:
                        raise ProtocolError("Connection closed before <EOF> terminator")
                    response.extend(chunk)
                    if len(response) > self.max_response_bytes + len(TERMINATOR):
                        raise ResponseTooLarge(
                            f"Response exceeded {self.max_response_bytes} bytes"
                        )
                    marker = response.find(TERMINATOR)
                    if marker >= 0:
                        if marker + len(TERMINATOR) != len(response):
                            raise ProtocolError("Unexpected bytes after <EOF> terminator")
                        payload_bytes = bytes(response[:marker])
                        if len(payload_bytes) > self.max_response_bytes:
                            raise ResponseTooLarge(
                                f"Response exceeded {self.max_response_bytes} bytes"
                            )
                        try:
                            payload = payload_bytes.decode("utf-8")
                        except UnicodeDecodeError as exc:
                            raise ProtocolError("Response is not UTF-8") from exc
                        return TransportResponse(
                            request_id=request_id,
                            command=command,
                            payload=payload,
                            elapsed_ms=(time.monotonic() - started) * 1000,
                            bytes_received=len(payload_bytes),
                            attempt=attempt,
                        )
            except OSError as exc:
                raise ConnectionFailed(f"Socket failure for request {request_id}") from exc


class ReplayTransport:
    """Return recorded responses in order without launching Stardew Valley."""

    def __init__(self, responses: Mapping[str, list[str] | tuple[str, ...]]) -> None:
        self._responses = {key: list(values) for key, values in responses.items()}
        self.requests: list[str] = []

    def request(self, command: str, *, idempotent: bool = False) -> TransportResponse:
        _validate_command(command)
        self.requests.append(command)
        available = self._responses.get(command)
        if not available:
            raise ProtocolError(f"No replay response remaining for {command!r}")
        payload = available.pop(0)
        return TransportResponse(
            request_id=f"replay-{len(self.requests):06d}",
            command=command,
            payload=payload,
            elapsed_ms=0.0,
            bytes_received=len(payload.encode("utf-8")),
            attempt=1,
        )


def _validate_command(command: str) -> None:
    if not command or command != command.strip():
        raise ConfigurationError("Command must be non-empty and have no outer whitespace")
    if "\x00" in command or "<EOF>" in command:
        raise ConfigurationError("Command contains forbidden framing characters")
