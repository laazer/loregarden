"""Tests for branch triage snapshot and diff review."""

import subprocess
from enum import Enum
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from loregarden.agents.executors.permission_bridge import (
    BRANCH_TRIAGE_STAGE_KEY,
    BridgeResult,
)
from loregarden.config import settings
from loregarden.models.domain import (
    AgentRun,
    BranchTriageMessage,
    RunStatus,
    Ticket,
    Workspace,
)
from loregarden.services.branch_triage_chat_service import invoke_branch_triage_model
from loregarden.services.branch_triage_run_service import (
    CANCELLED_TURN_MESSAGE,
    fail_interrupted_branch_triage_turns,
)
from loregarden.services.branch_triage_service import (
    branch_activity,
    branch_triage_snapshot,
    commit_snapshot,
    delete_branch,
    remove_branch_worktree,
)
from loregarden.services.file_editor import _list_branches
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session, select
from tests.repo_templates import from_template


def _init_repo(path: Path) -> None:
    subprocess.run(
        ["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True, text=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=path, check=True, capture_output=True
    )
    (path / "README.md").write_text("# test\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)


def _build_triage_repo(repo: Path) -> None:
    repo.mkdir()
    _init_repo(repo)
    subprocess.run(
        ["git", "branch", "loregarden/orphan"], cwd=repo, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "checkout", "loregarden/orphan"], cwd=repo, check=True, capture_output=True
    )
    (repo / "orphan.txt").write_text("orphan\n", encoding="utf-8")
    subprocess.run(["git", "add", "orphan.txt"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "orphan work"], cwd=repo, check=True, capture_output=True
    )
    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def triage_repo(tmp_path, monkeypatch):
    repo = from_template(tmp_path / "loregarden", "branch_triage", _build_triage_repo)
    monkeypatch.setenv("LOREGARDEN_REPO_ROOT", str(repo))
    monkeypatch.setattr("loregarden.config.settings.repo_root", repo.resolve())
    monkeypatch.setattr(settings, "browse_root", str(tmp_path))
    return repo


@pytest.fixture
def triage_session(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture
def triage_workspace(triage_repo, triage_session: Session) -> Workspace:
    ws = Workspace(name="Demo", slug="demo", repo_path=".")
    triage_session.add(ws)
    triage_session.commit()
    triage_session.refresh(ws)
    return ws


def test_branch_triage_flags_orphan_branch(triage_workspace, triage_session: Session):
    snapshot = branch_triage_snapshot(triage_session, triage_workspace)
    orphan = next(b for b in snapshot["branches"] if b["name"] == "loregarden/orphan")
    codes = {issue["code"] for issue in orphan["issues"]}
    assert "no_ticket" in codes
    assert orphan["ahead"] >= 1


def test_branch_triage_links_ticket(triage_workspace, triage_session: Session):
    ticket = Ticket(
        external_id="TK-orphan",
        workspace_id=triage_workspace.id,
        title="Orphan branch ticket",
        branch="loregarden/orphan",
    )
    triage_session.add(ticket)
    triage_session.commit()

    snapshot = branch_triage_snapshot(triage_session, triage_workspace)
    orphan = next(b for b in snapshot["branches"] if b["name"] == "loregarden/orphan")
    assert len(orphan["linked_tickets"]) == 1
    assert orphan["linked_tickets"][0]["external_id"] == "TK-orphan"


def test_branch_triage_treats_squash_merged_branch_as_not_ahead(
    triage_workspace, triage_repo, triage_session: Session
):
    subprocess.run(
        ["git", "checkout", "-b", "feature/squashed"],
        cwd=triage_repo,
        check=True,
        capture_output=True,
    )
    (triage_repo / "squash.txt").write_text("v1\n", encoding="utf-8")
    subprocess.run(["git", "add", "squash.txt"], cwd=triage_repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "wip 1"], cwd=triage_repo, check=True, capture_output=True
    )
    (triage_repo / "squash.txt").write_text("v2\n", encoding="utf-8")
    subprocess.run(
        ["git", "commit", "-am", "wip 2"], cwd=triage_repo, check=True, capture_output=True
    )

    subprocess.run(["git", "checkout", "main"], cwd=triage_repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "merge", "--squash", "feature/squashed"],
        cwd=triage_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "feature/squashed (squashed)"],
        cwd=triage_repo,
        check=True,
        capture_output=True,
    )

    snapshot = branch_triage_snapshot(triage_session, triage_workspace)
    squashed = next(b for b in snapshot["branches"] if b["name"] == "feature/squashed")
    assert squashed["ahead"] == 0
    # Zeroed `ahead` cannot say *why*. Branch cleanup offers to delete this
    # branch, so it has to be able to tell "squashed onto the base" from "never
    # had a commit of its own".
    assert squashed["squash_merged"] is True
    base = next(b for b in snapshot["branches"] if b["is_base"])
    assert base["squash_merged"] is False
    codes = {issue["code"] for issue in squashed["issues"]}
    assert "diverged" not in codes


