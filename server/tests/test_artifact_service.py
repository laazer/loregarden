import dataclasses
import inspect
import json
from pathlib import Path
from unittest import mock

import pytest
from loregarden.models.domain import Artifact, ArtifactKind, Workspace, Worktree, WorktreeState
from loregarden.services import artifact_service
from loregarden.services.artifact_service import (
    capture_git_diff,
    ensure_diff_artifact,
    parse_test_output,
    refresh_execution_artifacts,
)
from loregarden.services.worktree_service import WorktreeService
from sqlmodel import Session, select
from tests.worktree_helpers import git, make_repo, make_run, make_ticket


def test_parse_pytest_output():
    text = """
============================= test session starts ==============================
tests/test_foo.py::test_ok PASSED
tests/test_foo.py::test_bad FAILED - AssertionError: boom
========================= 1 passed, 1 failed in 0.42s =========================
"""
    result = parse_test_output(text, cmd="uv run pytest -q")
    assert result is not None
    assert "1 passed" in result["summary"]
    assert "1 failed" in result["summary"]
    assert len(result["rows"]) == 2
    assert result["rows"][0]["status"] == "pass"
    assert result["rows"][1]["status"] == "fail"
    assert "boom" in result["rows"][1]["msg"]


def test_parse_test_output_empty():
    assert parse_test_output("") is None


def test_capture_git_diff_from_repo():
    from loregarden.models.domain import Workspace

    ws = Workspace(slug="loregarden", name="Loregarden", repo_path=".")
    diff = capture_git_diff(ws)
    assert diff is None or "sections" in diff


def test_parse_unified_diff_lines():
    from loregarden.services.artifact_service import _parse_unified_diff

    patch = """diff --git a/foo.py b/foo.py
--- a/foo.py
+++ b/foo.py
@@ -1,2 +1,3 @@
 context
-old
+new
diff --git a/bar.ts b/bar.ts
--- a/bar.ts
+++ b/bar.ts
@@ -1 +1,2 @@
 keep
+added
"""
    sections = _parse_unified_diff(patch)
    assert len(sections) == 2
    assert sections[0]["path"] == "foo.py"
    assert sections[1]["path"] == "bar.ts"
    assert sections[0]["add"] == 1
    assert sections[0]["del"] == 1
    assert sections[1]["add"] == 1
    types = [line["type"] for line in sections[0]["lines"]]
    assert "h" in types
    assert "a" in types
    assert "d" in types


def test_extract_pytest_from_stream_json():
    from loregarden.models.domain import AgentRun
    from loregarden.services.artifact_service import (
        extract_pytest_sections_from_stream_json,
        extract_test_source_from_run,
    )

    assistant = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_bash1",
                        "name": "Bash",
                        "input": {"command": "python -m pytest -x 2>&1 | tail -50"},
                    }
                ]
            },
        }
    )
    result = json.dumps(
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_bash1",
                        "content": (
                            "tests/test_foo.py::test_ok PASSED\n"
                            "======================= 122 passed, 1 warning in 21.71s ========================"
                        ),
                    }
                ]
            },
        }
    )
    sections = extract_pytest_sections_from_stream_json(f"{assistant}\n{result}")
    assert len(sections) == 1
    assert "pytest" in sections[0][0]
    run = AgentRun(
        run_code="run_x",
        ticket_id="t1",
        workspace_id="w1",
        agent_id="static_qa",
        stage_key="testing",
        stdout=f"{assistant}\n{result}",
    )
    text, cmd = extract_test_source_from_run(run)
    parsed = __import__(
        "loregarden.services.artifact_service", fromlist=["parse_test_output"]
    ).parse_test_output(text, cmd=cmd)
    assert parsed is not None
    assert parsed["summary"] == "122 passed · 0 failed · 0 skipped"
    assert parsed["cmd"] == "python -m pytest -x 2>&1 | tail -50"
    assert parsed["rows"][0]["name"] == "tests/test_foo.py::test_ok"


def test_rejects_json_garbage_rows():
    garbage = '{"type":"user","message":{"content":"npm FAILED"}}\n'
    assert parse_test_output(garbage, cmd="claude --output-format stream-json") is None


# ---------------------------------------------------------------------------
# 1608 keepers + 1609 rewrites (R1–R6). vs-main is still correct when no parent.
# ---------------------------------------------------------------------------

_FAKE_SHA = "a" * 40
_FROZEN_COLUMN = "c" * 40
_CAPTURED = {
    "file": "ticket.py",
    "add": "+1",
    "del": "−0",
    "files": "1 file changed",
    "range": "main",
    "sections": [{"path": "ticket.py", "add": 1, "del": 0, "lines": []}],
}
_TICKET_LOCAL = {
    "file": "ticket.py",
    "add": "+1",
    "del": "−0",
    "files": "1 file changed",
    "range": _FAKE_SHA,
    "sections": [{"path": "ticket.py", "add": 1, "del": 0, "lines": []}],
}
_PYTEST_STDOUT = (
    "tests/test_foo.py::test_ok PASSED\n"
    "======================= 1 passed, 0 warning in 0.01s ========================\n"
)


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    return make_repo(tmp_path)


@pytest.fixture(name="workspace")
def workspace_fixture(session, repo):
    ws = Workspace(slug="proj", name="proj", repo_path=str(repo))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


@pytest.fixture(name="ticket")
def ticket_fixture(session, workspace):
    return make_ticket(session, workspace)


def _active_tree(session, workspace, ticket, repo, *, parent_branch: str = "main"):
    if parent_branch and parent_branch != "main":
        git(repo, "branch", parent_branch, "main")
    run = make_run(session, workspace, ticket, "r1")
    service = WorktreeService(session, repo_path=str(repo))
    row = service.get_or_create_for_ticket(ticket, run.id, parent_branch=parent_branch)
    assert row is not None
    assert Path(row.worktree_path).is_dir()
    return run, row


def _no_diff_row(session, ticket) -> None:
    existing = session.exec(
        select(Artifact).where(Artifact.ticket_id == ticket.id, Artifact.kind == ArtifactKind.DIFF)
    ).all()
    assert existing == [], "skip tests must have no stored valid diff or they pass vacuously"


def test_both_composers_call_the_private_plan_helpers():
    """AC-R2.7 ensure/refresh do not name live_diff_base or capture_git_diff.

    ensure plans first, then reuses or captures. refresh stays a thin
    `_capture_ticket_diff` wrapper. Rewrites test_both_composers_call_the_private_capture_helper.
    """
    assert hasattr(artifact_service, "_ticket_diff_plan")
    assert hasattr(artifact_service, "_capture_from_plan")
    ensure = inspect.getsource(ensure_diff_artifact)
    refresh = inspect.getsource(refresh_execution_artifacts)
    helper = inspect.getsource(artifact_service._capture_ticket_diff)
    plan = inspect.getsource(artifact_service._ticket_diff_plan)
    from_plan = inspect.getsource(artifact_service._capture_from_plan)
    assert "_ticket_diff_plan" in ensure
    assert "_capture_from_plan" in ensure
    assert "_capture_ticket_diff(" in refresh
    assert "live_diff_base" not in ensure
    assert "live_diff_base" not in refresh
    assert "capture_git_diff(" not in ensure
    assert "capture_git_diff(" not in refresh
    assert "live_diff_base" in plan
    assert "capture_git_diff(" not in plan
    assert "capture_git_diff(" in from_plan
    assert "live_diff_base" not in from_plan
    assert "_capture_from_plan" in helper
    assert "_ticket_diff_plan" in helper
    assert "live_diff_base" not in helper
    assert "capture_git_diff(" not in helper
    assert ensure.index("_ticket_diff_plan") < ensure.index("_capture_from_plan")


