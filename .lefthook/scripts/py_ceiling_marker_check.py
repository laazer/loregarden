#!/usr/bin/env python3
"""Validate the markers that say a code site is knowingly insufficient.

A ceiling is NOT a waiver. A waiver asserts a rule should not apply; a ceiling
asserts the code is knowingly insufficient, names the quantity at which it
stops holding, and names the upgrade path. That quantity is the whole reason
the debt class is enumerable: a number can be checked against a measurement and
the member CLOSED, where "this is simplified" can only accumulate.

The required shape, on the line or in the comment block directly above it:

    <marker> <quantity at which this stops holding> -- upgrade: <the path>

An em dash or " -- " separates the two clauses; an author on a keyboard without
an em dash must not be pushed into reaching for a waiver instead.

This gate is NOT a detector of unmarked shortcuts. "Find every place the code
is knowingly insufficient and has not said so" has as many instances as the
tree has corners and no terminating answer; it is out of scope by decision.
This validates markers that exist.

Two implementation choices the tests pin, both because the obvious version
cannot be committed:

- **The marker is a COMMENT, read through `tokenize`, never a raw line.** This
  file names the marker in its own strings and error messages, and so do the
  four suites that quote it. A line scan fails its own change, and finds that
  out at `git commit`.
- **Every violation in a file is reported, not only the first.** Otherwise the
  author pays one commit per marker and never sees the count.

Usage: py_ceiling_marker_check.py [staged files...]
       py_ceiling_marker_check.py --repo <root> --scope worktree [--base <ref>]
"""

import sys
import tokenize
from dataclasses import dataclass
from io import StringIO
from pathlib import Path

_LEFTHOOK_SCRIPTS = Path(__file__).resolve().parent
if str(_LEFTHOOK_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_LEFTHOOK_SCRIPTS))

# Before any sibling import, and before this module's own annotations are
# evaluated: the gates need 3.11, and on an older interpreter they raise at
# import time and exit 1 — indistinguishable from a finding.
from gate_python_guard import require_supported_python  # noqa: E402 - path set just above

require_supported_python(Path(__file__).name)

from precommit_git_diff import (  # noqa: E402 - sys.path is set up just above
    GateInvocation,
    UnexaminableError,
    parse_gate_argv,
    repo_relative_posix,
    resolve_gate_scope,
)
from py_organization_check import python_files_in_scope  # noqa: E402 - path set above

#: The marker itself. Settled by plan synthesis; downstream must not rename it.
MARKER = "ceiling:"
#: The clause that names the way off the shortcut.
UPGRADE = "upgrade:"
#: Mirrors `loregarden.services.fix_coverage.MIN_ANSWER`, which cannot be
#: imported here: this script runs from a workspace with no loregarden install.
MIN_ANSWER = 10
#: The largest file this gate opens, mirroring the shared reader's cap.
MAX_SOURCE_BYTES = 2_000_000

#: Bounds stated in words rather than digits. Incomplete by design: an author
#: with a bound outside the set states it with a number, and widening the set
#: later is a one-line change. "one process" is here because the requirement's
#: own worked example spells it that way.
NAMED_BOUNDS = (
    "global lock",
    "single process",
    "one process",
    "single host",
    "one host",
    "single node",
    "o(n^2)",
    "o(n2)",
    "o(n²)",
    "o(n log n)",
)

#: Waiver markers. A waiver offered as a ceiling is the exact confusion the
#: marker exists to prevent, so it is refused by name.
WAIVERS = (
    "silent-ok:",
    "py-silent: allow",
    "py-org: allow",
    "ux-ok:",
    "theme-ok:",
    "motion-ok:",
    "type-ok:",
)

#: Characters a separator is made of, trimmed off the end of a ceiling clause.
_SEPARATORS = " \t—–-"

_HELP = """
💡 Fix: a ceiling names the quantity and the way off it.

    def join_all(tickets):
        # {marker} 500 tickets {dash} {upgrade} paginate the join and merge per page
        return list(tickets)

The quantity is a number, or one of: {bounds}.
The upgrade clause says what replaces the shortcut ({minimum}+ characters).
" -- " works in place of the em dash.

A ceiling is not a waiver. A waiver says a rule should not apply here; a ceiling
says the code is knowingly insufficient and names what closes it. If what you
mean is "this rule should not apply", use that rule's own waiver marker instead.

The marker must sit on the code line, or in the comment block directly above it:
a detached marker names no code site, so the debt class cannot point at one.
""".format(
    marker=MARKER,
    dash="—",
    upgrade=UPGRADE,
    bounds=", ".join(NAMED_BOUNDS),
    minimum=MIN_ANSWER,
)


