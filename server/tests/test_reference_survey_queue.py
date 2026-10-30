"""The queue, the pass lifecycle, coverage as data, and the feeder that dispatches."""

from __future__ import annotations

import json
from unittest import mock

import pytest
from loregarden.models.domain import (
    Approval,
    ApprovalStatus,
    OrchestrationRun,
    OrchestrationRunStatus,
    Ticket,
    TicketState,
    WorkflowInstance,
    WorkflowTemplate,
    WorkItemType,
)
from loregarden.models.domain.survey_enums import (
    LicenceBasis,
    LicenceKind,
    LicenceUse,
    SurveyPassMode,
    SurveyPassOutcome,
    SurveyPassStatus,
    SurveyReadState,
    SurveySourceKind,
    SurveySourceStatus,
)
from loregarden.models.domain.survey_tables import SurveyPass, SurveySource
from loregarden.services.orchestration import ApprovalService
from loregarden.services.reference_survey import feeder
from loregarden.services.reference_survey.coverage import map_tree, tree_file_total
from loregarden.services.reference_survey.errors import SurveyError
from loregarden.services.reference_survey.passes import (
    assign_slice,
    coverage_report,
    finish_pass,
    record_reads,
)
from loregarden.services.reference_survey.queue import (
    MAX_PASSES_PER_QUESTION,
    enqueue_source,
    promote_resolved_sources,
    request_pass,
    target_workspaces,
)
from loregarden.services.reference_survey.record import open_unknown
from loregarden.services.reference_survey.schemas import (
    PassFinishIn,
    PassRequestIn,
    ReadIn,
    SourceIn,
    UnknownOpenIn,
)
from sqlmodel import Session, select
from tests.survey_support import (
    MIT_TEXT,
    build_world,
    running_pass,
    second_workspace,
    serve,
    write_tree,
)

TREE = {
    "README.md": "r",
    "docs/a.md": "a",
    "docs/design/b.md": "b",
    "docs/design/c.md": "c",
    "src/core/x.py": "x",
    "src/core/y.py": "y",
    "src/ui/z.ts": "z",
    "tests/t.py": "t",
}


@pytest.fixture(name="ctx")
def ctx_fixture(isolated_db, tmp_path):
    session = Session(isolated_db)
    world = build_world(session)
    tree = write_tree(tmp_path / "clone", TREE)
    yield session, world, tree
    session.close()


def _source_in(world, **kw):
    return SourceIn(
        **({"workspace_slugs": [world.workspace_slug], "kind": SurveySourceKind.REPO, "url": "https://github.com/acme/widget",
            "question": "How do they gate?", "queued_reason": "it ships a gate"} | kw)
    )  # fmt: skip


def test_a_new_source_waits_for_a_person_and_is_queued_only_on_approval(ctx):
    session, world, _ = ctx
    source = enqueue_source(session, _source_in(world)).source
    assert source.status is SurveySourceStatus.PROPOSED
    card = session.get(Approval, source.approval_id)
    assert card.status is ApprovalStatus.PENDING and "Admit reference source" in card.title
    with pytest.raises(SurveyError, match="already serves loregarden"):
        enqueue_source(session, _source_in(world, url="https://github.com/acme/widget.git"))
    assert promote_resolved_sources(session) == []
    ApprovalService(session).resolve(card.id, approved=True)
    assert [s.id for s in promote_resolved_sources(session)] == [source.id]
    session.refresh(source)
    assert source.status is SurveySourceStatus.ACTIVE
    initial = session.exec(select(SurveyPass).where(SurveyPass.source_id == source.id)).one()
    assert (initial.mode, initial.status, initial.question) == (
        SurveyPassMode.INITIAL,
        SurveyPassStatus.QUEUED,
        "How do they gate?",
    )


def test_a_rejected_source_is_refused_and_never_queued(ctx):
    session, world, _ = ctx
    source = enqueue_source(session, _source_in(world)).source
    ApprovalService(session).resolve(source.approval_id, approved=False)
    promote_resolved_sources(session)
    session.refresh(source)
    assert source.status is SurveySourceStatus.REFUSED
    assert session.exec(select(SurveyPass).where(SurveyPass.source_id == source.id)).all() == []


