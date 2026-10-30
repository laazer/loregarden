"""Live merge-base SHA for a worktree parent (R1–R4, R8).

Git-only: no Artifact row, no Worktree row. Graphs are built here; do not
reuse make_repo's seed as the advance-parent history.
"""

from __future__ import annotations

import ast
import inspect
import logging
import re
from pathlib import Path
from unittest import mock

import pytest
from loregarden.services import artifact_service, worktree_lifecycle
from loregarden.services.publish_ref import comparison_ref
from loregarden.services.worktree_lifecycle import _base_ref
from tests.worktree_helpers import commit_on, git, make_repo

_PARENT_REF_PATH = Path(__file__).resolve().parents[1] / "loregarden" / "services" / "parent_ref.py"
_SHA = re.compile(r"[0-9a-f]{40}")
_INTEGRATION = "integration/lg-root-1608"
_TICKET = "loregarden/lg-child-1608"


def _parent_ref():
    from loregarden.services import parent_ref

    return parent_ref


def _init_repo(path: Path) -> Path:
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "t@example.com")
    git(path, "config", "user.name", "Test")
    return path


def _commit(path: Path, rel: str, content: str, message: str) -> str:
    dest = path / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content)
    git(path, "add", "--", rel)
    git(path, "commit", "-q", "-m", message)
    return git(path, "rev-parse", "HEAD").stdout.strip()


def _advance_parent_graph(tmp_path: Path) -> tuple[Path, str, str]:
    """main has client/package.json; integration adds sibling.txt; ticket
    commits ticket.py; parent then advances with later_sibling.txt unmerged.

    Returns (ticket checkout, parent branch name, HEAD SHA at cut).
    """
    repo = _init_repo(tmp_path / "repo")
    _commit(repo, "client/package.json", '{"name":"app"}\n', "main package")
    git(repo, "checkout", "-q", "-b", _INTEGRATION)
    _commit(repo, "sibling.txt", "sibling\n", "sibling on integration")
    checkout = tmp_path / "ticket"
    git(repo, "worktree", "add", "-q", "-b", _TICKET, str(checkout), _INTEGRATION)
    cut_sha = git(checkout, "rev-parse", "HEAD").stdout.strip()
    _commit(checkout, "ticket.py", "print('ticket')\n", "ticket work")
    git(repo, "checkout", "-q", _INTEGRATION)
    _commit(repo, "later_sibling.txt", "later\n", "later sibling")
    git(repo, "checkout", "-q", "main")
    return checkout, _INTEGRATION, cut_sha


def _imported_names(source: str) -> set[str]:
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):  # py-org: allow-isinstance — AST node type
            for alias in node.names:
                names.add(alias.name)
                names.add(alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom):  # py-org: allow-isinstance — AST node type
            if node.module:
                names.add(node.module)
                names.update(alias.name for alias in node.names)
                names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def _call_names(source: str) -> set[str]:
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):  # py-org: allow-isinstance — AST node type
            continue
        func = node.func
        if isinstance(func, ast.Name):  # py-org: allow-isinstance — AST node type
            found.add(func.id)
        elif isinstance(func, ast.Attribute):  # py-org: allow-isinstance — AST node type
            found.add(func.attr)
            if isinstance(func.value, ast.Name):  # py-org: allow-isinstance — AST node type
                found.add(f"{func.value.id}.{func.attr}")
    return found


def _public_functions(source: str) -> list[str]:
    return [
        node.name
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef)  # py-org: allow-isinstance — AST node type
        and not node.name.startswith("_")
    ]


def _attribute_names(source: str) -> set[str]:
    tree = ast.parse(source)
    return {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)  # py-org: allow-isinstance — AST node type
    }


# ---------------------------------------------------------------------------
# R1 — parent_ref.py census
# ---------------------------------------------------------------------------


def test_parent_ref_module_exports_exactly_the_two_git_functions():
    """AC-R1.1 Public names are parent_comparison_ref and live_diff_base."""
    source = _PARENT_REF_PATH.read_text(encoding="utf-8")
    assert _public_functions(source) == ["parent_comparison_ref", "live_diff_base"]
    module = _parent_ref()
    assert callable(module.parent_comparison_ref)
    assert callable(module.live_diff_base)