def test_branch_triage_matches_the_batched_scan_when_for_each_ref_is_unavailable(
    triage_workspace, triage_repo, triage_session: Session, monkeypatch
):
    subprocess.run(
        ["git", "branch", "feature/fallback"], cwd=triage_repo, check=True, capture_output=True
    )
    batched = branch_triage_snapshot(triage_session, triage_workspace)
    monkeypatch.setattr(
        "loregarden.services.branch_triage_service._branch_refs_batch",
        lambda repo_root, base, remote_names: None,
    )
    per_branch = branch_triage_snapshot(triage_session, triage_workspace)

    assert per_branch == batched


def test_delete_unmerged_branch_requires_force(
    triage_workspace, triage_repo, triage_session: Session
):
    subprocess.run(
        ["git", "checkout", "-b", "feature/unmerged"],
        cwd=triage_repo,
        check=True,
        capture_output=True,
    )
    (triage_repo / "unmerged.txt").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "unmerged.txt"], cwd=triage_repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "unmerged"],
        cwd=triage_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "checkout", "main"], cwd=triage_repo, check=True, capture_output=True)

    with pytest.raises(ValueError, match="not fully merged"):
        delete_branch(triage_session, triage_workspace, "feature/unmerged", force=False)

    # The session is the first argument now (lg-external-harness-1026 (AC14/AC18)): the receipt gate resolves a
    # worktree's owner from the database, so every removal path needs one.
    delete_branch(triage_session, triage_workspace, "feature/unmerged", force=True)
    branch_names = _list_branches(resolve_workspace_root(triage_workspace))
    assert "feature/unmerged" not in branch_names


def test_delete_branch_is_idempotent_when_already_gone(
    triage_workspace, triage_repo, triage_session: Session
):
    subprocess.run(
        ["git", "branch", "feature/gone"],
        cwd=triage_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "branch", "-D", "feature/gone"],
        cwd=triage_repo,
        check=True,
        capture_output=True,
    )

    # `.deleted`, not the bare bool (lg-external-harness-1026 (AC14/AC18)): the answer carries the typed
    # outcome the three operator surfaces branch on. False still means "it was
    # already gone".
    assert (
        delete_branch(triage_session, triage_workspace, "feature/gone", force=True).deleted is False
    )


def test_delete_branch_api_already_gone(client: TestClient, triage_workspace):
    res = client.post(
        f"/api/workspaces/{triage_workspace.slug}/branch-triage/delete",
        params={"branch": "feature/missing"},
        json={"force": True},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["deleted"] == "feature/missing"
    assert body["already_gone"] is True


def test_delete_branch_blocked_by_worktree(
    triage_workspace, triage_repo, triage_session: Session, tmp_path
):
    subprocess.run(
        ["git", "branch", "feature/worktree"],
        cwd=triage_repo,
        check=True,
        capture_output=True,
    )
    wt_path = tmp_path / "wt-feature-worktree"
    subprocess.run(
        ["git", "worktree", "add", str(wt_path), "feature/worktree"],
        cwd=triage_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "checkout", "main"], cwd=triage_repo, check=True, capture_output=True)

    with pytest.raises(ValueError, match="worktree"):
        delete_branch(triage_session, triage_workspace, "feature/worktree", force=True)

    # BEHAVIOUR THE SPEC NO LONGER WANTS (lg-external-harness-1026 (AC14/AC18)). This worktree was cut
    # by raw git and has no `worktrees` row, so there is no owner to resolve a
    # receipt for and no record of whether the work it holds finished. AC14's
    # last sentence makes that a REFUSAL that is reported, not performed —
    # which is the branch-triage half of the bug this ticket closes — so the
    # branch survives with it. The blocked-without-remove_worktrees half above
    # is unchanged.
    report = delete_branch(
        triage_session,
        triage_workspace,
        "feature/worktree",
        force=True,
        remove_worktrees=True,
    )
    assert report.outcome.value == "refused"
    assert report.deleted is False
    assert report.reason and report.detail
    assert wt_path.is_dir()
    branch_names = _list_branches(resolve_workspace_root(triage_workspace))
    assert "feature/worktree" in branch_names


def test_remove_branch_worktree_leaves_branch_intact(
    triage_workspace, triage_repo, triage_session: Session, tmp_path
):
    subprocess.run(
        ["git", "branch", "feature/wt-cleanup"], cwd=triage_repo, check=True, capture_output=True
    )
    wt_path = tmp_path / "wt-feature-cleanup"
    subprocess.run(
        ["git", "worktree", "add", str(wt_path), "feature/wt-cleanup"],
        cwd=triage_repo,
        check=True,
        capture_output=True,
    )

    snapshot = branch_triage_snapshot(triage_session, triage_workspace)
    branch = next(b for b in snapshot["branches"] if b["name"] == "feature/wt-cleanup")
    assert len(branch["worktrees"]) == 1
    assert branch["worktrees"][0]["is_primary"] is False

    # BEHAVIOUR THE SPEC NO LONGER WANTS (lg-external-harness-1026 (AC14/AC18)): a path git has
    # registered and the database has not is refused and reported. What this
    # test is named for still holds — the branch is left alone either way.
    report = remove_branch_worktree(
        triage_session, triage_workspace, "feature/wt-cleanup", str(wt_path)
    )

    assert report.outcome.value == "refused"
    branch_names = _list_branches(resolve_workspace_root(triage_workspace))
    assert "feature/wt-cleanup" in branch_names
    assert wt_path.is_dir()


