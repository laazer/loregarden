"""Which running instance shows a build, starting one, and capturing its pages.

Matching and checkout run against a real git repository; the instance manager
is a stand-in, because launching a real control plane per test is the
Instances suite's job, not this one's.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest
from lore_eden.instances import (
    InstanceKind,
    InstanceListing,
    InstanceRole,
    InstanceState,
    InstanceView,
)
from loregarden.models.domain import Workspace
from loregarden.services import review_capture
from loregarden.services.git_subprocess import run_git
from loregarden.services.review_capture import CaptureError, CapturePaths, capture_pages
from loregarden.services.review_launcher import checkout_review_worktree, review_worktree_path
from loregarden.services.review_preview import (
    PreviewError,
    PreviewInstance,
    PreviewView,
    build_checkouts,
    preview_status,
    start_preview,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32
T0 = datetime(2026, 11, 22, tzinfo=timezone.utc)


def _git(cwd: Path, *args: str) -> str:
    return run_git(list(args), cwd=str(cwd), capture_output=True, text=True, check=True).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "ws" / "loregarden"
    root.mkdir(parents=True)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    (root / "marker.txt").write_text("main\n")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "main")
    _git(root, "branch", "feature/x")
    _git(root, "checkout", "-q", "-b", "integration/root-1")
    (root / "marker.txt").write_text("integration\n")
    _git(root, "commit", "-q", "-am", "integration")
    _git(root, "checkout", "-q", "main")
    return root


def _workspace(root: Path, slug: str = "loregarden") -> Workspace:
    return Workspace(slug=slug, name=slug, repo_path=str(root))


def _view(
    id_: str,
    kind: InstanceKind,
    worktree: str,
    *,
    state: InstanceState = InstanceState.READY,
    port: int = 5181,
    started: datetime = T0,
) -> InstanceView:
    return InstanceView(
        id=id_, project="loregarden", name=id_, kind=kind, role=InstanceRole.BRANCH, pid=1,
        host="127.0.0.1", port=port, url=f"http://127.0.0.1:{port}", labels={"worktree": worktree},
        started_at=started, state=state,
    )  # fmt: skip


def _manager(*views: InstanceView) -> mock.Mock:
    manager = mock.Mock()
    manager.list.return_value = InstanceListing(instances=list(views))
    return manager


def test_a_worktree_on_the_branch_is_the_build(repo: Path, tmp_path: Path):
    tree = tmp_path / "ws" / ".worktrees" / "feature-x"
    _git(repo, "worktree", "add", "-q", str(tree), "feature/x")

    assert build_checkouts(_workspace(repo), "feature/x") == {str(tree)}
    assert build_checkouts(_workspace(repo), "integration/root-1") == set()


def test_the_review_worktree_is_the_build_only_while_it_sits_at_the_tip(repo: Path):
    workspace = _workspace(repo)
    checkout_review_worktree(workspace, "integration/root-1")
    review = str(review_worktree_path(workspace))

    assert build_checkouts(workspace, "integration/root-1") == {review}
    # Detached at main's commit now: no longer a build of the integration branch.
    checkout_review_worktree(workspace, "main")
    assert review not in build_checkouts(workspace, "integration/root-1")


def test_status_picks_the_ready_client_running_the_build_not_another_branch(
    repo: Path, tmp_path: Path
):
    tree = tmp_path / "ws" / ".worktrees" / "feature-x"
    _git(repo, "worktree", "add", "-q", str(tree), "feature/x")
    manager = _manager(
        _view("other", InstanceKind.CLIENT, str(repo), port=5173),
        _view("dead", InstanceKind.CLIENT, str(tree), state=InstanceState.EXITED, port=5190),
        _view(
            "starting",
            InstanceKind.CLIENT,
            str(tree),
            state=InstanceState.STARTING,
            port=5191,
            started=T0 + timedelta(minutes=5),
        ),  # fmt: skip
        _view("ready", InstanceKind.CLIENT, str(tree), port=5192),
        _view("api", InstanceKind.SERVER, str(tree), port=8101),
    )

    view = preview_status(_workspace(repo), "feature/x", manager=manager)

    assert view.client is not None and view.client.id == "ready"
    assert view.server is not None and view.server.url == "http://127.0.0.1:8101"
    assert view.can_start is True


def test_another_workspace_is_told_where_to_start_one(repo: Path):
    view = preview_status(_workspace(repo, slug="blobert"), "main", manager=_manager())

    assert view.can_start is False
    assert "Instances page" in view.unavailable
    with pytest.raises(PreviewError, match="Instances page"):
        start_preview(_workspace(repo, slug="blobert"), "main", manager=_manager())


def test_start_reuses_a_running_client_and_launches_nothing(repo: Path, tmp_path: Path):
    tree = tmp_path / "ws" / ".worktrees" / "feature-x"
    _git(repo, "worktree", "add", "-q", str(tree), "feature/x")
    manager = _manager(_view("ready", InstanceKind.CLIENT, str(tree)))

    view = start_preview(_workspace(repo), "feature/x", manager=manager)

    assert view.client is not None and view.client.id == "ready"
    manager.launch.assert_not_called()


def test_start_launches_server_then_client_in_the_branch_worktree(repo: Path, tmp_path: Path):
    tree = tmp_path / "ws" / ".worktrees" / "feature-x"
    _git(repo, "worktree", "add", "-q", str(tree), "feature/x")
    manager = _manager()
    manager.launch.side_effect = [mock.Mock(id="srv-1"), mock.Mock(id="cli-1")]

    start_preview(_workspace(repo), "feature/x", manager=manager)

    server_req, client_req = (call.args[0] for call in manager.launch.call_args_list)
    assert server_req.template == "loregarden/server"
    assert server_req.params == {"worktree": str(tree), "database": "snapshot"}
    assert client_req.template == "loregarden/client"
    assert client_req.params == {"worktree": str(tree), "target": "srv-1"}


def test_start_moves_the_review_worktree_and_stops_what_ran_in_it(repo: Path):
    workspace = _workspace(repo)
    checkout_review_worktree(workspace, "main")
    review = str(review_worktree_path(workspace))
    manager = _manager(_view("stale", InstanceKind.CLIENT, review))
    manager.launch.side_effect = [mock.Mock(id="srv-1"), mock.Mock(id="cli-1")]

    start_preview(workspace, "integration/root-1", manager=manager)

    manager.stop.assert_called_once_with("stale")
    assert (Path(review) / "marker.txt").read_text() == "integration\n"
    assert manager.launch.call_args_list[0].args[0].params["worktree"] == review


# ── Capture ──────────────────────────────────────────────────────────────


def _preview(client: PreviewInstance | None) -> PreviewView:
    return PreviewView(ref="feature/x", client=client, can_start=True)


CLIENT = PreviewInstance(
    id="c", url="http://127.0.0.1:5181", state=InstanceState.READY, worktree="/w"
)


def test_capture_refuses_without_a_running_build(repo: Path):
    with (
        mock.patch.object(review_capture, "preview_status", return_value=_preview(None)),
        pytest.raises(CaptureError, match="start a preview"),
    ):
        capture_pages(
            mock.Mock(), _workspace(repo), mock.Mock(id="t"), ref="feature/x",
            request=CapturePaths(paths=["/x"]),
        )  # fmt: skip


def test_capture_files_shots_in_the_kit_and_reports_a_page_that_failed(repo: Path, tmp_path: Path):
    shot = tmp_path / "0.png"
    shot.write_bytes(PNG)
    pages = [
        review_capture._ScriptPage(path="/work-items", file=str(shot), error=""),
        review_capture._ScriptPage(path="/broken", file="", error="net::ERR_CONNECTION_REFUSED"),
    ]
    callbacks = mock.Mock()
    callbacks.return_value.attach_artifact.return_value = mock.Mock(id="art-1")
    ticket = mock.Mock(id="0f0f0f0f-aaaa")

    with (
        mock.patch.object(review_capture, "preview_status", return_value=_preview(CLIENT)),
        mock.patch.object(review_capture, "_run_script", return_value=pages) as run,
        mock.patch.object(review_capture, "OrchestrationCallbackService", callbacks),
        mock.patch.object(review_capture, "resolve_head_sha", return_value="abc"),
        mock.patch("loregarden.services.review_kit.captures_root", return_value=tmp_path / "caps"),
    ):
        result = capture_pages(
            mock.Mock(), _workspace(repo), ticket, ref="feature/x",
            request=CapturePaths(paths=["/work-items", "/broken"]),
        )  # fmt: skip

    assert run.call_args.args[0] == "http://127.0.0.1:5181"
    assert [(p.path, p.captured, p.note) for p in result.pages] == [
        ("/work-items", True, ""),
        ("/broken", False, "net::ERR_CONNECTION_REFUSED"),
    ]
    assert result.artifact_id == "art-1"
    content = callbacks.return_value.attach_artifact.call_args.kwargs["content"]
    (stored,) = content["review"]["screenshots"]
    assert stored["caption"] == "/work-items on feature/x"
    assert (tmp_path / "caps" / "0f0f0f0f-aaaa" / stored["capture_id"]).read_bytes() == PNG


def test_capture_paths_refuse_a_url():
    with pytest.raises(ValueError, match="single '/'"):
        CapturePaths(paths=["http://evil.example/"])
