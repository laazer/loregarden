"""Local CLI agent runner — deterministic subprocess target for dev and tests."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Loregarden local agent CLI runner")
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--skill", default="")
    parser.add_argument("--prompt-file", required=True)
    args = parser.parse_args()

    prompt_path = Path(args.prompt_file)
    if not prompt_path.is_file():
        print(f"prompt file not found: {prompt_path}", file=sys.stderr)
        return 2

    prompt = prompt_path.read_text(encoding="utf-8")
    if os.environ.get("LOREGARDEN_FORCE_AGENT_FAIL") == "1":
        print("agent run forced to fail (LOREGARDEN_FORCE_AGENT_FAIL=1)", file=sys.stderr)
        return 1

    result = {
        "agent_id": args.agent_id,
        "skill": args.skill,
        "prompt_chars": len(prompt),
        "status": "ok",
    }
    print(json.dumps(result))
    print(f"[{args.agent_id}] skill={args.skill or '—'} · {len(prompt)} chars")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