def test_parent_ref_is_git_only_and_does_not_import_orm_or_artifacts():
    """AC-R1.2 Load-bearing imports only; no Worktree, artifacts, or subprocess.run."""
    source = _PARENT_REF_PATH.read_text(encoding="utf-8")
    imported = _imported_names(source)
    calls = _call_names(source)
    assert "comparison_ref" in imported
    assert "merge_base" in imported
    assert "run_git" in imported
    assert "artifact_service" not in imported
    assert "worktree_lifecycle" not in imported
    assert "Worktree" not in imported
    assert "subprocess" not in imported
    assert "subprocess.run" not in calls
    assert "_git" not in imported
    assert "merge_base" not in _attribute_names(source)


# ---------------------------------------------------------------------------
# R2 — parent_comparison_ref naming loop
# ---------------------------------------------------------------------------


def test_an_integration_parent_is_the_local_ref_even_when_origin_holds_a_squash(tmp_path):
    """AC-R2.1 Mirror the stale-squash fixture: origin/integration is a different SHA."""
    repo = make_repo(tmp_path)
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-q", "origin", "main")
    git(repo, "branch", _INTEGRATION, "main")
    commit_on(repo, _INTEGRATION, "shared.txt", "the tree's version\n")
    tree = git(repo, "rev-parse", f"{_INTEGRATION}^{{tree}}").stdout.strip()
    squash = git(repo, "commit-tree", tree, "-p", "main", "-m", "publish").stdout.strip()
    git(repo, "push", "-q", "origin", f"{squash}:refs/heads/{_INTEGRATION}")
    git(repo, "fetch", "-q", "origin")
    local = git(repo, "rev-parse", _INTEGRATION).stdout.strip()
    planted = git(repo, "rev-parse", f"origin/{_INTEGRATION}").stdout.strip()
    assert local != planted, "fixture: origin/integration must be a different SHA"
    checkout = tmp_path / "ticket"
    git(repo, "worktree", "add", "-q", "-b", _TICKET, str(checkout), _INTEGRATION)

    named = _parent_ref().parent_comparison_ref(checkout, _INTEGRATION)

    assert named == _INTEGRATION
    assert named == comparison_ref(_INTEGRATION)
    assert named != f"origin/{_INTEGRATION}"
    assert not _SHA.fullmatch(named or "")


def test_a_main_parent_prefers_origin_main_then_main(tmp_path):
    """AC-R2.2 dict.fromkeys((comparison_ref('main'), 'main')) order."""
    repo = make_repo(tmp_path)
    assert _parent_ref().parent_comparison_ref(repo, "main") == "main"

    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-q", "origin", "main")
    git(repo, "fetch", "-q", "origin")

    named = _parent_ref().parent_comparison_ref(repo, "main")
    assert named == "origin/main"
    assert named == comparison_ref("main")
    assert not _SHA.fullmatch(named)


def test_a_missing_parent_ref_is_silent_none(tmp_path, caplog):
    """AC-R2.3 Missing ref → None, no log, no exception."""
    repo = make_repo(tmp_path)
    with caplog.at_level(logging.DEBUG, logger="loregarden.services.parent_ref"):
        named = _parent_ref().parent_comparison_ref(repo, "release/deleted-after-the-merge")
    assert named is None
    assert caplog.records == []


# ---------------------------------------------------------------------------
# R3 — live_diff_base SHA + fail-closed
# ---------------------------------------------------------------------------


def test_live_diff_base_equals_merge_base_and_is_not_the_parent_tip(tmp_path):
    """AC-R3.1 + AC-R3.2 Advance-parent graph, then merge parent into the ticket."""
    checkout, parent, cut_sha = _advance_parent_graph(tmp_path)
    parent_ref = _parent_ref()
    named = parent_ref.parent_comparison_ref(checkout, parent)
    assert named is not None
    expected = git(checkout, "merge-base", "HEAD", named).stdout.strip()
    parent_tip = git(checkout, "rev-parse", named).stdout.strip()

    result = parent_ref.live_diff_base(checkout, parent)

    assert result == expected
    assert _SHA.fullmatch(result or "")
    assert result != parent_tip
    assert result != parent
    assert result != named
    assert result != f"origin/{parent}"
    assert not (result or "").startswith("origin/")

    git(checkout, "merge", "-q", "--no-edit", named)
    after = parent_ref.live_diff_base(checkout, parent)
    new_base = git(checkout, "merge-base", "HEAD", named).stdout.strip()
    assert after == new_base
    assert after == parent_tip
    assert after != cut_sha
    assert _SHA.fullmatch(after or "")


def test_live_diff_base_returns_none_and_warns_when_the_parent_ref_is_missing(tmp_path, caplog):
    """AC-R3.3 Missing ref → None plus WARNING, no exception."""
    repo = make_repo(tmp_path)
    with caplog.at_level(logging.WARNING, logger="loregarden.services.parent_ref"):
        result = _parent_ref().live_diff_base(repo, "release/deleted-after-the-merge")
    assert result is None
    assert any(record.levelno == logging.WARNING for record in caplog.records)