def test_capture_git_diff_against_is_keyword_only():
    """AC-R1.1 against is KEYWORD_ONLY, default None; positional two-arg still binds.

    Rewrites test_capture_git_diff_signature_has_no_against_parameter.
    """
    params = inspect.signature(capture_git_diff).parameters
    assert list(params) == ["workspace", "repo_root", "against"]
    assert params["against"].kind == inspect.Parameter.KEYWORD_ONLY
    assert params["against"].default is None
    ws = Workspace(slug="x", name="x", repo_path=".")
    inspect.signature(capture_git_diff).bind(ws)
    inspect.signature(capture_git_diff).bind(ws, Path("."))
    with pytest.raises(TypeError):
        inspect.signature(capture_git_diff).bind(ws, Path("."), "deadbeef")


def test_ensure_diff_artifact_skips_capture_when_live_diff_base_is_none(
    session, workspace, ticket, repo
):
    """AC-R6.2 parent_branch set + live_diff_base None → None, capture not called."""
    _active_tree(session, workspace, ticket, repo, parent_branch="integration/lg-root-1")
    _no_diff_row(session, ticket)
    with (
        mock.patch.object(artifact_service, "live_diff_base", return_value=None) as live,
        mock.patch.object(artifact_service, "capture_git_diff", return_value=_CAPTURED) as capture,
    ):
        result = ensure_diff_artifact(session, ticket=ticket, workspace=workspace)
    assert result is None
    live.assert_called_once()
    capture.assert_not_called()
    _no_diff_row(session, ticket)


def test_refresh_execution_artifacts_skips_diff_capture_but_still_upserts_tests(
    session, workspace, ticket, repo
):
    """AC-R6.3 Same skip around diff capture only; test-artifact upsert still runs."""
    run, _row = _active_tree(
        session, workspace, ticket, repo, parent_branch="integration/lg-root-1"
    )
    run.agent_id = "test_designer"
    run.stage_key = "test-design"
    run.stdout = _PYTEST_STDOUT
    session.add(run)
    session.commit()
    _no_diff_row(session, ticket)
    with (
        mock.patch.object(artifact_service, "live_diff_base", return_value=None) as live,
        mock.patch.object(artifact_service, "capture_git_diff", return_value=_CAPTURED) as capture,
    ):
        refresh_execution_artifacts(session, ticket=ticket, run=run, workspace=workspace)
    live.assert_called_once()
    capture.assert_not_called()
    _no_diff_row(session, ticket)
    tests = session.exec(
        select(Artifact).where(Artifact.ticket_id == ticket.id, Artifact.kind == ArtifactKind.TEST)
    ).all()
    assert tests, "test-artifact upsert still runs when diff capture is skipped"


def test_orm_default_main_parent_is_set_and_takes_the_live_base_path(
    session, workspace, ticket, repo
):
    """R5/R6: ORM default 'main' is set — fail-closed skip, not treated as unset."""
    _active_tree(session, workspace, ticket, repo, parent_branch="main")
    _no_diff_row(session, ticket)
    with (
        mock.patch.object(artifact_service, "live_diff_base", return_value=None),
        mock.patch.object(artifact_service, "capture_git_diff", return_value=_CAPTURED) as capture,
    ):
        result = ensure_diff_artifact(session, ticket=ticket, workspace=workspace)
    assert result is None
    capture.assert_not_called()


def test_no_worktree_still_captures_vs_main(session, workspace, ticket, repo):
    """AC-R7.1 no worktree / parent_branch '' → capture_git_diff as today."""
    with (
        mock.patch.object(artifact_service, "live_diff_base", return_value=_FAKE_SHA) as live,
        mock.patch.object(artifact_service, "capture_git_diff", return_value=_CAPTURED) as capture,
    ):
        result = ensure_diff_artifact(session, ticket=ticket, workspace=workspace)
    live.assert_not_called()
    capture.assert_called_once()
    args, kwargs = capture.call_args
    assert args == (workspace, repo)
    assert "against" not in kwargs
    assert result == _CAPTURED


def test_empty_parent_branch_on_a_directory_worktree_still_captures(
    session, workspace, ticket, repo
):
    """AC-R7.1 Empty parent_branch means unset; omit the live-base path."""
    _run, row = _active_tree(session, workspace, ticket, repo, parent_branch="main")
    row.parent_branch = ""
    session.add(row)
    session.commit()
    _no_diff_row(session, ticket)
    with (
        mock.patch.object(artifact_service, "live_diff_base", return_value=_FAKE_SHA) as live,
        mock.patch.object(artifact_service, "capture_git_diff", return_value=_CAPTURED) as capture,
    ):
        result = ensure_diff_artifact(session, ticket=ticket, workspace=workspace)
    live.assert_not_called()
    capture.assert_called_once()
    args, kwargs = capture.call_args
    assert args == (workspace, Path(row.worktree_path))
    assert "against" not in kwargs
    assert result == _CAPTURED


def test_a_resolved_live_base_sha_is_passed_as_against(session, workspace, ticket, repo):
    """AC-R2.3 parent_branch set AND live SHA → capture(..., against=that SHA).

    Rewrites test_a_resolved_live_base_sha_is_discarded_and_capture_stays_vs_main.
    """
    _run, row = _active_tree(
        session, workspace, ticket, repo, parent_branch="integration/lg-root-1"
    )
    _no_diff_row(session, ticket)
    with (
        mock.patch.object(artifact_service, "live_diff_base", return_value=_FAKE_SHA) as live,
        mock.patch.object(
            artifact_service, "capture_git_diff", return_value=_TICKET_LOCAL
        ) as capture,
    ):
        result = ensure_diff_artifact(session, ticket=ticket, workspace=workspace)
    live.assert_called_once()
    assert live.call_args.args == (Path(row.worktree_path), "integration/lg-root-1")
    capture.assert_called_once()
    args, kwargs = capture.call_args
    assert args == (workspace, Path(row.worktree_path))
    assert kwargs == {"against": _FAKE_SHA}
    assert result == _TICKET_LOCAL


