#!/usr/bin/env python3
"""Keep branch-moving git commands in the one module that owns branch moves.

`integration/lg-milestone-that-491` lost lg-milestone-that-1316's landing
between 10-20 and 10-30, and the tree was published twice without it. Which
writer dropped it could not be recovered: the branch was later recreated and
its reflog went with it. Every writer of an integration branch was a bare
`update-ref`, `branch -D` or force push in its own service, each with its own
idea of what was safe to leave behind (lg-milestone-that-2624).

`loregarden.services.integration_branch_moves` is the one place allowed to
move or delete a branch: it runs the landing guard, keeps a tip it leaves under
a settled ref, and writes a ledger row naming the writer. This gate keeps new
ref writes going through it, because a bare one looks exactly like a correct
one at the call site.

What it flags, on changed lines only (diff-scoped like its siblings, so the
writers that predate it are not this change's problem): a call whose literal
argv carries

- `update-ref` (any form, `-d` included);
- `branch` with `-D`, `-d`, `--delete`, `-f` or `--force` (`-d` deletes too:
  it asks git a different question first, then drops the ref the same way);
- `checkout` or `switch` with `-B` / `-C`;
- `push` with `-f` or any `--force*`;
- `api` with `DELETE` and a `git/refs` path (`gh api -X DELETE .../git/refs/...`).

Only string literals are read, as a list literal's elements or as positional
arguments, so `run_git([...])`, `_git(repo, "update-ref", ...)` and
`subprocess.run(["gh", "api", ...])` are all seen. An argv built from a
variable is not statically knowable and is skipped rather than guessed at.

Waive one with `# py-org: allow-ref-write - <reason>` on the call or in the
comment block just above it, for a ref the owner does not govern (a chat
branch, a scratch ref, a throwaway worktree branch). The reason is a claim a
reviewer checks.

Usage: py_ref_writers_check.py [--repo R --scope S [--base B]] [files...]
"""

import ast
import sys
from collections.abc import Sequence
from pathlib import Path

_LEFTHOOK_SCRIPTS = Path(__file__).resolve().parent
if str(_LEFTHOOK_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_LEFTHOOK_SCRIPTS))

# Before any sibling import: the gates need 3.11, and on an older interpreter
# they raise at import time and exit 1, indistinguishable from a finding.
from gate_python_guard import require_supported_python  # noqa: E402 - path set just above

require_supported_python(Path(__file__).name)

from precommit_git_diff import (  # noqa: E402 - sys.path is set up just above
    GateInvocation,
    UnexaminableError,
    UnexaminableFileError,
    parse_gate_argv,
    parse_python_source,
    read_source_text,
    resolve_gate_scope,
)
from py_organization_check import python_files_in_scope  # noqa: E402 - path set above

#: The module that owns branch moves: the only one allowed to write refs bare.
_OWNER_SUFFIX = ("loregarden", "services", "integration_branch_moves.py")

_ALLOW_MARKER = "# py-org: allow-ref-write"

#: A marker with nothing after it is a reflex, not a decision.
_MIN_WAIVER_REASON_CHARS = 12

_HELP = """
💡 Fix: move the branch through its owner.

    from loregarden.services.integration_branch_moves import move_integration_branch

    move_integration_branch(session, workspace, branch, new_tip, why="...")   # new_tip="" deletes

It refuses a move that drops a landing the base lacks, keeps the tip it leaves
under refs/loregarden/settled/, and records who moved what in the ledger.

For a ref the owner does not govern (a chat branch, a scratch ref), waive the
line with a reason a reviewer can check:

    _git(repo, "branch", "-D", scratch)  # py-org: allow-ref-write - scratch branch created above, holds nothing
"""


def _is_exempt(path: Path) -> bool:
    """The owner writes refs by definition; tests build throwaway repos and move refs directly."""
    parts = path.parts
    if "tests" in parts or path.name.startswith("test_"):
        return True
    return parts[-3:] == _OWNER_SUFFIX


def _literal(node: ast.expr) -> str | None:
    """The string a literal argv element spells, if it is statically knowable.

    An f-string counts by its literal head (`f"--force-with-lease={ref}"` is a
    force push whatever `ref` is); one that starts with a placeholder does not.
    """
    # `ast` node types are a closed, foreign hierarchy, and `match` would be a
    # SyntaxError on the old interpreter the guard above exists to refuse.
    if isinstance(node, ast.Constant) and isinstance(node.value, str):  # py-org: allow-isinstance
        return node.value
    if isinstance(node, ast.JoinedStr) and node.values:  # py-org: allow-isinstance
        return _literal(node.values[0])
    return None


