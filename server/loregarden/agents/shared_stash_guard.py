#!/usr/bin/env python3
"""Refuse the `git stash` forms that take or move entries on the shared stash stack.

Every worktree of a repository shares one stash stack. On 2026-11-23 a QA run
(`run_8fa4e2`) ran `git stash` with nothing to save, then `git stash pop`: the
pop applied and dropped the operator's own stash, 3,391 files, into the ticket's
worktree. The agent's prompt already forbade it, and 102 recorded runs had
stashed anyway, so this is enforced where the command is issued.

Two policies:

- **agent** (``--agent-run``, and any process `LOREGARDEN_MCP_ORCHESTRATED`
  marks): every CLI this control plane spawns, stage run or chat turn. Only the
  forms that neither add to nor take from the stack pass: `list`, `show`,
  `create`, `store`, and `apply` of a literal commit SHA.
- **session**: a person's own Claude Code session, through the repository's
  hooks or the loregarden plugin. Only the forms that pick an entry by its
  position are refused (`pop`, `clear`, `branch`, a bare `drop`, `apply` of
  anything but a SHA or a variable), and only where the repository has more than
  one worktree. Saving under a tag, applying by SHA and dropping a named entry
  is how such sessions are told to work, and how they mostly do.

It reads command text, so it stops an agent that reaches for the command, not
one set on hiding it (an alias, a script file). Text that only mentions a stash,
in a quoted message or a heredoc, is not a command and passes; a shell's `-c`
string, `eval`, a substitution and a heredoc fed to a shell are, and do not.

Run by Claude Code (`--settings` from `cli_adapters`, `.claude/settings.json`,
the plugin's `hooks.json`) and, with ``--cursor``, by cursor-agent
(`cursor_plugin/`). `.claude/hooks/shared_stash_guard.py` is a symlink here, and
`tool_auto_approve` imports it, so there is one answer.

Standard library only, Python 3.9: hooks run under the system `python3`.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterator

ORCHESTRATED_ENV = "LOREGARDEN_MCP_ORCHESTRATED"

#: Forms that neither add an entry to the stack nor take one off it.
_ALWAYS_ALLOWED = frozenset({"list", "show", "create", "store", "-h", "--help"})
#: Options of `git` itself that take the next word as their value.
_GIT_VALUE_OPTIONS = frozenset(
    {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix", "--config-env"}
)
_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh"})
_SHELL_COMMAND_FLAG = re.compile(r"-[A-Za-z]*c[A-Za-z]*\Z")
_SHA = re.compile(r"[0-9a-f]{7,64}\Z", re.IGNORECASE)
_HEREDOC = re.compile(r"<<(-?)[ \t]*(['\"]?)([^\s'\";&|<>()]+)\2")
#: Seven digits are an index to git (`stash@{1234567}`); forty are a SHA.
_FULL_SHA_LENGTHS = (40, 64)

_AGENT_REASON = (
    "Blocked: `git stash{form}` is refused in agent runs. The stash stack is shared by every "
    "worktree of this repository, so it can take or bury another session's entry: run_8fa4e2 "
    "stashed nothing, then popped the operator's own 3,391-file stash into a ticket worktree. "
    "To learn whether a failure predates your work, read the prior agent's checkpoint log; its "
    "recorded failures are the baseline. To see a file without your change, use `git show "
    "HEAD:<path>`. To set work aside, make a temporary commit (`git commit -am wip`) and undo it "
    "before your stage ends (`git reset --soft HEAD~1`). `git stash list`, `show`, `create`, "
    "`store` and `apply <commit SHA>` stay allowed."
)
_SESSION_REASON = (
    "Blocked: `git stash{form}` takes whichever entry is on top, and this repository's stash "
    "stack is shared by all its worktrees, so that entry may be another session's. Find yours "
    "with `git stash list --format='%H %gd %gs'`, restore it with `git stash apply <its SHA>`, "
    "then drop it by name with `git stash drop stash@{{n}}`. A temporary commit avoids the stack "
    "altogether."
)


# --- reading shell text ------------------------------------------------------------


class _Reader:
    """Splits shell text into simple commands, each a list of unquoted words.

    ``nested`` collects code that is quoted inside the text and so still needs
    reading: backtick substitutions, and the bodies of heredocs fed to a shell.
    A `$(…)` is read in place by a reader of its own, which stops at the `)`
    that closes it: only a reader that knows quotes and heredocs can find it.
    """

    def __init__(self, text: str, start: int = 0, *, in_parens: bool = False) -> None:
        self.text = text
        self.position = start
        self.commands: list[list[str]] = []
        self.nested: list[str] = []
        self._in_parens = in_parens
        self._depth = 0
        self._words: list[str] = []
        self._word: list[str] | None = None  # its pieces; joined when it ends
        self._redirect_target = False
        self._heredocs: list[tuple[str, bool, bool]] = []
        self._last_command: list[str] = []

    def read(self) -> None:
        while self.position < len(self.text):
            if self._in_parens and self._depth == 0 and self.text[self.position] == ")":
                self.position += 1
                break
            self._step()
        self._end_command()

    def _add(self, text: str) -> None:
        if self._word is None:
            self._word = []
        self._word.append(text)

    def _end_word(self) -> None:
        if self._word is None:
            return
        if not self._redirect_target:
            self._words.append("".join(self._word))
        self._redirect_target = False
        self._word = None

    def _end_command(self) -> None:
        self._end_word()
        self._redirect_target = False
        if self._words:
            self.commands.append(self._words)
        self._last_command = self._words
        self._words = []

    def _step(self) -> None:
        char = self.text[self.position]
        if char == "'":
            self._single_quoted()
        elif char == '"':
            self._double_quoted()
        elif char == "\\":
            self._escaped()
        elif self._substitution():
            return
        elif char in "<>" or self.text.startswith("&>", self.position):
            self._redirect()
        elif char == "#" and self._word is None:
            end = self.text.find("\n", self.position)
            self.position = len(self.text) if end < 0 else end
        elif char in ";&|()\n":
            self._depth += {"(": 1, ")": -1}.get(char, 0)
            self._end_command()
            self.position += 1
            if char == "\n":
                self._heredoc_bodies()
        else:
            if char.isspace():
                self._end_word()
            else:
                self._add(char)
            self.position += 1

    def _single_quoted(self) -> None:
        end = self.text.find("'", self.position + 1)
        end = len(self.text) if end < 0 else end
        self._add(self.text[self.position + 1 : end])
        self.position = end + 1

    def _escaped(self) -> None:
        char = self.text[self.position + 1 : self.position + 2]
        if char != "\n":  # a line continuation is no character at all
            self._add(char)
        self.position += 2

    def _double_quoted(self) -> None:
        self._add("")
        self.position += 1
        self.read_expanded('"')
        self.position += 1

    def read_expanded(self, closing: str = "") -> None:
        """Read text that is data but for its substitutions, up to ``closing``."""
        while self.position < len(self.text) and self.text[self.position] != closing:
            if self.text[self.position] == "\\":
                self._escaped()
            elif not self._substitution():
                self._add(self.text[self.position])
                self.position += 1

    def _substitution(self) -> bool:
        """Read the `$(…)` or backticks here, if any. Its output is a word part nobody knows."""
        if self.text.startswith("$(", self.position):
            inner = _Reader(self.text, self.position + 2, in_parens=True)
            inner.read()
            self.position = inner.position
            self.commands.extend(inner.commands)
            self.nested.extend(inner.nested)
        elif self.text[self.position] == "`":
            end = self.position + 1
            while end < len(self.text) and self.text[end] != "`":
                end += 2 if self.text[end] == "\\" else 1
            self.nested.append(self.text[self.position + 1 : end].replace("\\`", "`"))
            self.position = end + 1
        else:
            return False
        self._add("$()")
        return True

    def _redirect(self) -> None:
        if self._word is not None and "".join(self._word).isdigit():
            self._word = None  # a file descriptor: `2>`
        self._end_word()
        if self.text.startswith("<<<", self.position):
            # A here-string is what the command reads; a shell runs it, as it would `-c`.
            self._words.append("-c")
            self.position += 3
        elif self.text.startswith("<<", self.position):
            self._heredoc_start()
        elif self.text[self.position + 1 : self.position + 2] == "(":
            self.position += 1  # process substitution: `(` starts a command of its own
        else:
            while self.position < len(self.text) and self.text[self.position] in "<>&|":
                self.position += 1
            self._redirect_target = True

    def _heredoc_start(self) -> None:
        match = _HEREDOC.match(self.text, self.position)
        if match is None:
            self.position += 2
            return
        dash, quote, delimiter = match.groups()
        literal = bool(quote) or "\\" in delimiter
        self._heredocs.append((delimiter.replace("\\", ""), literal, bool(dash)))
        self.position = match.end()

    def _heredoc_bodies(self) -> None:
        pending, self._heredocs = self._heredocs, []
        # The command the heredoc feeds is the one the newline just ended.
        to_shell = any(os.path.basename(word) in _SHELLS for word in self._last_command)
        for delimiter, literal, strip_tabs in pending:
            body = []
            while self.position < len(self.text):
                end = self.text.find("\n", self.position)
                end = len(self.text) if end < 0 else end
                line = self.text[self.position : end]
                self.position = end + 1
                if (line.lstrip("\t") if strip_tabs else line) == delimiter:
                    break
                body.append(line)
            text = "\n".join(body)
            if to_shell:
                self.nested.append(text)
            elif not literal:
                inner = _Reader(text)
                inner.read_expanded()
                self.commands.extend(inner.commands)
                self.nested.extend(inner.nested)


def _stash_arguments(words: list[str]) -> Iterator[list[str]]:
    """What follows `stash` for each `git … stash` among one command's words.

    Anywhere among them, so a wrapper (`env X=1 git …`, `timeout 5 git …`,
    `xargs git …`) hides nothing.
    """
    for index, word in enumerate(words):
        name = os.path.basename(word)
        if name == "git-stash":
            yield words[index + 1 :]
        if name != "git":
            continue
        position = index + 1
        while position < len(words) and words[position].startswith("-"):
            position += 2 if words[position] in _GIT_VALUE_OPTIONS else 1
        if position < len(words) and words[position] == "stash":
            yield words[position + 1 :]


def _run_as_code(words: list[str]) -> Iterator[str]:
    """The strings among ``words`` that the command runs as shell code."""
    for index, word in enumerate(words):
        if word == "eval":
            yield " ".join(words[index + 1 :])
        if os.path.basename(word) not in _SHELLS:
            continue
        rest = words[index + 1 :]
        for offset, flag in enumerate(rest):
            if not flag.startswith("-"):
                break
            if _SHELL_COMMAND_FLAG.match(flag):
                yield " ".join(rest[offset + 1 : offset + 2])
                break


def shell_commands(
    command: str, *, enters: Callable[[list[str]], bool] = lambda words: True
) -> Iterator[list[str]]:
    """The words of every simple command that ``command`` would run.

    The code a command runs for itself (`sh -c '…'`, `eval`) is read too, unless
    ``enters`` refuses that command.
    """
    pending = [command]
    while pending:
        reader = _Reader(pending.pop())
        reader.read()
        pending.extend(reader.nested)
        for words in reader.commands:
            yield words
            if enters(words):
                pending.extend(_run_as_code(words))


def stash_commands(command: str) -> Iterator[list[str]]:
    """The arguments of every `git stash` that ``command`` would run."""
    for words in shell_commands(command):
        yield from _stash_arguments(words)


# --- the policies ------------------------------------------------------------------


def _is_sha(entry: str) -> bool:
    if not _SHA.match(entry):
        return False
    return not entry.isdigit() or len(entry) in _FULL_SHA_LENGTHS


def _entry(arguments: list[str]) -> str:
    """The entry an `apply` or `drop` names; "" for none."""
    return next((word for word in arguments[1:] if not word.startswith("-")), "")


def _agent_refuses(arguments: list[str]) -> bool:
    action = arguments[0] if arguments else "push"
    if action in _ALWAYS_ALLOWED:
        return False
    return not (action == "apply" and _is_sha(_entry(arguments)))


def _session_refuses(arguments: list[str]) -> bool:
    action = arguments[0] if arguments else "push"
    if action in ("pop", "clear", "branch"):
        return True
    entry = _entry(arguments)
    if action == "drop":
        return not entry
    return action == "apply" and not (_is_sha(entry) or "$" in entry)


def denial(command: str, *, agent_run: bool) -> str:
    """Why ``command`` is refused under the policy, or "" when it is not."""
    refuses = _agent_refuses if agent_run else _session_refuses
    reason = _AGENT_REASON if agent_run else _SESSION_REASON
    if "stash" not in command:
        return ""
    try:
        refused = next(filter(refuses, stash_commands(command)), None)
    except RecursionError:
        # Hundreds of nested `$(`: nothing an agent writes to get work done, and
        # nothing this can vouch for. Split up, the command is read normally.
        return reason.format(form=" <nested too deeply to read>")
    if refused is None:
        return ""
    return reason.format(form="".join(f" {word}" for word in refused[:2]))


def stack_is_shared(cwd: str) -> bool:
    """Whether the repository at ``cwd`` has several worktrees; True when git cannot say."""
    # GIT_DIR beats cwd; a hook inherits whatever the session exported.
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (
        OSError,
        subprocess.SubprocessError,
    ):  # silent-ok: counts as shared, so the guard stays shut
        return True
    return sum(line.startswith("worktree ") for line in result.stdout.splitlines()) > 1


def command_of(payload: dict) -> str:
    """The shell command a hook was asked about, in Claude Code's and cursor's payloads."""
    return payload.get("command") or (payload.get("tool_input") or {}).get("command") or ""


def decide(payload: dict, *, agent_run: bool) -> str:
    """Why the hook's command is refused, or ""."""
    reason = denial(command_of(payload), agent_run=agent_run)
    if reason and not agent_run and not stack_is_shared(payload.get("cwd") or os.getcwd()):
        return ""
    return reason


def _is_agent_run(argv: list[str]) -> bool:
    marked = os.environ.get(ORCHESTRATED_ENV, "").lower() in ("1", "true", "yes")
    return marked or "--agent-run" in argv


def respond(argv: list[str], reason_for: Callable[[dict], str], *, name: str) -> int:
    """Answer the hook on stdin with ``reason_for`` its payload, in the calling CLI's form."""
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        # Exit 1 is a non-blocking hook error in both CLIs: it is shown, and the
        # command runs. A hook that cannot read its input must not wedge the shell.
        print(f"{name}: unreadable hook input: {exc}", file=sys.stderr)
        return 1
    reason = reason_for(payload)
    if not reason:
        return 0
    if "--cursor" in argv:
        print(json.dumps({"permission": "deny", "user_message": reason, "agent_message": reason}))
        return 0
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }
        )
    )
    return 0


def main(argv: list[str]) -> int:
    agent_run = _is_agent_run(argv)
    return respond(
        argv, lambda payload: decide(payload, agent_run=agent_run), name="shared_stash_guard"
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