def test_missing_dir_captures_vs_main_and_does_not_call_live_diff_base(
    session, workspace, ticket, repo
):
    """AC-R7.4 Missing-dir checkout → capture is called; live_diff_base is not."""
    run = make_run(session, workspace, ticket, "r1")
    gone = repo.parent / "gone-worktree"
    session.add(
        Worktree(
            workspace_id=workspace.id,
            agent_run_id=run.id,
            ticket_id=ticket.id,
            parent_branch="integration/lg-root-1",
            worktree_path=str(gone),
            branch=ticket.branch,
            state=WorktreeState.ACTIVE,
        )
    )
    session.commit()
    _no_diff_row(session, ticket)
    with (
        mock.patch.object(artifact_service, "live_diff_base", return_value=None) as live,
        mock.patch.object(artifact_service, "capture_git_diff", return_value=_CAPTURED) as capture,
    ):
        result = ensure_diff_artifact(session, ticket=ticket, workspace=workspace)
    live.assert_not_called()
    capture.assert_called_once()
    args, kwargs = capture.call_args
    assert args == (workspace, repo)
    assert "against" not in kwargs
    assert result == _CAPTURED


def test_fail_closed_does_not_refuse_a_stored_valid_vs_main_row(session, workspace, ticket, repo):
    """AC-R4.1 skip + valid stored → return stored; live_diff_base WAS called (plan first)."""
    _active_tree(session, workspace, ticket, repo, parent_branch="integration/lg-root-1")
    stored = {
        "file": "old.py",
        "range": "main",
        "sections": [{"path": "old.py", "add": 1, "del": 0, "lines": []}],
    }
    session.add(
        Artifact(
            ticket_id=ticket.id,
            kind=ArtifactKind.DIFF,
            title="old.py",
            content_json=json.dumps(stored),
        )
    )
    session.commit()
    with (
        mock.patch.object(artifact_service, "live_diff_base", return_value=None) as live,
        mock.patch.object(artifact_service, "capture_git_diff", return_value=_CAPTURED) as capture,
    ):
        result = ensure_diff_artifact(session, ticket=ticket, workspace=workspace)
    live.assert_called_once()
    capture.assert_not_called()
    assert result == stored


# ---------------------------------------------------------------------------
# test-break — skip/capture mutations the design suite still lets through
# ---------------------------------------------------------------------------


def test_fail_closed_skip_asks_live_diff_base_on_the_worktree_not_the_shared_checkout(
    session, workspace, ticket, repo
):
    """R6: skip path must name the worktree. A shared-checkout probe is the missing-dir bug."""
    _run, row = _active_tree(
        session, workspace, ticket, repo, parent_branch="integration/lg-root-1"
    )
    _no_diff_row(session, ticket)
    with (
        mock.patch.object(artifact_service, "live_diff_base", return_value=None) as live,
        mock.patch.object(artifact_service, "capture_git_diff", return_value=_CAPTURED) as capture,
    ):
        result = ensure_diff_artifact(session, ticket=ticket, workspace=workspace)
    assert result is None
    live.assert_called_once_with(Path(row.worktree_path), "integration/lg-root-1")
    assert live.call_args.args[0] != Path(workspace.repo_path)
    capture.assert_not_called()


def test_fail_closed_skip_ignores_a_stored_worktree_merge_base_column(
    session, workspace, ticket, repo
):
    """R8: never read Worktree.merge_base. A frozen cut SHA must not unblock skip. # CHECKPOINT"""
    _run, row = _active_tree(
        session, workspace, ticket, repo, parent_branch="integration/lg-root-1"
    )
    row.merge_base = _FAKE_SHA
    session.add(row)
    session.commit()
    _no_diff_row(session, ticket)
    with (
        mock.patch.object(artifact_service, "live_diff_base", return_value=None),
        mock.patch.object(artifact_service, "capture_git_diff", return_value=_CAPTURED) as capture,
    ):
        result = ensure_diff_artifact(session, ticket=ticket, workspace=workspace)
    assert result is None
    capture.assert_not_called()
    _no_diff_row(session, ticket)


def test_a_stored_merge_base_column_is_not_passed_as_against(session, workspace, ticket, repo):
    """AC-R2.5 against=live SHA, never Worktree.merge_base."""
    _run, row = _active_tree(
        session, workspace, ticket, repo, parent_branch="integration/lg-root-1"
    )
    frozen = _FROZEN_COLUMN
    row.merge_base = frozen
    session.add(row)
    session.commit()
    _no_diff_row(session, ticket)
    with (
        mock.patch.object(artifact_service, "live_diff_base", return_value=_FAKE_SHA),
        mock.patch.object(
            artifact_service, "capture_git_diff", return_value=_TICKET_LOCAL
        ) as capture,
    ):
        result = ensure_diff_artifact(session, ticket=ticket, workspace=workspace)
    capture.assert_called_once()
    args, kwargs = capture.call_args
    assert args == (workspace, Path(row.worktree_path))
    assert kwargs == {"against": _FAKE_SHA}
    assert frozen not in args
    assert frozen not in kwargs.values()
    assert result == _TICKET_LOCAL


def test_invalid_stored_diff_does_not_bypass_fail_closed_skip(session, workspace, ticket, repo):
    """R6+R7: empty sections are not valid reuse; skip still applies. # CHECKPOINT"""
    _active_tree(session, workspace, ticket, repo, parent_branch="integration/lg-root-1")
    session.add(
        Artifact(
            ticket_id=ticket.id,
            kind=ArtifactKind.DIFF,
            title="old.py",
            content_json=json.dumps({"file": "old.py", "sections": []}),
        )
    )
    session.commit()
    with (
        mock.patch.object(artifact_service, "live_diff_base", return_value=None),
        mock.patch.object(artifact_service, "capture_git_diff", return_value=_CAPTURED) as capture,
    ):
        result = ensure_diff_artifact(session, ticket=ticket, workspace=workspace)
    assert result is None
    capture.assert_not_called()


def test_refresh_passes_against_sha_and_still_upserts_tests(session, workspace, ticket, repo):
    """AC-R2.3 + R3: refresh captures against=SHA; test upsert still runs.

    Rewrites test_refresh_discards_a_resolved_sha_and_still_upserts_tests.
    """
    run, row = _active_tree(session, workspace, ticket, repo, parent_branch="integration/lg-root-1")
    run.agent_id = "test_designer"
    run.stage_key = "test-design"
    run.stdout = _PYTEST_STDOUT
    session.add(run)
    session.commit()
    _no_diff_row(session, ticket)
    with (
        mock.patch.object(artifact_service, "live_diff_base", return_value=_FAKE_SHA) as live,
        mock.patch.object(
            artifact_service, "capture_git_diff", return_value=_TICKET_LOCAL
        ) as capture,
    ):
        refresh_execution_artifacts(session, ticket=ticket, run=run, workspace=workspace)
    live.assert_called_once_with(Path(row.worktree_path), "integration/lg-root-1")
    capture.assert_called_once()
    args, kwargs = capture.call_args
    assert args == (workspace, Path(row.worktree_path))
    assert kwargs == {"against": _FAKE_SHA}
    tests = session.exec(
        select(Artifact).where(Artifact.ticket_id == ticket.id, Artifact.kind == ArtifactKind.TEST)
    ).all()
    assert tests, "test-artifact upsert still runs when capture is against a live SHA"
    diff_rows = session.exec(
        select(Artifact).where(Artifact.ticket_id == ticket.id, Artifact.kind == ArtifactKind.DIFF)
    ).all()
    assert len(diff_rows) == 1
    assert diff_rows[0].title == "ticket.py"