def test_an_imported_candidate_is_proposed_by_enqueueing_it(ctx):
    session, world, _ = ctx
    imported = SurveySource(
        workspace_id=world.workspace_id,
        kind=SurveySourceKind.REPO,
        url="https://github.com/acme/widget",
    )
    session.add(imported)
    session.commit()
    proposed = enqueue_source(session, _source_in(world)).source
    assert proposed.id == imported.id and proposed.approval_id is not None


def test_a_second_workspace_joins_the_source_rather_than_surveying_it_again(ctx):
    """One reading serves every backlog: a known URL gains workspaces, not passes."""
    session, world, _ = ctx
    second_workspace(session, world)
    source = enqueue_source(session, _source_in(world)).source
    joined = enqueue_source(
        session, _source_in(world, workspace_slugs=["blobert"], question="Their asset pipeline?")
    )
    assert (joined.source.id, joined.attached, joined.scoped_pass) == (source.id, ["blobert"], None)
    assert len(session.exec(select(SurveySource)).all()) == 1
    targets = target_workspaces(session, source.id)
    assert [(w.slug, link.question) for w, link in targets] == [
        ("loregarden", "How do they gate?"),
        ("blobert", "Their asset pipeline?"),
    ]
    ApprovalService(session).resolve(source.approval_id, approved=True)
    promote_resolved_sources(session)
    # Still one pass: it reads for both workspaces.
    assert len(session.exec(select(SurveyPass).where(SurveyPass.source_id == source.id)).all()) == 1


def test_a_finished_source_gets_one_scoped_pass_for_a_new_workspace(ctx):
    session, world, tree = ctx
    second_workspace(session, world, "lore-eden")
    source, survey_pass = running_pass(session, world, tree)
    record_reads(
        session, survey_pass.id, [ReadIn(path="src/ui", state=SurveyReadState.READ, files_read=1)]
    )
    finish_pass(
        session,
        PassFinishIn(
            pass_id=survey_pass.id,
            outcome=SurveyPassOutcome.ANSWERED,
            outcome_note="n",
            stopped_at="s",
        ),
    )
    joined = enqueue_source(
        session,
        _source_in(world, workspace_slugs=["lore-eden"], question="Does the UI kit fit ours?"),
    )
    assert joined.scoped_pass is not None
    assert (joined.scoped_pass.mode, joined.scoped_pass.question) == (
        SurveyPassMode.SCOPED,
        "Does the UI kit fit ours?",
    )
    assert joined.source.status is SurveySourceStatus.ACTIVE
    assert joined.source.approval_id is None  # admitted once; no second card


def test_the_map_is_lstat_only_and_its_leaves_sum_to_the_tree(tmp_path):
    tree = write_tree(tmp_path / "tree", TREE)
    entries = map_tree(tree)
    assert tree_file_total(entries) == len(TREE)
    paths = {e.path for e in entries}
    assert {".", "docs", "docs/.", "docs/design", "src/core", "src/ui", "tests", "tests/."} <= paths


def test_coverage_is_a_query_with_a_denominator(ctx):
    session, world, tree = ctx
    source, survey_pass = running_pass(session, world, tree)
    record_reads(
        session,
        survey_pass.id,
        [
            ReadIn(path="src/core", state=SurveyReadState.READ, files_read=2),
            ReadIn(path="docs/design/b.md", state=SurveyReadState.READ, files_read=1, file_count=1),
            ReadIn(path="tests/.", state=SurveyReadState.SKIPPED, note="fixtures only"),
        ],
    )
    report = coverage_report(session, source.id)
    assert report["tree_files"] == len(TREE)
    assert report["files_in_read_leaves"] == 2
    assert report["files_in_partial_leaves"] == 2  # docs/design: one of its two files read
    assert report["files_in_skipped_leaves"] == 1
    assert report["files_in_unopened_leaves"] == len(TREE) - 5
    assert f"{len(TREE)} in all" in report["denominator_note"]
    assert [e["path"] for e in report["finer_entries"]] == ["docs/design/b.md"]


