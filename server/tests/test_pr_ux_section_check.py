"""The PR UX check: a PR touching a user-facing surface says what it is for."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / ".github" / "scripts" / "pr_ux_section_check.py"
_spec = importlib.util.spec_from_file_location("pr_ux_section_check", _SCRIPT)
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)

PAGE = ["client/src/pages/MemoryPage.tsx"]
TEMPLATE = (
    Path(__file__).resolve().parents[2] / ".github" / "pull_request_template.md"
).read_text()

FILLED = """## Summary
x

## User-facing surfaces
- **Question:** which tickets need me right now, and why?
- **Action:** each ticket links to its detail pane
- **Real data:** 138 findings on the sandbox; 53 on live tickets, grouped into 12 cards

## Verification
"""


@pytest.mark.parametrize(
    ("path", "surface"),
    [
        ("client/src/pages/MemoryPage.tsx", True),
        ("client/src/components/reader/Reader.css", True),
        ("client/src/components/__tests__/X.test.tsx", False),
        ("client/src/lib/monitorFindings.ts", False),
        ("server/loregarden/api/memory.py", False),
    ],
)
def test_what_counts_as_a_surface(path, surface):
    assert check.is_surface(path) is surface


def test_a_pr_with_no_surface_needs_no_section():
    assert check.problems("", ["server/loregarden/api/memory.py"]) == []


def test_a_filled_section_passes():
    assert check.problems(FILLED, PAGE) == []


def test_the_untouched_template_fails_every_field():
    found = check.problems(TEMPLATE, PAGE)

    assert len(found) == 3
    assert all("placeholder" in problem for problem in found)


def test_a_missing_section_fails():
    assert "no '## User-facing surfaces' section" in check.problems("## Summary\nx", PAGE)[0]


def test_real_data_needs_a_measured_number():
    body = FILLED.replace(
        "138 findings on the sandbox; 53 on live tickets, grouped into 12 cards",
        "looked great on the sandbox, much better",
    )

    [problem] = check.problems(body, PAGE)

    assert "no number" in problem


def test_a_commented_out_answer_does_not_count():
    body = FILLED.replace(
        "- **Action:** each ticket links to its detail pane",
        "<!-- - **Action:** each ticket links to its detail pane -->",
    )

    [problem] = check.problems(body, PAGE)

    assert "'Action'" in problem


def test_the_no_visible_change_waiver_needs_a_reason():
    ok = "## User-facing surfaces\n- **No user-visible change:** split the file; same DOM, same styles\n"
    thin = "## User-facing surfaces\n- **No user-visible change:** refactor\n"

    assert check.problems(ok, PAGE) == []
    assert "real reason" in check.problems(thin, PAGE)[0]


def test_the_cli_exits_non_zero_and_names_the_problem(tmp_path, capsys):
    body = tmp_path / "body.md"
    body.write_text(TEMPLATE)
    changed = tmp_path / "changed.txt"
    changed.write_text("client/src/pages/MemoryPage.tsx\nserver/x.py\n")

    assert check.main(["--body", str(body), "--changed", str(changed)]) == 1
    assert "::error::" in capsys.readouterr().out