def test_remove_branch_worktree_rejects_unknown_path(
    triage_workspace, triage_repo, triage_session: Session
):
    subprocess.run(
        ["git", "branch", "feature/no-worktree"],
        cwd=triage_repo,
        check=True,
        capture_output=True,
    )
    with pytest.raises(ValueError, match="No worktree"):
        remove_branch_worktree(
            triage_session, triage_workspace, "feature/no-worktree", "/tmp/not-a-worktree"
        )


def test_remove_branch_worktree_rejects_primary_checkout(
    triage_workspace, triage_repo, triage_session: Session
):
    with pytest.raises(ValueError, match="primary repository checkout"):
        remove_branch_worktree(triage_session, triage_workspace, "main", str(triage_repo.resolve()))


def test_remove_worktree_api(client: TestClient, triage_repo, db_session: Session, tmp_path):
    ws = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    assert ws is not None
    # The client fixture repoints the seeded workspace at its own throwaway repo;
    # restore the relative repo_path so it resolves against triage_repo instead.
    ws.repo_path = "."
    db_session.add(ws)
    db_session.commit()

    subprocess.run(
        ["git", "branch", "feature/wt-api"], cwd=triage_repo, check=True, capture_output=True
    )
    wt_path = tmp_path / "wt-feature-api"
    subprocess.run(
        ["git", "worktree", "add", str(wt_path), "feature/wt-api"],
        cwd=triage_repo,
        check=True,
        capture_output=True,
    )

    res = client.post(
        f"/api/workspaces/{ws.slug}/branch-triage/worktrees/remove",
        params={"branch": "feature/wt-api"},
        json={"path": str(wt_path)},
    )
    # BEHAVIOUR THE SPEC NO LONGER WANTS (lg-external-harness-1026 (AC14/AC18)): this worktree has no
    # `worktrees` row, so the gate refuses it — a 200 carrying `refused`, not a
    # 4xx, because the gate worked and nothing was lost. The two pre-existing
    # keys keep their meaning beside the new ones.
    assert res.status_code == 200
    body = res.json()
    assert body["branch"] == "feature/wt-api"
    assert body["removed_path"] == str(wt_path)
    assert body["outcome"] == "refused"
    assert body["reason"] and body["detail"]
    assert body["parked_path"] == ""
    assert wt_path.is_dir()

    # A path that is not a registered worktree at all is still a 400: that is
    # a bad request, not a gate decision.
    bad = client.post(
        f"/api/workspaces/{ws.slug}/branch-triage/worktrees/remove",
        params={"branch": "feature/wt-api"},
        json={"path": str(wt_path / "nowhere")},
    )
    assert bad.status_code == 400