def test_reads_must_be_against_the_map(ctx):
    session, world, tree = ctx
    _, survey_pass = running_pass(session, world, tree)
    with pytest.raises(SurveyError, match="not under any path in the map"):
        record_reads(
            session,
            survey_pass.id,
            [ReadIn(path="../etc/passwd", state=SurveyReadState.READ, file_count=1)],
        )
    with pytest.raises(SurveyError, match="give its own file_count"):
        record_reads(
            session, survey_pass.id, [ReadIn(path="src/core/x.py", state=SurveyReadState.READ)]
        )
    with pytest.raises(SurveyError, match="cannot have been read"):
        record_reads(
            session,
            survey_pass.id,
            [ReadIn(path="src/core", state=SurveyReadState.READ, files_read=9)],
        )
    with pytest.raises(SurveyError, match="reason in note"):
        record_reads(
            session, survey_pass.id, [ReadIn(path="src/ui", state=SurveyReadState.SKIPPED)]
        )


def test_a_slice_takes_ranked_entries_first_and_respects_the_budget(ctx):
    session, world, tree = ctx
    source, survey_pass = running_pass(session, world, tree)
    record_reads(
        session, survey_pass.id, [ReadIn(path="src/core", state=SurveyReadState.READ, files_read=2)]
    )
    finish_pass(
        session,
        PassFinishIn(
            pass_id=survey_pass.id, outcome=SurveyPassOutcome.PARTIAL, outcome_note="half",
            stopped_at="after src/core", unread_priority=["src/ui", "docs/design"],
        ),
    )  # fmt: skip
    follow = session.exec(
        select(SurveyPass).where(
            SurveyPass.source_id == source.id, SurveyPass.status == SurveyPassStatus.QUEUED
        )
    ).one()
    assert follow.mode is SurveyPassMode.CONTINUATION
    assert assign_slice(session, follow, file_budget=3) == ["src/ui", "docs/design"]
    assert "src/core" not in assign_slice(session, follow, file_budget=100)


def test_finish_refuses_answered_while_a_question_it_opened_is_open(ctx):
    session, world, tree = ctx
    source, survey_pass = running_pass(session, world, tree)
    with pytest.raises(SurveyError, match="before finishing"):
        finish_pass(
            session,
            PassFinishIn(
                pass_id=survey_pass.id,
                outcome=SurveyPassOutcome.ANSWERED,
                outcome_note="n",
                stopped_at="s",
            ),
        )
    record_reads(
        session, survey_pass.id, [ReadIn(path="src/ui", state=SurveyReadState.READ, files_read=1)]
    )
    open_unknown(
        session,
        UnknownOpenIn(
            source_id=source.id, pass_id=survey_pass.id, question="q", authority_required="their CI"
        ),
    )
    with pytest.raises(SurveyError, match="still open"):
        finish_pass(
            session,
            PassFinishIn(
                pass_id=survey_pass.id,
                outcome=SurveyPassOutcome.ANSWERED,
                outcome_note="n",
                stopped_at="s",
            ),
        )
    done = finish_pass(
        session,
        PassFinishIn(
            pass_id=survey_pass.id,
            outcome=SurveyPassOutcome.PARTIAL,
            outcome_note="n",
            stopped_at="s",
        ),
    )
    assert done.status is SurveyPassStatus.FINISHED
    session.refresh(source)
    assert source.status is SurveySourceStatus.DONE


