"""Initiative membership: tracking a ticket without re-parenting it.

The case it exists for: a feature under a milestone whose integration branch
already holds landed work joins an initiative's plan, keeps its parent, and
keeps landing where it always did.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from loregarden.db.versions.initiative_members import m_initiative_members
from loregarden.mcp.tools import execute_tool, normalize_tool_arguments
from loregarden.models.domain import (
    InitiativeMember,
    Ticket,
    TicketState,
    WorkItemType,
    Workspace,
)
from loregarden.services.git_subprocess import scrubbed_git_env
from loregarden.services.initiative_coverage import initiative_coverage, initiative_roots
from loregarden.services.initiative_membership import (
    InitiativeMembershipError,
    MembershipConflictError,
    add_member,
    remove_member,
)
from loregarden.services.initiative_plan_service import plan_view
from loregarden.services.target_branch import resolve_target_branch, target_branch_name
from loregarden.services.ticket_rollup import reconcile_ancestors
from loregarden.services.ticket_service import TicketService
from loregarden.testing.factories import make_ticket
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select


@pytest.fixture(autouse=True)
def _agent_time():
    """Plans price tickets by agent run-time; these tests are about membership."""
    with patch("loregarden.services.initiative_graph.TicketTreeEstimator") as estimator:
        estimator.return_value.estimate.return_value.projected_seconds.return_value = 86_400.0
        yield


@pytest.fixture(name="workspace")
def workspace_fixture(db_session: Session) -> Workspace:
    return db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()


def _initiative(session: Session, title: str = "Ship it") -> Ticket:
    return TicketService(session).create_ticket(title=title, work_item_type=WorkItemType.INITIATIVE)


def _ticket(
    session: Session,
    workspace: Workspace,
    name: str,
    kind: WorkItemType,
    parent: Ticket | None = None,
    state: TicketState | None = None,
) -> Ticket:
    return make_ticket(
        session,
        workspace_id=workspace.id,
        external_id=name,
        title=name,
        work_item_type=kind,
        parent_ticket_id=parent.id if parent else None,
        state=state,
    )


@pytest.fixture(name="tree")
def tree_fixture(db_session: Session, workspace: Workspace) -> dict[str, Ticket]:
    """`lg-milestone-that-491` with landed work, and `-780` still open under it."""
    milestone = _ticket(db_session, workspace, "ms-491", WorkItemType.MILESTONE)
    landed = _ticket(
        db_session, workspace, "ft-landed", WorkItemType.FEATURE, milestone, TicketState.DONE
    )
    feature = _ticket(db_session, workspace, "ft-780", WorkItemType.FEATURE, milestone)
    capability = _ticket(db_session, workspace, "cap-780", WorkItemType.CAPABILITY, feature)
    task = _ticket(db_session, workspace, "task-780", WorkItemType.TASK, capability)
    return {
        "milestone": milestone,
        "landed": landed,
        "feature": feature,
        "capability": capability,
        "task": task,
    }


def _call(session: Session, name: str, args: dict[str, Any]) -> str:
    return execute_tool(session, name, normalize_tool_arguments(name, args))


# --- the shared function -----------------------------------------------------


def test_coverage_is_children_plus_member_subtrees(db_session, workspace, tree):
    initiative = _initiative(db_session)
    owned = _ticket(db_session, workspace, "ms-owned", WorkItemType.MILESTONE)
    owned.parent_ticket_id = initiative.id
    db_session.add(owned)
    db_session.commit()

    add_member(db_session, initiative.id, tree["feature"].id, actor="test")
    coverage = initiative_coverage(db_session, initiative.id)

    assert {t.id for t in coverage.roots} == {owned.id, tree["feature"].id}
    assert coverage.member_ids == {tree["feature"].id}
    assert coverage.ticket_ids() == {
        owned.id,
        tree["feature"].id,
        tree["capability"].id,
        tree["task"].id,
    }
    # The rest of its milestone stays out.
    assert tree["landed"].id not in coverage.ticket_ids()


def test_a_member_reparented_under_the_initiative_is_one_root(db_session, workspace, tree):
    initiative = _initiative(db_session)
    add_member(db_session, initiative.id, tree["feature"].id, actor="test")
    tree["feature"].parent_ticket_id = initiative.id
    db_session.add(tree["feature"])
    db_session.commit()

    roots = initiative_roots(db_session, [initiative.id])[initiative.id]
    assert [t.id for t in roots.roots] == [tree["feature"].id]
    assert not roots.is_member(tree["feature"].id)


def test_a_root_inside_another_roots_subtree_is_counted_once(db_session, workspace, tree):
    """Membership refuses this overlap, but a later re-parent can create it."""
    initiative = _initiative(db_session)
    add_member(db_session, initiative.id, tree["capability"].id, actor="test")
    tree["milestone"].parent_ticket_id = initiative.id
    db_session.add(tree["milestone"])
    db_session.commit()

    tickets = [t.id for t in initiative_coverage(db_session, initiative.id).tickets()]
    assert len(tickets) == len(set(tickets))
    assert tree["task"].id in tickets


# --- adding and removing -----------------------------------------------------


@pytest.mark.parametrize("kind", ["feature", "capability", "task", "milestone"])
def test_any_type_but_an_initiative_can_be_a_member(db_session, workspace, tree, kind):
    initiative = _initiative(db_session)
    add_member(db_session, initiative.id, tree[kind].id, actor="test")
    assert initiative_roots(db_session, [initiative.id])[initiative.id].is_member(tree[kind].id)


def test_adding_a_member_changes_nothing_on_the_ticket(db_session, workspace, tree):
    initiative = _initiative(db_session)
    feature = tree["feature"]
    db_session.refresh(feature)
    before = feature.model_dump()

    add_member(db_session, initiative.id, feature.id, actor="test")
    db_session.refresh(feature)
    assert feature.model_dump() == before

    remove_member(db_session, initiative.id, feature.id)
    db_session.refresh(feature)
    assert feature.model_dump() == before


def test_an_initiative_cannot_be_a_member(db_session):
    initiative, other = _initiative(db_session), _initiative(db_session, "Other")
    with pytest.raises(InitiativeMembershipError, match="is an initiative"):
        add_member(db_session, initiative.id, other.id, actor="test")


def test_a_duplicate_is_refused(db_session, tree):
    initiative = _initiative(db_session)
    add_member(db_session, initiative.id, tree["feature"].id, actor="test")
    with pytest.raises(MembershipConflictError, match="already a member"):
        add_member(db_session, initiative.id, tree["feature"].id, actor="test")


def test_a_child_or_anything_under_one_is_already_covered(db_session, tree):
    initiative = _initiative(db_session)
    tree["milestone"].parent_ticket_id = initiative.id
    db_session.add(tree["milestone"])
    db_session.commit()

    with pytest.raises(MembershipConflictError, match="already a child"):
        add_member(db_session, initiative.id, tree["milestone"].id, actor="test")
    with pytest.raises(MembershipConflictError, match="through ms-491, a child"):
        add_member(db_session, initiative.id, tree["task"].id, actor="test")


def test_anything_under_a_member_is_already_covered(db_session, tree):
    initiative = _initiative(db_session)
    add_member(db_session, initiative.id, tree["feature"].id, actor="test")
    with pytest.raises(MembershipConflictError, match="through ft-780, a member"):
        add_member(db_session, initiative.id, tree["task"].id, actor="test")


def test_a_ticket_holding_a_member_is_refused_until_the_member_goes(db_session, tree):
    initiative = _initiative(db_session)
    add_member(db_session, initiative.id, tree["capability"].id, actor="test")
    with pytest.raises(MembershipConflictError, match="contains cap-780"):
        add_member(db_session, initiative.id, tree["milestone"].id, actor="test")

    remove_member(db_session, initiative.id, tree["capability"].id)
    add_member(db_session, initiative.id, tree["milestone"].id, actor="test")


def test_a_parent_loop_is_refused(db_session, workspace, tree):
    """The schema allows a parent cycle the hierarchy rules do not; never walk one."""
    tree["milestone"].parent_ticket_id = tree["capability"].id
    db_session.add(tree["milestone"])
    db_session.commit()
    initiative = _initiative(db_session)
    with pytest.raises(InitiativeMembershipError, match="loops back"):
        add_member(db_session, initiative.id, tree["task"].id, actor="test")


def test_one_ticket_can_belong_to_two_initiatives(db_session, tree):
    first, second = _initiative(db_session), _initiative(db_session, "Second")
    add_member(db_session, first.id, tree["feature"].id, actor="test")
    add_member(db_session, second.id, tree["feature"].id, actor="test")
    roots = initiative_roots(db_session, [first.id, second.id])
    assert roots[first.id].is_member(tree["feature"].id)
    assert roots[second.id].is_member(tree["feature"].id)


def test_removing_a_child_is_refused_with_how_to_detach_it(db_session, tree):
    initiative = _initiative(db_session)
    tree["milestone"].parent_ticket_id = initiative.id
    db_session.add(tree["milestone"])
    db_session.commit()
    with pytest.raises(LookupError, match="a child of .* not a member"):
        remove_member(db_session, initiative.id, tree["milestone"].id)


def test_the_pair_is_unique_in_the_table(db_session, tree):
    initiative = _initiative(db_session)
    db_session.add(InitiativeMember(initiative_id=initiative.id, ticket_id=tree["task"].id))
    db_session.commit()
    db_session.add(InitiativeMember(initiative_id=initiative.id, ticket_id=tree["task"].id))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


# --- rollup ------------------------------------------------------------------


def test_the_initiative_rolls_up_over_its_members(db_session, tree):
    initiative = _initiative(db_session)
    add_member(db_session, initiative.id, tree["landed"].id, actor="test")
    db_session.refresh(initiative)
    assert initiative.state == TicketState.DONE

    add_member(db_session, initiative.id, tree["feature"].id, actor="test")
    db_session.refresh(initiative)
    assert initiative.state == TicketState.IN_PROGRESS

    remove_member(db_session, initiative.id, tree["feature"].id)
    db_session.refresh(initiative)
    assert initiative.state == TicketState.DONE


def test_a_member_finishing_reaches_the_initiative_through_the_push_rollup(db_session, workspace):
    milestone = _ticket(db_session, workspace, "ms", WorkItemType.MILESTONE)
    feature = _ticket(db_session, workspace, "ft", WorkItemType.FEATURE, milestone)
    task = _ticket(db_session, workspace, "tk", WorkItemType.TASK, feature, TicketState.IN_PROGRESS)
    initiative = _initiative(db_session)
    add_member(db_session, initiative.id, feature.id, actor="test")
    reconcile_ancestors(db_session, task)
    db_session.refresh(initiative)
    assert initiative.state == TicketState.IN_PROGRESS

    task.state = TicketState.DONE
    db_session.add(task)
    db_session.commit()
    changed = reconcile_ancestors(db_session, task)

    assert initiative.id in {t.id for t in changed}
    db_session.refresh(initiative)
    assert initiative.state == TicketState.DONE


def test_deleting_a_member_ticket_drops_its_membership(db_session, tree):
    initiative = _initiative(db_session)
    add_member(db_session, initiative.id, tree["task"].id, actor="test")
    TicketService(db_session).delete_ticket(tree["task"].id)
    assert db_session.exec(select(InitiativeMember)).all() == []


def test_deleting_an_initiative_drops_its_memberships_not_its_members(db_session, tree):
    initiative = _initiative(db_session)
    add_member(db_session, initiative.id, tree["feature"].id, actor="test")
    TicketService(db_session).delete_ticket(initiative.id)
    assert db_session.exec(select(InitiativeMember)).all() == []
    assert db_session.get(Ticket, tree["feature"].id) is not None


# --- the plan ----------------------------------------------------------------


def test_a_member_and_its_subtree_are_in_the_plan_and_leave_it_when_removed(db_session, tree):
    initiative = _initiative(db_session)
    add_member(db_session, initiative.id, tree["feature"].id, actor="test")

    plan = plan_view(db_session, initiative.id)
    [phase] = plan.milestones
    assert (phase.id, phase.member, phase.work_item_type) == (
        tree["feature"].id,
        True,
        WorkItemType.FEATURE,
    )
    # Every work item below it, as under a milestone; the phase itself is not one.
    assert {n.id for n in plan.nodes} == {tree["capability"].id, tree["task"].id}
    assert all(n.milestone_id == tree["feature"].id for n in plan.nodes)
    assert phase.forecast_date is not None

    remove_member(db_session, initiative.id, tree["feature"].id)
    empty = plan_view(db_session, initiative.id)
    assert empty.milestones == [] and empty.nodes == []
    db_session.refresh(tree["feature"])
    assert tree["feature"].parent_ticket_id == tree["milestone"].id


def test_a_feature_parented_directly_is_in_the_view_and_the_plan(db_session, workspace, client):
    """VALID_HIERARCHY already allowed it; now every reader shows it."""
    initiative = _initiative(db_session)
    bug = make_ticket(
        db_session,
        workspace_id=workspace.id,
        external_id="bug-direct",
        work_item_type=WorkItemType.BUG,
        parent_ticket_id=initiative.id,
    )
    view = client.get(f"/api/initiatives/{initiative.id}").json()
    assert [(m["id"], m["member"]) for m in view["milestones"]] == [(bug.id, False)]
    plan = plan_view(db_session, initiative.id)
    assert [n.id for n in plan.nodes] == [bug.id]


# --- branch targeting --------------------------------------------------------


def _git(args: list[str], *, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=scrubbed_git_env()
    ).stdout.strip()


@pytest.fixture(name="repo_workspace")
def repo_workspace_fixture(db_session: Session, tmp_path: Path) -> Workspace:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-b", "main"], cwd=repo)
    _git(["config", "user.email", "test@example.com"], cwd=repo)
    _git(["config", "user.name", "Test"], cwd=repo)
    (repo / "README.md").write_text("# test\n", encoding="utf-8")
    _git(["add", "."], cwd=repo)
    _git(["commit", "-m", "init"], cwd=repo)
    workspace = Workspace(slug="membership-branch-test", name="Branch", repo_path=str(repo))
    db_session.add(workspace)
    db_session.commit()
    return workspace


def test_membership_never_moves_a_tickets_branch_or_worktree_base(db_session, repo_workspace):
    repo = Path(repo_workspace.repo_path)
    milestone = _ticket(db_session, repo_workspace, "lg-milestone-that-491", WorkItemType.MILESTONE)
    feature = _ticket(db_session, repo_workspace, "ft-780", WorkItemType.FEATURE, milestone)
    task = _ticket(db_session, repo_workspace, "tk-780", WorkItemType.TASK, feature)
    initiative = _initiative(db_session)

    def where_it_lands() -> list[str]:
        return [
            *(target_branch_name(db_session, t, repo_workspace) for t in (feature, task)),
            # The start point a worktree is cut from (`ticket_worktree`).
            *(
                resolve_target_branch(db_session, t, repo_workspace, repo_root=repo)
                for t in (feature, task)
            ),
        ]

    before = where_it_lands()
    assert before == ["integration/lg-milestone-that-491"] * 4

    add_member(db_session, initiative.id, feature.id, actor="test")
    assert where_it_lands() == before
    remove_member(db_session, initiative.id, feature.id)
    assert where_it_lands() == before


# --- API and MCP -------------------------------------------------------------


def test_api_adds_lists_and_removes_a_member(client, db_session, tree):
    initiative = _initiative(db_session)
    feature = tree["feature"]

    candidates = client.get(
        f"/api/initiatives/{initiative.id}/member-candidates", params={"search": "780"}
    )
    assert candidates.status_code == 200
    assert {c["external_id"] for c in candidates.json()} == {"ft-780", "cap-780", "task-780"}
    assert {c["home_milestone"] for c in candidates.json()} == {"ms-491"}

    added = client.post(f"/api/initiatives/{initiative.id}/members", json={"ticket_id": feature.id})
    assert added.status_code == 201, added.text
    [row] = added.json()["milestones"]
    assert (row["id"], row["member"], row["home_milestone"], row["workspace_slug"]) == (
        feature.id,
        True,
        "ms-491",
        "loregarden",
    )
    # Covered now, so no longer offered.
    again = client.get(
        f"/api/initiatives/{initiative.id}/member-candidates", params={"search": "780"}
    )
    assert again.json() == []

    listed = [i for i in client.get("/api/initiatives").json() if i["id"] == initiative.id]
    assert listed[0]["milestones"] == added.json()["milestones"]

    board = client.get("/api/tickets", params={"ancestor_ticket_id": initiative.id}).json()
    assert {t["id"] for t in board} >= {feature.id, tree["task"].id}

    removed = client.delete(f"/api/initiatives/{initiative.id}/members/{feature.id}")
    assert removed.status_code == 204
    assert client.get(f"/api/initiatives/{initiative.id}").json()["milestones"] == []


def test_api_refusals_carry_their_reason(client, db_session, tree):
    initiative, other = _initiative(db_session), _initiative(db_session, "Other")
    url = f"/api/initiatives/{initiative.id}/members"

    assert client.post(url, json={"ticket_id": other.id}).status_code == 400
    assert client.post(url, json={"ticket_id": "nope"}).status_code == 404
    assert client.post(url, json={"ticket_id": tree["feature"].id}).status_code == 201
    dup = client.post(url, json={"ticket_id": tree["feature"].id})
    assert dup.status_code == 409 and "already a member" in dup.json()["detail"]
    under = client.post(url, json={"ticket_id": tree["task"].id})
    assert under.status_code == 409 and "already covers" in under.json()["detail"]
    missing = client.delete(f"{url}/{tree['task'].id}")
    assert missing.status_code == 404 and "not a member" in missing.json()["detail"]
    assert client.post("/api/initiatives/nope/members", json={"ticket_id": "x"}).status_code == 404


def test_mcp_tools_take_external_ids(db_session, tree):
    initiative = _initiative(db_session)
    added = json.loads(
        _call(
            db_session,
            "loregarden_add_initiative_member",
            {"initiative_id": initiative.external_id, "ticket_id": "ft-780"},
        )
    )
    assert added["items"] == [
        {
            "id": tree["feature"].id,
            "external_id": "ft-780",
            "work_item_type": "feature",
            "workspace_slug": "loregarden",
            "member": True,
            "home_milestone": "ms-491",
        }
    ]
    removed = json.loads(
        _call(
            db_session,
            "loregarden_remove_initiative_member",
            {"initiative_id": initiative.id, "ticket_id": tree["feature"].id},
        )
    )
    assert removed["items"] == []


def test_mcp_refusal_is_an_error_not_a_success(client, db_session):
    initiative, other = _initiative(db_session), _initiative(db_session, "Other")
    body = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "loregarden_add_initiative_member",
                "arguments": {"initiative_id": initiative.id, "ticket_id": other.id},
            },
        },
    ).json()
    assert body["result"]["isError"]
    assert "is an initiative" in body["result"]["content"][0]["text"]


# --- migration ---------------------------------------------------------------


def test_migration_creates_the_table_and_is_idempotent(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE tickets (id VARCHAR PRIMARY KEY)"))
        conn.execute(text("INSERT INTO tickets (id) VALUES ('i'), ('t')"))
        m_initiative_members(conn)
        m_initiative_members(conn)
        conn.execute(
            text(
                "INSERT INTO initiative_members (initiative_id, ticket_id, added_at, added_by) "
                "VALUES ('i', 't', CURRENT_TIMESTAMP, 'test')"
            )
        )
        indexes = {
            row[0]
            for row in conn.execute(
                text("SELECT name FROM sqlite_master WHERE tbl_name = 'initiative_members'")
            )
        }
    assert "ix_initiative_members_ticket_id" in indexes
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO initiative_members (initiative_id, ticket_id, added_at) "
                "VALUES ('i', 't', CURRENT_TIMESTAMP)"
            )
        )