def _argv(node: ast.Call) -> list[str]:
    """The literal tokens of the command this call runs, in order.

    Read from a list or tuple literal argument when there is one (the
    `run_git([...])` shape), else from the positional arguments (the
    `_git(repo, "update-ref", ...)` shape). Non-literals are dropped; a
    shell-string argument is split on whitespace.
    """
    tokens: list[str] = []
    for arg in node.args:
        if isinstance(arg, (ast.List, ast.Tuple)):  # py-org: allow-isinstance
            return [text for elt in arg.elts if (text := _literal(elt)) is not None]
        text = _literal(arg)
        if text is not None:
            tokens.extend(text.split())
    return tokens


def _ref_write(tokens: list[str]) -> str | None:
    """What kind of ref write ``tokens`` spells, or None."""
    present = set(tokens)
    if "update-ref" in present:
        return "update-ref"
    if "branch" in present and present & {"-D", "-d", "--delete", "-f", "--force"}:
        return "branch -D/-d/-f"
    if present & {"checkout", "switch"} and present & {"-B", "-C"}:
        return "checkout -B"
    if "push" in present and ("-f" in present or any(t.startswith("--force") for t in tokens)):
        return "push --force"
    if "api" in present and "DELETE" in present and any("git/refs" in t for t in tokens):
        return "gh api DELETE git/refs"
    return None


def _waiver_reason(line: str) -> str | None:
    if _ALLOW_MARKER not in line:
        return None
    return line.split(_ALLOW_MARKER, 1)[1].lstrip(" -:\t").strip()


def _waiver(lines: list[str], lineno: int, end_lineno: int) -> str | None:
    """The reason written on the call's lines or the comment block above, if any."""
    first = lineno
    while first > 1 and lines[first - 2].strip().startswith("#"):
        first -= 1
    for i in range(first, min(end_lineno, len(lines)) + 1):
        reason = _waiver_reason(lines[i - 1])
        if reason is not None:
            return reason
    return None


def violations_in(path: Path, *, repo: Path | None) -> list[tuple[int, int, str]]:
    """``(lineno, end_lineno, message)`` for each unowned ref write in ``path``."""
    source = read_source_text(path, repo=repo)
    try:
        tree = parse_python_source(source, path)
    except SyntaxError as exc:
        # A file this gate could not parse is not a file it cleared.
        raise UnexaminableFileError(
            f"{path}:{exc.lineno}: this run could not parse it, so it cannot be "
            f"reported clean ({exc.msg})"
        ) from exc
    lines = source.splitlines()

    found: list[tuple[int, int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):  # py-org: allow-isinstance
            continue
        kind = _ref_write(_argv(node))
        if kind is None:
            continue
        end = node.end_lineno or node.lineno
        reason = _waiver(lines, node.lineno, end)
        if reason is not None and len(reason) >= _MIN_WAIVER_REASON_CHARS:
            continue
        if reason is not None:
            message = (
                f"`{kind}` waived with no real reason; say which ref this is and why the owner "
                f"does not govern it, in at least {_MIN_WAIVER_REASON_CHARS} characters"
            )
        else:
            message = f"`{kind}` outside integration_branch_moves"
        found.append((node.lineno, end, message))
    return sorted(found)


def _graded_files(repo: Path | None, candidates: Sequence[Path], discovered: bool) -> list[Path]:
    in_scope = python_files_in_scope(repo, candidates, discovered)
    return [path for path in in_scope if not _is_exempt(path)]


def main(argv: list[str]) -> int:
    invocation = parse_gate_argv(argv)
    try:
        return _check(invocation)
    except UnexaminableError as exc:
        print(f"{invocation.label}: cannot determine what to examine: {exc}")
        return 1


def _check(invocation: GateInvocation) -> int:
    run = resolve_gate_scope(
        label=invocation.label,
        repo=invocation.repo,
        diff_scope=invocation.diff_scope,
        base_ref=invocation.base_ref,
        explicit_files=invocation.files,
        select=_graded_files,
    )
    if not run.files:
        return 0

    failures: list[str] = []
    for path in run.files:
        touched = run.touched_lines(path)
        for lineno, end, message in violations_in(path, repo=run.repo):
            # A call is this change's when any of its lines is: a moved argument
            # or a new waiver line is an edit to the call.
            if touched is not None and not touched & set(range(lineno, end + 1)):
                continue
            failures.append(f"   {path}:{lineno}: {message}")

    if not failures:
        print(f"{invocation.label}: ref writer check passed.")
        return 0

    print(f"{invocation.label}: ❌ Branch moved outside its owner:")
    print(
        "   A bare ref write can drop a landing no base holds, and leaves no record of who did it."
    )
    print()
    for failure in failures:
        print(failure)
    print(_HELP)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