@dataclass(frozen=True)
class _Comment:
    """One comment, and whether code shares its line."""

    lineno: int
    text: str
    trailing: bool


def _read_text(path: Path) -> tuple[str, str]:
    """``(source, problem)`` — exactly one of them is empty.

    Decoded with ``errors="replace"`` rather than refused: a file with a few
    undecodable bytes still has readable comments, and a gate that raises on
    one reports nothing at all, which greps as zero findings. A file that
    cannot be OPENED is a different fact, and it is returned as a problem so
    the caller reports it rather than passing it clean.
    """
    try:
        real = path.resolve(strict=True)
        if not real.is_file():
            return "", f"not a regular file (resolves to {real})"
        size = real.stat().st_size
        if size > MAX_SOURCE_BYTES:
            return "", f"{size} bytes exceeds the {MAX_SOURCE_BYTES}-byte grading limit"
        return real.read_bytes().decode("utf-8", errors="replace"), ""
    except (OSError, RuntimeError) as exc:
        return "", f"this run could not read it ({type(exc).__name__}: {exc})"


def _comments_and_code(source: str) -> tuple[list[_Comment], set[int], str]:
    """Every comment, the lines carrying code, and how the read ended.

    Tokenized rather than scanned, because the marker is a comment: a raw-line
    gate flags this file's own messages, the suites that quote it, and every
    docstring that shows the shape.

    A tokenizer that gives up part way through returns what it read plus the
    reason. Nothing is reported clean on the strength of a read that stopped.
    """
    comments: list[_Comment] = []
    code_lines: set[int] = set()
    reader = tokenize.generate_tokens(StringIO(source).readline)
    try:
        for token in reader:
            if token.type == tokenize.COMMENT:
                line = token.line[: token.start[1]]
                comments.append(
                    _Comment(
                        lineno=token.start[0],
                        text=token.string.rstrip(),
                        trailing=bool(line.strip()),
                    )
                )
            elif token.type not in (
                tokenize.NL,
                tokenize.NEWLINE,
                tokenize.INDENT,
                tokenize.DEDENT,
                tokenize.ENDMARKER,
                tokenize.ENCODING,
            ):
                for number in range(token.start[0], token.end[0] + 1):
                    code_lines.add(number)
    except (tokenize.TokenError, IndentationError, SyntaxError) as exc:
        return comments, code_lines, f"{type(exc).__name__}: {exc}"
    return comments, code_lines, ""


def _attached(comment: _Comment, code_lines: set[int], total: int) -> bool:
    """True when this comment names a code site.

    On the code line, or in the comment block DIRECTLY above one: a blank line
    between the marker and the code, or nothing below it at all, means the
    marker points at nothing and cannot be resolved as `path/to/file.py:<line>`.
    """
    if comment.trailing or comment.lineno in code_lines:
        return True
    number = comment.lineno + 1
    while number <= total:
        if number in code_lines:
            return True
        number += 1
    return False


def _blank_between(comment: _Comment, code_lines: set[int], lines: list[str]) -> bool:
    """True when a blank line separates the marker from the next code line."""
    number = comment.lineno + 1
    while number <= len(lines) and number not in code_lines:
        if not lines[number - 1].strip():
            return True
        number += 1
    return False


def marker_body(text: str) -> str | None:
    """What this comment declares after the marker, or None if it declares none.

    The marker OPENS the comment; a mention of it inside prose is prose. Without
    that rule the gate flags "deliberately malformed `<marker>` markers inside
    string literals" — a sentence in its own test suite — and this change cannot
    land, which is the same trap as scanning raw lines, one layer in.
    """
    body = text.lstrip("#").strip()
    if not body.startswith(MARKER):
        return None
    return body[len(MARKER) :]


def _clauses(rest: str) -> tuple[str, str | None]:
    """The ceiling clause and the upgrade clause, or ``None`` for no upgrade."""
    if UPGRADE not in rest:
        return rest.strip(_SEPARATORS), None
    before, _, after = rest.partition(UPGRADE)
    return before.strip(_SEPARATORS), after.strip()


