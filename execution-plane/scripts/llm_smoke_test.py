from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.llm import LlmClient, LlmMessage, LlmRequest


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a real LLM JSON smoke test.")
    parser.add_argument("--config", help="Path to llm JSON config file.", default=None)
    parser.add_argument("--provider", help="Provider override for this request.", default=None)
    parser.add_argument("--model", help="Model override for this request.", default=None)
    args = parser.parse_args()

    client = LlmClient.from_sources(config_path=args.config)
    result = client.complete_json(
        LlmRequest(
            task="manual_llm_smoke_test",
            provider=args.provider,
            model=args.model,
            temperature=0,
            messages=(
                LlmMessage(
                    "system",
                    "You are a smoke test endpoint. Return only valid JSON.",
                ),
                LlmMessage(
                    "user",
                    'Return {"ok": true, "message": "llm smoke test passed"}.',
                ),
            ),
            json_schema={
                "type": "object",
                "required": ["ok", "message"],
                "properties": {
                    "ok": {"type": "boolean"},
                    "message": {"type": "string"},
                },
            },
        )
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