def test_branch_diff_endpoint(client: TestClient, triage_repo, db_session: Session):
    ws = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    assert ws is not None
    # The client fixture repoints the seeded workspace at its own throwaway repo;
    # restore the relative repo_path so it resolves against triage_repo instead.
    ws.repo_path = "."
    db_session.add(ws)
    db_session.commit()

    res = client.get(
        f"/api/workspaces/{ws.slug}/branch-triage/diff",
        params={"branch": "main"},
    )
    assert res.status_code == 404

    subprocess.run(
        ["git", "branch", "feature/triage"], cwd=triage_repo, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "checkout", "feature/triage"], cwd=triage_repo, check=True, capture_output=True
    )
    (triage_repo / "feature.txt").write_text("feature\n", encoding="utf-8")
    subprocess.run(["git", "add", "feature.txt"], cwd=triage_repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "feature work"], cwd=triage_repo, check=True, capture_output=True
    )
    subprocess.run(["git", "checkout", "main"], cwd=triage_repo, check=True, capture_output=True)

    res = client.get(
        f"/api/workspaces/{ws.slug}/branch-triage/diff",
        params={"branch": "feature/triage"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["branch"] == "feature/triage"
    diff = body["diff"]
    assert diff["file_entries"]
    assert diff["file_entries"][0]["path"] == "feature.txt"
    assert diff["sections"] == []

    file_res = client.get(
        f"/api/workspaces/{ws.slug}/branch-triage/diff",
        params={"branch": "feature/triage", "file": "feature.txt"},
    )
    assert file_res.status_code == 200
    file_diff = file_res.json()["diff"]
    assert file_diff["sections"]
    assert file_diff["sections"][0]["path"] == "feature.txt"
    assert file_diff["sections"][0]["lines"]


def test_branch_diff_remote_and_working_tree_modes(
    client: TestClient, triage_repo, triage_workspace, triage_session: Session
):
    ws = triage_workspace

    subprocess.run(
        ["git", "checkout", "-b", "feature/remote"],
        cwd=triage_repo,
        check=True,
        capture_output=True,
    )
    (triage_repo / "remote.txt").write_text("v1\n", encoding="utf-8")
    subprocess.run(["git", "add", "remote.txt"], cwd=triage_repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "v1"], cwd=triage_repo, check=True, capture_output=True)
    v1_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=triage_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/feature/remote", v1_sha],
        cwd=triage_repo,
        check=True,
        capture_output=True,
    )
    (triage_repo / "remote.txt").write_text("v2\n", encoding="utf-8")
    subprocess.run(["git", "commit", "-am", "v2"], cwd=triage_repo, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "main"], cwd=triage_repo, check=True, capture_output=True)

    remote = client.get(
        f"/api/workspaces/{ws.slug}/branch-triage/diff",
        params={"branch": "feature/remote", "mode": "remote"},
    )
    assert remote.status_code == 200
    assert remote.json()["mode"] == "remote"
    remote_diff = remote.json()["diff"]
    assert remote_diff["file_entries"]
    assert remote_diff["sections"] == []

    remote_file = client.get(
        f"/api/workspaces/{ws.slug}/branch-triage/diff",
        params={"branch": "feature/remote", "mode": "remote", "file": "remote.txt"},
    )
    assert remote_file.status_code == 200
    assert remote_file.json()["diff"]["sections"]

    (triage_repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    unstaged = client.get(
        f"/api/workspaces/{ws.slug}/branch-triage/diff",
        params={"branch": "main", "mode": "unstaged"},
    )
    assert unstaged.status_code == 200
    body = unstaged.json()["diff"]
    assert body["range"] == "unstaged changes"
    assert body["file_entries"]
    assert body["sections"] == []

    unstaged_file = client.get(
        f"/api/workspaces/{ws.slug}/branch-triage/diff",
        params={"branch": "main", "mode": "unstaged", "file": "dirty.txt"},
    )
    assert unstaged_file.status_code == 200
    file_body = unstaged_file.json()["diff"]
    assert file_body["sections"]
    assert file_body["sections"][0]["lines"][0]["type"] == "a"

    snapshot = branch_triage_snapshot(triage_session, ws)
    main = next(item for item in snapshot["branches"] if item["name"] == "main")
    remote_branch = next(item for item in snapshot["branches"] if item["name"] == "feature/remote")
    assert main["is_current"] is True
    assert any(option["mode"] == "unstaged" for option in main["diff_options"])
    assert any(option["mode"] == "remote" for option in remote_branch["diff_options"])
    assert not any(option["mode"] == "unstaged" for option in remote_branch["diff_options"])


def test_branch_diff_comments_and_submit(
    client: TestClient, triage_repo, db_session: Session, monkeypatch
):
    ws = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    assert ws is not None

    subprocess.run(
        ["git", "branch", "feature/review"], cwd=triage_repo, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "checkout", "feature/review"], cwd=triage_repo, check=True, capture_output=True
    )
    (triage_repo / "review.txt").write_text("review\n", encoding="utf-8")
    subprocess.run(["git", "add", "review.txt"], cwd=triage_repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "review work"], cwd=triage_repo, check=True, capture_output=True
    )
    subprocess.run(["git", "checkout", "main"], cwd=triage_repo, check=True, capture_output=True)

    ticket = Ticket(
        external_id="TK-review",
        workspace_id=ws.id,
        title="Review branch ticket",
        branch="feature/review",
    )
    db_session.add(ticket)
    db_session.commit()

    branch = "feature/review"
    created = client.post(
        f"/api/workspaces/{ws.slug}/branch-triage/diff-comments",
        params={"branch": branch},
        json={
            "file_path": "review.txt",
            "line_index": 0,
            "line_kind": "a",
            "content": "Drop this file before merge",
        },
    )
    assert created.status_code == 200

    listed = client.get(
        f"/api/workspaces/{ws.slug}/branch-triage/diff-comments",
        params={"branch": branch},
    )
    assert listed.status_code == 200
    assert listed.json()["total"] == 1

    monkeypatch.setenv("LOREGARDEN_TRIAGE_STUB_RESPONSE", "Acknowledged.")
    submitted = client.post(
        f"/api/workspaces/{ws.slug}/branch-triage/diff-comments/submit-to-agent",
        params={"branch": branch},
        json={"instructions": "Please clean up"},
    )
    assert submitted.status_code == 200
    payload = submitted.json()
    assert payload["ticket_id"] == ticket.id
    assert payload["submitted_comments"] == 1