def test_a_file_at_worktree_path_captures_vs_main_and_does_not_call_live_diff_base(
    session, workspace, ticket, repo
):
    """R5+R7: file is not a directory; composer must not merge-base the shared checkout. # CHECKPOINT"""
    run = make_run(session, workspace, ticket, "r1")
    not_a_tree = repo.parent / "not-a-tree"
    not_a_tree.write_text("a file, not a directory\n")
    session.add(
        Worktree(
            workspace_id=workspace.id,
            agent_run_id=run.id,
            ticket_id=ticket.id,
            parent_branch="integration/lg-root-1",
            worktree_path=str(not_a_tree),
            branch=ticket.branch,
            state=WorktreeState.ACTIVE,
        )
    )
    session.commit()
    _no_diff_row(session, ticket)
    with (
        mock.patch.object(artifact_service, "live_diff_base", return_value=None) as live,
        mock.patch.object(artifact_service, "capture_git_diff", return_value=_CAPTURED) as capture,
    ):
        result = ensure_diff_artifact(session, ticket=ticket, workspace=workspace)
    live.assert_not_called()
    capture.assert_called_once()
    args, kwargs = capture.call_args
    assert args == (workspace, repo)
    assert "against" not in kwargs
    assert result == _CAPTURED


def test_plan_helpers_pass_against_sha_not_a_branch_name():
    """AC-R2.6 plan/from_plan may contain against=; never a branch name or merge_base.

    Rewrites test_capture_helper_does_not_pass_against_or_a_branch_name_or_preferred_base.
    """
    assert hasattr(artifact_service, "_ticket_diff_plan")
    assert hasattr(artifact_service, "_capture_from_plan")
    plan = inspect.getsource(artifact_service._ticket_diff_plan)
    from_plan = inspect.getsource(artifact_service._capture_from_plan)
    helper = inspect.getsource(artifact_service._capture_ticket_diff)
    for src in (plan, from_plan, helper):
        assert "comparison_ref" not in src
        assert ".merge_base" not in src
        assert "_PREFERRED_BASE" not in src
        assert "working tree" not in src
    assert "against=plan.against" in from_plan
    assert "against=None" not in from_plan
    assert "parent_branch" not in from_plan


def test_capture_git_diff_omitted_path_still_has_preferred_base():
    """AC-R1.4 omitted path still starts at _PREFERRED_BASE; capture never sees Worktree.

    Rewrites test_capture_git_diff_body_stays_vs_main_and_gains_no_against.
    """
    src = inspect.getsource(capture_git_diff)
    assert "_PREFERRED_BASE" in src
    assert "live_diff_base" not in src
    assert "parent_comparison_ref" not in src
    assert "Worktree" not in src
    assert "parent_branch" not in src
    assert "comparison_ref" not in src


def test_git_base_ref_is_left_alone():
    """R8: git_base_ref is not rewritten onto parent_comparison_ref."""
    src = inspect.getsource(artifact_service.git_base_ref)
    assert "_PREFERRED_BASE" in src
    assert "origin/main" in src
    assert "parent_comparison_ref" not in src
    assert "live_diff_base" not in src


def test_complete_run_does_not_swallow_a_live_diff_base_failure():
    """R6: complete_run_tail has no try/except around refresh (run_completion.py:449-454)."""
    from loregarden.services.run_completion import complete_run_tail

    src = inspect.getsource(complete_run_tail)
    idx = src.index("refresh_execution_artifacts(")
    window = src[max(0, idx - 160) : idx + 280]
    assert "try:" not in window
    assert "except" not in window


# ---------------------------------------------------------------------------
# 1609 — against=, three-state plan, recapture, dirty-tracked graph
# ---------------------------------------------------------------------------

_INTEGRATION = "integration/lg-root-1609"
_TICKET_BRANCH = "loregarden/lg-child-1609"


@dataclasses.dataclass(frozen=True)
class _LocalGraph:
    repo: Path
    checkout: Path
    parent: str
    merge_base: str
    parent_tip: str


def _init_git(path: Path) -> Path:
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "t@example.com")
    git(path, "config", "user.name", "Test")
    return path


def _commit_file(cwd: Path, rel: str, content: str, message: str) -> str:
    dest = cwd / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content)
    git(cwd, "add", "--", rel)
    git(cwd, "commit", "-q", "-m", message)
    return git(cwd, "rev-parse", "HEAD").stdout.strip()


def _ticket_local_graph(
    tmp_path: Path, *, commit_ticket: bool = True, dirty_foo: bool = True
) -> _LocalGraph:
    """Advance-parent graph with a dirty tracked server file.

    Rebuilt here. Do not import test_parent_ref._advance_parent_graph (no dirty modify).
    """
    repo = _init_git(tmp_path / "repo")
    _commit_file(repo, "client/package.json", '{"name":"app"}\n', "main package")
    _commit_file(repo, "server/foo.py", "original\n", "tracked server foo")
    git(repo, "checkout", "-q", "-b", _INTEGRATION)
    _commit_file(repo, "sibling.txt", "sibling\n", "sibling on integration")
    checkout = tmp_path / "ticket"
    git(repo, "worktree", "add", "-q", "-b", _TICKET_BRANCH, str(checkout), _INTEGRATION)
    if commit_ticket:
        _commit_file(checkout, "ticket.py", "print('ticket')\n", "ticket work")
    if dirty_foo:
        (checkout / "server" / "foo.py").write_text("dirty tracked\n")
        (checkout / "untracked.txt").write_text("untracked stays out\n")
    git(repo, "checkout", "-q", _INTEGRATION)
    _commit_file(repo, "later_sibling.txt", "later\n", "later sibling")
    parent_tip = git(repo, "rev-parse", "HEAD").stdout.strip()
    git(repo, "checkout", "-q", "main")
    merge_base = git(checkout, "merge-base", "HEAD", _INTEGRATION).stdout.strip()
    return _LocalGraph(repo, checkout, _INTEGRATION, merge_base, parent_tip)


def _capture_ws(repo: Path) -> Workspace:
    return Workspace(slug="graph", name="graph", repo_path=str(repo))


def _graph_workspace(session, repo: Path) -> Workspace:
    ws = _capture_ws(repo)
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


