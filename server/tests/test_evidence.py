"""Evidence artifacts: proof of behaviour, bound to the commit it proves (S1)."""

import pytest
from loregarden.mcp.tools import execute_tool, tool_names
from loregarden.models.domain import Artifact, Ticket, WorkItemType, Workspace
from loregarden.services.evidence import EVIDENCE_KINDS, evidence_for_commit, has_evidence
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from sqlmodel import Session, SQLModel, create_engine, select


@pytest.fixture()
def session_and_ticket(tmp_path, git_repo):
    engine = create_engine(f"sqlite:///{tmp_path / 'db.sqlite'}")
    SQLModel.metadata.create_all(engine)
    session = Session(engine)
    session.add(Workspace(id="ws", slug="ws", name="WS", repo_path=str(git_repo)))
    ticket = Ticket(
        id="t1",
        external_id="42-evidence",
        workspace_id="ws",
        title="Evidence",
        work_item_type=WorkItemType.TASK,
    )
    session.add(ticket)
    session.commit()
    return session, ticket


def test_evidence_tool_is_registered_and_auto_approved():
    from loregarden.agents.executors.permission_bridge import AUTO_APPROVED_MCP_TOOLS

    assert "loregarden_attach_evidence" in tool_names()
    # Writes only to the artifacts table, same trust level as attach_artifact.
    assert "loregarden_attach_evidence" in AUTO_APPROVED_MCP_TOOLS


def test_evidence_is_stamped_with_the_current_head(session_and_ticket, git_repo):
    session, ticket = session_and_ticket
    svc = OrchestrationCallbackService(session)
    from loregarden.services.evidence import resolve_head_sha

    head = resolve_head_sha(session, ticket)
    assert head  # a real sha, not empty

    svc.attach_artifact(
        ticket,
        kind="evidence",
        title="POST /tickets returns 201",
        content={"status": 201},
        evidence_kind="real_surface",
        commit_sha=head,
    )
    stored = session.exec(select(Artifact).where(Artifact.kind == "evidence")).first()
    assert stored.commit_sha == head
    assert stored.evidence_kind == "real_surface"


def test_evidence_is_scoped_to_the_commit_it_proves(session_and_ticket, git_repo):
    """Evidence captured before the last edit proves nothing about the new code."""
    session, ticket = session_and_ticket
    svc = OrchestrationCallbackService(session)
    svc.attach_artifact(
        ticket,
        kind="evidence",
        title="old proof",
        content={},
        evidence_kind="real_surface",
        commit_sha="oldsha",
    )

    assert has_evidence(session, ticket, commit_sha="oldsha") is True
    assert has_evidence(session, ticket, commit_sha="newsha") is False
    assert len(evidence_for_commit(session, ticket)) == 1


def test_evidence_can_be_narrowed_by_kind(session_and_ticket):
    session, ticket = session_and_ticket
    svc = OrchestrationCallbackService(session)
    svc.attach_artifact(
        ticket,
        kind="evidence",
        title="tests",
        content={},
        evidence_kind="test_red_green",
        commit_sha="sha1",
    )
    # The floor is present but the ceiling is not — what a two-artifact gate asks.
    assert has_evidence(session, ticket, commit_sha="sha1", evidence_kind="test_red_green")
    assert not has_evidence(session, ticket, commit_sha="sha1", evidence_kind="real_surface")


def test_unknown_evidence_kind_is_rejected(session_and_ticket):
    """A free-form kind would make a gate's question unanswerable."""
    session, ticket = session_and_ticket
    from loregarden.models.domain import OrchestrationRun

    session.add(
        OrchestrationRun(
            id="orun",
            run_code="orun_1",
            ticket_id=ticket.id,
            workspace_id="ws",
        )
    )
    session.commit()

    with pytest.raises(ValueError, match="Unknown evidence_kind"):
        execute_tool(
            session,
            "loregarden_attach_evidence",
            {
                "run_id": "orun",
                "evidence_kind": "vibes",
                "title": "trust me",
            },
        )


def test_evidence_kinds_cover_floor_ceiling_and_verdict():
    assert set(EVIDENCE_KINDS) == {"test_red_green", "real_surface", "verify_verdict"}