def test_continuations_stop_at_the_per_question_cap(ctx):
    session, world, tree = ctx
    source, survey_pass = running_pass(session, world, tree)
    current = survey_pass
    for _ in range(MAX_PASSES_PER_QUESTION):
        record_reads(
            session,
            current.id,
            [ReadIn(path="src/ui", state=SurveyReadState.PARTIAL, files_read=0)],
        )
        finish_pass(
            session,
            PassFinishIn(
                pass_id=current.id,
                outcome=SurveyPassOutcome.PARTIAL,
                outcome_note="n",
                stopped_at="s",
                unread_priority=["src/ui"],
            ),
        )
        current = session.exec(
            select(SurveyPass).where(
                SurveyPass.source_id == source.id, SurveyPass.status == SurveyPassStatus.QUEUED
            )
        ).first()
        if current is None:
            break
        current.status = SurveyPassStatus.DISPATCHED
        session.add(current)
        session.commit()
    assert current is None
    assert (
        len(session.exec(select(SurveyPass).where(SurveyPass.source_id == source.id)).all())
        == MAX_PASSES_PER_QUESTION
    )
    session.refresh(source)
    assert source.status is SurveySourceStatus.DONE


def test_a_scoped_pass_reopens_a_finished_source_against_a_new_question(ctx):
    session, world, tree = ctx
    source, survey_pass = running_pass(session, world, tree)
    record_reads(
        session, survey_pass.id, [ReadIn(path="src/ui", state=SurveyReadState.READ, files_read=1)]
    )
    finish_pass(
        session,
        PassFinishIn(
            pass_id=survey_pass.id,
            outcome=SurveyPassOutcome.ANSWERED,
            outcome_note="n",
            stopped_at="s",
        ),
    )
    scoped = request_pass(
        session,
        PassRequestIn(
            source_id=source.id, mode=SurveyPassMode.SCOPED, question="How do they test the canvas?"
        ),
    )
    session.refresh(source)
    assert source.status is SurveySourceStatus.ACTIVE
    assert (scoped.ordinal, scoped.mode, scoped.question) == (
        2,
        SurveyPassMode.SCOPED,
        "How do they test the canvas?",
    )
    with pytest.raises(SurveyError, match="not requested"):
        request_pass(
            session, PassRequestIn(source_id=source.id, mode=SurveyPassMode.INITIAL, question="q")
        )


def _queued_source(session, world, *, licence_basis=None, licence_kind=None, licence_use=None):
    source = SurveySource(
        workspace_id=world.workspace_id, kind=SurveySourceKind.REPO, url="https://github.com/acme/widget",
        name="widget", question="How do they gate?", queued_reason="r", status=SurveySourceStatus.ACTIVE,
        licence_basis=licence_basis, licence_kind=licence_kind, licence_use=licence_use, licence_name="GPL-3.0",
    )  # fmt: skip
    session.add(source)
    session.commit()
    serve(session, source, world.workspace_id)
    survey_pass = SurveyPass(
        source_id=source.id, ordinal=1, mode=SurveyPassMode.INITIAL, question=source.question
    )
    session.add(survey_pass)
    session.commit()
    return source, survey_pass


def test_the_feeder_dispatches_the_head_as_a_carrier_on_the_survey_workflow(ctx):
    session, world, tree = ctx
    (tree / "LICENSE").write_text(MIT_TEXT)
    source, survey_pass = _queued_source(session, world)
    lanes = mock.MagicMock()
    with (
        mock.patch.object(feeder, "clone_or_refresh", return_value=(tree, "main", "c" * 40)),
        mock.patch.object(feeder, "QueueLaneService", return_value=lanes),
        mock.patch.object(feeder, "least_busy_lane", return_value=1),
    ):
        result = feeder.feed_once(session)
    assert result.dispatched == survey_pass.id
    session.expire_all()
    survey_pass = session.get(SurveyPass, survey_pass.id)
    assert survey_pass.status is SurveyPassStatus.DISPATCHED
    assert survey_pass.read_commit == "c" * 40
    assert json.loads(survey_pass.slice_json)
    carrier = session.get(Ticket, survey_pass.carrier_ticket_id)
    assert carrier.parent_ticket_id == world.carrier_parent_id
    assert carrier.work_item_type is WorkItemType.CAPABILITY
    assert survey_pass.id in carrier.description
    instance = session.exec(
        select(WorkflowInstance).where(WorkflowInstance.ticket_id == carrier.id)
    ).one()
    assert session.get(WorkflowTemplate, instance.template_id).slug == feeder.SURVEY_WORKFLOW_SLUG
    lanes.add_to_lane.assert_called_once_with(
        ticket_id=carrier.id, slot_number=1, auto_approve=True
    )
    source = session.get(SurveySource, source.id)
    assert (source.licence_kind, source.licence_use, source.mapped_commit) == (
        LicenceKind.OPEN,
        LicenceUse.FULL,
        "c" * 40,
    )
    # One running is the limit: the next tick waits.
    assert "at the limit" in feeder.feed_once(session).waiting


