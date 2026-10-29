"""The "PR fix coverage" check: a fix says how it covers the whole bad state."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "pr_fix_coverage_check", _ROOT / ".github" / "scripts" / "pr_fix_coverage_check.py"
)
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)

#: A test that exists in this checkout, and an invariant this checkout registers.
REAL_TEST = "server/tests/test_invariants.py::test_invariant_names_are_unique"
REAL_INVARIANT = "every_block_is_settled"


def _body(
    *,
    existing: str = "0 rows: `select count(*) from tickets where block_kind is null` on live",
    reentry: str = f"`{REAL_INVARIANT}` reports a block written around settle_block",
    prevention: str = REAL_TEST,
    learning: str = "a block written outside settle_block reads as handled when it was not",
) -> str:
    return (
        "## Summary\nx\n\n## Fix coverage\n"
        f"- **Existing:** {existing}\n- **Re-entry:** {reentry}\n"
        f"- **Prevention:** {prevention}\n- **Learning:** {learning}\n\n## Verification\n"
    )


def _problems(title: str, body: str) -> list[str]:
    return check.problems(title, body, _ROOT)


@pytest.mark.parametrize("title", ["fix: x", "fix(publish): x", "fix!: x", "Fix(db): x"])
def test_a_fix_title_needs_the_section(title):
    assert _problems(title, "## Summary\nx\n") != []


@pytest.mark.parametrize(
    "title", ["feat: x", "lg-x-1: a fix for y", "refactor: fix-ish", "fixup: x"]
)
def test_other_titles_need_no_section(title):
    assert _problems(title, "## Summary\nx\n") == []


def test_a_complete_section_passes():
    assert _problems("fix: x", _body()) == []


def test_a_present_section_is_checked_whatever_the_title():
    """Tree PRs are titled `<external_id>: <title>`; their section is still checked."""
    assert _problems("lg-x-1: y", _body(learning="<why>")) != []


def test_placeholders_fail():
    found = _problems("fix: x", _body(existing="<how>", learning="<why>"))
    assert len(found) == 2


def test_an_unregistered_invariant_fails():
    found = _problems("fix: x", _body(reentry="`no_such_invariant` catches it coming back"))
    assert any("not registered" in p for p in found)


def test_reentry_with_no_invariant_named_fails():
    found = _problems("fix: x", _body(reentry="the reconciler will notice it eventually"))
    assert any("names no invariant" in p for p in found)


def test_no_detector_with_a_reason_passes():
    reentry = "no detector: the state lives in git refs on the host, which nothing scans"
    assert _problems("fix: x", _body(reentry=reentry)) == []


def test_no_detector_without_a_reason_fails():
    assert _problems("fix: x", _body(reentry="no detector: n/a lol")) != []


def test_a_prevention_test_that_does_not_exist_fails():
    found = _problems("fix: x", _body(prevention="server/tests/test_invariants.py::test_nope"))
    assert any("does not exist" in p for p in found)


def test_prevention_that_names_no_test_fails():
    found = _problems("fix: x", _body(prevention="the new guard in run_completion covers it"))
    assert any("names no test" in p for p in found)


def test_the_waiver_passes_with_a_reason_and_fails_without():
    waived = "## Fix coverage\n- **Not a state bug:** a typo in a toast; nothing is persisted\n"
    assert _problems("fix: x", waived) == []
    assert _problems("fix: x", "## Fix coverage\n- **Not a state bug:** typo\n") != []


def test_a_tree_section_checks_each_ticket_block_alone():
    good = _body().split("## Fix coverage\n", 1)[1].split("\n\n## Verification", 1)[0]
    body = (
        "## Fix coverage\n\n#### lg-a-1: good\n" + good + "\n\n#### lg-b-2: bad\n"
        "- **Existing:** <how>\n"
    )
    found = _problems("lg-root-3: tree", body)
    assert found and all(p.startswith("lg-b-2: bad: ") for p in found)


def test_the_template_comment_is_not_an_answer():
    body = (
        "## Fix coverage\n<!-- - **Not a state bug:** a typo in a toast; nothing is persisted -->\n"
    )
    assert _problems("fix: x", body) != []


def test_the_registry_is_read_without_importing_it():
    assert REAL_INVARIANT in check.registered_invariants(_ROOT)


#: A jest test that exists in this checkout, named the way the check reads it.
REAL_JEST_TEST = (
    'client/src/lib/__tests__/usabilityCheck.test.ts::"passes a name with three short tags"'
)
_NO_DETECTOR = "no detector: the state lives in a browser store the server never sees"
_JEST_FILE = "client/src/__tests__/guard.test.ts"
_JEST_SOURCE = """
describe("guard", () => {
  it("refuses a write with no owner", () => {});
  test('keeps the "last" row', () => {});
  it(`holds a template name with no placeholders`, () => {});
  it.skip("is skipped for now", () => {});
  it.todo("is not written yet");
  it(`renders ${kind} rows`, () => {});
  it.each([1, 2])("handles %s", () => {});
});
"""


@pytest.fixture
def jest_root(tmp_path: Path) -> Path:
    file = tmp_path / _JEST_FILE
    file.parent.mkdir(parents=True)
    file.write_text(_JEST_SOURCE, encoding="utf-8")
    (file.parent / "plain.test.ts").write_text(
        'it("refuses a write", () => {});\n', encoding="utf-8"
    )
    (tmp_path / "client/src/lib").mkdir()
    (tmp_path / "client/src/lib/guard.test.ts").write_text(_JEST_SOURCE, encoding="utf-8")
    return tmp_path


def _jest_problems(root: Path, prevention: str) -> list[str]:
    return check.problems("fix: x", _body(reentry=_NO_DETECTOR, prevention=prevention), root)


def test_a_jest_test_in_this_checkout_passes():
    assert _problems("fix: x", _body(prevention=f"`{REAL_JEST_TEST}`")) == []


@pytest.mark.parametrize(
    "name",
    [
        "refuses a write with no owner",
        'keeps the \\"last\\" row',
        "holds a template name with no placeholders",
    ],
)
def test_a_literally_named_jest_test_passes(jest_root, name):
    assert _jest_problems(jest_root, f'{_JEST_FILE}::"{name}"') == []


def test_a_jest_file_with_no_such_test_fails(jest_root):
    found = _jest_problems(jest_root, 'client/src/__tests__/plain.test.ts::"refuses a writ"')
    assert any("does not exist" in p for p in found)


def test_a_jest_file_that_does_not_exist_fails(jest_root):
    found = _jest_problems(jest_root, 'client/src/__tests__/nope.test.ts::"refuses a write"')
    assert any("does not exist" in p for p in found)


@pytest.mark.parametrize("name", ["renders table rows", "renders ${kind} rows", "handles %s"])
def test_a_jest_test_named_at_runtime_cannot_be_verified(jest_root, name):
    found = _jest_problems(jest_root, f'{_JEST_FILE}::"{name}"')
    assert any("cannot be verified" in p for p in found)


def test_an_unverifiable_jest_test_fails_beside_one_that_exists(jest_root):
    prevention = f'{_JEST_FILE}::"refuses a write with no owner", {_JEST_FILE}::"handles %s"'
    assert any("cannot be verified" in p for p in _jest_problems(jest_root, prevention))


def test_a_jest_each_table_alone_cannot_be_verified(jest_root):
    file = jest_root / "client/src/__tests__/each.test.ts"
    file.write_text('it.each([1, 2])("handles %s", () => {});\n', encoding="utf-8")
    found = _jest_problems(jest_root, 'client/src/__tests__/each.test.ts::"handles 1"')
    assert any("cannot be verified" in p for p in found)


@pytest.mark.parametrize("name", ["is skipped for now", "is not written yet"])
def test_a_skipped_jest_test_fails(jest_root, name):
    assert any("skipped" in p for p in _jest_problems(jest_root, f'{_JEST_FILE}::"{name}"'))


def test_a_jest_file_jest_does_not_collect_fails(jest_root):
    reference = 'client/src/lib/guard.test.ts::"refuses a write with no owner"'
    found = _jest_problems(jest_root, reference)
    assert any("testMatch" in p for p in found)


def test_an_unquoted_jest_name_says_to_quote_it(jest_root):
    found = _jest_problems(jest_root, f"{_JEST_FILE}::refuses a write with no owner")
    assert any("without quoting" in p for p in found)


def test_a_jest_reference_does_not_need_the_test_prefix_or_a_def(jest_root):
    """The Python branch still wants a `def test_…`; a jest name is just its string."""
    found = _jest_problems(jest_root, "server/tests/test_invariants.py::refuses_a_write")
    assert any("names no test" in p for p in found)