def test_branch_chat_messages(client: TestClient, triage_repo, db_session: Session, monkeypatch):
    ws = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    assert ws is not None

    subprocess.run(
        ["git", "branch", "feature/chat"], cwd=triage_repo, check=True, capture_output=True
    )

    empty = client.get(
        "/api/workspaces/loregarden/branch-triage/chat",
        params={"branch": "feature/chat"},
    )
    assert empty.status_code == 200
    assert empty.json()["messages"] == []

    assert empty.json()["run_status"] == "idle"

    monkeypatch.setenv(
        "LOREGARDEN_TRIAGE_STUB_RESPONSE",
        "Checkout main and delete this branch.\n"
        '```loregarden\n{"primitive":"thinking","content":"weighing it"}\n```\n',
    )
    sent = client.post(
        "/api/workspaces/loregarden/branch-triage/chat/messages",
        params={"branch": "feature/chat"},
        json={"content": "Should I delete this branch?"},
    )
    assert sent.status_code == 202
    payload = sent.json()
    assert "assistant_message" not in payload
    assert payload["status"] == "queued"

    snapshot = client.get(
        "/api/workspaces/loregarden/branch-triage/chat",
        params={"branch": "feature/chat"},
    )
    assert snapshot.status_code == 200
    body = snapshot.json()
    assert len(body["messages"]) == 2
    assert body["run_status"] == "idle"
    assert "Checkout main and delete this branch." in body["messages"][-1]["content"]
    # Parts are stored with the turn, so the card survives a reload.
    assert any(part.get("primitive") == "thinking" for part in body["messages"][-1]["parts"])

    # ...and the turn's run is settled, so its approvals have a closed parent.
    assistant = db_session.get(BranchTriageMessage, body["messages"][-1]["id"])
    assert assistant is not None
    assert assistant.run_id
    run = db_session.get(AgentRun, assistant.run_id)
    assert run is not None
    assert run.status == RunStatus.SUCCEEDED
    assert run.finished_at is not None


def test_branch_chat_send_does_not_run_the_turn_on_the_request_thread(
    client: TestClient, triage_repo, db_session: Session
):
    """The POST hands off and returns: the turn is queued, not executed inline.

    Patching the scheduler proves the request path never invokes the CLI — the
    property that kept the old blocking endpoint hostage to a 600s subprocess.
    """
    subprocess.run(
        ["git", "branch", "feature/async"], cwd=triage_repo, check=True, capture_output=True
    )

    with patch("loregarden.api.branch_triage.schedule_branch_triage_turn") as scheduled:
        sent = client.post(
            "/api/workspaces/loregarden/branch-triage/chat/messages",
            params={"branch": "feature/async"},
            json={"content": "async please"},
        )

    assert sent.status_code == 202
    payload = sent.json()
    assert payload["status"] == "queued"
    assert "assistant_message" not in payload
    scheduled.assert_called_once_with(payload["active_turn_id"])

    # The turn is durably queued, so the UI reports "running" from server state alone.
    body = client.get(
        "/api/workspaces/loregarden/branch-triage/chat",
        params={"branch": "feature/async"},
    ).json()
    assert body["run_status"] == "running"
    assert body["active_turn_id"] == payload["active_turn_id"]
    assert [m["role"] for m in body["messages"]] == ["user"]

    assistant = db_session.get(BranchTriageMessage, payload["active_turn_id"])
    assert assistant is not None
    assert assistant.run_id
    run = db_session.get(AgentRun, assistant.run_id)
    assert run is not None
    assert run.ticket_id is None
    assert run.workspace_id == assistant.workspace_id
    assert run.stage_key == BRANCH_TRIAGE_STAGE_KEY
    assert run.status == RunStatus.QUEUED


def test_branch_chat_uses_permission_bridge_for_checked_out_branch(
    triage_workspace: Workspace,
    triage_repo: Path,
    triage_session: Session,
    monkeypatch,
):
    from loregarden.services import agent_turn_runner, branch_triage_chat_service

    captured: dict[str, object] = {}
    monkeypatch.delenv("LOREGARDEN_TRIAGE_STUB_RESPONSE", raising=False)
    monkeypatch.setattr(
        branch_triage_chat_service, "resolve_effective_adapter", lambda **_: "claude"
    )
    monkeypatch.setattr(branch_triage_chat_service, "_branch_entry", lambda *_: None)

    def fake_invocation(**kwargs):
        captured["root"] = kwargs["workspace_root"]
        return MagicMock(
            adapter="claude",
            argv=["claude"],
            cwd=str(kwargs["workspace_root"]),
            resume_session_id="",
        )

    def fake_bridge(self, **kwargs):
        captured["bridge"] = kwargs
        return BridgeResult(
            status=RunStatus.SUCCEEDED,
            stdout='{"type":"result","result":"changed the branch"}',
            stderr="",
        )

    monkeypatch.setattr(agent_turn_runner, "build_interactive_invocation", fake_invocation)
    monkeypatch.setattr(agent_turn_runner.PermissionBridgeRunner, "run", fake_bridge)

    triage_session.add(
        AgentRun(
            id="run_branch",
            run_code="run_branch",
            workspace_id=triage_workspace.id,
            agent_id="triage",
            stage_key=BRANCH_TRIAGE_STAGE_KEY,
            status=RunStatus.RUNNING,
        )
    )
    triage_session.commit()

    reply = invoke_branch_triage_model(
        triage_session,
        triage_workspace,
        "main",
        "Fix the branch",
        run_id="run_branch",
    )

    assert reply == "changed the branch"
    assert captured["root"] == triage_repo.resolve()
    bridge_kwargs = captured["bridge"]
    assert bridge_kwargs["workspace"] is triage_workspace
    assert bridge_kwargs["workspace_stage_key"] == BRANCH_TRIAGE_STAGE_KEY
    assert "real file, shell, git" in bridge_kwargs["prompt"]