def _bind_worktree(
    session, workspace, ticket, graph: _LocalGraph, *, frozen: str | None = _FROZEN_COLUMN
):
    run = make_run(session, workspace, ticket, "r1")
    row = Worktree(
        workspace_id=workspace.id,
        agent_run_id=run.id,
        ticket_id=ticket.id,
        parent_branch=graph.parent,
        worktree_path=str(graph.checkout),
        branch=ticket.branch,
        state=WorktreeState.ACTIVE,
        merge_base=frozen,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return run, row


def _section_paths(diff: dict) -> set[str]:
    return {section["path"] for section in diff["sections"]}


def _diff_rows(session, ticket):
    return session.exec(
        select(Artifact).where(Artifact.ticket_id == ticket.id, Artifact.kind == ArtifactKind.DIFF)
    ).all()


def _plant_vs_main(session, ticket) -> dict:
    stored = {
        "file": "client/package.json",
        "range": "main",
        "sections": [{"path": "client/package.json", "add": 1, "del": 0, "lines": []}],
    }
    session.add(
        Artifact(
            ticket_id=ticket.id,
            kind=ArtifactKind.DIFF,
            title="client/package.json",
            content_json=json.dumps(stored),
        )
    )
    session.commit()
    return stored


def test_ticket_diff_plan_is_private_frozen_and_not_exported():
    """AC-R2.1 _TicketDiffPlan is frozen; not in parent_ref or ticket_worktree."""
    from loregarden.services import parent_ref, ticket_worktree

    assert hasattr(artifact_service, "_TicketDiffPlan")
    cls = artifact_service._TicketDiffPlan
    assert dataclasses.is_dataclass(cls)
    assert cls.__dataclass_params__.frozen
    assert [field.name for field in dataclasses.fields(cls)] == ["path", "against", "skip"]
    assert not hasattr(parent_ref, "_TicketDiffPlan")
    assert not hasattr(ticket_worktree, "_TicketDiffPlan")


def test_capture_git_diff_against_set_git_failure_returns_none_not_vs_main(tmp_path):
    """AC-R1.2 / AC-R5.3 against-set git failure → None; omitted path would not be."""
    graph = _ticket_local_graph(tmp_path)
    ws = _capture_ws(graph.repo)
    omitted = capture_git_diff(ws, graph.checkout)
    assert omitted is not None
    failed = capture_git_diff(ws, graph.checkout, against="this-ref-does-not-exist")
    assert failed is None


def test_capture_git_diff_against_set_empty_stat_returns_none_not_vs_main(tmp_path):
    """AC-R1.3 empty stat vs SHA → None; no vs-main / vs-index fallback.

    A fully clean tree is not enough: today's empty-stat path at
    `_stat_and_patch(cwd, None)` is vs-index, which is also empty then.
    Stage a file and delete it from the worktree so vs-index is nonempty
    while `git diff <SHA>` stays empty — falling through yields
    range='working tree', not None.
    """
    graph = _ticket_local_graph(tmp_path, commit_ticket=False, dirty_foo=False)
    ws = _capture_ws(graph.repo)
    omitted = capture_git_diff(ws, graph.checkout)
    assert omitted is not None
    assert "sibling.txt" in _section_paths(omitted)
    staged = graph.checkout / "staged.txt"
    staged.write_text("staged vs-index bait\n")
    git(graph.checkout, "add", "--", "staged.txt")
    staged.unlink()
    empty = capture_git_diff(ws, graph.checkout, against=graph.merge_base)
    assert empty is None


def test_against_omitted_none_or_empty_keeps_vs_main_payload(tmp_path):
    """AC-R1.4 omitted / None / empty against keep today's vs-main body."""
    graph = _ticket_local_graph(tmp_path)
    ws = _capture_ws(graph.repo)
    omitted = capture_git_diff(ws, graph.checkout)
    assert omitted is not None
    assert capture_git_diff(ws, graph.checkout, against=None) == omitted
    assert capture_git_diff(ws, graph.checkout, against="") == omitted
    assert omitted["range"] != graph.merge_base
    assert "sibling.txt" in _section_paths(omitted)


def test_whitespace_against_is_against_set_not_omitted(tmp_path):
    """Whitespace-only against is nonempty → against-set → git fail → None."""
    graph = _ticket_local_graph(tmp_path)
    ws = _capture_ws(graph.repo)
    assert capture_git_diff(ws, graph.checkout) is not None
    assert capture_git_diff(ws, graph.checkout, against="   ") is None


def test_against_set_payload_range_is_the_string_passed(tmp_path):
    """AC-R1.1 range is the against string; no SHA-shape rewrite."""
    graph = _ticket_local_graph(tmp_path)
    ws = _capture_ws(graph.repo)
    diff = capture_git_diff(ws, graph.checkout, against="HEAD")
    assert diff is not None
    assert diff["range"] == "HEAD"
    assert "server/foo.py" in _section_paths(diff)


def test_capture_against_merge_base_sha_is_ticket_local_and_includes_dirty_tracked(tmp_path):
    """AC-R5.1 + AC-R5.2 ticket-local two-dot includes dirty foo.py; triple-dot does not."""
    graph = _ticket_local_graph(tmp_path)
    ws = _capture_ws(graph.repo)
    diff = capture_git_diff(ws, graph.checkout, against=graph.merge_base)
    assert diff is not None
    paths = _section_paths(diff)
    assert "ticket.py" in paths
    assert "server/foo.py" in paths
    assert "sibling.txt" not in paths
    assert "later_sibling.txt" not in paths
    assert "client/package.json" not in paths
    assert "untracked.txt" not in paths
    assert diff["range"] == graph.merge_base
    assert diff["range"] != "main"
    assert diff["range"] != graph.parent_tip
    assert diff["range"] != _FROZEN_COLUMN
    triple = git(graph.checkout, "diff", f"{graph.parent}...HEAD")
    assert "server/foo.py" not in triple.stdout
    assert "ticket.py" in triple.stdout


def test_upsert_titles_stay_first_stat_path():
    """AC-R3.1 both upserts stay str(diff.get('file') or 'git diff'); no picker."""
    refresh = inspect.getsource(refresh_execution_artifacts)
    ensure = inspect.getsource(ensure_diff_artifact)
    needle = 'str(diff.get("file") or "git diff")'
    assert needle in refresh
    assert needle in ensure
    assert "dirty_paths" not in refresh
    assert "dirty_paths" not in ensure


def test_refresh_on_integration_parent_upserts_ticket_local_title(session, tmp_path):
    """AC-R3.2 / AC-R5.4 refresh title is ticket-local, not client/package.json."""
    graph = _ticket_local_graph(tmp_path)
    workspace = _graph_workspace(session, graph.repo)
    ticket = make_ticket(session, workspace)
    run, _row = _bind_worktree(session, workspace, ticket, graph)
    refresh_execution_artifacts(session, ticket=ticket, run=run, workspace=workspace)
    rows = _diff_rows(session, ticket)
    assert len(rows) == 1
    payload = json.loads(rows[0].content_json)
    paths = _section_paths(payload)
    assert rows[0].title != "client/package.json"
    assert rows[0].title == str(payload.get("file") or "git diff")
    assert rows[0].title in paths
    assert "ticket.py" in paths
    assert "server/foo.py" in paths
    assert "sibling.txt" not in paths
    assert "later_sibling.txt" not in paths
    assert "client/package.json" not in paths
    assert payload["range"] == graph.merge_base
    assert payload["range"] != _FROZEN_COLUMN


def test_stored_vs_main_diff_is_recaptured_for_an_integration_parent(session, tmp_path):
    """AC-R4.3 plant stored range=main titled client/package.json; ensure recaptures."""
    graph = _ticket_local_graph(tmp_path)
    workspace = _graph_workspace(session, graph.repo)
    ticket = make_ticket(session, workspace)
    _bind_worktree(session, workspace, ticket, graph)
    _plant_vs_main(session, ticket)
    with mock.patch.object(artifact_service, "capture_git_diff", wraps=capture_git_diff) as capture:
        result = ensure_diff_artifact(session, ticket=ticket, workspace=workspace)
    capture.assert_called_once()
    assert capture.call_args.kwargs["against"] == graph.merge_base
    assert result is not None
    paths = _section_paths(result)
    assert result["range"] == graph.merge_base
    assert result["range"] != "main"
    assert result["file"] != "client/package.json"
    assert "ticket.py" in paths
    assert "server/foo.py" in paths
    assert "sibling.txt" not in paths
    assert "client/package.json" not in paths
    rows = _diff_rows(session, ticket)
    assert len(rows) == 1
    assert rows[0].title != "client/package.json"
    assert rows[0].title == str(result.get("file") or "git diff")
    stored = json.loads(rows[0].content_json)
    assert stored["range"] == graph.merge_base


def test_artifacts_grouped_recaptures_stored_vs_main_for_an_integration_parent(session, tmp_path):
    """R2 grouped twin: plant vs-main with sections; _artifacts_grouped recaptures.

    Covering only ensure_diff_artifact is not done. Must fail on the
    sections-only short-circuit in api.tickets._artifacts_grouped.

    Cheats this refuses (test-break). Recapture is the proof — inspect.getsource
    that the if is gone is not enough. # CHECKPOINT
    (a) plant without sections — bait is _diff_artifact_is_valid
    (b) call ensure then grouped — pre-state is still vs-main
    (c) skip _bind_worktree — worktree row is the integration parent
    (d) assert only grouped['diff'] is not None — recapture assertions below
    (e) plant a log so the live-run early return skips ensure
    (f) parent-ahead only on build_verify_context — this test never calls it
    (g) inspect.getsource that the if is gone — recapture runs first
    """
    from loregarden.api.tickets import _artifacts_grouped

    graph = _ticket_local_graph(tmp_path)
    assert graph.merge_base != "main"
    assert graph.parent != "main"
    workspace = _graph_workspace(session, graph.repo)
    ticket = make_ticket(session, workspace)
    _run, tree = _bind_worktree(session, workspace, ticket, graph)
    planted = _plant_vs_main(session, ticket)

    # (a) bait: sections-only valid vs-main. Empty sections would call ensure today.
    assert planted["range"] == "main"
    assert planted["file"] == "client/package.json"
    assert artifact_service._diff_artifact_is_valid(planted)

    # (c) integration parent worktree, not the shared checkout.
    assert tree.parent_branch == graph.parent
    assert Path(tree.worktree_path) == graph.checkout

    # (e) no log rows: make_run is RUNNING; a log trips the L440-441 early return.
    logs = session.exec(
        select(Artifact).where(Artifact.ticket_id == ticket.id, Artifact.kind == ArtifactKind.LOG)
    ).all()
    assert logs == []

    # (b) stored row is still the plant — ensure has not run yet.
    pre = _diff_rows(session, ticket)
    assert len(pre) == 1
    assert pre[0].title == "client/package.json"
    assert json.loads(pre[0].content_json)["range"] == "main"

    with mock.patch.object(artifact_service, "capture_git_diff", wraps=capture_git_diff) as capture:
        grouped = _artifacts_grouped(session, ticket)

    # (e) did not take the live-log early return
    assert grouped.get("live") != "Agent running…"
    capture.assert_called_once()
    assert capture.call_args.args[1] == graph.checkout
    assert capture.call_args.kwargs == {"against": graph.merge_base}
    result = grouped["diff"]
    assert result is not None  # (d) not sufficient alone
    paths = _section_paths(result)
    assert result["range"] == graph.merge_base
    assert result["range"] != "main"
    assert result["file"] != "client/package.json"
    assert result["file"] in {"ticket.py", "server/foo.py"}
    assert "ticket.py" in paths
    assert "server/foo.py" in paths
    assert "sibling.txt" not in paths
    assert "later_sibling.txt" not in paths
    assert "client/package.json" not in paths
    assert "untracked.txt" not in paths
    rows = _diff_rows(session, ticket)
    assert len(rows) == 1
    assert rows[0].title != "client/package.json"
    assert rows[0].title in {"ticket.py", "server/foo.py"}
    assert rows[0].title == str(result.get("file") or "git diff")
    stored = json.loads(rows[0].content_json)
    assert stored["range"] == graph.merge_base


def test_artifacts_grouped_hoists_ensure_and_keeps_tests_and_log_paths():
    """R1 hoist + R5 leave-alones. Not the recapture chokepoint. # CHECKPOINT

    inspect.getsource that the if is gone is not enough — see the grouped twin.
    """
    from loregarden.api import tickets as tickets_api

    assert tickets_api.ensure_diff_artifact is ensure_diff_artifact
    assert tickets_api.ensure_test_artifact is artifact_service.ensure_test_artifact
    assert tickets_api._test_artifact_is_valid is artifact_service._test_artifact_is_valid
    grouped_src = inspect.getsource(tickets_api._artifacts_grouped)
    assert "from loregarden.services.artifact_service import" not in grouped_src
    assert "_diff_artifact_is_valid" not in grouped_src
    assert "_test_artifact_is_valid" in grouped_src
    assert "_apply_log_artifacts" in grouped_src


def test_build_verify_context_parent_ahead_names_ticket_local_paths(session, tmp_path):
    """R3 pin: claimed change names ticket-local paths, not sibling vs-main files.

    Pin, not the chokepoint — 1609 ensure already makes this pass on HEAD. (f)
    """
    from loregarden.agents.verify_context import build_verify_context

    graph = _ticket_local_graph(tmp_path)
    workspace = _graph_workspace(session, graph.repo)
    ticket = make_ticket(session, workspace)
    _bind_worktree(session, workspace, ticket, graph)
    _plant_vs_main(session, ticket)
    text = build_verify_context(session, ticket, workspace)
    assert "ticket.py" in text
    assert "sibling.txt" not in text
    assert "later_sibling.txt" not in text
    assert "client/package.json" not in text


def test_matching_sha_reuses_stored_row(session, workspace, ticket, repo):
    """AC-R4 valid stored + stored.range == plan.against → reuse, no capture."""
    _active_tree(session, workspace, ticket, repo, parent_branch="integration/lg-root-1")
    stored = {
        "file": "ticket.py",
        "range": _FAKE_SHA,
        "sections": [{"path": "ticket.py", "add": 1, "del": 0, "lines": []}],
    }
    session.add(
        Artifact(
            ticket_id=ticket.id,
            kind=ArtifactKind.DIFF,
            title="ticket.py",
            content_json=json.dumps(stored),
        )
    )
    session.commit()
    with (
        mock.patch.object(artifact_service, "live_diff_base", return_value=_FAKE_SHA) as live,
        mock.patch.object(
            artifact_service, "capture_git_diff", return_value=_TICKET_LOCAL
        ) as capture,
    ):
        result = ensure_diff_artifact(session, ticket=ticket, workspace=workspace)
    live.assert_called_once()
    capture.assert_not_called()
    assert result == stored


def test_no_parent_reuses_valid_stored_without_asking_live_base(session, workspace, ticket, repo):
    """AC-R4 valid stored + against is None → today's sections-only reuse."""
    stored = {
        "file": "old.py",
        "range": "main",
        "sections": [{"path": "old.py", "add": 1, "del": 0, "lines": []}],
    }
    session.add(
        Artifact(
            ticket_id=ticket.id,
            kind=ArtifactKind.DIFF,
            title="old.py",
            content_json=json.dumps(stored),
        )
    )
    session.commit()
    with (
        mock.patch.object(artifact_service, "live_diff_base", return_value=_FAKE_SHA) as live,
        mock.patch.object(artifact_service, "capture_git_diff", return_value=_CAPTURED) as capture,
    ):
        result = ensure_diff_artifact(session, ticket=ticket, workspace=workspace)
    live.assert_not_called()
    capture.assert_not_called()
    assert result == stored


def test_missing_range_on_stored_row_recaptures_when_against_is_sha(
    session, workspace, ticket, repo
):
    """AC-R4 missing range is not equal to the SHA; recapture."""
    _active_tree(session, workspace, ticket, repo, parent_branch="integration/lg-root-1")
    stored = {
        "file": "old.py",
        "sections": [{"path": "old.py", "add": 1, "del": 0, "lines": []}],
    }
    session.add(
        Artifact(
            ticket_id=ticket.id,
            kind=ArtifactKind.DIFF,
            title="old.py",
            content_json=json.dumps(stored),
        )
    )
    session.commit()
    with (
        mock.patch.object(artifact_service, "live_diff_base", return_value=_FAKE_SHA),
        mock.patch.object(
            artifact_service, "capture_git_diff", return_value=_TICKET_LOCAL
        ) as capture,
    ):
        result = ensure_diff_artifact(session, ticket=ticket, workspace=workspace)
    capture.assert_called_once()
    assert capture.call_args.kwargs == {"against": _FAKE_SHA}
    assert result == _TICKET_LOCAL


def test_parent_branch_main_still_recaptures_stored_range_main(session, workspace, ticket, repo):
    """AC-R4 no parent==main exception: live SHA != the string 'main'."""
    _active_tree(session, workspace, ticket, repo, parent_branch="main")
    _plant_vs_main(session, ticket)
    with (
        mock.patch.object(artifact_service, "live_diff_base", return_value=_FAKE_SHA),
        mock.patch.object(
            artifact_service, "capture_git_diff", return_value=_TICKET_LOCAL
        ) as capture,
    ):
        result = ensure_diff_artifact(session, ticket=ticket, workspace=workspace)
    capture.assert_called_once()
    assert capture.call_args.kwargs == {"against": _FAKE_SHA}
    assert result == _TICKET_LOCAL
    rows = _diff_rows(session, ticket)
    assert len(rows) == 1
    assert rows[0].title == "ticket.py"


def test_diff_artifact_is_valid_stays_sections_only():
    """AC-R4.4 _diff_artifact_is_valid does not grow a range check."""
    src = inspect.getsource(artifact_service._diff_artifact_is_valid)
    assert "range" not in src
    assert "sections" in src


def test_plan_and_capture_stay_in_artifact_service():
    """AC-R6.3 no new capture module; _TicketDiffPlan lives next to the composer."""
    assert capture_git_diff.__module__ == "loregarden.services.artifact_service"
    assert hasattr(artifact_service, "_TicketDiffPlan")
    assert artifact_service._TicketDiffPlan.__module__ == "loregarden.services.artifact_service"
    assert hasattr(artifact_service, "_ticket_diff_plan")
    assert artifact_service._ticket_diff_plan.__module__ == "loregarden.services.artifact_service"
    assert hasattr(artifact_service, "_capture_from_plan")
    assert artifact_service._capture_from_plan.__module__ == "loregarden.services.artifact_service"


# ---------------------------------------------------------------------------
# test-break — mutations the design suite still lets through
# ---------------------------------------------------------------------------


def test_ticket_diff_plan_encodes_skip_separately_from_against_none(
    session, workspace, ticket, repo
):
    """R2: skip is a third state. against=None is no-parent (vs-main is correct).

    An implementer who encodes skip as against=None makes skip+valid-stored
    indistinguishable from no-parent reuse, and skip+absent-stored fall through
    to vs-main. # CHECKPOINT
    """
    unset = artifact_service._ticket_diff_plan(session, ticket=ticket, workspace=workspace)
    assert unset.skip is False
    assert unset.against is None
    assert unset.path == repo

    _active_tree(session, workspace, ticket, repo, parent_branch="integration/lg-root-1")
    with mock.patch.object(artifact_service, "live_diff_base", return_value=None) as live:
        skipped = artifact_service._ticket_diff_plan(session, ticket=ticket, workspace=workspace)
    live.assert_called_once()
    assert skipped.skip is True
    assert skipped.against is None


def test_capture_from_plan_three_states(tmp_path):
    """R2: skip → None (no capture); against set → kwargs; else omit against=."""
    graph = _ticket_local_graph(tmp_path)
    ws = _capture_ws(graph.repo)
    plan_cls = artifact_service._TicketDiffPlan

    with mock.patch.object(
        artifact_service, "capture_git_diff", return_value=_TICKET_LOCAL
    ) as capture:
        skipped = artifact_service._capture_from_plan(
            ws, plan_cls(path=graph.checkout, against=None, skip=True)
        )
    assert skipped is None
    capture.assert_not_called()

    with mock.patch.object(
        artifact_service, "capture_git_diff", return_value=_TICKET_LOCAL
    ) as capture:
        against = artifact_service._capture_from_plan(
            ws, plan_cls(path=graph.checkout, against=_FAKE_SHA, skip=False)
        )
    capture.assert_called_once()
    args, kwargs = capture.call_args
    assert args == (ws, graph.checkout)
    assert kwargs == {"against": _FAKE_SHA}
    assert against == _TICKET_LOCAL

    with mock.patch.object(artifact_service, "capture_git_diff", return_value=_CAPTURED) as capture:
        omitted = artifact_service._capture_from_plan(
            ws, plan_cls(path=graph.checkout, against=None, skip=False)
        )
    capture.assert_called_once()
    args, kwargs = capture.call_args
    assert args == (ws, graph.checkout)
    assert "against" not in kwargs
    assert omitted == _CAPTURED


def test_refresh_overwrites_a_stored_vs_main_diff(session, tmp_path):
    """R3 + R4: reuse lives only in ensure. refresh recaptures stored vs-main."""
    graph = _ticket_local_graph(tmp_path)
    workspace = _graph_workspace(session, graph.repo)
    ticket = make_ticket(session, workspace)
    run, _row = _bind_worktree(session, workspace, ticket, graph)
    _plant_vs_main(session, ticket)
    refresh_execution_artifacts(session, ticket=ticket, run=run, workspace=workspace)
    rows = _diff_rows(session, ticket)
    assert len(rows) == 1
    payload = json.loads(rows[0].content_json)
    paths = _section_paths(payload)
    assert rows[0].title != "client/package.json"
    assert rows[0].title == str(payload.get("file") or "git diff")
    assert "ticket.py" in paths
    assert "server/foo.py" in paths
    assert "sibling.txt" not in paths
    assert "client/package.json" not in paths
    assert payload["range"] == graph.merge_base
    assert payload["range"] != "main"


def test_refresh_has_no_stored_row_reuse_gate(session, workspace, ticket, repo):
    """R4: matching stored.range still recaptures on refresh. # CHECKPOINT"""
    run, row = _active_tree(session, workspace, ticket, repo, parent_branch="integration/lg-root-1")
    stored = {
        "file": "ticket.py",
        "range": _FAKE_SHA,
        "sections": [{"path": "ticket.py", "add": 1, "del": 0, "lines": []}],
    }
    session.add(
        Artifact(
            ticket_id=ticket.id,
            kind=ArtifactKind.DIFF,
            title="ticket.py",
            content_json=json.dumps(stored),
        )
    )
    session.commit()
    with (
        mock.patch.object(artifact_service, "live_diff_base", return_value=_FAKE_SHA),
        mock.patch.object(
            artifact_service, "capture_git_diff", return_value=_TICKET_LOCAL
        ) as capture,
    ):
        refresh_execution_artifacts(session, ticket=ticket, run=run, workspace=workspace)
    capture.assert_called_once()
    args, kwargs = capture.call_args
    assert args == (workspace, Path(row.worktree_path))
    assert kwargs == {"against": _FAKE_SHA}


@pytest.mark.parametrize("stored_range", ["master", "origin/main"])
def test_sha_inequality_recaptures_without_special_casing_main(
    session, workspace, ticket, repo, stored_range
):
    """R4: recapture is SHA inequality, not `range == 'main'`."""
    _active_tree(session, workspace, ticket, repo, parent_branch="integration/lg-root-1")
    stored = {
        "file": "client/package.json",
        "range": stored_range,
        "sections": [{"path": "client/package.json", "add": 1, "del": 0, "lines": []}],
    }
    session.add(
        Artifact(
            ticket_id=ticket.id,
            kind=ArtifactKind.DIFF,
            title="client/package.json",
            content_json=json.dumps(stored),
        )
    )
    session.commit()
    with (
        mock.patch.object(artifact_service, "live_diff_base", return_value=_FAKE_SHA),
        mock.patch.object(
            artifact_service, "capture_git_diff", return_value=_TICKET_LOCAL
        ) as capture,
    ):
        result = ensure_diff_artifact(session, ticket=ticket, workspace=workspace)
    capture.assert_called_once()
    assert capture.call_args.kwargs == {"against": _FAKE_SHA}
    assert result == _TICKET_LOCAL
    rows = _diff_rows(session, ticket)
    assert len(rows) == 1
    assert rows[0].title == "ticket.py"


def test_invalid_stored_diff_with_a_live_sha_recaptures(session, workspace, ticket, repo):
    """R4: empty sections + against is SHA → recapture, not skip-to-None."""
    _active_tree(session, workspace, ticket, repo, parent_branch="integration/lg-root-1")
    session.add(
        Artifact(
            ticket_id=ticket.id,
            kind=ArtifactKind.DIFF,
            title="old.py",
            content_json=json.dumps({"file": "old.py", "range": "main", "sections": []}),
        )
    )
    session.commit()
    with (
        mock.patch.object(artifact_service, "live_diff_base", return_value=_FAKE_SHA),
        mock.patch.object(
            artifact_service, "capture_git_diff", return_value=_TICKET_LOCAL
        ) as capture,
    ):
        result = ensure_diff_artifact(session, ticket=ticket, workspace=workspace)
    capture.assert_called_once()
    assert capture.call_args.kwargs == {"against": _FAKE_SHA}
    assert result == _TICKET_LOCAL


def test_against_set_never_calls_git_base_ref(tmp_path):
    """R1: against-set has no fallback to git_base_ref / _PREFERRED_BASE / vs-index."""
    empty_root = tmp_path / "empty"
    dirty_root = tmp_path / "dirty"
    empty_root.mkdir()
    dirty_root.mkdir()
    empty_graph = _ticket_local_graph(empty_root, commit_ticket=False, dirty_foo=False)
    dirty_graph = _ticket_local_graph(dirty_root)
    staged = empty_graph.checkout / "staged.txt"
    staged.write_text("staged vs-index bait\n")
    git(empty_graph.checkout, "add", "--", "staged.txt")
    staged.unlink()
    empty_ws = _capture_ws(empty_graph.repo)
    dirty_ws = _capture_ws(dirty_graph.repo)
    with mock.patch.object(artifact_service, "git_base_ref", return_value="main") as base:
        empty = capture_git_diff(empty_ws, empty_graph.checkout, against=empty_graph.merge_base)
        failed = capture_git_diff(empty_ws, empty_graph.checkout, against="this-ref-does-not-exist")
        hit = capture_git_diff(dirty_ws, dirty_graph.checkout, against=dirty_graph.merge_base)
    assert empty is None
    assert failed is None
    assert hit is not None
    assert hit["range"] == dirty_graph.merge_base
    base.assert_not_called()


def test_short_sha_against_is_emitted_as_range_without_expansion(tmp_path):
    """R1: no SHA-shape check and no rev-parse rewrite. range is the string passed."""
    graph = _ticket_local_graph(tmp_path)
    ws = _capture_ws(graph.repo)
    short = graph.merge_base[:7]
    diff = capture_git_diff(ws, graph.checkout, against=short)
    assert diff is not None
    assert diff["range"] == short
    assert diff["range"] != graph.merge_base
    assert "ticket.py" in _section_paths(diff)
    assert "server/foo.py" in _section_paths(diff)


def test_ensure_reuses_after_recapture_on_the_same_sha(session, tmp_path):
    """R4 order: first ensure recaptures vs-main; second ensure reuses that SHA."""
    graph = _ticket_local_graph(tmp_path)
    workspace = _graph_workspace(session, graph.repo)
    ticket = make_ticket(session, workspace)
    _bind_worktree(session, workspace, ticket, graph)
    _plant_vs_main(session, ticket)
    first = ensure_diff_artifact(session, ticket=ticket, workspace=workspace)
    assert first is not None
    assert first["range"] == graph.merge_base
    with mock.patch.object(artifact_service, "capture_git_diff", wraps=capture_git_diff) as capture:
        second = ensure_diff_artifact(session, ticket=ticket, workspace=workspace)
    capture.assert_not_called()
    assert second is not None
    assert second["range"] == graph.merge_base
    assert _section_paths(second) == _section_paths(first)