def _has_quantity(clause: str) -> bool:
    lowered = clause.lower()
    return any(character.isdigit() for character in clause) or any(
        bound in lowered for bound in NAMED_BOUNDS
    )


def _problem_with(body: str) -> str | None:
    """Why this marker is refused, or None. One reason, the most specific first."""
    ceiling, upgrade = _clauses(body)
    lowered = ceiling.lower()
    offered = [waiver for waiver in WAIVERS if waiver in lowered]
    if offered:
        return (
            f"{offered[0]!r} is a waiver, not a ceiling: a waiver says a rule should not "
            "apply, a ceiling says the code is knowingly insufficient and names the "
            "quantity that closes it"
        )
    if not _has_quantity(ceiling):
        return (
            f"no quantity in {ceiling or '<nothing>'!r}: name the quantity at which this "
            f"stops holding, as a number or one of {', '.join(NAMED_BOUNDS)}"
        )
    if upgrade is None:
        return f"no '{UPGRADE}' clause: name the path off the shortcut after the separator"
    if len(upgrade) < MIN_ANSWER:
        return (
            f"the '{UPGRADE}' clause {upgrade or '<nothing>'!r} is a placeholder "
            f"({MIN_ANSWER}+ characters): name what replaces the shortcut"
        )
    return None


def violations_in(path: Path, *, repo: Path | None) -> list[tuple[int, str]]:
    """Every malformed marker in ``path``, as ``(line number, why)``.

    Never raises. A gate that raises under the hook reports nothing and reads
    as clean, which this repository has already met once; a file that could not
    be read or could not be fully tokenized is reported as a violation of its
    own instead.
    """
    del repo  # The reader here is path-local; the scope resolver owns the repo.
    source, problem = _read_text(path)
    if problem:
        return [(1, f"{problem}, so it cannot be reported clean")]
    comments, code_lines, broke_off = _comments_and_code(source)
    lines = source.splitlines()
    found: list[tuple[int, str]] = []
    for comment in comments:
        body = marker_body(comment.text)
        if body is None:
            continue
        if not comment.trailing and (
            _blank_between(comment, code_lines, lines)
            or not _attached(comment, code_lines, len(lines))
        ):
            found.append(
                (
                    comment.lineno,
                    f"this {MARKER} marker is not attached to a code line: put it on the "
                    "line it describes, or in the comment block directly above it",
                )
            )
            continue
        why = _problem_with(body)
        if why is not None:
            found.append((comment.lineno, why))
    if broke_off:
        found.append(
            (
                len(lines) or 1,
                f"this run could not finish reading the file ({broke_off}), so the part "
                "after that point was not examined and cannot be reported clean",
            )
        )
    return sorted(found)


def _graded_files(repo: Path | None, candidates, discovered: bool) -> list[Path]:
    """Every Python file in scope. No exemption for tests: the pre-commit glob
    is `server/**/*.py`, and a malformed marker in a test is still one."""
    return python_files_in_scope(repo, candidates, discovered)


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
        # The count above already said zero; adding "check passed" to it would
        # be a success claim about nothing examined.
        return 0

    failures: list[str] = []
    for path in run.files:
        touched = run.touched_lines(path)
        relpath = repo_relative_posix(path, run.repo) if run.repo else str(path)
        for lineno, why in violations_in(path, repo=run.repo):
            # Diff-scoped: the rule is "don't add another", not "fix the tree
            # on sight". A gate that blocks on debt the author never wrote
            # teaches waiving by reflex, which is what a ceiling must not become.
            if touched is not None and lineno not in touched:
                continue
            failures.append(f"   {relpath}:{lineno}: {why}")

    if not failures:
        print(f"{invocation.label}: {MARKER} marker check passed.")
        return 0

    print(f"❌ Malformed {MARKER} marker:")
    print("   A ceiling names the quantity at which the code stops holding, and the way off it.")
    print()
    for failure in failures:
        print(failure)
    print(_HELP)
    return 1


def main(argv: list[str]) -> int:
    invocation = parse_gate_argv(argv)
    try:
        return _check(invocation)
    except UnexaminableError as exc:
        print(f"{invocation.label}: cannot determine what to examine: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
