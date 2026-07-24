"""The commit-scoped evidence ledger surfaced to downstream consumer agents.

Lists proof already established for the exact current tree (recorded at HEAD,
clean working tree) so a consumer reuses it instead of redoing it — with the
verifier deliberately excluded to keep its check independent.
"""

import pytest
from loregarden.agents.evidence_context import build_evidence_ledger
from loregarden.models.domain import Ticket, WorkItemType, Workspace
from loregarden.services.evidence import resolve_head_sha
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from sqlmodel import Session, SQLModel, create_engine


@pytest.fixture()
def session_and_ticket(tmp_path, git_repo):
    engine = create_engine(f"sqlite:///{tmp_path / 'db.sqlite'}")
    SQLModel.metadata.create_all(engine)
    session = Session(engine)
    session.add(Workspace(id="ws", slug="ws", name="WS", repo_path=str(git_repo)))
    ticket = Ticket(
        id="t1",
        external_id="42-ledger",
        workspace_id="ws",
        title="Ledger",
        work_item_type=WorkItemType.TASK,
    )
    session.add(ticket)
    session.commit()
    return session, ticket


def _record(session, ticket, evidence_kind, commit_sha):
    OrchestrationCallbackService(session).attach_artifact(
        ticket,
        kind="evidence",
        title=evidence_kind,
        content={},
        evidence_kind=evidence_kind,
        commit_sha=commit_sha,
    )


def test_ledger_is_empty_when_nothing_is_proven(session_and_ticket, git_repo):
    session, ticket = session_and_ticket
    assert build_evidence_ledger(session, ticket, git_repo, is_verify=False) == ""


def test_ledger_lists_evidence_recorded_at_head(session_and_ticket, git_repo):
    session, ticket = session_and_ticket
    head = resolve_head_sha(session, ticket)
    _record(session, ticket, "full_suite_green", head)
    _record(session, ticket, "real_surface", head)

    ledger = build_evidence_ledger(session, ticket, git_repo, is_verify=False)
    assert "full regression suite passed" in ledger.lower()
    assert "Do not re-run it" in ledger
    assert "real surface" in ledger.lower()


def test_ledger_is_withheld_from_the_verifier(session_and_ticket, git_repo):
    """A verifier primed with what was already concluded is no longer independent."""
    session, ticket = session_and_ticket
    _record(session, ticket, "full_suite_green", resolve_head_sha(session, ticket))
    assert build_evidence_ledger(session, ticket, git_repo, is_verify=True) == ""


def test_ledger_excludes_evidence_from_an_earlier_commit(session_and_ticket, git_repo):
    session, ticket = session_and_ticket
    _record(session, ticket, "full_suite_green", "an-earlier-commit")
    assert build_evidence_ledger(session, ticket, git_repo, is_verify=False) == ""


def test_ledger_is_empty_when_the_tree_is_dirty(session_and_ticket, git_repo):
    """An uncommitted edit moves the tree off what was proven, so nothing is
    surfaced as reusable."""
    session, ticket = session_and_ticket
    _record(session, ticket, "full_suite_green", resolve_head_sha(session, ticket))
    (git_repo / "drift.txt").write_text("x", encoding="utf-8")
    assert build_evidence_ledger(session, ticket, git_repo, is_verify=False) == ""
