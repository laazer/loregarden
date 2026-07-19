"""Recall of what was already attempted on tickets like this one (Cat F)."""

from loregarden.models.domain import Ticket, TicketState, WorkItemType, Workspace
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.prior_work import search_prior_work
from sqlmodel import Session, SQLModel, create_engine


def _session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'pw.db'}")
    SQLModel.metadata.create_all(engine)
    session = Session(engine)
    session.add(Workspace(id="ws", slug="ws", name="WS", repo_path="."))
    session.commit()
    return session


def _ticket(session, external_id, title, *, state=TicketState.DONE, description=""):
    ticket = Ticket(
        external_id=external_id,
        workspace_id="ws",
        title=title,
        description=description,
        state=state,
        work_item_type=WorkItemType.TASK,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def test_finds_a_finished_ticket_covering_the_same_ground(tmp_path):
    session = _session(tmp_path)
    _ticket(session, "30-smart-import", "Smart ticket import from markdown")
    hits = search_prior_work(session, "smart import of markdown tickets", workspace_slug="ws")
    assert [h["external_id"] for h in hits] == ["30-smart-import"]
    assert "import" in hits[0]["matched_terms"]


def test_unrelated_work_is_not_returned(tmp_path):
    """A recall tool that always answers is worse than one that says nothing."""
    session = _session(tmp_path)
    _ticket(session, "30-smart-import", "Smart ticket import from markdown")
    assert search_prior_work(session, "quantum blockchain renderer", workspace_slug="ws") == []


def test_in_flight_tickets_are_excluded(tmp_path):
    """A ticket still in progress has not established anything yet."""
    session = _session(tmp_path)
    _ticket(session, "40-running", "Smart import rework", state=TicketState.IN_PROGRESS)
    assert search_prior_work(session, "smart import rework", workspace_slug="ws") == []


def test_abandoned_work_still_counts(tmp_path):
    """Why an approach was dropped is exactly what a later attempt needs."""
    session = _session(tmp_path)
    _ticket(session, "41-dropped", "Smart import via websocket", state=TicketState.WONT_DO)
    hits = search_prior_work(session, "smart import via websocket", workspace_slug="ws")
    assert [h["state"] for h in hits] == ["wont_do"]


def test_the_current_ticket_is_not_its_own_prior_work(tmp_path):
    session = _session(tmp_path)
    current = _ticket(session, "50-self", "Smart import parser")
    hits = search_prior_work(
        session, "smart import parser", workspace_slug="ws", exclude_ticket_id=current.id
    )
    assert hits == []


def test_errors_surface_before_successes(tmp_path):
    """What went wrong is more use than what eventually went right."""
    session = _session(tmp_path)
    ticket = _ticket(session, "34-route-import", "Route smart import selection")
    svc = OrchestrationCallbackService(session)
    svc.attach_artifact(ticket, kind="diff", title="the change", content={})
    svc.attach_artifact(ticket, kind="error", title="gate failed twice", content={})

    hits = search_prior_work(session, "route smart import selection", workspace_slug="ws")
    kinds = [a["kind"] for a in hits[0]["artifacts"]]
    assert kinds[0] == "error"


def test_ranking_prefers_the_closer_match(tmp_path):
    session = _session(tmp_path)
    _ticket(session, "10-weak", "Import parser tweak")
    _ticket(session, "11-strong", "Smart import parser rewrite for markdown tickets")
    hits = search_prior_work(session, "smart import parser markdown rewrite", workspace_slug="ws")
    assert hits[0]["external_id"] == "11-strong"


def test_web_reads_are_approved_by_policy_not_per_url():
    """The stored allowlist keys on exact tool input, so every URL would need its
    own rule and an unattended research run stalls on the first fetch."""
    from loregarden.agents.executors.permission_bridge import is_auto_approved_cli_tool

    assert is_auto_approved_cli_tool("WebFetch") is True
    assert is_auto_approved_cli_tool("WebSearch") is True
    # Anything that can touch the repo still asks.
    assert is_auto_approved_cli_tool("Bash") is False
    assert is_auto_approved_cli_tool("Write") is False