def test_branch_chat_stays_advisory_without_a_checkout(
    triage_workspace: Workspace,
    triage_session: Session,
    monkeypatch,
):
    from loregarden.services import agent_turn_runner, branch_triage_chat_service

    captured: dict[str, str] = {}
    monkeypatch.delenv("LOREGARDEN_TRIAGE_STUB_RESPONSE", raising=False)
    monkeypatch.setattr(
        branch_triage_chat_service, "resolve_effective_adapter", lambda **_: "claude"
    )
    monkeypatch.setattr(branch_triage_chat_service, "_branch_entry", lambda *_: None)

    def fake_one_shot(_profile, *, prompt, **_kwargs):
        captured["prompt"] = prompt
        captured["read_only"] = str(_kwargs.get("read_only"))
        return "check it out first"

    monkeypatch.setattr(agent_turn_runner, "run_cli_agent_turn", fake_one_shot)

    reply = invoke_branch_triage_model(
        triage_session,
        triage_workspace,
        "loregarden/orphan",
        "Edit this branch",
        run_id="run_branch",
    )

    assert reply == "check it out first"
    assert "advisory only" in captured["prompt"]
    assert "not checked out in a worktree" in captured["prompt"]
    assert captured["read_only"] == "True"


def test_branch_chat_codex_executes_on_checked_out_branch(
    triage_workspace: Workspace,
    triage_repo: Path,
    triage_session: Session,
    monkeypatch,
):
    """Writable oneshot adapters execute when a checkout exists — not Claude-only."""
    from loregarden.services import agent_turn_runner, branch_triage_chat_service
    from loregarden.services.agent_turn_runner import AgentTurnResult

    captured: dict[str, object] = {}
    monkeypatch.delenv("LOREGARDEN_TRIAGE_STUB_RESPONSE", raising=False)
    monkeypatch.setattr(
        branch_triage_chat_service, "resolve_effective_adapter", lambda **_: "codex"
    )
    monkeypatch.setattr(branch_triage_chat_service, "_branch_entry", lambda *_: None)

    def fake_turn(request):
        captured["intent"] = request.intent
        captured["adapter"] = request.adapter
        captured["prompt"] = request.prompt
        captured["workspace_root"] = request.workspace_root
        return AgentTurnResult(
            reply="edited on codex",
            strategy="writable_oneshot",
            adapter="codex",
            run_id=request.run_id,
        )

    monkeypatch.setattr(agent_turn_runner, "run_agent_turn", fake_turn)
    monkeypatch.setattr(branch_triage_chat_service, "run_agent_turn", fake_turn)

    reply = invoke_branch_triage_model(
        triage_session,
        triage_workspace,
        "main",
        "Fix the branch",
        run_id="run_branch",
    )

    assert reply == "edited on codex"
    assert captured["adapter"] == "codex"
    assert captured["intent"] == "execute"
    assert captured["workspace_root"] == triage_repo.resolve()
    prompt = str(captured["prompt"])
    assert "real file, shell, git" in prompt
    assert "advisory only" not in prompt


def test_branch_chat_rejects_a_second_turn_while_one_is_in_flight(
    client: TestClient, triage_repo, db_session: Session
):
    ws = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    assert ws is not None
    subprocess.run(
        ["git", "branch", "feature/busy"], cwd=triage_repo, check=True, capture_output=True
    )
    db_session.add(
        BranchTriageMessage(
            workspace_id=ws.id,
            branch="feature/busy",
            role="assistant",
            content="",
            status="pending",
        )
    )
    db_session.commit()

    res = client.post(
        "/api/workspaces/loregarden/branch-triage/chat/messages",
        params={"branch": "feature/busy"},
        json={"content": "second message"},
    )
    assert res.status_code == 409

    snapshot = client.get(
        "/api/workspaces/loregarden/branch-triage/chat",
        params={"branch": "feature/busy"},
    ).json()
    assert snapshot["run_status"] == "running"
    # A pending turn has no content yet, so it must not surface as an empty reply.
    assert snapshot["messages"] == []


def test_interrupted_branch_chat_turn_is_settled_so_the_composer_recovers(
    client: TestClient, triage_repo, db_session: Session
):
    """A restart mid-turn must not leave the branch stuck 'running' forever."""
    ws = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    assert ws is not None
    subprocess.run(
        ["git", "branch", "feature/orphan"], cwd=triage_repo, check=True, capture_output=True
    )
    db_session.add(
        BranchTriageMessage(
            workspace_id=ws.id,
            branch="feature/orphan",
            role="assistant",
            content="",
            status="pending",
        )
    )
    db_session.commit()

    settled = fail_interrupted_branch_triage_turns(db_session)
    assert len(settled) == 1

    snapshot = client.get(
        "/api/workspaces/loregarden/branch-triage/chat",
        params={"branch": "feature/orphan"},
    ).json()
    assert snapshot["run_status"] == "idle"
    assert len(snapshot["messages"]) == 1


