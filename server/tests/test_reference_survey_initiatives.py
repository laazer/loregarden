"""Initiatives that follow a survey source: open tickets join, settled ones stay
out, later findings join too, and unlinking removes only what the link holds."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from loregarden.models.domain import InitiativeMember, Ticket, TicketState, WorkItemType
from loregarden.models.domain.survey_enums import (
    SurveyEpistemic,
    SurveyFindingClaim,
    SurveyTicketAction,
)
from loregarden.models.domain.survey_tables import SurveyPassTicket
from loregarden.services.initiative_membership import add_member
from loregarden.services.reference_survey.errors import SurveyError
from loregarden.services.reference_survey.initiatives import (
    LINK_ACTOR,
    link_initiative,
    linked_initiatives,
    unlink_initiative,
)
from loregarden.services.reference_survey.record import record_evidence, record_finding
from loregarden.services.reference_survey.schemas import EvidenceIn, FindingIn, TicketWriteIn
from loregarden.services.reference_survey.ticket_writer import write_findings_to_ticket
from sqlmodel import Session, select
from tests.factories import make_ticket
from tests.survey_support import build_world, running_pass, write_tree


@pytest.fixture(name="ctx")
def ctx_fixture(isolated_db, tmp_path):
    session = Session(isolated_db)
    world = build_world(session)
    source, survey_pass = running_pass(
        session, world, write_tree(tmp_path / "clone", {"a.py": "a"})
    )
    initiative = make_ticket(
        session, workspace_id=None, external_id="init-survey-1", title="Make surveys pay off",
        work_item_type=WorkItemType.INITIATIVE,
    )  # fmt: skip

    def ticket(ext: str, state: TicketState) -> Ticket:
        row = make_ticket(
            session, workspace_id=world.workspace_id, external_id=ext, title=ext,
            work_item_type=WorkItemType.CAPABILITY, parent_ticket_id=world.feature_id, state=state,
        )  # fmt: skip
        session.add(
            SurveyPassTicket(
                pass_id=survey_pass.id, ticket_id=row.id, action=SurveyTicketAction.APPENDED
            )
        )
        session.commit()
        return row

    open_one = ticket("lg-open-1", TicketState.BACKLOG)
    open_two = ticket("lg-open-2", TicketState.IN_PROGRESS)
    done = ticket("lg-done-3", TicketState.DONE)
    yield session, world, source, survey_pass, initiative, (open_one, open_two, done)
    session.close()


def _members(session, initiative):
    return {
        session.get(Ticket, m.ticket_id).external_id: m.added_by
        for m in session.exec(
            select(InitiativeMember).where(InitiativeMember.initiative_id == initiative.id)
        )
    }


def test_linking_brings_in_open_tickets_and_leaves_settled_ones_out(ctx):
    session, _world, source, _pass, initiative, _ = ctx
    result = link_initiative(session, source.id, initiative.external_id, actor="operator")
    assert (result.added, result.settled_left_out, result.skipped) == (
        ["lg-open-1", "lg-open-2"],
        1,
        [],
    )
    assert _members(session, initiative) == {"lg-open-1": LINK_ACTOR, "lg-open-2": LINK_ACTOR}
    assert [(i.external_id, i.members_added) for i in linked_initiatives(session, source.id)] == [
        ("init-survey-1", 2)
    ]
    again = link_initiative(session, source.id, initiative.id, actor="operator")
    assert again.added == []


def test_a_ticket_the_initiative_already_covers_is_skipped_with_the_reason(ctx):
    session, _world, source, _pass, initiative, (open_one, _two, _done) = ctx
    child = make_ticket(
        session, workspace_id=open_one.workspace_id, external_id="lg-child-4", title="child",
        work_item_type=WorkItemType.TASK, parent_ticket_id=open_one.id,
    )  # fmt: skip
    session.add(
        SurveyPassTicket(pass_id=_pass.id, ticket_id=child.id, action=SurveyTicketAction.CREATED)
    )
    session.commit()
    result = link_initiative(session, source.id, initiative.id, actor="operator")
    assert "lg-child-4" not in result.added
    assert [s.ticket for s in result.skipped] == ["lg-child-4"]
    assert "already covers" in result.skipped[0].reason


def test_new_findings_join_linked_initiatives(ctx):
    session, world, source, survey_pass, initiative, _ = ctx
    link_initiative(session, source.id, initiative.id, actor="operator")
    ev = record_evidence(
        session,
        EvidenceIn(
            source_id=source.id,
            pass_id=survey_pass.id,
            epistemic=SurveyEpistemic.OBSERVED,
            operation="read",
        ),
    )
    finding = record_finding(
        session,
        FindingIn(source_id=source.id, pass_id=survey_pass.id, claim=SurveyFindingClaim.ASSERTION,
                  epistemic=SurveyEpistemic.OBSERVED, statement="s", evidence_ids=[ev.id]),
    )  # fmt: skip
    result = write_findings_to_ticket(
        session,
        TicketWriteIn(
            pass_id=survey_pass.id, finding_ids=[finding.id], block="b", force_new_reason="new area",
            create={"title": "Brand new survey capability", "parent": world.feature_ext,
                    "work_item_type": "capability", "description": "d"},
        ),
    )  # fmt: skip
    assert result.ticket.external_id in _members(session, initiative)
    assert any("joined linked initiative init-survey-1" in note for note in result.notes)


def test_unlinking_removes_only_what_the_link_alone_holds(ctx, tmp_path):
    session, world, source, _pass, initiative, (open_one, open_two, _done) = ctx
    # A person tracked lg-open-1 before the link existed: it is theirs.
    add_member(session, initiative.id, open_one.id, actor="person")
    # A second linked source holds lg-open-2 too.
    other, other_pass = running_pass(
        session,
        world,
        write_tree(tmp_path / "other", {"b.py": "b"}),
        url="https://github.com/acme/other",
    )
    session.add(
        SurveyPassTicket(
            pass_id=other_pass.id, ticket_id=open_two.id, action=SurveyTicketAction.APPENDED
        )
    )
    session.commit()
    link_initiative(session, source.id, initiative.id, actor="operator")
    link_initiative(session, other.id, initiative.id, actor="operator")

    first = unlink_initiative(session, source.id, initiative.id)
    assert (first.removed, first.kept) == ([], ["lg-open-2"])
    assert _members(session, initiative) == {"lg-open-1": "person", "lg-open-2": LINK_ACTOR}
    second = unlink_initiative(session, other.id, initiative.id)
    assert (second.removed, second.kept) == (["lg-open-2"], [])
    assert _members(session, initiative) == {"lg-open-1": "person"}
    with pytest.raises(SurveyError, match="not linked"):
        unlink_initiative(session, source.id, initiative.id)


def test_only_an_initiative_can_follow_a_source(ctx):
    session, world, source, _pass, _initiative, _ = ctx
    with pytest.raises(SurveyError, match="is not an initiative"):
        link_initiative(session, source.id, world.feature_ext, actor="operator")


def test_the_api_links_and_unlinks(client: TestClient, isolated_db, tmp_path):
    with Session(isolated_db) as session:
        world = build_world(session)
        source, _ = running_pass(session, world, write_tree(tmp_path / "api-clone", {"a.py": "a"}))
        initiative = make_ticket(
            session,
            workspace_id=None,
            external_id="init-api-2",
            title="i",
            work_item_type=WorkItemType.INITIATIVE,
        )
        source_id, initiative_id = source.id, initiative.id
    linked = client.post(
        f"/api/reference-library/{source_id}/initiatives", json={"initiative_id": "init-api-2"}
    )
    assert linked.status_code == 200, linked.text
    assert linked.json()["initiative"] == "init-api-2"
    detail = client.get(f"/api/reference-library/{source_id}").json()
    assert [i["external_id"] for i in detail["initiatives"]] == ["init-api-2"]
    assert (
        client.delete(f"/api/reference-library/{source_id}/initiatives/{initiative_id}").status_code
        == 200
    )
    assert (
        client.delete(f"/api/reference-library/{source_id}/initiatives/{initiative_id}").status_code
        == 422
    )
    missing = client.post(
        f"/api/reference-library/{source_id}/initiatives", json={"initiative_id": "nope"}
    )
    assert missing.status_code == 404