def test_live_diff_base_returns_none_and_warns_on_unrelated_histories(tmp_path, caplog):
    """AC-R3.3 Unrelated histories (merge_base returns '') → None plus WARNING."""
    repo = _init_repo(tmp_path / "repo")
    _commit(repo, "client/package.json", "{}\n", "main")
    other = _init_repo(tmp_path / "other")
    _commit(other, "other.txt", "x\n", "other")
    git(repo, "remote", "add", "other", str(other))
    git(repo, "fetch", "-q", "other")
    git(repo, "branch", "unrelated", "other/main")

    with caplog.at_level(logging.WARNING, logger="loregarden.services.parent_ref"):
        result = _parent_ref().live_diff_base(repo, "unrelated")
    assert result is None
    assert any(record.levelno == logging.WARNING for record in caplog.records)


def test_live_diff_base_swallows_runtime_error_from_merge_base_and_warns(tmp_path, caplog):
    """AC-R3.3 merge_base raises RuntimeError → None plus WARNING, no exception."""
    repo = make_repo(tmp_path)
    with (
        mock.patch(
            "loregarden.services.parent_ref.merge_base",
            side_effect=RuntimeError("git merge-base failed"),
        ),
        caplog.at_level(logging.WARNING, logger="loregarden.services.parent_ref"),
    ):
        result = _parent_ref().live_diff_base(repo, "main")
    assert result is None
    assert any(record.levelno == logging.WARNING for record in caplog.records)


def test_live_diff_base_does_not_catch_non_runtime_errors(tmp_path):
    """AC-R3 Catch RuntimeError only; other exceptions still raise."""
    repo = make_repo(tmp_path)
    with (
        mock.patch(
            "loregarden.services.parent_ref.merge_base",
            side_effect=ValueError("not a merge-base failure"),
        ),
        pytest.raises(ValueError),
    ):
        _parent_ref().live_diff_base(repo, "main")


# ---------------------------------------------------------------------------
# R4 — worktree_lifecycle._base_ref one-liner
# ---------------------------------------------------------------------------


def test_base_ref_keeps_its_signature_and_retirement_docstring_and_delegates():
    """AC-R4.1 Body is return parent_comparison_ref(path, parent_branch)."""
    assert list(inspect.signature(_base_ref).parameters) == ["path", "parent_branch"]
    doc = inspect.getdoc(_base_ref)
    assert doc is not None
    assert "cut from" in doc
    body = inspect.getsource(_base_ref)
    assert "return parent_comparison_ref(path, parent_branch)" in body
    assert "rev-parse" not in body
    preserved = inspect.getsource(worktree_lifecycle._has_preserved_commits)
    assert "_base_ref(" in preserved
    assert "parent_comparison_ref" not in preserved


def test_worktree_lifecycle_no_longer_imports_comparison_ref():
    """AC-R4 Drop the unused comparison_ref import; naming lives in parent_ref."""
    source = Path(worktree_lifecycle.__file__).read_text(encoding="utf-8")
    imported = _imported_names(source)
    assert "parent_comparison_ref" in imported
    assert "comparison_ref" not in imported


# ---------------------------------------------------------------------------
# R8 — leave-alone census
# ---------------------------------------------------------------------------


def test_artifact_service_does_not_import_worktree_comparison_ref_or_lifecycle():
    """AC-R8.1 artifact_service must not import Worktree, comparison_ref, worktree_lifecycle."""
    source = Path(artifact_service.__file__).read_text(encoding="utf-8")
    imported = _imported_names(source)
    assert "Worktree" not in imported
    assert "comparison_ref" not in imported
    assert "worktree_lifecycle" not in imported
    assert "merge_base" not in _attribute_names(source)


def test_parent_ref_does_not_read_worktree_merge_base():
    """AC-R8.2 parent_ref.py does not mention Worktree.merge_base / import Worktree."""
    source = _PARENT_REF_PATH.read_text(encoding="utf-8")
    assert "Worktree" not in _imported_names(source)
    assert "merge_base" not in _attribute_names(source)


