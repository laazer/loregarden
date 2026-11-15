#!/usr/bin/env python3
"""A merge may not quietly delete a file that only its own branch added.

`8ee19bc68`, "Merge origin/main into integration/lg-milestone-that-491"
(2026-10-30), resolved a ToastHost conflict by taking main's design, and with
it deleted the six files lg-milestone-that-1316 had landed on that branch.
The commit message said so; nothing else did. The branch still had 1316's
landing as an ancestor, so every ancestry check read the work as held, and the
tree was published twice without it (lg-milestone-that-2622). The doctor's
content check found it two weeks later, because it runs only when asked.

The check is exact, needing no ticket data: a file that exists on the first
parent and in no merge base was added by the branch since the sides parted.
git's own merge never deletes such a file, because the other side cannot have
deleted what it never had. So when one is missing from the result, the
resolution deleted it. A rename is not a deletion (`-M`).

Two ways in:

- ``--msg-file F``: the commit-msg hook, for a merge being concluded by hand
  (``MERGE_HEAD`` present; the result is the staged index). Each dropped file
  must be named in the message on a line ``Drops: <path> — <why>``, which puts
  the decision where the next reader of the merge will see it. Not a merge:
  passes untouched.
- ``--commit SHA``: a merge commit already written. The integration merge turn
  (`integration_merge_agent`) runs this on the commit it built and refuses a
  resolution that drops anything; an agent cannot decide that for a person.

Exit 0 clean, 1 drops found, 69 (EX_UNAVAILABLE) when git could not answer.

Usage: merge_drops_check.py (--msg-file F | --commit SHA) [--repo R]
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

_LEFTHOOK_SCRIPTS = Path(__file__).resolve().parent
if str(_LEFTHOOK_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_LEFTHOOK_SCRIPTS))

# Before any sibling import: the gates need 3.11, and on an older interpreter
# they raise at import time and exit 1, indistinguishable from a finding.
from gate_python_guard import require_supported_python  # noqa: E402 - path set just above

require_supported_python(Path(__file__).name)

from precommit_git_diff import scrubbed_git_env  # noqa: E402 - sys.path is set up just above

EX_UNAVAILABLE = 69
#: `Drops: <path> — <why>`; an em dash, en dash, `-` or `--` separates them.
_DROPS_LINE = re.compile(r"^Drops:\s*(\S+)\s+(?:—|–|--|-)\s+(.+?)\s*$")
#: A reason is a claim for the reviewer; "ok" or "intentional" says nothing.
_MIN_REASON_WORDS = 3
#: `git commit --verbose` appends the diff below this line; nothing after it is message.
_SCISSORS = "# ------------------------ >8 ------------------------"


class GitUnanswered(RuntimeError):
    """git could not answer a question the check depends on."""


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", *args],
        cwd=repo,
        env=scrubbed_git_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        raise GitUnanswered(f"git {' '.join(args)}: {(done.stderr or done.stdout).strip()}")
    return done.stdout


def _rev(repo: Path, ref: str) -> str:
    """``ref``'s commit, or "" when it does not exist."""
    done = subprocess.run(
        ["git", "rev-parse", "-q", "--verify", f"{ref}^{{commit}}"],
        cwd=repo,
        env=scrubbed_git_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    return done.stdout.strip() if done.returncode == 0 else ""


def _deleted(repo: Path, diff_args: list[str]) -> list[str]:
    out = _git(repo, "diff", "-z", "--name-only", "-M", "--diff-filter=D", *diff_args)
    return [path for path in out.split("\0") if path]


def _merge_bases(repo: Path, first: str, others: list[str]) -> list[str]:
    bases: list[str] = []
    for other in others:
        out = _git(repo, "merge-base", "--all", first, other)
        bases += out.split()
    return bases


def _present(repo: Path, commit: str, paths: list[str]) -> set[str]:
    out = _git(repo, "ls-tree", "-r", "-z", "--name-only", commit, "--", *paths)
    return {path for path in out.split("\0") if path}


def branch_only_drops(repo: Path, first: str, others: list[str], diff_args: list[str]) -> list[str]:
    """Files ``first`` has, no merge base with ``others`` has, and the result lacks."""
    deleted = _deleted(repo, diff_args)
    if not deleted:
        return []
    in_a_base: set[str] = set()
    for base in _merge_bases(repo, first, others):
        in_a_base |= _present(repo, base, deleted)
    return sorted(path for path in deleted if path not in in_a_base)


def _merge_heads(repo: Path) -> list[str]:
    head_file = Path(_git(repo, "rev-parse", "--git-path", "MERGE_HEAD").strip())
    if not head_file.is_absolute():
        head_file = repo / head_file
    if not head_file.exists():
        return []
    return head_file.read_text(encoding="utf-8").split()


def declared_drops(message: str) -> dict[str, str]:
    """The ``Drops:`` lines of a commit message, path to reason."""
    declared: dict[str, str] = {}
    # A `# Drops:` comment line never matches: the pattern is anchored at `Drops:`.
    for line in message.split(_SCISSORS, 1)[0].splitlines():
        found = _DROPS_LINE.match(line.strip())
        if found:
            declared[found.group(1)] = found.group(2)
    return declared


def _undeclared(drops: list[str], declared: dict[str, str]) -> list[str]:
    return [path for path in drops if len(declared.get(path, "").split()) < _MIN_REASON_WORDS]


def _check_message(repo: Path, msg_file: Path) -> int:
    others = _merge_heads(repo)
    if not others:
        return 0
    head = _rev(repo, "HEAD")
    if not head:
        return 0
    drops = branch_only_drops(repo, head, others, ["--cached", head])
    missing = _undeclared(drops, declared_drops(msg_file.read_text(encoding="utf-8")))
    if not missing:
        return 0
    print("This merge deletes files that only its own branch added:")
    for path in missing:
        print(f" - {path}")
    print(
        "git's merge never deletes these, so the resolution did, and the work they hold will\n"
        "not reach the base. Restore them, or, if dropping one is the decision, say so in the\n"
        "commit message, one line per file, with a reason a reviewer can check:\n"
        "  Drops: <path> — <why it is dropped>"
    )
    return 1


def _check_commit(repo: Path, commit: str) -> int:
    parents = _git(repo, "rev-list", "--parents", "-n", "1", commit).split()[1:]
    if len(parents) < 2:
        return 0
    drops = branch_only_drops(repo, parents[0], parents[1:], [parents[0], commit])
    for path in drops:
        print(f" - {path}")
    return 1 if drops else 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--msg-file", type=Path)
    which.add_argument("--commit")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    try:
        if args.msg_file is not None:
            return _check_message(args.repo, args.msg_file)
        return _check_commit(args.repo, args.commit)
    except (GitUnanswered, OSError) as exc:
        print(f"merge_drops_check could not examine the merge: {exc}", file=sys.stderr)
        return EX_UNAVAILABLE


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
