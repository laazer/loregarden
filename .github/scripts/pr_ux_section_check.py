#!/usr/bin/env python3
"""A PR that changes a user-facing surface says what the surface is for.

Most client work reaches main as a direct PR, not through the ticket pipeline
(48 of 64 client commits in Sept 2026), so the `ui-design` and `visual_qa`
stages never see it. This check is the one place every UI change passes.

A PR touching `client/src/pages` or `client/src/components` must carry a
"User-facing surfaces" section with:

- **Question:** what the operator asks this surface;
- **Action:** the link, button or filter they use next;
- **Real data:** what it showed on `task sandbox` (or `task sandbox -- --seeded`
  where the live database is out of reach), with at least one number — a
  measured count, not "looks good";

or `**No user-visible change:** <reason>` for a refactor nobody can see.

It checks that the answers exist and are not the template's placeholders. It
cannot check they are true; that is the reviewer's job, as with `silent-ok:`.

    pr_ux_section_check.py --body BODY_FILE --changed CHANGED_FILES_LIST
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SURFACE_DIRS = ("client/src/pages/", "client/src/components/")
SURFACE_SUFFIXES = (".tsx", ".css")
FIELDS = ("Question", "Action", "Real data")
MIN_ANSWER = 10
MIN_WAIVER = 20

_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_SECTION = re.compile(r"^#{2,3}\s*User-facing surfaces\s*$", re.IGNORECASE | re.MULTILINE)
_NEXT_HEADING = re.compile(r"^#{1,3}\s", re.MULTILINE)
_FIELD = re.compile(
    r"^\s*[-*]?\s*\*\*(?P<name>[^*]+?)\s*:?\*\*\s*:?\s*(?P<value>.*)$", re.MULTILINE
)


def is_surface(path: str) -> bool:
    if not path.startswith(SURFACE_DIRS) or not path.endswith(SURFACE_SUFFIXES):
        return False
    return "/__tests__/" not in path and ".test." not in path


def _section(body: str) -> str | None:
    text = _COMMENT.sub("", body)
    start = _SECTION.search(text)
    if start is None:
        return None
    rest = text[start.end() :]
    end = _NEXT_HEADING.search(rest)
    return rest[: end.start()] if end else rest


def _answered(value: str, minimum: int) -> bool:
    value = value.strip()
    return len(value) >= minimum and not value.startswith("<")


def problems(body: str, changed: list[str]) -> list[str]:
    """What is missing, or [] when the PR passes."""
    surfaces = [path for path in changed if is_surface(path)]
    if not surfaces:
        return []
    section = _section(body)
    named = f"This PR changes {len(surfaces)} user-facing file(s), e.g. {surfaces[0]}"
    if section is None:
        return [f"{named}, and its description has no '## User-facing surfaces' section."]

    fields = {m["name"].strip().lower(): m["value"] for m in _FIELD.finditer(section)}
    waiver = fields.get("no user-visible change")
    if waiver is not None:
        if _answered(waiver, MIN_WAIVER):
            return []
        return [f"'No user-visible change' needs a real reason ({MIN_WAIVER}+ characters)."]

    missing = []
    for field in FIELDS:
        value = fields.get(field.lower())
        if value is None or not _answered(value, MIN_ANSWER):
            missing.append(f"'{field}' is missing or still the template placeholder.")
    real = fields.get("real data", "")
    if _answered(real, MIN_ANSWER) and not re.search(r"\d", real):
        missing.append(
            "'Real data' has no number in it — say what `task sandbox` (or `-- --seeded`) showed "
            "(how many rows, the largest case), not that it looked fine."
        )
    return [f"{named}. {m}" for m in missing]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--body", type=Path, required=True)
    parser.add_argument("--changed", type=Path, required=True)
    args = parser.parse_args(argv)
    body = args.body.read_text(encoding="utf-8")
    changed = [line.strip() for line in args.changed.read_text(encoding="utf-8").splitlines()]
    found = problems(body, [path for path in changed if path])
    if not found:
        print("PR UX section: ok")
        return 0
    for problem in found:
        print(f"::error::{problem}")
    print(
        "\nFill in the 'User-facing surfaces' section (see .github/pull_request_template.md). "
        "Run `task sandbox` to see the change on a copy of production data, or "
        "`task sandbox -- --seeded` for its production-shaped scenario."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
