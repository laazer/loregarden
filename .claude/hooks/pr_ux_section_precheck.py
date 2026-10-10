#!/usr/bin/env python3
"""PreToolUse/Bash: run the "PR UX section" check before `gh pr create` does.

The check (`.github/scripts/pr_ux_section_check.py`) runs only in CI, after the
PR exists, and agents create PRs with `--body`/`--body-file`, which skips the
template that would have shown them the section. Its first run failed on 21 of
74 PR branches in its first eight days. This runs the same `problems()` on the
body about to be sent and the branch's changed files, and denies the command
with the check's own message, so the section is written before the PR opens.

Covers `gh pr create` and `gh pr edit` with a body. A body the hook cannot read
(`--fill`, `--web`, a `$(...)` substitution, a missing file) is not blocked:
the agent is told the precheck did not run and CI will check.

Standard library only, Python 3.9: hooks run under the system `python3`.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from types import ModuleType

_CHECK = Path(".github") / "scripts" / "pr_ux_section_check.py"
_GH_PR = re.compile(r"\bgh\s+pr\s+(create|edit)\b")
_CD = re.compile(r"(?:^|[;&|\s])cd\s+(\"[^\"]+\"|'[^']+'|[^\s;&|]+)")
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n(.*?)\n\2[ \t]*(?:\n|$)", re.DOTALL)
_STOP = {"&&", "||", ";", "|", "<", "<<", ">", ">>", "&", "\n"}
#: shlex's own punctuation, plus a newline: outside quotes it ends the command.
_PUNCTUATION = "();<>|&\n"


class Unreadable(Exception):
    """The body the command would send cannot be known before it runs."""


def _emit(decision: str | None, message: str) -> None:
    output = {"hookEventName": "PreToolUse"}
    if decision is None:
        output["additionalContext"] = message
    else:
        output["permissionDecision"] = decision
        output["permissionDecisionReason"] = message
    print(json.dumps({"hookSpecificOutput": output}))


def _gh_args(command: str, match: re.Match) -> list[str]:
    """The `gh pr …` invocation's own arguments, up to the first shell operator.

    A quoted body may span lines; an unquoted newline ends the command.
    """
    lexer = shlex.shlex(command[match.start() :], posix=True, punctuation_chars=_PUNCTUATION)
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    args: list[str] = []
    for token in lexer:
        if token in _STOP:
            break
        args.append(token)
    return args[3:]  # drop "gh pr create"


def _option(args: list[str], *names: str) -> str | None:
    for i, arg in enumerate(args):
        for name in names:
            if arg == name and i + 1 < len(args):
                return args[i + 1]
            if name.startswith("--") and arg.startswith(name + "="):
                return arg[len(name) + 1 :]
    return None


def _workdir(command: str, match: re.Match, cwd: Path) -> Path:
    """The directory `gh` runs in: the session's, after any `cd` before it."""
    found = _CD.findall(command[: match.start()])
    if not found:
        return cwd
    target = Path(os.path.expanduser(found[-1].strip("'\"")))
    return target if target.is_absolute() else cwd / target


def _body(command: str, match: re.Match, args: list[str], workdir: Path) -> str | None:
    """The description the command sends; None when it sends none (an edit of other fields)."""
    if any(flag in args for flag in ("--fill", "--fill-first", "--fill-verbose", "--web", "-w")):
        raise Unreadable("the body comes from --fill or the browser")
    inline = _option(args, "--body", "-b")
    path = _option(args, "--body-file", "-F")
    if inline is None and path is None:
        if match.group(1) == "create":
            raise Unreadable("no --body or --body-file was given")
        return None
    if inline is not None:
        if "$(" in inline or "`" in inline:
            raise Unreadable("the body is built by a shell substitution")
        return inline
    if path == "-":
        heredoc = _HEREDOC.search(command, match.end())
        if heredoc is None:
            raise Unreadable("the body is read from stdin, not a heredoc in the command")
        return heredoc.group(3)
    file = Path(os.path.expanduser(path or ""))
    file = file if file.is_absolute() else workdir / file
    try:
        return file.read_text(encoding="utf-8")
    except OSError as exc:
        raise Unreadable(f"could not read {file}: {exc.strerror}") from exc


def _git(workdir: Path, *args: str) -> str:
    # GIT_DIR beats cwd; a hook inherits whatever the session exported.
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}
    try:
        result = subprocess.run(
            ["git", *args], cwd=workdir, env=env, capture_output=True, text=True, timeout=20
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Unreadable(f"`git {' '.join(args)}` did not run: {exc}") from exc
    if result.returncode != 0:
        raise Unreadable(f"`git {' '.join(args)}` failed: {result.stderr.strip()[:200]}")
    return result.stdout.strip()


def _changed(workdir: Path, args: list[str]) -> list[str]:
    base = _option(args, "--base", "-B") or "main"
    for ref in (f"origin/{base}", base):
        try:
            _git(workdir, "rev-parse", "--verify", "--quiet", ref)
        except Unreadable:
            continue  # silent-ok: tries the next spelling; none resolving raises below
        return [p for p in _git(workdir, "diff", "--name-only", f"{ref}...HEAD").splitlines() if p]
    raise Unreadable(f"base branch {base!r} is not known locally")


def _load_check(root: Path) -> ModuleType | None:
    script = root / _CHECK
    if not script.is_file():
        return None
    spec = importlib.util.spec_from_file_location("pr_ux_section_check", script)
    if spec is None or spec.loader is None:
        raise Unreadable(f"could not load {script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def decide(payload: dict) -> tuple[str | None, str] | None:
    """(decision, message) for a command this hook has something to say about.

    decision is "deny", or None to pass the message to the agent and let the
    command run.
    """
    command = (payload.get("tool_input") or {}).get("command") or ""
    match = _GH_PR.search(command)
    if match is None:
        return None
    workdir = _workdir(command, match, Path(payload.get("cwd") or os.getcwd()))
    try:
        args = _gh_args(command, match)
        body = _body(command, match, args, workdir)
        if body is None:
            return None
        check = _load_check(Path(_git(workdir, "rev-parse", "--show-toplevel")))
        if check is None:  # another repository: it has no such check
            return None
        found = check.problems(body, _changed(workdir, args))
    except (Unreadable, ValueError) as exc:  # ValueError: shlex on unbalanced quotes
        return None, (
            f"The PR UX section precheck did not run ({exc}). If this PR touches "
            "client/src/pages or client/src/components, its description needs a "
            "'## User-facing surfaces' section (Question, Action, Real data with a number; "
            "see .github/pull_request_template.md) or the 'PR UX section' CI check fails."
        )
    if not found:
        return None
    return "deny", (
        "The 'PR UX section' CI check would fail on this description:\n- "
        + "\n- ".join(found)
        + "\n\nAdd a '## User-facing surfaces' section with **Question:**, **Action:** and "
        "**Real data:** (a measured number from `task sandbox`, or `task sandbox -- --seeded`), "
        "or **No user-visible change:** <reason>. Template: .github/pull_request_template.md."
    )


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        _emit(
            None, "The PR UX section precheck could not read its hook input; CI will still check."
        )
        return 0
    verdict = decide(payload)
    if verdict is not None:
        _emit(*verdict)
    return 0


if __name__ == "__main__":
    sys.exit(main())