def test_stopping_a_branch_chat_turn_unlocks_the_composer(
    client: TestClient, triage_repo, db_session: Session
):
    """A turn whose CLI hangs outlives the restart reaper — the operator needs an exit."""
    ws = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    assert ws is not None
    subprocess.run(
        ["git", "branch", "feature/hung"], cwd=triage_repo, check=True, capture_output=True
    )
    run = AgentRun(
        run_code="run_stop01",
        ticket_id=None,
        workspace_id=ws.id,
        agent_id="triage",
        stage_key=BRANCH_TRIAGE_STAGE_KEY,
        status=RunStatus.RUNNING,
    )
    db_session.add(run)
    db_session.add(
        BranchTriageMessage(
            workspace_id=ws.id,
            branch="feature/hung",
            role="assistant",
            content="",
            status="pending",
            run_id=run.id,
        )
    )
    db_session.commit()

    res = client.post(
        "/api/workspaces/loregarden/branch-triage/chat/stop",
        params={"branch": "feature/hung"},
    )
    assert res.status_code == 200
    snapshot = res.json()
    assert snapshot["run_status"] == "idle"
    assert snapshot["active_turn_id"] is None
    assert [msg["content"] for msg in snapshot["messages"]] == [CANCELLED_TURN_MESSAGE]

    # The composer is only genuinely unlocked if the next send is accepted.
    db_session.expire_all()
    stopped_run = db_session.get(AgentRun, run.id)
    assert stopped_run is not None
    assert stopped_run.status == RunStatus.FAILED
    assert stopped_run.cancel_requested_at is not None


def test_stopping_an_idle_branch_chat_is_a_no_op(
    client: TestClient, triage_repo, db_session: Session
):
    """The recovery path has to answer from whatever state the operator is looking at."""
    subprocess.run(
        ["git", "branch", "feature/quiet"], cwd=triage_repo, check=True, capture_output=True
    )

    res = client.post(
        "/api/workspaces/loregarden/branch-triage/chat/stop",
        params={"branch": "feature/quiet"},
    )
    assert res.status_code == 200
    assert res.json()["run_status"] == "idle"
    assert res.json()["messages"] == []


def test_branch_activity_marks_unpushed_commits_as_local(triage_workspace, triage_repo, tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", str(remote)], check=True, capture_output=True, text=True
    )
    subprocess.run(
        ["git", "remote", "add", "origin", str(remote)],
        cwd=triage_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "push", "-u", "origin", "main"], cwd=triage_repo, check=True, capture_output=True
    )

    (triage_repo / "local.txt").write_text("local\n", encoding="utf-8")
    subprocess.run(["git", "add", "local.txt"], cwd=triage_repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "local only"], cwd=triage_repo, check=True, capture_output=True
    )

    activity = branch_activity(triage_workspace, "main")

    assert activity["branch"] == "main"
    assert activity["upstream"]
    assert [commit["message"] for commit in activity["commits"]] == ["local only", "init"]
    pushed_by_message = {c["message"]: c["pushed"] for c in activity["commits"]}
    assert pushed_by_message["local only"] is False
    assert pushed_by_message["init"] is True


def test_branch_activity_without_upstream_reports_nothing_pushed(triage_workspace):
    activity = branch_activity(triage_workspace, "loregarden/orphan")

    assert activity["upstream"] is None
    assert activity["commits"]
    assert all(commit["pushed"] is False for commit in activity["commits"])


def test_commit_snapshot_reads_metadata_and_stats(triage_workspace):
    commit = commit_snapshot(triage_workspace, "HEAD")

    assert commit["message"] == "init"
    assert commit["short_sha"]
    assert commit["author"]
    assert commit["files_changed"] == 1
    assert commit["insertions"] == 1
    assert commit["deletions"] == 0
    assert commit["pushed"] is False


def test_commit_snapshot_rejects_arbitrary_revision(triage_workspace):
    with pytest.raises(ValueError, match="Commit ref"):
        commit_snapshot(triage_workspace, "--all")


def test_branch_activity_endpoint_honours_limit(
    client: TestClient, triage_repo, db_session: Session
):
    ws = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    assert ws is not None
    ws.repo_path = "."
    db_session.add(ws)
    db_session.commit()

    for index in range(3):
        (triage_repo / f"f{index}.txt").write_text("x\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=triage_repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", f"commit {index}"],
            cwd=triage_repo,
            check=True,
            capture_output=True,
        )

    res = client.get(
        f"/api/workspaces/{ws.slug}/branch-triage/activity",
        params={"branch": "main", "limit": 2},
    )
    assert res.status_code == 200
    body = res.json()
    assert [c["message"] for c in body["commits"]] == ["commit 2", "commit 1"]
    assert all(c["short_sha"] and c["author"] for c in body["commits"])


# ===========================================================================
# lg-external-harness-1026 / AC22 — the outcome is a typed field, not the
# absence of an exception.
# ===========================================================================