def test_a_recorded_pass_holds_the_limit_until_its_carrier_stops_driving(ctx):
    """The agent records a pass well before its orchestration frees the lane;
    counting only DISPATCHED let a second pass start beside it."""
    session, world, tree = ctx
    _source, survey_pass = running_pass(session, world, tree)
    carrier = Ticket(
        external_id="lg-carrier-1",
        workspace_id=world.workspace_id,
        title="carrier",
        parent_ticket_id=world.carrier_parent_id,
    )
    session.add(carrier)
    session.commit()
    orchestration = OrchestrationRun(
        run_code="orch_t", ticket_id=carrier.id, workspace_id=world.workspace_id,
        status=OrchestrationRunStatus.RUNNING,
    )  # fmt: skip
    survey_pass.status = SurveyPassStatus.FINISHED
    survey_pass.carrier_ticket_id = carrier.id
    session.add_all([orchestration, survey_pass])
    session.commit()

    assert feeder.running_passes(session) == 1

    orchestration.status = OrchestrationRunStatus.SUCCEEDED
    session.add(orchestration)
    session.commit()
    assert feeder.running_passes(session) == 0


def test_a_hand_recorded_licence_the_file_contradicts_is_reported(ctx):
    session, world, tree = ctx
    (tree / "LICENSE").write_text(MIT_TEXT)
    _queued_source(
        session, world, licence_basis=LicenceBasis.RECORDED_BY_HAND, licence_kind=LicenceKind.LIMITED,
        licence_use=LicenceUse.PATTERNS_ONLY,
    )  # fmt: skip
    with (
        mock.patch.object(feeder, "clone_or_refresh", return_value=(tree, "main", "c" * 40)),
        mock.patch.object(feeder, "QueueLaneService"),
        mock.patch.object(feeder, "least_busy_lane", return_value=1),
    ):
        feeder.feed_once(session)
    assert session.exec(
        select(Approval).where(Approval.title == "Licence disagreement: widget")
    ).one()


def test_no_carrier_parent_is_an_exception_and_nothing_dispatches(ctx):
    session, world, tree = ctx
    carrier_parent = session.get(Ticket, world.carrier_parent_id)
    carrier_parent.tags_json = "[]"
    session.add(carrier_parent)
    session.commit()
    _, survey_pass = _queued_source(session, world)
    with mock.patch.object(feeder, "clone_or_refresh") as clone:
        result = feeder.feed_once(session)
    clone.assert_not_called()
    assert result.waiting == "no carrier parent"
    assert session.get(SurveyPass, survey_pass.id).status is SurveyPassStatus.QUEUED
    assert session.exec(
        select(Approval).where(Approval.title == "Reference survey has no carrier parent")
    ).one()


def test_a_carrier_that_closes_mid_pass_abandons_it_visibly(ctx):
    session, world, tree = ctx
    _, survey_pass = running_pass(session, world, tree)
    carrier = Ticket(
        external_id="lg-carrier-9",
        workspace_id=world.workspace_id,
        title="c",
        state=TicketState.DONE,
    )
    session.add(carrier)
    session.commit()
    survey_pass.carrier_ticket_id = carrier.id
    session.add(survey_pass)
    session.commit()
    assert feeder.feed_once(session).abandoned == 1
    session.refresh(survey_pass)
    assert survey_pass.status is SurveyPassStatus.ABANDONED
    assert "closed" in survey_pass.stopped_at
    assert session.exec(
        select(Approval).where(Approval.title.contains("Survey pass abandoned"))
    ).one()
