"""How often a stage asks git, and that asking less never answers from a stale tree.

A stage dispatch and completion used to start ~23 git processes, most of them
the same question asked again by another service (lg-build-verification-847).
These pin the three things that change made true:

- the ceiling — at most `GIT_CALLS_PER_STAGE` per stage on the orchestration path
  `test_parent_orchestration_runs_child_workflow_first` exercises;
- freshness — a tree that changes after a snapshot is recorded as it is *after*
  the change, in the changed paths, the diff artifact, the gate's tree and the
  commit;
- the doctor's repository-shape checks run once per orchestration run per
  repository, not once per dispatch.

Git is counted at `subprocess.Popen`, not at `run_git`: every module imports
`run_git` by name, so patching the function would count none of them.
"""

from __future__ import annotations

import json
import subprocess
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from loregarden.agents.executors import cli as cli_module
from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.agents.executors.run_evidence import record_run_evidence
from loregarden.config import settings
from loregarden.models.domain import (
    AgentRun,
    Artifact,
    ArtifactKind,
    DoctorCheck,
    OrchestrationRun,
    RunStatus,
    Ticket,
    Workspace,
)
from loregarden.services import doctor
from loregarden.services.doctor import preflight_run
from loregarden.services.gate_producer import snapshot_worktree
from loregarden.services.git_commit_push_service import commit_paths
from loregarden.services.preflight_ledger import PreflightLedger
from loregarden.services.ticket_worktree import resolve_ticket_root
from sqlmodel import Session, col, select
from tests.factories import make_workspace_ticket
from tests.worktree_helpers import git, make_repo

#: AC1. Measured on this path: 20.9 per stage before the snapshot, 9.0 after.
GIT_CALLS_PER_STAGE = 10

_CORE_BARE = ["git", "config", "--local", "--get", "core.bare"]
_GIT_DIR = ["git", "rev-parse", "--git-dir"]


@pytest.fixture(name="git_argvs")
def git_argvs_fixture() -> Iterator[list[list[str]]]:
    """Every git argv started while the fixture is active, in order.

    `run_git` always passes a list starting with the literal "git", so that is
    what is matched; a string command (shell=True) never reads as git.
    """
    argvs: list[list[str]] = []
    original = subprocess.Popen.__init__

    def counting(self, args, *rest, **kwargs):
        argv = [str(part) for part in list(args)]
        if argv[:1] == ["git"]:
            argvs.append(argv)
        original(self, args, *rest, **kwargs)

    with mock.patch.object(subprocess.Popen, "__init__", counting):
        yield argvs


def _orchestrate_child_first(client: TestClient, tmp_path: Path) -> tuple[str, str]:
    """The setup of `test_parent_orchestration_runs_child_workflow_first`.

    `settings.repo_root` is redirected for the same reason it is there: the real
    loregarden profile's gate commands assume a real checkout.
    """
    milestone_id = next(
        t["id"]
        for t in client.get("/api/tickets?workspace=loregarden").json()
        if t["work_item_type"] == "milestone"
    )
    feature = client.post(
        "/api/tickets",
        json={
            "workspace_slug": "loregarden",
            "title": "Parent feature for child orchestration",
            "work_item_type": "feature",
            "parent_ticket_id": milestone_id,
        },
    ).json()
    child = client.post(
        "/api/tickets",
        json={
            "workspace_slug": "loregarden",
            "title": "Child bug under feature",
            "work_item_type": "bug",
            "parent_ticket_id": feature["id"],
        },
    ).json()
    return feature["id"], child["id"]


def _without_cwd(argv: list[str]) -> list[str]:
    """`git -C <dir> diff` counted as `git diff`, so the message names the question."""
    return argv[:1] + argv[3:] if argv[1:2] == ["-C"] else argv


def _stage_runs(engine, ticket_ids: tuple[str, ...]) -> list[AgentRun]:
    with Session(engine) as session:
        return list(
            session.exec(select(AgentRun).where(col(AgentRun.ticket_id).in_(ticket_ids))).all()
        )


