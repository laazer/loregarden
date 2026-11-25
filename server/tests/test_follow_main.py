"""The dev server follows main: it pulls what the queue lands and reloads onto it.

Before this, a fix the merge queue landed (including one to the queue itself)
did not run until someone pulled the primary checkout and reloaded by hand.
These run `follow_main` over real repositories: a bare origin, the server's
checkout cloned from it, and a second clone that pushes to main.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest import mock

import pytest
from loregarden.dev_supervisor import STATE_DIR
from loregarden.models.domain import (
    DoctorCheck,
    DoctorStatus,
    FollowMainState,
    RunStatus,
    Workspace,
)
from loregarden.services import doctor, follow_main, reload_hold
from loregarden.services.doctor_dev_server import check_server_follows_main
from loregarden.services.follow_main_status import read_follow_status
from loregarden.services.self_improve_restart import RELOAD_SENTINEL
from sqlmodel import Session
from tests.factories import make_agent_run, make_workspace

_ENV = {k: v for k, v in os.environ.items() if k not in {"GIT_DIR", "GIT_WORK_TREE"}}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *args],
        cwd=repo,
        env=_ENV,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _commit(repo: Path, rel: str, text: str) -> str:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    _git(repo, "add", rel)
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", f"change {rel}")
    return _git(repo, "rev-parse", "HEAD")


class Repos:
    def __init__(self, root: Path) -> None:
        self.origin = root / "origin.git"
        self.server = root / "server-checkout"
        self.upstream = root / "upstream"
        _git(root, "init", "-q", "--bare", "-b", "main", str(self.origin))
        _git(root, "clone", "-q", str(self.origin), str(self.upstream))
        _commit(self.upstream, "server/app.py", "VERSION = 1\n")
        _commit(self.upstream, "docs/notes.md", "notes\n")
        _git(self.upstream, "push", "-q", "origin", "HEAD:main")
        _git(root, "clone", "-q", str(self.origin), str(self.server))

    def land(self, rel: str, text: str) -> str:
        """What the merge queue does: a new commit on origin's main."""
        sha = _commit(self.upstream, rel, text)
        _git(self.upstream, "push", "-q", "origin", "HEAD:main")
        return sha

    def head(self) -> str:
        return _git(self.server, "rev-parse", "HEAD")

    @property
    def sentinel(self) -> Path:
        return self.server / RELOAD_SENTINEL


@pytest.fixture(name="repos")
def repos_fixture(tmp_path: Path) -> Repos:
    return Repos(tmp_path)


@pytest.fixture(name="workspace")
def workspace_fixture(db_session: Session, repos: Repos) -> Workspace:
    workspace = make_workspace(db_session, slug="loregarden")
    workspace.repo_path = str(repos.server)
    db_session.add(workspace)
    db_session.commit()
    db_session.refresh(workspace)
    return workspace


@pytest.fixture(autouse=True)
def server_settings(repos: Repos):
    fake = mock.Mock(repo_root=repos.server, follow_main=True, sandbox=False)
    with (
        mock.patch("loregarden.services.follow_main.settings", fake),
        mock.patch("loregarden.services.self_improve_restart.settings", fake),
        mock.patch("loregarden.services.doctor_dev_server.settings", fake),
        mock.patch("loregarden.services.follow_main.base_branch", return_value="main"),
    ):
        follow_main.remember_running_build(repos.server)
        yield fake


@pytest.fixture(autouse=True)
def _forget_the_hold():
    reload_hold.reset()
    yield
    reload_hold.reset()


def _tick(session: Session, workspace: Workspace):
    return follow_main.follow_main(session, workspace)


def test_a_landed_server_change_is_pulled_and_reloaded(
    db_session: Session, workspace: Workspace, repos: Repos
):
    landed = repos.land("server/app.py", "VERSION = 2\n")

    status = _tick(db_session, workspace)

    assert status.state is FollowMainState.RELOADING
    assert repos.head() == landed
    assert repos.sentinel.exists()
    assert read_follow_status(repos.server) == status


def test_a_reload_is_requested_once_per_head(
    db_session: Session, workspace: Workspace, repos: Repos
):
    repos.land("server/app.py", "VERSION = 2\n")
    _tick(db_session, workspace)
    repos.sentinel.unlink()

    # The supervisor refused the build, say: asking again every tick would only
    # re-run its import check. The dev_server_reload check reports the refusal.
    again = _tick(db_session, workspace)

    assert again.state is FollowMainState.RELOADING
    assert not repos.sentinel.exists()