def _gate_fixture(session, slug: str, root: Path):
    """A workspace with one ticket worktree the receipt gate will refuse.

    Committed, clean, ahead of its base — every observation of shape says
    "delete me" — and nothing on record says the work finished.

    Returns plain strings rather than ORM rows: the caller's session closes
    before the request runs, and a detached instance raises on attribute
    access rather than failing the assertion you wrote.
    """
    from loregarden.models.domain import AgentRun, RunStatus, Ticket, Workspace
    from loregarden.services.ticket_worktree import resolve_execution_root
    from tests.worktree_helpers import git as wgit
    from tests.worktree_helpers import make_repo

    repo = make_repo(root / f"gate-{slug}", name=f"repo-{slug}")
    ws = Workspace(slug=slug, name=slug, repo_path=str(repo))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    ticket = Ticket(
        external_id=f"{slug}-1",
        workspace_id=ws.id,
        title="Add the thing",
        branch=f"loregarden/{slug}-1",
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    run = AgentRun(
        run_code=f"run-{slug}",
        ticket_id=ticket.id,
        workspace_id=ws.id,
        agent_id="backend_implementer",
        status=RunStatus.RUNNING,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    path = resolve_execution_root(session, run, ticket, ws)
    (path / "work.txt").write_text("real work\n")
    wgit(path, "add", "-A")
    wgit(path, "commit", "-q", "-m", "work")
    return ws.slug, ticket.branch, repo, path


def test_the_delete_endpoint_carries_an_explicit_refused_outcome(client, isolated_db, tmp_path):
    """A 200 whose body says nothing is what let `BranchCleanupModal` count a
    refusal as a deletion. The outcome is the field the client branches on."""
    with Session(isolated_db) as session:
        slug, branch, _repo, path = _gate_fixture(session, "gateone", tmp_path)

    response = client.post(
        f"/api/workspaces/{slug}/branch-triage/delete",
        params={"branch": branch},
        json={"force": True, "remove_worktrees": True},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "refused"
    assert body["reason"], "a refusal with no reason cannot be grouped or explained"
    assert body["reason"] == body["reason"].lower().replace(" ", "_"), (
        "the reason is a stable key the client groups on, not a rendered sentence"
    )
    assert body["detail"]
    assert body["parked_path"] == ""
    # The pre-existing keys keep their meaning (AC22) — which is a claim about
    # their VALUES on this answer, not just their presence. A refusal leaves
    # the branch exactly where it was, so "it was not there" is false about it;
    # reporting the branch this gate just declined to touch as already gone
    # would be the same kind of record-false-about-its-own-subject this ticket
    # exists to delete, re-created in the body that reports the refusal.
    assert set(body) >= {"deleted", "already_gone", "removed_worktrees"}
    assert body["already_gone"] is False
    assert body["removed_worktrees"] is False
    assert path.is_dir()


def test_the_worktree_remove_endpoint_carries_the_same_three_value_outcome(
    client, isolated_db, tmp_path
):
    with Session(isolated_db) as session:
        slug, branch, _repo, path = _gate_fixture(session, "gatetwo", tmp_path)

    response = client.post(
        f"/api/workspaces/{slug}/branch-triage/worktrees/remove",
        params={"branch": branch},
        json={"path": str(path)},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "refused"
    assert body["reason"] and body["detail"]
    assert body["parked_path"] == ""
    assert set(body) >= {"branch", "removed_path"}
    assert path.is_dir()


def test_the_outcome_vocabulary_is_an_enum_and_not_three_string_literals():
    """The py-organization gate's rule, and the reason behind it: a literal
    compared in the API layer has nothing behind it when someone types
    `"refuse"` instead of `"refused"`."""
    from loregarden.models.domain import enums

    candidates = [
        member
        for member in vars(enums).values()
        if isinstance(member, type)
        and issubclass(member, Enum)
        and {m.value for m in member} == {"deleted", "refused", "parked"}
    ]
    assert candidates, (
        "no enum in models/domain/enums.py carries deleted | refused | parked — "
        "the API would be comparing bare string literals"
    )

    api_source = (
        Path(__file__).resolve().parents[1] / "loregarden" / "api" / "branch_triage.py"
    ).read_text(encoding="utf-8")
    for literal in ('"refused"', '"parked"', '"deleted"'):
        assert literal not in api_source, f"{literal} is compared as a bare string in the API layer"


def test_a_delete_the_gate_allows_still_reports_deleted(client, isolated_db, tmp_path):
    """The happy path keeps working, and keeps saying so in the new field."""
    from tests.worktree_helpers import grant_receipt

    with Session(isolated_db) as session:
        slug, branch, repo, path = _gate_fixture(session, "gatethree", tmp_path)
        ticket = session.exec(select(Ticket).where(Ticket.branch == branch)).one()
        grant_receipt(session, ticket, repo)

    response = client.post(
        f"/api/workspaces/{slug}/branch-triage/delete",
        params={"branch": branch},
        json={"force": True, "remove_worktrees": True},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "deleted"
    assert body["parked_path"] == ""
    assert not path.exists()
