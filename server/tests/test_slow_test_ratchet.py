"""The inherited slow-test lists may only shrink.

`tests/slow_test_ratchet.py` (pytest) and `client/src/test/slowTestRatchet.json`
(jest) exempt the tests that were already over the per-test ceiling when it
landed. A branch that adds an entry has found a new way to be slow and listed it
instead of saying why; that fails here. A branch that removes one passes.

Compared against where this branch left main, not main's tip, so a branch that is
merely behind is not accused of adding what main removed. Needs `origin/main` and
its history, as test_migration_registry does; CI checks out with full depth.
"""

from __future__ import annotations

import ast
import json

import pytest
from loregarden.db.shared_database_guard import SHIPPED_REF
from loregarden.services.git_subprocess import run_git
from tests.repo_paths import REPO_ROOT
from tests.slow_test_ratchet import INHERITED

SERVER_RATCHET = "server/tests/slow_test_ratchet.py"
CLIENT_RATCHET = "client/src/test/slowTestRatchet.json"


def server_entries(source: str) -> frozenset[str]:
    """The ids in a version of slow_test_ratchet.py: the set literal `INHERITED` wraps."""
    for node in ast.walk(ast.parse(source)):
        if (
            type(node) is ast.AnnAssign
            and type(node.target) is ast.Name
            and node.target.id == "INHERITED"
            and type(node.value) is ast.Call
        ):
            return frozenset(ast.literal_eval(node.value.args[0]))
    raise ValueError(f"{SERVER_RATCHET} has no `INHERITED: frozenset[str] = frozenset({{...}})`")


def client_entries(source: str) -> frozenset[str]:
    return frozenset(json.loads(source)["inherited"])


PARSERS = {SERVER_RATCHET: server_entries, CLIENT_RATCHET: client_entries}


def added_entries(base: frozenset[str] | None, current: frozenset[str]) -> list[str]:
    """Entries this version has that the base did not. No base: the list is new, nothing added."""
    if base is None:
        return []
    return sorted(current - base)


def _at_fork_point(path: str) -> str | None:
    # branch-rule: no branch adds an entry to the inherited slow-test lists
    fork_point = run_git(
        ["merge-base", "HEAD", SHIPPED_REF],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    shown = run_git(
        ["show", f"{fork_point}:{path}"], cwd=REPO_ROOT, check=False, capture_output=True, text=True
    )
    if shown.returncode != 0:
        # Absent at the fork point is the one expected failure (the branch that
        # introduces the list); anything else is a broken check, not a pass.
        assert "does not exist" in shown.stderr or "exists on disk" in shown.stderr, shown.stderr
        return None
    return shown.stdout


@pytest.mark.parametrize("path", sorted(PARSERS))
def test_no_branch_adds_an_inherited_slow_test(path):
    parse = PARSERS[path]
    base_source = _at_fork_point(path)
    base = None if base_source is None else parse(base_source)

    added = added_entries(base, parse((REPO_ROOT / path).read_text()))

    assert not added, (
        f"{path} gained {added}. The list only shrinks: make the test faster, or mark it "
        "(@pytest.mark.slow(reason=...) / slowTest(reason, ...)) and say why it cannot be."
    )


def test_the_conftest_reads_the_same_list_this_check_parses():
    assert server_entries((REPO_ROOT / SERVER_RATCHET).read_text()) == INHERITED


def test_adding_an_entry_fails_and_removing_one_passes():
    base = frozenset({"a", "b"})

    assert added_entries(base, frozenset({"a", "b", "c"})) == ["c"]
    assert added_entries(base, frozenset({"a"})) == []
    assert added_entries(None, frozenset({"a"})) == []