def test_a_change_outside_server_is_pulled_without_a_reload(
    db_session: Session, workspace: Workspace, repos: Repos
):
    landed = repos.land("docs/notes.md", "more notes\n")

    status = _tick(db_session, workspace)

    assert status.state is FollowMainState.PULLED
    assert repos.head() == landed
    assert not repos.sentinel.exists()


def test_nothing_landed_is_current(db_session: Session, workspace: Workspace, repos: Repos):
    status = _tick(db_session, workspace)

    assert status.state is FollowMainState.CURRENT
    assert not repos.sentinel.exists()


def test_in_flight_work_holds_a_server_pull_until_it_finishes(
    db_session: Session, workspace: Workspace, repos: Repos
):
    before = repos.head()
    run = make_agent_run(db_session, workspace_id=workspace.id, status=RunStatus.RUNNING)
    landed = repos.land("server/app.py", "VERSION = 2\n")

    held = _tick(db_session, workspace)

    assert held.state is FollowMainState.WAITING_FOR_IDLE
    assert "active_agent_runs" in held.detail
    assert repos.head() == before
    assert not repos.sentinel.exists()

    run.status = RunStatus.SUCCEEDED
    db_session.add(run)
    db_session.commit()

    assert _tick(db_session, workspace).state is FollowMainState.RELOADING
    assert repos.head() == landed


@pytest.fixture(name="held_run")
def held_run_fixture(db_session: Session, workspace: Workspace, repos: Repos):
    """A landed change that has waited long enough for new work to be held."""
    run = make_agent_run(db_session, workspace_id=workspace.id, status=RunStatus.RUNNING)
    repos.land("server/app.py", "VERSION = 2\n")
    with mock.patch.object(reload_hold, "_now", return_value=1_000.0):
        assert _tick(db_session, workspace).state is FollowMainState.WAITING_FOR_IDLE
    with mock.patch.object(reload_hold, "_now", return_value=5_000.0):
        status = _tick(db_session, workspace)
        assert status.state is FollowMainState.HOLDING
        assert reload_hold.holds_starts()
        yield run


def _finish(session: Session, run) -> None:
    run.status = RunStatus.SUCCEEDED
    session.add(run)
    session.commit()


def test_a_hold_lasts_through_the_reload_it_was_for(
    db_session: Session, workspace: Workspace, repos: Repos, held_run
):
    _finish(db_session, held_run)

    assert _tick(db_session, workspace).state is FollowMainState.RELOADING

    assert repos.sentinel.exists()
    assert reload_hold.holds_starts(), "work started now would be killed by the reload"


def test_a_hold_ends_when_the_supervisor_refused_the_build(
    db_session: Session, workspace: Workspace, held_run
):
    _finish(db_session, held_run)
    _tick(db_session, workspace)

    assert _tick(db_session, workspace).state is FollowMainState.RELOADING

    assert not reload_hold.holds_starts()


def test_a_hold_ends_when_the_checkout_leaves_main(
    db_session: Session, workspace: Workspace, repos: Repos, held_run
):
    _git(repos.server, "switch", "-q", "-c", "elsewhere")

    assert _tick(db_session, workspace).state is FollowMainState.OFF_BASE

    assert not reload_hold.holds_starts()


def test_a_failed_fetch_leaves_the_hold_to_its_deadline(
    db_session: Session, workspace: Workspace, repos: Repos, held_run
):
    repos.origin.rename(repos.origin.with_name("gone.git"))

    assert _tick(db_session, workspace).state is FollowMainState.GIT_FAILED

    assert reload_hold.holds_starts()


def test_a_prompt_waiting_on_a_person_ends_the_hold_and_says_so(
    db_session: Session, workspace: Workspace, held_run
):
    held_run.status = RunStatus.AWAITING_PERMISSION
    db_session.add(held_run)
    db_session.commit()

    status = _tick(db_session, workspace)

    assert status.state is FollowMainState.WAITING_FOR_IDLE
    assert held_run.run_code in status.detail
    assert not reload_hold.holds_starts()


