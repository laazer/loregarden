#!/usr/bin/env python3
"""`--emit-scope-json`: a gate run's scope, resolved by `precommit_git_diff`,
for a gate written in another language (`ts_organization_check.cjs`).

Split out of `precommit_git_diff.py`, which is a library every Python gate
imports; this is the one piece of it that is a command.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from collections.abc import Sequence
from contextlib import redirect_stdout
from pathlib import Path

_LEFTHOOK_SCRIPTS = Path(__file__).resolve().parent
if str(_LEFTHOOK_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_LEFTHOOK_SCRIPTS))

# An entry point like the Python gates: refuse an interpreter too old to import
# the library below, as "could not run" rather than as a crash (657).
from gate_python_guard import require_supported_python  # noqa: E402 - path set just above

require_supported_python(Path(__file__).name)

from precommit_git_diff import (  # noqa: E402 - path set above
    DEFAULT_BASE_REF,
    EXIT_UNEXAMINABLE,
    STAGED,
    GateFileSelector,
    UnexaminableError,
    git_repo_root,
    git_source_paths,
    located_path,
    resolve_gate_scope,
)

# --------------------------------------------------------------------------- #
# `--emit-scope-json` — the scope, resolved once, for a gate in another language
# --------------------------------------------------------------------------- #
#
# About 560 lines of `ts_organization_check.cjs` were a hand-port of this module:
# the error classes, git-path decoding, env scrubbing, ref validation, scope
# resolution, untracked discovery, submodule announcement and diff-suppression
# detection. None of it was TypeScript-specific, and three of one review's nine
# defects were the two copies drifting apart. The conformance table catches that
# drift after the fact, on the repo states it enumerates; it does not remove the
# surface, and every fix still had to be written twice (580).
#
# So the `.cjs` asks this instead. It already shells out to git repeatedly; one
# more subprocess buys it a single implementation of scope policy.
#
# The language-specific half stays with the caller and is passed *in*: which
# suffixes that gate grades, and which source root it confines discovery to.
# That keeps the count honest — it is computed after the caller's own filter,
# by the same code that computes it for the Python gates — without this module
# needing to know what a TypeScript file is.


def _suffix_selector(suffixes: frozenset[str], select_root: Path | None) -> GateFileSelector:
    """Build the caller's file filter from flags rather than from knowledge here.

    `select_root` is applied only when the run *discovered* its own candidates,
    matching every gate's existing behaviour: an explicitly named file was
    chosen by the caller (lefthook passes staged paths) and is not second-
    guessed, while a path the scope turned up is confined to the source root so
    a stray match elsewhere in the repo is not graded.
    """

    def select(repo: Path | None, candidates: Sequence[Path], discovered: bool) -> list[Path]:
        chosen = []
        for candidate in candidates:
            if candidate.suffix not in suffixes:
                continue
            if discovered and select_root is not None:
                # `located_path`, not `resolve()`: resolving the whole path
                # follows a symlinked source out of the tree, drops it from the
                # filter, and reports `examined 0` over a file the read guard
                # was supposed to refuse. That is the vacuous pass this module's
                # own docstring warns about, and using `resolve()` here
                # reintroduced it (caught by the ts-org symlink tests).
                try:
                    located_path(candidate).relative_to(select_root)
                except ValueError:
                    continue
            chosen.append(candidate)
        return chosen

    return select


def emit_scope_json(argv: Sequence[str] | None = None) -> int:
    """Print one JSON object describing what a gate run should examine.

    Everything a gate needs before it can apply a single rule: the resolved
    scope, the files that survived the caller's filter, which of them git is not
    tracking, which of them git changed but would not diff, and the line numbers
    the change touched in each.

    Two things are deliberately *not* decided here, because they are the
    caller's: which suffixes to grade, and what to do about the result. The
    caller also prints `notices` itself rather than this process writing to the
    terminal — stdout is the JSON channel, so the human-facing lines
    `resolve_gate_scope` emits are captured and handed back to be printed in
    the caller's own order.

    An unresolvable scope exits `EXIT_UNEXAMINABLE` with the reason on the JSON,
    not a traceback: the caller has its own sentence for "cannot determine what
    to examine" and needs the message, not a Python stack.
    """
    parser = argparse.ArgumentParser(
        prog="gate_scope_json.py --emit-scope-json",
        description="Resolve a gate run's scope and print it as JSON.",
    )
    parser.add_argument("--emit-scope-json", action="store_true", required=True)
    parser.add_argument("--repo", default=None)
    # No `choices=`: an unknown scope is rejected by `resolve_gate_scope`, which
    # owns that rule and whose message says why it matters. Validating it twice
    # is how the two copies start disagreeing.
    parser.add_argument("--scope", dest="diff_scope", default=STAGED)
    parser.add_argument("--base", dest="base_ref", default=DEFAULT_BASE_REF)
    parser.add_argument("--label", default="gate")
    parser.add_argument(
        "--suffix",
        action="append",
        default=[],
        help="File suffix this gate grades, with the dot (repeatable).",
    )
    parser.add_argument(
        "--select-root",
        default=None,
        help="Confine discovered candidates to this directory.",
    )
    parser.add_argument(
        "--select-root-candidate",
        action="append",
        default=[],
        help=(
            "Repo-relative directory to confine discovered candidates to, first "
            "one that exists wins (repeatable). Unlike --select-root this is "
            "resolved against the repository root *this* process derived, so a "
            "caller that does not know the root yet can still express its own "
            "source-root policy."
        ),
    )
    parser.add_argument("files", nargs="*")
    args = parser.parse_args(list(argv) if argv is not None else sys.argv[1:])

    # `git_repo_root()` when unset, exactly as every Python gate does it. A
    # caller that resolves its own root from `cwd` gets a different answer than
    # the Python gates whenever it is not standing at the top level, and the
    # containment guard is then measured against the wrong tree — skipped
    # entirely for a file outside that root (594).
    repo = Path(args.repo).resolve() if args.repo else git_repo_root()
    select_root = Path(args.select_root).resolve() if args.select_root else None
    if select_root is None and repo is not None:
        for candidate in args.select_root_candidate:
            if (repo / candidate).is_dir():
                select_root = (repo / candidate).resolve()
                break
        else:
            select_root = repo if args.select_root_candidate else None
    selector = _suffix_selector(frozenset(args.suffix), select_root)

    # `resolve_gate_scope` prints the examined line (and any submodule notice)
    # as it goes. Captured rather than suppressed: the caller still has to show
    # them, and re-deriving the wording on the other side would reintroduce
    # exactly the duplication this entry point exists to delete.
    captured = io.StringIO()
    try:
        with redirect_stdout(captured):
            run = resolve_gate_scope(
                label=args.label,
                repo=repo,
                diff_scope=args.diff_scope,
                base_ref=args.base_ref,
                # Same reason as the selector above: `resolve()` here hands
                # the caller the symlink's *target*, so a link pointing out of
                # the repository arrives as an ordinary file and the caller's
                # read guard has nothing left to refuse. The link's own path is
                # what git spells and what must be graded.
                explicit_files=[located_path(Path(f)) for f in args.files],
                select=selector,
            )
        source_files = (
            None
            if repo is None or select_root is None
            else [
                str(repo / rel)
                for rel in git_source_paths(repo, select_root)
                if Path(rel).suffix in args.suffix
            ]
        )
    except UnexaminableError as exc:
        json.dump({"error": str(exc), "notices": captured.getvalue().splitlines()}, sys.stdout)
        sys.stdout.write("\n")
        return EXIT_UNEXAMINABLE

    json.dump(
        {
            "notices": captured.getvalue().splitlines(),
            "scope": {
                "diff_scope": run.scope.diff_scope,
                "base_ref": run.scope.base_ref,
                "description": run.scope.description,
                "degraded": run.scope.degraded,
                "includes_untracked": run.scope.includes_untracked,
            },
            # The root every check downstream must measure against — the
            # caller does not re-derive it.
            "repo_root": str(repo) if repo is not None else None,
            "select_root": str(select_root) if select_root is not None else None,
            # What a gate reading other files for context may read: the
            # suffixed files under `select_root` that git does not ignore.
            "source_files": source_files,
            "files": [str(path) for path in run.files],
            "untracked": sorted(run.untracked),
            # Graded whole: git changed them but produced no usable diff.
            "undiffable": sorted(run.numstat.undiffable),
            "additions": {rel: sorted(lines) for rel, lines in run.additions.items()},
            # (added, deleted) per relpath — the caller's "is this file growing?"
            "counts": {rel: list(pair) for rel, pair in run.numstat.counts.items()},
        },
        sys.stdout,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(emit_scope_json())
