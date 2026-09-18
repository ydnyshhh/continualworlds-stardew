"""Small bounded Prime Agent RPC health probe used to distinguish provider from task failures."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from prime_stardew.agent import PrimeRpcProvider, ProviderRequest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prime-executable", type=Path, required=True)
    parser.add_argument("--node-dir", type=Path)
    parser.add_argument("--prime-config-dir", type=Path, required=True)
    parser.add_argument("--prime-session-dir", type=Path, required=True)
    parser.add_argument("--provider", default="openrouter")
    parser.add_argument("--model", default="z-ai/glm-5.3")
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()
    environment = {
        "PRIME_AGENT_CODING_AGENT_DIR": str(args.prime_config_dir.resolve()),
        "PI_SKIP_VERSION_CHECK": "1",
    }
    if args.node_dir:
        environment["PATH"] = str(args.node_dir.resolve()) + os.pathsep + os.environ.get("PATH", "")
    provider = PrimeRpcProvider(
        executable=str(args.prime_executable.resolve()), provider=args.provider,
        model=args.model, session_dir=args.prime_session_dir, cwd=Path.cwd(),
        timeout=args.timeout, environment=environment,
        extra_args=("--no-tools", "--no-skills", "--no-context-files", "--no-session"),
    )
    try:
        response = provider.complete(ProviderRequest(
            call_id="health-probe",
            prompt='Return exactly this JSON and nothing else: {"status":"ok"}',
            estimated_input_tokens=16,
            decoding={"temperature": 0, "top_p": 1, "max_output_tokens": 32},
        ))
        print(json.dumps(response.model_dump(mode="json"), indent=2))
    finally:
        provider.close()


if __name__ == "__main__":
    main()
