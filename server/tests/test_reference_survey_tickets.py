"""Landing survey findings on tickets: read-back, the cap, done tickets, hierarchy."""

from __future__ import annotations

import json
from unittest import mock

import pytest
from loregarden.models.domain import (
    Approval,
    Artifact,
    Ticket,
    TicketState,
    UpdateTicketRequest,
    WorkItemType,
)
from loregarden.models.domain.survey_enums import (
    SurveyEpistemic,
    SurveyFindingClaim,
    SurveyTicketAction,
)
from loregarden.models.domain.survey_tables import SurveyPassTicket, SurveySource
from loregarden.services.acceptance_criteria import load_criteria, serialize_criteria
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.reference_survey.errors import SurveyError, TicketWriteMismatch
from loregarden.services.reference_survey.record import (
    finding_evidence,
    record_evidence,
    record_finding,
    retract_finding,
)
from loregarden.services.reference_survey.schemas import (
    EvidenceIn,
    FindingIn,
    RetractIn,
    TicketWriteIn,
)
from loregarden.services.reference_survey.ticket_writer import (
    DOCUMENT_TITLE,
    POINTER_HEADER,
    SELF_DEFECT_TAG,
    publish_retraction,
    write_findings_to_ticket,
)
from loregarden.services.ticket_relations import TicketRelationService
from loregarden.services.ticket_tags import load_tags
from sqlmodel import Session, col, select
from tests.factories import make_ticket
from tests.survey_support import build_world, running_pass, second_workspace, serve, write_tree


@pytest.fixture(name="ctx")
def ctx_fixture(isolated_db, tmp_path):
    session = Session(isolated_db)
    world = build_world(session)
    source, survey_pass = running_pass(
        session, world, write_tree(tmp_path / "clone", {"src/gate.py": "x"})
    )
    ev = record_evidence(
        session,
        EvidenceIn(
            source_id=source.id, pass_id=survey_pass.id, epistemic=SurveyEpistemic.OBSERVED,
            operation="read src/gate.py", corpus=["src/gate.py"],
        ),
    )  # fmt: skip
    finding = record_finding(
        session,
        FindingIn(
            source_id=source.id, pass_id=survey_pass.id, claim=SurveyFindingClaim.ASSERTION,
            epistemic=SurveyEpistemic.OBSERVED, statement="Their gate recomputes the verdict.", evidence_ids=[ev.id],
        ),
    )  # fmt: skip
    yield session, world, survey_pass, finding
    session.close()


def _write(session, survey_pass, finding, **kw):
    body = {
        "pass_id": survey_pass.id,
        "finding_ids": [finding.id],
        "block": "The host recomputes the verdict.",
    } | kw
    return write_findings_to_ticket(session, TicketWriteIn(**body))


def _doc(session, ticket_id):
    row = session.exec(
        select(Artifact)
        .where(Artifact.ticket_id == ticket_id, Artifact.title == DOCUMENT_TITLE)
        .order_by(col(Artifact.created_at).desc())
    ).first()
    return json.loads(row.content_json)["body"]


def _with_criteria(session, ticket_id, n):
    ticket = session.get(Ticket, ticket_id)
    ticket.acceptance_criteria_json = serialize_criteria([f"existing {i}" for i in range(n)])
    session.add(ticket)
    session.commit()
    return ticket


def test_append_writes_criteria_document_and_pointer_and_reads_them_back(ctx):
    session, world, survey_pass, finding = ctx
    first = _write(
        session,
        survey_pass,
        finding,
        ticket=world.feature_ext,
        criteria=["The verdict is recomputed by the host."],
    )
    assert first.action is SurveyTicketAction.APPENDED
    ticket = session.get(Ticket, world.feature_id)
    assert load_criteria(ticket.acceptance_criteria_json) == [
        "The verdict is recomputed by the host."
    ]
    body = _doc(session, ticket.id)
    assert body.startswith("## Prior art — widget (https://github.com/acme/widget, MIT, @abc1234d)")
    evidence_id = finding_evidence(session, finding.id)[0].id
    assert f"**Observed**: Their gate recomputes the verdict. (evidence `{evidence_id}`)" in body
    assert ticket.description.count(POINTER_HEADER) == 1
    assert first.document_artifact_id in ticket.description

    second = _write(
        session, survey_pass, finding, ticket=world.feature_ext, block="A second block."
    )
    ticket = session.get(Ticket, world.feature_id)
    assert _doc(session, ticket.id).startswith(body)
    assert ticket.description.count(POINTER_HEADER) == 1
    assert second.document_artifact_id in ticket.description
    assert first.document_artifact_id not in ticket.description
    links = session.exec(
        select(SurveyPassTicket).where(SurveyPassTicket.pass_id == survey_pass.id)
    ).all()
    assert [link.verified for link in links] == [True, True]


