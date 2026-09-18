from __future__ import annotations

import socket
import threading
from collections.abc import Iterable
from contextlib import contextmanager

import pytest

from prime_stardew.env.errors import ConfigurationError, ProtocolError, ResponseTooLarge
from prime_stardew.env.transport import SocketTransport


@contextmanager
def server(chunks: Iterable[bytes]):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    def serve() -> None:
        try:
            connection, _ = listener.accept()
            with connection:
                connection.recv(4096)
                for chunk in chunks:
                    connection.sendall(chunk)
        finally:
            listener.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        thread.join(timeout=2)


@contextmanager
def multi_server(responses: Iterable[Iterable[bytes]]):
    queued = list(responses)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(len(queued))
    port = listener.getsockname()[1]

    def serve() -> None:
        try:
            for chunks in queued:
                connection, _ = listener.accept()
                with connection:
                    connection.recv(4096)
                    for chunk in chunks:
                        connection.sendall(chunk)
        finally:
            listener.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        thread.join(timeout=2)


def test_reads_chunked_utf8_response_until_terminator() -> None:
    with server([b'{"ok":', b"true}", b"<EOF>"]) as port:
        response = SocketTransport(port=port, idempotent_attempts=1).request("observe_v2%1")

    assert response.payload == '{"ok":true}'
    assert response.bytes_received == len(response.payload)
    assert response.attempt == 1


def test_rejects_connection_closed_without_terminator() -> None:
    with server([b"partial"]) as port:
        with pytest.raises(ProtocolError, match="before <EOF>"):
            SocketTransport(port=port, idempotent_attempts=1).request("turn%1")


def test_rejects_trailing_bytes_after_terminator() -> None:
    with server([b"True<EOF>junk"]) as port:
        with pytest.raises(ProtocolError, match="after <EOF>"):
            SocketTransport(port=port, idempotent_attempts=1).request("pause")


def test_enforces_response_limit() -> None:
    with server([b"12345<EOF>"]) as port:
        with pytest.raises(ResponseTooLarge):
            SocketTransport(port=port, max_response_bytes=4, idempotent_attempts=1).request(
                "observe_v2%1"
            )


def test_refuses_non_loopback_host() -> None:
    with pytest.raises(ConfigurationError, match="loopback"):
        SocketTransport(host="192.0.2.1")


def test_idempotent_request_recovers_after_disconnect() -> None:
    with multi_server([[b"partial"], [b"recovered<EOF>"]]) as port:
        response = SocketTransport(port=port, idempotent_attempts=2).request(
            "observe_v2%1",
            idempotent=True,
        )

    assert response.payload == "recovered"
    assert response.attempt == 2