def test_a_pulled_change_waiting_to_reload_is_held_for_too(
    db_session: Session, workspace: Workspace, repos: Repos
):
    """The checkout is on main's tip, but the process runs the build before it."""
    repos.land("server/app.py", "VERSION = 2\n")
    _git(repos.server, "pull", "-q", "--ff-only")
    make_agent_run(db_session, workspace_id=workspace.id, status=RunStatus.RUNNING)
    with mock.patch.object(reload_hold, "_now", return_value=1_000.0):
        _tick(db_session, workspace)

    with mock.patch.object(reload_hold, "_now", return_value=5_000.0):
        assert _tick(db_session, workspace).state is FollowMainState.HOLDING


def test_holding_is_the_server_keeping_up(
    db_session: Session, workspace: Workspace, repos: Repos, held_run
):
    finding = check_server_follows_main(db_session, workspace, repos.server)

    assert finding.status is DoctorStatus.PASS
    assert "holding" in finding.finding


def test_a_checkout_on_another_branch_is_left_alone(
    db_session: Session, workspace: Workspace, repos: Repos
):
    _git(repos.server, "checkout", "-q", "-b", "feature")
    before = repos.head()
    repos.land("server/app.py", "VERSION = 2\n")

    status = _tick(db_session, workspace)

    assert status.state is FollowMainState.OFF_BASE
    assert "feature" in status.detail
    assert repos.head() == before


def test_local_commits_on_main_are_never_moved(
    db_session: Session, workspace: Workspace, repos: Repos
):
    local = _commit(repos.server, "docs/local.md", "mine\n")
    repos.land("server/app.py", "VERSION = 2\n")

    status = _tick(db_session, workspace)

    assert status.state is FollowMainState.DIVERGED
    assert repos.head() == local


def test_uncommitted_changes_to_a_landed_file_refuse_the_pull_and_survive(
    db_session: Session, workspace: Workspace, repos: Repos
):
    before = repos.head()
    (repos.server / "server" / "app.py").write_text("VERSION = 'wip'\n", encoding="utf-8")
    repos.land("server/app.py", "VERSION = 2\n")

    status = _tick(db_session, workspace)

    assert status.state is FollowMainState.REFUSED
    assert repos.head() == before
    assert (repos.server / "server" / "app.py").read_text(encoding="utf-8") == "VERSION = 'wip'\n"


def test_a_stuck_outcome_is_a_doctor_warning(
    db_session: Session, workspace: Workspace, repos: Repos
):
    _git(repos.server, "checkout", "-q", "-b", "feature")
    _tick(db_session, workspace)

    finding = check_server_follows_main(db_session, workspace, repos.server)

    assert finding.check is DoctorCheck.SERVER_FOLLOWS_MAIN
    assert finding.status is DoctorStatus.WARN
    assert "feature" in finding.finding


def test_keeping_up_or_never_following_passes(
    db_session: Session, workspace: Workspace, repos: Repos
):
    assert check_server_follows_main(db_session, workspace, repos.server).status is (
        DoctorStatus.PASS
    )
    _tick(db_session, workspace)
    assert check_server_follows_main(db_session, workspace, repos.server).status is (
        DoctorStatus.PASS
    )


def test_an_unreadable_status_warns(db_session: Session, workspace: Workspace, repos: Repos):
    (repos.server / STATE_DIR).mkdir(parents=True)
    (repos.server / STATE_DIR / "follow-main.json").write_text("{nope", encoding="utf-8")

    finding = check_server_follows_main(db_session, workspace, repos.server)

    assert finding.status is DoctorStatus.WARN


@pytest.mark.parametrize(
    ("change", "follows"),
    [
        ({}, True),
        ({"follow_main": False}, False),
        ({"sandbox": True}, False),
        ({"repo_root": Path("/elsewhere")}, False),
    ],
)
def test_only_the_dev_servers_own_checkout_follows(
    workspace: Workspace, server_settings: mock.Mock, change: dict, follows: bool
):
    for name, value in change.items():
        setattr(server_settings, name, value)

    assert follow_main.is_server_checkout(workspace) is follows


def test_another_workspace_never_follows(db_session: Session, workspace: Workspace, repos: Repos):
    other = make_workspace(db_session, slug="blobert", repo_path=str(repos.server))

    assert follow_main.is_server_checkout(other) is False


def test_the_check_is_registered_but_does_not_park_dispatch():
    assert doctor.CHECKS[DoctorCheck.SERVER_FOLLOWS_MAIN] is check_server_follows_main
    assert DoctorCheck.SERVER_FOLLOWS_MAIN not in doctor.DISPATCH_PREFLIGHT_CHECKS