def test_criteria_dropped_by_the_write_path_are_caught_not_trusted(ctx):
    """The PATCH trap: a write that answers success and drops the criteria."""
    session, world, survey_pass, finding = ctx
    real = OrchestrationService.update_ticket_manual

    def drops_criteria(self, ticket, body: UpdateTicketRequest):
        return real(self, ticket, body.model_copy(update={"acceptance_criteria": None}))

    with mock.patch.object(OrchestrationService, "update_ticket_manual", drops_criteria):
        with pytest.raises(TicketWriteMismatch, match="criteria read back differ"):
            _write(
                session,
                survey_pass,
                finding,
                ticket=world.feature_ext,
                criteria=["A new criterion."],
            )
    cards = session.exec(
        select(Approval).where(Approval.title.contains("Survey write did not land"))
    ).all()
    assert len(cards) == 1
    assert session.exec(select(SurveyPassTicket)).all() == []


@pytest.mark.parametrize(
    "existing, action", [(11, SurveyTicketAction.APPENDED), (12, SurveyTicketAction.SPLIT)]
)
def test_the_split_happens_exactly_at_the_cap(ctx, existing, action):
    session, world, survey_pass, finding = ctx
    target = _with_criteria(session, world.feature_id, existing)
    result = _write(session, survey_pass, finding, ticket=world.feature_ext, criteria=["One more."])
    assert result.action is action
    target = session.get(Ticket, target.id)
    if action is SurveyTicketAction.APPENDED:
        assert len(load_criteria(target.acceptance_criteria_json)) == 12
        return
    assert len(load_criteria(target.acceptance_criteria_json)) == 12
    sibling = result.ticket
    assert sibling.id != target.id
    assert sibling.parent_ticket_id == target.parent_ticket_id
    assert sibling.work_item_type is target.work_item_type
    assert load_criteria(sibling.acceptance_criteria_json) == ["One more."]
    assert target.id in TicketRelationService(session).related(sibling.id)
    assert result.aimed_at.id == target.id


def test_criteria_aimed_at_a_done_ticket_go_to_a_new_related_one(ctx):
    session, world, survey_pass, finding = ctx
    done = _with_criteria(session, world.feature_id, 3)
    done.state = TicketState.DONE
    session.add(done)
    session.commit()
    result = _write(
        session, survey_pass, finding, ticket=world.feature_ext, criteria=["Follow-up."]
    )
    assert result.action is SurveyTicketAction.ROUTED_FROM_DONE
    assert len(load_criteria(session.get(Ticket, done.id).acceptance_criteria_json)) == 3
    assert done.id in TicketRelationService(session).related(result.ticket.id)
    # No criteria: the block still lands on the done ticket's own document.
    doc_only = _write(session, survey_pass, finding, ticket=world.feature_ext)
    assert (doc_only.action, doc_only.ticket.id) == (SurveyTicketAction.APPENDED, done.id)


def test_hierarchy_is_checked_before_anything_is_created(ctx):
    session, world, survey_pass, finding = ctx
    before = len(session.exec(select(Ticket)).all())
    with pytest.raises(SurveyError, match="Resolve the parent before creating"):
        _write(
            session, survey_pass, finding,
            create={"title": "Brand new gate thing", "parent": world.feature_ext, "work_item_type": "feature", "description": "d"},
        )  # fmt: skip
    assert len(session.exec(select(Ticket)).all()) == before