def test_a_stage_starts_at_most_ten_git_processes(
    client: TestClient, isolated_db, tmp_path: Path, git_argvs: list[list[str]]
):
    """AC1, counted over the orchestration request alone — the fixture's own git
    setup and the reads around it are not stage work."""
    with mock.patch.object(settings, "repo_root", tmp_path):
        feature_id, child_id = _orchestrate_child_first(client, tmp_path)
        git_argvs.clear()
        res = client.post(f"/api/tickets/{feature_id}/orchestrate", json={"max_stages": 1})
        during = list(git_argvs)
    assert res.status_code == 200

    runs = _stage_runs(isolated_db, (feature_id, child_id))
    assert runs, "no stage ran, so there is nothing to divide by"
    by_command = Counter(" ".join(_without_cwd(argv)[1:3]) for argv in during)
    assert len(during) <= GIT_CALLS_PER_STAGE * len(runs), (
        f"{len(during)} git processes over {len(runs)} stage runs: {by_command.most_common()}"
    )


def test_the_doctor_asks_git_once_per_orchestration_run_per_repository(
    client: TestClient, isolated_db, tmp_path: Path, git_argvs: list[list[str]]
):
    """AC3, on the same path: dispatches of one run share one answer."""
    with mock.patch.object(settings, "repo_root", tmp_path):
        feature_id, child_id = _orchestrate_child_first(client, tmp_path)
        git_argvs.clear()
        client.post(f"/api/tickets/{feature_id}/orchestrate", json={"max_stages": 1})
        during = list(git_argvs)

    runs = _stage_runs(isolated_db, (feature_id, child_id))
    pairs = {(run.orchestration_run_id, run.start_repo_path) for run in runs}
    assert len(runs) > len(pairs), "every run had its own pair, so nothing was shared"
    assert 1 <= during.count(_CORE_BARE) <= len(pairs)
    assert 1 <= during.count(_GIT_DIR) <= len(pairs)


# --- the ledger itself ------------------------------------------------------


@pytest.fixture(name="preflight_case")
def preflight_case_fixture(db_session: Session, tmp_path: Path):
    """A run inside an orchestration run, against a real repository."""
    repo = make_repo(tmp_path)
    ticket = make_workspace_ticket(db_session, "preflight-once")
    workspace = db_session.get(Workspace, ticket.workspace_id)
    orch = OrchestrationRun(run_code="orch-once", ticket_id=ticket.id, workspace_id=workspace.id)
    db_session.add(orch)
    db_session.commit()
    return repo, ticket, workspace, orch


def _new_run(
    db_session: Session, ticket: Ticket, orchestration_run_id: str | None, code: str
) -> AgentRun:
    run = AgentRun(
        run_code=code,
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="backend_implementer",
        stage_key="implement",
        status=RunStatus.RUNNING,
        orchestration_run_id=orchestration_run_id,
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)
    return run


def test_a_passed_check_is_not_asked_again_in_the_same_run(db_session, preflight_case):
    repo, ticket, workspace, orch = preflight_case
    ledger = PreflightLedger()
    first = _new_run(db_session, ticket, orch.id, "pf-first")
    second = _new_run(db_session, ticket, orch.id, "pf-second")

    with mock.patch.object(doctor, "run_checks", wraps=doctor.run_checks) as checks:
        preflight_run(db_session, first, workspace, repo, ledger=ledger)
        findings = preflight_run(db_session, second, workspace, repo, ledger=ledger)

    asked_second = checks.call_args_list[1].kwargs["checks"]
    assert DoctorCheck.GIT_CORE_BARE not in asked_second
    assert DoctorCheck.GIT_WRITABLE not in asked_second
    # The run still carries every finding, in the dispatch order.
    assert [f.check for f in findings] == list(doctor.DISPATCH_PREFLIGHT_CHECKS)