def test_leave_alone_surfaces_do_not_import_parent_ref():
    """AC-R8.3 No Diff-tab / verify-prompt wiring (sibling 1610)."""
    package = Path(__file__).resolve().parents[1] / "loregarden"
    for relative in (
        "api/tickets.py",
        "agents/verify_context.py",
    ):
        source = (package / relative).read_text(encoding="utf-8")
        imported = _imported_names(source)
        assert "parent_ref" not in imported, relative
        assert "live_diff_base" not in imported, relative
        assert "parent_comparison_ref" not in imported, relative
        assert "TicketCheckout" not in imported, relative
    client_src = Path(__file__).resolve().parents[2] / "client" / "src"
    offenders: list[str] = []
    for pattern in ("*.ts", "*.tsx"):
        for path in client_src.rglob(pattern):
            text = path.read_text(encoding="utf-8")
            if (
                "live_diff_base" in text
                or "parent_comparison_ref" in text
                or "TicketCheckout" in text
            ):
                offenders.append(str(path.relative_to(client_src)))
    assert offenders == []


# ---------------------------------------------------------------------------
# test-break — mutations the design suite still lets through
# ---------------------------------------------------------------------------


def test_parent_comparison_ref_is_the_verbatim_rev_parse_loop_not_rev():
    """R2: do not rewrite onto git_merge_noco.rev (that probe is ref^{commit} and a SHA)."""
    source = _PARENT_REF_PATH.read_text(encoding="utf-8")
    assert "dict.fromkeys((comparison_ref(parent_branch), parent_branch))" in source
    assert "rev-parse" in source
    assert "--verify" in source
    assert "--quiet" in source
    assert "cwd=str(repo_root)" in source
    assert "check=False" in source
    assert "{commit}" not in source
    assert "return probe.stdout" not in source


def test_live_diff_base_source_asks_merge_base_for_head_and_the_named_ref():
    """R3: merge_base(repo_root, 'HEAD', named) — never parent_branch, never Exception."""
    source = _PARENT_REF_PATH.read_text(encoding="utf-8")
    head_named = (
        'merge_base(repo_root, "HEAD", named)' in source
        or "merge_base(repo_root, 'HEAD', named)" in source
    )
    assert head_named
    assert "except RuntimeError" in source
    assert "except Exception" not in source
    assert "merge_base(repo_root, 'HEAD', parent_branch)" not in source
    assert 'merge_base(repo_root, "HEAD", parent_branch)' not in source


def test_live_diff_base_uses_the_named_ref_when_origin_main_and_local_main_diverge(
    tmp_path,
):
    """R2+R3: origin/main behind local main → SHA is merge-base HEAD origin/main.

    Passing parent_branch into merge_base picks the local tip instead.
    """
    repo = _init_repo(tmp_path / "repo")
    origin_tip = _commit(repo, "client/package.json", '{"name":"app"}\n', "main package")
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-q", "origin", "main")
    git(repo, "fetch", "-q", "origin")
    local_tip = _commit(repo, "later.txt", "later\n", "local main advances")
    checkout = tmp_path / "ticket"
    git(repo, "worktree", "add", "-q", "-b", _TICKET, str(checkout), "main")
    _commit(checkout, "ticket.py", "print('ticket')\n", "ticket work")

    parent_ref = _parent_ref()
    named = parent_ref.parent_comparison_ref(checkout, "main")
    assert named == "origin/main"
    against_origin = git(checkout, "merge-base", "HEAD", "origin/main").stdout.strip()
    against_local = git(checkout, "merge-base", "HEAD", "main").stdout.strip()
    assert against_origin != against_local
    assert against_origin == origin_tip
    assert against_local == local_tip

    result = parent_ref.live_diff_base(checkout, "main")
    assert result == against_origin
    assert result != against_local
    assert result != "main"
    assert result != "origin/main"
    assert result != local_tip
    assert _SHA.fullmatch(result or "")


def test_live_diff_base_does_not_call_merge_base_when_the_parent_cannot_be_named(
    tmp_path,
):
    """R3: naming miss is fail-closed — do not probe merge_base with the raw branch."""
    repo = make_repo(tmp_path)
    with mock.patch("loregarden.services.parent_ref.merge_base") as merge:
        result = _parent_ref().live_diff_base(repo, "release/deleted-after-the-merge")
    assert result is None
    merge.assert_not_called()


def test_live_diff_base_treats_a_none_sha_as_fail_closed(tmp_path, caplog):
    """R3: `if not sha` covers None as well as ''. # CHECKPOINT"""
    repo = make_repo(tmp_path)
    with (
        mock.patch("loregarden.services.parent_ref.merge_base", return_value=None),
        caplog.at_level(logging.WARNING, logger="loregarden.services.parent_ref"),
    ):
        result = _parent_ref().live_diff_base(repo, "main")
    assert result is None
    assert any(record.levelno == logging.WARNING for record in caplog.records)
