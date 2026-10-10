"""An in-memory stand-in for `gh issue` / `gh repo`, patched in at `run_gh`."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

REPO = "acme/widgets"


class FakeGh:
    """Just enough of `gh issue`/`gh repo` to be the other side of a sync."""

    def __init__(self) -> None:
        self.issues: dict[int, dict[str, Any]] = {}
        self.calls: list[list[str]] = []
        self.fail_next: str = ""
        #: Every call with this verb fails, e.g. "edit".
        self.fail_verb: str = ""

    def add(
        self,
        title: str,
        body: str = "",
        *,
        state: str = "OPEN",
        reason: str | None = None,
        labels=(),
    ) -> int:
        number = len(self.issues) + 1
        self.issues[number] = {
            "number": number,
            "title": title,
            "body": body,
            "state": state,
            "stateReason": reason,
            "url": f"https://github.com/{REPO}/issues/{number}",
            "labels": [{"name": name} for name in labels],
        }
        return number

    @staticmethod
    def _opt(args: list[str], flag: str) -> str | None:
        return args[args.index(flag) + 1] if flag in args else None

    def __call__(self, args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        if self.fail_next or args[1] == self.fail_verb:
            message, self.fail_next = self.fail_next or "HTTP 502", ""
            return subprocess.CompletedProcess(args, 1, "", message)
        out = self._dispatch(args)
        return subprocess.CompletedProcess(args, 0, out, "")

    def _dispatch(self, args: list[str]) -> str:
        noun, verb = args[0], args[1]
        if (noun, verb) == ("repo", "view"):
            return REPO + "\n"
        if verb == "list":
            return json.dumps([i for i in self.issues.values() if i["state"] == "OPEN"])
        if verb == "create":
            number = self.add(self._opt(args, "--title") or "", self._opt(args, "--body") or "")
            return self.issues[number]["url"] + "\n"
        issue = self.issues[int(args[2])]
        if verb == "view":
            return json.dumps(issue)
        if verb == "edit":
            for flag, key in (("--title", "title"), ("--body", "body")):
                if (value := self._opt(args, flag)) is not None:
                    issue[key] = value
        elif verb == "close":
            issue["state"] = "CLOSED"
            reason = self._opt(args, "--reason")
            issue["stateReason"] = "NOT_PLANNED" if reason == "not planned" else "COMPLETED"
        elif verb == "reopen":
            issue["state"], issue["stateReason"] = "OPEN", "REOPENED"
        return ""
