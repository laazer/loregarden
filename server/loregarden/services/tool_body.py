"""Whether a tool command's stdout belongs in the log, or only its shape.

Most of what a `TOOL` line stores is the stdout of a read-only command — a
`sed -n '1,320p' foo.ts` keeping the 320 lines it printed. That body tells the
reader nothing the headline does not, and the file it came from is one click
away in the editor. A test's output is the opposite case: the body *is* the
finding, so it stays. Replayed over the 300 most recent run logs, this policy
drops 40% of `TOOL` volume and 14% of the whole log corpus.

The split is by what the command does, not by how big its output was, because
size is a bad proxy — a two-line pytest summary matters and a two-line `ls`
does not. A failing command always keeps its body: `rg: no such file` is the
only place that failure is recorded.
"""

from __future__ import annotations

import re
from enum import Enum

from loregarden.dot_line import LogLine, size


class CommandOutcome(Enum):
    """How a tool command ended, in this repo's terms rather than an adapter's."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"
    """No adapter reported one. Distinct from failure: an unread outcome must
    not silently shape a body away."""


class ToolBodyPolicy(Enum):
    """What to do with a command's captured output."""

    KEEP = "keep"
    """The output is the finding — tests, gates, git, anything unrecognised."""

    SHAPE = "shape"
    """A read-only command that succeeded — record how much it printed, not what."""


#: Executables that only report on the tree. A command built entirely from these
#: prints something the operator can re-read at will, so the log keeps its shape
#: and drops its contents.
_READ_ONLY_COMMANDS = frozenset(
    {
        "awk",
        "basename",
        "cat",
        "dirname",
        "echo",
        "file",
        "find",
        "grep",
        "egrep",
        "fgrep",
        "head",
        "jq",
        "ls",
        "nl",
        "pwd",
        "realpath",
        "rg",
        "sed",
        "stat",
        "tail",
        "tree",
        "wc",
    }
)

#: Shells invoked as `<shell> -lc '<real command>'`. The wrapper is not the
#: command, and reading it instead would classify every line the same way.
_SHELL_WRAPPERS = frozenset({"bash", "sh", "zsh", "fish", "dash"})

#: Codex's own success values for a `command_execution` item, and the exit code
#: some adapters send instead. Its vocabulary, not ours.
_SUCCESS_STATUSES = frozenset({"completed", "0"})  # py-org: allow-string
_FAILURE_STATUSES = frozenset({"failed"})  # py-org: allow-string

_SEGMENT_SPLIT = re.compile(r"&&|\|\||[;|]")
_ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_REDIRECT = re.compile(r"[<>]")


def command_outcome(status: str) -> CommandOutcome:  # py-org: allow-string - adapter vocabulary
    """Read an adapter's raw `command_execution` status into our own outcome.

    The only place the foreign vocabulary is spoken; everything downstream takes
    the enum. An unrecognised status is UNKNOWN rather than a failure — this
    decides how much of a log to keep, not whether the run went well.
    """
    text = status.strip()
    if text in _SUCCESS_STATUSES:
        return CommandOutcome.SUCCEEDED
    if text in _FAILURE_STATUSES:
        return CommandOutcome.FAILED
    return CommandOutcome.UNKNOWN


def _head_tokens(command: str) -> list[str]:
    """The executable at the head of each pipeline segment, wrapper stripped."""
    stripped = command.strip().strip("'\"")
    tokens = stripped.split()
    # `/bin/zsh -lc 'sed -n 1,20p foo'` — drop the shell and its flags, then read
    # the quoted script that follows as the real command line.
    if tokens and tokens[0].rsplit("/", 1)[-1] in _SHELL_WRAPPERS:
        rest = stripped.split(None, 1)[1] if len(tokens) > 1 else ""
        rest = re.sub(r"^-[A-Za-z]+\s*", "", rest).strip()
        stripped = rest.strip("'\"")

    heads: list[str] = []
    for segment in _SEGMENT_SPLIT.split(stripped):
        if _REDIRECT.search(segment):
            # A redirect writes somewhere; that is no longer a read-only command.
            return []
        parts = [part for part in segment.split() if not _ENV_ASSIGNMENT.match(part)]
        if not parts:
            continue
        heads.append(parts[0].rsplit("/", 1)[-1])
    return heads


def body_policy(command: str, *, outcome: CommandOutcome) -> ToolBodyPolicy:
    """Whether this command's output is worth storing verbatim."""
    if outcome is not CommandOutcome.SUCCEEDED:
        return ToolBodyPolicy.KEEP
    heads = _head_tokens(command)
    if not heads:
        return ToolBodyPolicy.KEEP
    if all(head in _READ_ONLY_COMMANDS for head in heads):
        return ToolBodyPolicy.SHAPE
    return ToolBodyPolicy.KEEP


def output_shape(output: str) -> str:
    """`320 lines, 11.2 KB` — what a read-only command printed, not what it said."""
    lines = output.count("\n") + 1
    return f"{lines} lines, {size(len(output))}"


def with_tool_output(
    line: LogLine, *, command: str, output: object, outcome: CommandOutcome
) -> LogLine:
    """Attach a command's output to its log line under the body policy."""
    text = str(output).strip() if output is not None else ""
    if not text:
        return line
    if body_policy(command, outcome=outcome) is ToolBodyPolicy.KEEP:
        return line.with_body(text)
    return line / output_shape(text)