def test_a_failed_check_is_asked_again(db_session, preflight_case):
    """A failure parks the stage for a human who may fix it; the re-run must look."""
    repo, ticket, workspace, orch = preflight_case
    ledger = PreflightLedger()
    git(repo, "config", "--local", "core.bare", "true")
    failed = _new_run(db_session, ticket, orch.id, "pf-failed")
    preflight_run(db_session, failed, workspace, repo, ledger=ledger)
    db_session.refresh(failed)
    assert json.loads(failed.start_preflight_failures_json) == [DoctorCheck.GIT_CORE_BARE.value]
    git(repo, "config", "--local", "core.bare", "false")

    rerun = _new_run(db_session, ticket, orch.id, "pf-rerun")
    preflight_run(db_session, rerun, workspace, repo, ledger=ledger)

    db_session.refresh(rerun)
    assert json.loads(rerun.start_preflight_failures_json) == []


def test_another_run_or_no_run_asks_again(db_session, preflight_case):
    repo, ticket, workspace, orch = preflight_case
    ledger = PreflightLedger()
    preflight_run(
        db_session, _new_run(db_session, ticket, orch.id, "pf-once"), workspace, repo, ledger=ledger
    )

    assert ledger.passed(orch.id, repo)
    assert not ledger.passed("another-orchestration-run", repo)
    assert not ledger.passed(orch.id, repo / "elsewhere")
    assert not ledger.passed(None, repo), "a run outside any orchestration checks every time"


# --- freshness: AC2 ---------------------------------------------------------

_AGENT_TEXT = "written by the agent\n"
_LATER_TEXT = "rewritten after the post-agent snapshot\n"


@pytest.fixture(name="implement_run")
def implement_run_fixture(db_session: Session) -> tuple[Ticket, AgentRun]:
    ticket = make_workspace_ticket(db_session, "snapshot-freshness")
    run = AgentRun(
        run_code="snapshot_freshness",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="backend_implementer",
        stage_key="implement",
        status=RunStatus.RUNNING,
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)
    return ticket, run


def test_a_tree_changed_after_the_snapshot_is_recorded_as_changed(
    db_session: Session, implement_run: tuple[Ticket, AgentRun]
):
    """AC2, through the real dispatch: the agent writes a file, the post-agent
    snapshot is taken, and then the file changes again before the artifacts,
    the gate's tree and the commit are made. Every one of them must describe
    the tree after that change.

    The agent's write is what tells the before/after split apart: a post-agent
    record built from the pre-agent snapshot would attribute nothing.
    """
    ticket, run = implement_run
    written: dict[str, Path] = {}

    def agent(*, repo_root: Path, **_kwargs):
        (repo_root / "README.md").write_text(_AGENT_TEXT)
        written["root"] = repo_root
        return "", "", RunStatus.SUCCEEDED

    def evidence_then_change(session, run, *, repo_root: Path, **kwargs):
        record_run_evidence(session, run, repo_root=repo_root, **kwargs)
        (repo_root / "README.md").write_text(_LATER_TEXT)

    with (
        mock.patch.object(cli_module, "run_print_mode", side_effect=agent),
        mock.patch.object(cli_module, "record_run_evidence", side_effect=evidence_then_change),
    ):
        CliAgentExecutor(db_session).execute(run, ticket)

    root = written["root"]
    workspace = db_session.get(Workspace, ticket.workspace_id)
    assert resolve_ticket_root(db_session, ticket, workspace) == root

    db_session.refresh(run)
    assert json.loads(run.changed_paths_json) == ["README.md"]

    diff = db_session.exec(
        select(Artifact).where(Artifact.ticket_id == ticket.id, Artifact.kind == ArtifactKind.DIFF)
    ).one()
    added = [
        line["text"]
        for section in json.loads(diff.content_json)["sections"]
        if section["path"] == "README.md"
        for line in section["lines"]
        if line["type"] == "a"
    ]
    assert added == [_LATER_TEXT.strip()]

    gate_tree = snapshot_worktree(root)
    assert gate_tree.error == ""
    assert git(root, "show", f"{gate_tree.tree_sha}:README.md").stdout == _LATER_TEXT

    assert commit_paths(db_session, ticket, "freshness", ["README.md"])
    assert git(root, "show", "HEAD:README.md").stdout == _LATER_TEXT
    assert git(root, "status", "--porcelain").stdout == ""
