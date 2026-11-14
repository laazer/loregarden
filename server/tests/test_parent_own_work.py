"""A parent is not closed as its children's summary while its own work has landed nowhere.

lg-milestone-that-1429 ran its own stages to its gate; a review run then filed
tickets under it. Once they were closed, the rollup derived it `done` with its
31 files on no branch but its own (lg-milestone-that-2604).
"""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest
from loregarden.models.domain import (
    AgentRun,
    BlockKind,
    RunStatus,
    StageStatus,
    Ticket,
    TicketState,
    WorkflowTemplate,
    WorkItemType,
    Workspace,
)
from loregarden.models.domain.stage_types import StageType
from loregarden.services.closed_sources import parents_closed_without_their_work
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.subtree_auto_run import finalize_aggregator_ticket
from loregarden.services.target_branch import resolve_target_branch
from loregarden.services.ticket_rollup import (
    OWN_WORK_NOTE,
    reconcile_all_parents,
    reconcile_ancestors,
    reconcile_parent,
)
from loregarden.services.workflow_service import resolve_ticket_stages
from loregarden.services.workflow_state import set_stage_status
from sqlmodel import Session
from tests.worktree_helpers import commit_on, git, make_repo, make_tree_ticket


@pytest.fixture(name="session")
def session_fixture(client, isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    return make_repo(tmp_path)


@pytest.fixture(name="family")
def family_fixture(session, repo) -> tuple[Ticket, Ticket, str]:
    """A capability with a commit of its own on its branch and one closed child under it."""
    workspace = Workspace(slug="own-work", name="own-work", repo_path=str(repo))
    session.add(workspace)
    session.commit()
    milestone = make_tree_ticket(session, workspace, "lg-own-1", kind=WorkItemType.MILESTONE)
    source = make_tree_ticket(
        session, workspace, "lg-own-2", parent=milestone, kind=WorkItemType.CAPABILITY
    )
    child = make_tree_ticket(session, workspace, "lg-own-3", parent=source)
    child.state = TicketState.WONT_DO
    session.add(child)
    session.commit()
    target = resolve_target_branch(session, source, workspace, repo_root=repo)
    git(repo, "branch", source.branch, target)
    commit_on(repo, source.branch, "server/loregarden/own.py", "its own work\n")
    return source, child, target


def _assert_held_open(source: Ticket) -> None:
    assert source.state is TicketState.BLOCKED
    assert source.block_kind is BlockKind.HUMAN_ACTION
    assert source.blocking_issues.startswith(OWN_WORK_NOTE)
    assert source.branch in source.blocking_issues


def _rollup(session: Session, source: Ticket, child: Ticket) -> None:
    reconcile_parent(session, source)
    session.commit()


def _boot_sweep(session: Session, source: Ticket, child: Ticket) -> None:
    reconcile_all_parents(session)


def _child_moved(session: Session, source: Ticket, child: Ticket) -> None:
    reconcile_ancestors(session, child)


def _aggregator_finalise(session: Session, source: Ticket, child: Ticket) -> None:
    finalize_aggregator_ticket(session, OrchestrationService(session), source)


CLOSING_PATHS: dict[str, Callable[[Session, Ticket, Ticket], None]] = {
    "rollup": _rollup,
    "boot sweep": _boot_sweep,
    "a child moved": _child_moved,
    "aggregator finalise": _aggregator_finalise,
}


@pytest.mark.parametrize("close", CLOSING_PATHS.values(), ids=CLOSING_PATHS.keys())
def test_no_closing_path_closes_a_parent_over_its_own_unlanded_work(session, family, close):
    source, child, _ = family
    close(session, source, child)
    session.refresh(source)
    _assert_held_open(source)


def test_once_its_branch_lands_the_rollup_closes_it_and_clears_its_note(session, repo, family):
    source, _, target = family
    reconcile_parent(session, source)
    _assert_held_open(source)

    tree = repo.parent / "tmp-land"
    git(repo, "worktree", "add", "-q", str(tree), target)
    git(tree, "merge", "-q", "--no-ff", "-m", "land", source.branch)
    git(repo, "worktree", "remove", "--force", str(tree))

    assert reconcile_parent(session, source) is True
    assert source.state is TicketState.DONE
    assert (source.block_kind, source.blocking_issues) == (None, "")


def test_a_parent_with_nothing_of_its_own_closes_as_before(session, repo, family):
    source, _, target = family
    git(repo, "branch", "-f", source.branch, target)
    assert reconcile_parent(session, source) is True
    assert source.state is TicketState.DONE


def test_a_block_its_own_run_recorded_keeps_its_kind_and_words(session, family):
    source, _, _ = family
    source.state = TicketState.BLOCKED
    source.block_kind = BlockKind.HARNESS
    source.blocking_issues = "The gate could not get capacity."
    session.add(source)
    session.commit()

    reconcile_parent(session, source)
    assert source.state is TicketState.BLOCKED
    assert (source.block_kind, source.blocking_issues) == (
        BlockKind.HARNESS,
        "The gate could not get capacity.",
    )


def test_a_parent_already_closed_is_reported_not_reopened(session, family):
    source, _, _ = family
    source.state = TicketState.DONE
    session.add(source)
    session.add(
        AgentRun(
            run_code="run_own",
            ticket_id=source.id,
            workspace_id=source.workspace_id,
            agent_id="backend_implementer",
            status=RunStatus.SUCCEEDED,
            changed_paths_json=json.dumps(["server/loregarden/own.py"]),
        )
    )
    session.commit()

    reconcile_parent(session, source)
    assert source.state is TicketState.DONE
    [violation] = parents_closed_without_their_work(session)
    assert violation.subject_id == source.id
    assert source.branch in violation.detail


def test_a_closed_parent_whose_landing_is_on_record_is_not_reported(session, family):
    source, _, _ = family
    source.state = TicketState.DONE
    source.landed_sha = "0" * 40
    session.add(source)
    session.add(
        AgentRun(
            run_code="run_own",
            ticket_id=source.id,
            workspace_id=source.workspace_id,
            agent_id="backend_implementer",
            status=RunStatus.SUCCEEDED,
            changed_paths_json=json.dumps(["server/loregarden/own.py"]),
        )
    )
    session.commit()
    assert parents_closed_without_their_work(session) == []


#: Loregarden TDD V3's shape: its plan fans out too, before the spec.
_V3_SHAPE = json.dumps(
    [
        {"key": key, "name": key, "order": i, "agent_id": agent, "stage_type": kind}
        for i, (key, agent, kind) in enumerate(
            [
                ("triage", "ticket_scoper", "classify"),
                ("plan", "planner", "parallel"),
                ("spec", "spec", "agent"),
                ("implement", "backend_implementer", "agent"),
                ("verify", "verifier", "verify"),
                ("review", "architecture_reviewer", "parallel"),
                ("gate", "gatekeeper", "gate"),
                ("done", "", "agent"),
            ]
        )
    ]
)


def _reviewed_with_gate_pending(session: Session, source: Ticket) -> list[str]:
    """Its review lanes and verify done, its gate not run. Returns the gate keys."""
    v3 = WorkflowTemplate(slug="own-work-v3", name="own-work-v3", stages_json=_V3_SHAPE)
    session.add(v3)
    session.commit()
    workspace = session.get(Workspace, source.workspace_id)
    workspace.workflow_template_id = v3.id
    session.add(workspace)
    session.commit()
    orch = OrchestrationService(session)
    instance, _ = orch.ensure_workflow_instance(source)
    _, stages = resolve_ticket_stages(session, source)
    gates = [s.key for s in stages if s.stage_type == StageType.GATE]
    assert (
        instance is not None
        and gates
        and any(s.stage_type in (StageType.PARALLEL, StageType.VERIFY) for s in stages)
    )
    for stage in stages:
        if stage.key in ("verify", "review"):
            set_stage_status(source, instance, stages, stage.key, StageStatus.DONE)
    session.add(instance)
    session.commit()
    return gates


@pytest.mark.parametrize("close", CLOSING_PATHS.values(), ids=CLOSING_PATHS.keys())
def test_no_closing_path_retires_a_reviewed_parents_gate(session, repo, family, close):
    source, child, target = family
    git(repo, "branch", "-f", source.branch, target)  # git sees nothing of its own
    gates = _reviewed_with_gate_pending(session, source)

    close(session, source, child)

    session.refresh(source)
    assert source.state is TicketState.BLOCKED
    assert source.blocking_issues.startswith(OWN_WORK_NOTE)
    assert gates[0] in source.blocking_issues


def test_a_parent_that_only_planned_before_it_split_still_closes(session, repo, family):
    source, child, target = family
    git(repo, "branch", "-f", source.branch, target)
    _reviewed_with_gate_pending(session, source)
    orch = OrchestrationService(session)
    instance = orch.get_workflow_instance(source.id)
    _, stages = resolve_ticket_stages(session, source)
    for stage in stages:
        status = StageStatus.DONE if stage.key in ("triage", "plan") else StageStatus.PENDING
        set_stage_status(source, instance, stages, stage.key, status)
    session.add(instance)
    session.commit()

    assert reconcile_parent(session, source) is True
    assert source.state is TicketState.DONE
