#!/usr/bin/env python3
"""A PR that fixes a bad state says how it covers the whole state, not just one row.

A fix has four jobs: repair rows already in the state, catch rows that enter it
anyway, prevent entry, and leave a learning. Most fixes here did the third and
sometimes the first. The sweeps that did the second could not tell a guard
failing from a guard working (`server/loregarden/services/invariants.py`).

A PR whose title is a conventional `fix` (`fix:`, `fix(scope):`, `fix!:`), or
whose description already carries the section, must have a "Fix coverage"
section with:

- **Existing:** how rows already in the state were found and repaired, or how
  you checked there are none (a query and its count);
- **Re-entry:** the registered invariant that catches the state coming back,
  in backticks (`every_block_is_settled`), or `no detector: <reason>`;
- **Prevention:** the test proving the state cannot be entered, as
  `path/to/test_file.py::test_name`;
- **Learning:** why the state was reachable, as a rule the next change can use;

or `**Not a state bug:** <reason>` for a fix that leaves no state behind (a
crash, a wrong label, a typo).

A tree PR's section holds one `####` block per ticket; each is checked alone.
Unlike the UX section, two answers are checked against the checkout: the
invariant must be registered and the test must exist. The rest it cannot check
are true; that is the reviewer's job, as with `silent-ok:`.

    pr_fix_coverage_check.py --title TITLE --body BODY_FILE [--root REPO]

Standard library only, Python 3.9: the precheck hook loads this under the
system `python3`.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

FIELDS = ("Existing", "Re-entry", "Prevention", "Learning")
WAIVER = "not a state bug"
NO_DETECTOR = "no detector:"
MIN_ANSWER = 10
MIN_WAIVER = 20
REGISTRY = Path("server") / "loregarden" / "services" / "invariant_registry.py"

_FIX_TITLE = re.compile(r"^fix(\([^)]*\))?!?:", re.IGNORECASE)
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_SECTION = re.compile(r"^#{2,3}\s*Fix coverage\s*$", re.IGNORECASE | re.MULTILINE)
_NEXT_HEADING = re.compile(r"^#{1,3}\s", re.MULTILINE)
_BLOCK = re.compile(r"^####\s+(?P<name>.+)$", re.MULTILINE)
_FIELD = re.compile(
    r"^\s*[-*]?\s*\*\*(?P<name>[^*]+?)\s*:?\*\*\s*:?\s*(?P<value>.*)$", re.MULTILINE
)
_BACKTICKED = re.compile(r"`([a-z][a-z0-9_]*)`")
_TEST_REF = re.compile(r"(?P<path>[\w./-]+\.py)::(?P<name>test_\w+)")


def is_fix_title(title: str) -> bool:
    return bool(_FIX_TITLE.match(title.strip()))


def _section(body: str) -> str | None:
    text = _COMMENT.sub("", body)
    start = _SECTION.search(text)
    if start is None:
        return None
    rest = text[start.end() :]
    end = _NEXT_HEADING.search(rest)
    return rest[: end.start()] if end else rest


def _blocks(section: str) -> list[tuple[str, str]]:
    """(label, text) per `####` ticket block, or the whole section as one."""
    heads = list(_BLOCK.finditer(section))
    if not heads:
        return [("", section)]
    blocks = []
    for i, head in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(section)
        blocks.append((head["name"].strip(), section[head.end() : end]))
    return blocks


def _answered(value: str, minimum: int) -> bool:
    value = value.strip()
    return len(value) >= minimum and not value.startswith("<")


def registered_invariants(root: Path) -> set[str]:
    """Every `name=` the registry passes to `Invariant(...)`, read without importing it."""
    path = root / REGISTRY
    if not path.is_file():
        return set()
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if (
            node.__class__ is ast.Call
            and node.func.__class__ is ast.Name
            and node.func.id == "Invariant"
        ):
            for keyword in node.keywords:
                if keyword.arg == "name" and keyword.value.__class__ is ast.Constant:
                    names.add(str(keyword.value.value))
    return names


def _test_exists(root: Path, path: str, name: str) -> bool:
    file = root / path
    if not file.is_file():
        return False
    pattern = re.compile(rf"^\s*(async\s+)?def\s+{re.escape(name)}\s*\(", re.MULTILINE)
    return bool(pattern.search(file.read_text(encoding="utf-8")))


def _reentry_problem(value: str, invariants: set[str]) -> str | None:
    if value.strip().lower().startswith(NO_DETECTOR):
        reason = value.strip()[len(NO_DETECTOR) :]
        if _answered(reason, MIN_WAIVER):
            return None
        return f"'Re-entry: {NO_DETECTOR}' needs a real reason ({MIN_WAIVER}+ characters)."
    named = _BACKTICKED.findall(value)
    if not named:
        return (
            "'Re-entry' names no invariant: put its registered name in backticks, "
            f"or write '{NO_DETECTOR} <reason>'."
        )
    unknown = [name for name in named if name not in invariants]
    if len(unknown) == len(named):
        return (
            f"'Re-entry' names {', '.join(f'`{n}`' for n in named)}, which is not registered in "
            f"{REGISTRY}. Register the invariant there, or fix the name."
        )
    return None


def _prevention_problem(value: str, root: Path) -> str | None:
    refs = list(_TEST_REF.finditer(value))
    if not refs:
        return "'Prevention' names no test: give it as `path/to/test_file.py::test_name`."
    missing = [
        f"{m['path']}::{m['name']}" for m in refs if not _test_exists(root, m["path"], m["name"])
    ]
    if len(missing) == len(refs):
        return f"'Prevention' names {', '.join(missing)}, which does not exist in this checkout."
    return None


def _block_problems(text: str, root: Path, invariants: set[str]) -> list[str]:
    fields = {m["name"].strip().lower(): m["value"] for m in _FIELD.finditer(text)}
    waiver = fields.get(WAIVER)
    if waiver is not None:
        if _answered(waiver, MIN_WAIVER):
            return []
        return [f"'Not a state bug' needs a real reason ({MIN_WAIVER}+ characters)."]
    found = []
    for field in FIELDS:
        value = fields.get(field.lower())
        if value is None or not _answered(value, MIN_ANSWER):
            found.append(f"'{field}' is missing or still the template placeholder.")
    reentry = fields.get("re-entry", "")
    if _answered(reentry, MIN_ANSWER):
        problem = _reentry_problem(reentry, invariants)
        if problem:
            found.append(problem)
    prevention = fields.get("prevention", "")
    if _answered(prevention, MIN_ANSWER):
        problem = _prevention_problem(prevention, root)
        if problem:
            found.append(problem)
    return found


def problems(title: str, body: str, root: Path) -> list[str]:
    """What is missing, or [] when the PR passes."""
    section = _section(body)
    if section is None:
        if is_fix_title(title):
            return [
                f"This PR is titled as a fix ({title.strip()!r}) and has no '## Fix coverage' section."
            ]
        return []
    invariants = registered_invariants(root)
    found = []
    for label, text in _blocks(section):
        prefix = f"{label}: " if label else ""
        found += [prefix + problem for problem in _block_problems(text, root, invariants)]
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--title", required=True)
    parser.add_argument("--body", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    found = problems(args.title, args.body.read_text(encoding="utf-8"), args.root)
    if not found:
        print("PR fix coverage: ok")
        return 0
    for problem in found:
        print(f"::error::{problem}")
    print(
        "\nFill in the 'Fix coverage' section (see .github/pull_request_template.md and "
        "CLAUDE.md 'Fixing a bad state'), or '**Not a state bug:** <reason>'."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