def test_creating_beside_a_similar_open_ticket_needs_a_reason(ctx):
    session, world, survey_pass, finding = ctx
    spec = {
        "title": "Typed reference sources, again",
        "parent": world.feature_ext,
        "work_item_type": "capability",
        "description": "d",
    }
    with pytest.raises(SurveyError, match="lg-survey-f-2"):
        _write(session, survey_pass, finding, create=spec)
    created = _write(session, survey_pass, finding, create=spec, force_new_reason="different layer")
    assert created.action is SurveyTicketAction.CREATED
    assert created.ticket.parent_ticket_id == world.feature_id
    assert any("different layer" in note for note in created.notes)


def test_a_defect_in_our_own_tooling_is_filed_as_a_tagged_bug(ctx):
    session, world, survey_pass, finding = ctx
    result = _write(
        session, survey_pass, finding, self_defect=True,
        create={
            "title": "sandbox.sh launches Vite under whatever node is on PATH",
            "parent": world.feature_ext, "work_item_type": "bug", "description": "Node 14 dies on ??=.",
        },
    )  # fmt: skip
    assert result.action is SurveyTicketAction.SELF_DEFECT
    assert result.ticket.work_item_type is WorkItemType.BUG
    assert SELF_DEFECT_TAG in load_tags(result.ticket.tags_json)


def test_a_retraction_is_appended_to_every_ticket_that_carries_the_finding(ctx):
    session, world, survey_pass, finding = ctx
    _write(session, survey_pass, finding, ticket=world.feature_ext)
    before = _doc(session, world.feature_id)
    retracted = retract_finding(
        session, RetractIn(finding_id=finding.id, reason="the runner lives in client/scripts")
    )
    assert publish_retraction(session, retracted) == [world.feature_ext]
    after = _doc(session, world.feature_id)
    assert after.startswith(before)
    assert f"## Retracted — finding `{finding.id}`" in after
    assert "the runner lives in client/scripts" in after


def test_a_retracted_finding_cannot_be_landed(ctx):
    session, world, survey_pass, finding = ctx
    retract_finding(session, RetractIn(finding_id=finding.id, reason="wrong"))
    with pytest.raises(SurveyError, match="retracted"):
        _write(session, survey_pass, finding, ticket=world.feature_ext)


def test_one_pass_lands_findings_in_every_workspace_its_source_serves(ctx):
    session, world, survey_pass, finding = ctx
    blobert = second_workspace(session, world)
    source = session.get(SurveySource, survey_pass.source_id)
    serve(session, source, blobert.id, question="Their asset pipeline?")
    milestone = make_ticket(
        session, workspace_id=blobert.id, external_id="blob-m-1", title="Assets",
        work_item_type=WorkItemType.MILESTONE,
    )  # fmt: skip
    feature = make_ticket(
        session, workspace_id=blobert.id, external_id="blob-f-2", title="Asset import",
        work_item_type=WorkItemType.FEATURE, parent_ticket_id=milestone.id,
    )  # fmt: skip

    # Append: the external id is found in whichever workspace holds it.
    there = _write(
        session, survey_pass, finding, ticket="blob-f-2", criteria=["Imports are validated."]
    )
    assert (there.ticket.id, there.ticket.workspace_id) == (feature.id, blobert.id)
    here = _write(session, survey_pass, finding, ticket=world.feature_ext)
    assert here.ticket.workspace_id == world.workspace_id

    # Create: with two workspaces served, the new ticket's workspace must be named.
    spec = {
        "title": "Validate imported meshes",
        "parent": "blob-f-2",
        "work_item_type": "capability",
        "description": "d",
    }
    with pytest.raises(SurveyError, match="name create.workspace_slug"):
        _write(session, survey_pass, finding, create=spec)
    with pytest.raises(SurveyError, match="not one of this source's workspaces"):
        _write(session, survey_pass, finding, create=spec | {"workspace_slug": "nowhere"})
    created = _write(session, survey_pass, finding, create=spec | {"workspace_slug": "blobert"})
    assert (created.ticket.workspace_id, created.ticket.parent_ticket_id) == (
        blobert.id,
        feature.id,
    )


def test_a_ticket_outside_the_sources_workspaces_is_not_found(ctx):
    session, world, survey_pass, finding = ctx
    elsewhere = second_workspace(session, world, "elsewhere")
    make_ticket(session, workspace_id=elsewhere.id, external_id="else-1", title="x")
    with pytest.raises(SurveyError, match="No ticket 'else-1' in loregarden"):
        _write(session, survey_pass, finding, ticket="else-1")
