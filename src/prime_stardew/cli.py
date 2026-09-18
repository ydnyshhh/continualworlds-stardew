"""Small diagnostic CLI backed by the typed SDK."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .env.client import StarDojoClient
from .env.transport import SocketTransport


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("message", help="Raw StarDojo command, with percent-separated arguments")
    parser.add_argument("--port", type=int, default=10783)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--max-response-mib", type=int, default=32)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    method, *arguments = args.message.split("%")
    if method == "observe":
        parser.error("Use observe_v2 for the text transport")

    transport = SocketTransport(
        port=args.port,
        timeout=args.timeout,
        max_response_bytes=args.max_response_mib * 1024 * 1024,
    )
    response = StarDojoClient(transport).raw(method, *arguments)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(response.payload, encoding="utf-8")
        print(
            json.dumps(
                {
                    "request_id": response.request_id,
                    "output": str(args.output),
                    "bytes": response.bytes_received,
                    "elapsed_ms": round(response.elapsed_ms, 3),
                    "attempt": response.attempt,
                }
            )
        )
    else:
        print(response.payload[:2000])


if __name__ == "__main__":
    main()
