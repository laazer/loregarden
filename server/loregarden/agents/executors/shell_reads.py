"""What files a shell command READ, recovered from its text.

`read_paths` covers the Read tool. Reviewers mostly do not use it: measured over
482 passing review, verify and gate runs since 2026-10-20, a reviewer that read
only through the Read tool was the exception, and refusing a pass on Read
evidence alone would have refused about 94% of honest reviews. They read
through the shell (`git diff -- path`, `sed -n 1,80p path`, `grep -n x path`),
in Claude's `Bash` tool and Cursor's `shellToolCall`.

This reads that text. It is a heuristic over shell syntax, not a shell, and it
leans one way on purpose: the caller refuses a pass only when a run read
*nothing* of the diff, so a token wrongly counted as a read costs nothing,
while a read missed refuses an honest review.

Three things are recovered from one command:

- **named**: path-like arguments of a command that reads (`cat`, `sed`, `grep`,
  `git show`, …). A command that only names a file without reading it (`ls`,
  `wc`, `rm`) names nothing.
- **pathspecs**: what follows `--` in `git diff`/`show`/`log`, matched by the
  caller against the diff as a path, a directory or a glob.
- **whole_diff**: a `git diff`/`show`/`log -p` that prints patch text with no
  pathspec narrowing it, which shows every changed file at once. A summary
  flag (`--stat`, `--name-only`) prints no file contents and does not count.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

#: Commands that take a path without reading its contents.
NON_READING_COMMANDS = frozenset(
    {
        "ls",
        "wc",
        "stat",
        "file",
        "test",
        "[",
        "touch",
        "mkdir",
        "rm",
        "mv",
        "cp",
        "echo",
        "which",
        "cd",
        "du",
        "find",
    }
)

#: Git subcommands that print file contents.
GIT_READING_SUBCOMMANDS = frozenset({"diff", "show", "blame", "grep", "cat-file"})

#: Git subcommands whose `--` pathspec selects files from the diff.
GIT_PATHSPEC_SUBCOMMANDS = frozenset({"diff", "show", "log"})

#: Flags under which `git diff`/`show` prints no patch text.
SUMMARY_FLAGS = frozenset(
    {
        "--stat",
        "--name-only",
        "--name-status",
        "--numstat",
        "--shortstat",
        "--summary",
        "--quiet",
        "--exit-code",
    }
)

#: Pathspec magic that excludes rather than selects.
EXCLUDE_PREFIXES = (":!", ":^", ":(exclude)")

_SEPARATORS = re.compile(r"\s*(?:&&|\|\||;|\||\n)\s*")
_EXTENSION = re.compile(r"\.\w{1,5}$")


@dataclass(frozen=True)
class ShellReads:
    """What one command read. See the module docstring for each field."""

    named: frozenset[str] = frozenset()
    pathspecs: frozenset[str] = frozenset()
    whole_diff: bool = False


def _argv(segment: str) -> list[str]:
    try:
        return shlex.split(segment)
    except ValueError:
        # An unbalanced quote, usually a heredoc or a multi-line script the
        # separator split cut in half. Whitespace splitting still recovers the
        # path arguments, which is all this needs.
        return segment.split()


#: Git's global options that take a value as the next argument.
_GIT_VALUE_OPTIONS = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace"})


def _git_argv(argv: list[str]) -> list[str] | None:
    """A git command as `git <subcommand> …`, dropping global options; None if not git.

    `git -C x --no-pager diff …` becomes `git diff …`, so argv[1] is always the
    subcommand. A bare `git` (no subcommand) is not a git command worth reading.
    """
    if argv[0] != "git":
        return None
    rest = argv[1:]
    while rest and rest[0].startswith("-"):
        rest = rest[2:] if rest[0] in _GIT_VALUE_OPTIONS else rest[1:]
    return [argv[0], *rest] if rest else None


def _prints_patch(argv: list[str]) -> bool:
    subcommand = argv[1]
    if subcommand == "log":
        return "-p" in argv or "--patch" in argv
    return subcommand in ("diff", "show")


def _after_double_dash(argv: list[str]) -> list[str]:
    return argv[argv.index("--") + 1 :] if "--" in argv else []


def _is_whole_diff(argv: list[str]) -> bool:
    if not _prints_patch(argv):
        return False
    if any(arg in SUMMARY_FLAGS or arg.startswith("--stat") for arg in argv):
        return False
    return all(spec.startswith(EXCLUDE_PREFIXES) for spec in _after_double_dash(argv))


def _git_reads_contents(git: list[str]) -> bool:
    return git[1] in GIT_READING_SUBCOMMANDS or _prints_patch(git)


def _path_like(token: str) -> bool:
    return "/" in token or bool(_EXTENSION.search(token))


def _under(cwd: str, token: str) -> str:
    return f"{cwd}/{token}" if cwd and not token.startswith("/") else token


def _named_paths(argv: list[str], cwd: str) -> set[str]:
    found: set[str] = set()
    after_dd = False
    for token in argv[1:]:
        if token == "--":
            after_dd = True
            continue
        if token.startswith("-") and not after_dd:
            continue
        if _path_like(token):
            found.add(_under(cwd, token))
    return found


def shell_reads(command: str) -> ShellReads:
    """What `command` read: the paths it named, its pathspecs, whether whole-diff.

    A `cd` carries to the segments after it, so `cd server && sed -n 1p x.py`
    names `server/x.py`. Only the last `cd` before a segment counts; `cd ..` and
    other relative climbs are taken literally and simply match nothing.
    """
    named: set[str] = set()
    pathspecs: set[str] = set()
    whole = False
    cwd = ""
    for segment in _SEPARATORS.split(command):
        argv = _argv(segment)
        if not argv:
            continue
        if argv[0] == "cd":
            cwd = argv[1].rstrip("/") if len(argv) > 1 else ""
            continue
        git = _git_argv(argv)
        if git is None:
            if argv[0] not in NON_READING_COMMANDS:
                named.update(_named_paths(argv, cwd))
            continue
        if git[1] in GIT_PATHSPEC_SUBCOMMANDS:
            whole = whole or _is_whole_diff(git)
            pathspecs.update(
                _under(cwd, spec) for spec in _after_double_dash(git) if not spec.startswith(":")
            )
        if _git_reads_contents(git):
            # `git show HEAD:path` names the path after the revision.
            named.update(_named_paths([token.rpartition(":")[2] for token in git], cwd))
    return ShellReads(named=frozenset(named), pathspecs=frozenset(pathspecs), whole_diff=whole)
