#!/usr/bin/env python3
"""PreToolUse/Bash: deny git commands that turn commit signing off.

`main`'s ruleset requires signed commits, and this repository signs every
commit (`commit.gpgsign=true`, an SSH key). Two agent runs on
lg-durable-remote-336 (`run_9931e9`, `run_dd8a15`) committed with
`git -c commit.gpgsign=false commit` on their first attempt, with no signing
error before it, and the unsigned commits reached PR #555. Nothing stopped them.

Denies, in any command that runs git:

- `--no-gpg-sign`;
- `-c commit.gpgsign=<false>` (any spelling git accepts as false);
- `git config … commit.gpgsign <false>` and `git config --unset commit.gpgsign`;
- `GIT_CONFIG_*` / `GIT_CONFIG_PARAMETERS` environment overrides naming it.

If signing genuinely fails, the fix is the signer (the key, the agent), and a
person decides that. The pre-push gate `.lefthook/scripts/signed-commits.sh`
backs this up for commits made any other way.

Standard library only, Python 3.9: hooks run under the system `python3`.
"""

from __future__ import annotations

import json
import re
import sys

_GIT = re.compile(r"(?:^|[\s;&|(])git(?:\s|$)")
_FALSE = r"(?:false|no|off|0)"
_KEY = r"commit\.gpgsign"
_RULES = (
    (re.compile(r"(?:^|[\s'\"])--no-gpg-sign(?:[\s'\"]|$)"), "--no-gpg-sign"),
    (
        re.compile(rf"-c\s+['\"]?{_KEY}\s*=\s*['\"]?{_FALSE}\b", re.IGNORECASE),
        "-c commit.gpgsign=false",
    ),
    (
        re.compile(rf"\bconfig\b[^;&|\n]*\b{_KEY}\s+['\"]?{_FALSE}\b", re.IGNORECASE),
        "git config commit.gpgsign false",
    ),
    (
        re.compile(rf"\bconfig\b[^;&|\n]*--unset(?:-all)?\s+{_KEY}\b", re.IGNORECASE),
        "git config --unset commit.gpgsign",
    ),
    (
        re.compile(rf"GIT_CONFIG_(?:KEY_\d+|PARAMETERS)=[^\s;&|]*{_KEY}", re.IGNORECASE),
        "a GIT_CONFIG_* override of commit.gpgsign",
    ),
)


def decide(payload: dict):
    """("deny", reason) or None."""
    command = (payload.get("tool_input") or {}).get("command") or ""
    if not _GIT.search(command):
        return None
    for pattern, what in _RULES:
        if pattern.search(command):
            return "deny", (
                f"Blocked: {what} commits without a signature. This repository signs every "
                "commit and main's ruleset requires it. Commit normally; if signing fails, "
                "stop and report the signer's error (key, ssh-agent) to the user rather than "
                "turning signing off."
            )
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:  # silent-ok: nothing to inspect; the pre-push signature gate still checks every pushed commit
        return 0
    verdict = decide(payload)
    if verdict is not None:
        decision, reason = verdict
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": decision,
                        "permissionDecisionReason": reason,
                    }
                }
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
