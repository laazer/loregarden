"""Researching a reference for another project, without loregarden.

- A review can read for some of the workspaces a source serves (`only_for`),
  for that pass alone; the source keeps serving the rest.
- Carriers file under loregarden's carrier parent when the source's home has
  none. Before this, a source homed elsewhere could never dispatch ("no carrier
  parent"), because only loregarden has one; four blobert sources were waiting
  on admission to meet that.
"""

from __future__ import annotations

import json
from unittest import mock

import pytest
from loregarden.models.domain import Ticket, WorkItemType
from loregarden.models.domain.survey_enums import (
    SurveyPassMode,
    SurveySourceKind,
    SurveySourceStatus,
)
from loregarden.models.domain.survey_tables import SurveyPass, SurveySource
from loregarden.services.reference_survey import feeder
from loregarden.services.reference_survey.errors import SurveyError
from loregarden.services.reference_survey.queue import target_workspaces
from loregarden.services.reference_survey.schedule import ReviewNowIn, review_now
from loregarden.services.ticket_tags import serialize_tags
from sqlmodel import Session, select
from tests.factories import make_ticket, make_workspace
from tests.survey_support import MIT_TEXT, build_world, serve, write_tree


@pytest.fixture(name="ctx")
def ctx_fixture(isolated_db, tmp_path):
    session = Session(isolated_db)
    world = build_world(session)
    blobert = make_workspace(session, slug="blobert")
    tree = write_tree(tmp_path / "clone", {"LICENSE": MIT_TEXT, "src/a.py": "a"})
    yield session, world, blobert, tree
    session.close()


def _source(session: Session, home_id: str, name: str = "widget") -> SurveySource:
    source = SurveySource(
        workspace_id=home_id, kind=SurveySourceKind.REPO, url=f"https://github.com/acme/{name}",
        name=name, question="How do they gate?", queued_reason="r", status=SurveySourceStatus.ACTIVE,
    )  # fmt: skip
    session.add(source)
    session.commit()
    serve(session, source, home_id)
    session.add(
        SurveyPass(source_id=source.id, ordinal=1, mode=SurveyPassMode.INITIAL, question="q")
    )
    session.commit()
    session.refresh(source)
    return source


def _review(session: Session, tree, source: SurveySource, body: ReviewNowIn):
    with (
        mock.patch.object(feeder, "clone_or_refresh", return_value=(tree, "main", "c" * 40)),
        mock.patch.object(feeder, "QueueLaneService"),
        mock.patch.object(feeder, "least_busy_lane", return_value=1),
    ):
        return review_now(session, source.id, body)


def _carrier(session: Session, source: SurveySource) -> Ticket:
    survey_pass = session.exec(select(SurveyPass).where(SurveyPass.source_id == source.id)).one()
    return session.get(Ticket, survey_pass.carrier_ticket_id)


# ---- where carriers file ----------------------------------------------------


def test_a_source_homed_elsewhere_runs_under_loregardens_carrier_parent(ctx):
    session, world, blobert, tree = ctx
    source = _source(session, blobert.id)

    result = _review(session, tree, source, ReviewNowIn())

    assert result.started, result.waiting
    carrier = _carrier(session, source)
    assert carrier.parent_ticket_id == world.carrier_parent_id
    assert carrier.workspace_id == world.workspace_id


def test_a_home_with_its_own_carrier_parent_keeps_it(ctx):
    """The control: the fallback is for a home with none, not a redirect."""
    session, _world, blobert, _tree = ctx
    own = make_ticket(
        session,
        workspace_id=blobert.id,
        title="blobert surveys",
        work_item_type=WorkItemType.FEATURE,
    )
    own.tags_json = serialize_tags([feeder.CARRIER_PARENT_TAG])
    session.add(own)
    session.commit()

    assert feeder.carrier_parent(session, blobert.id).id == own.id
    assert feeder.carrier_parent(session, _world.workspace_id).id == _world.carrier_parent_id


# ---- one review for some of what it serves ------------------------------------


def test_a_review_reads_for_another_project_alone_and_keeps_serving_loregarden(ctx):
    session, world, _blobert, tree = ctx
    source = _source(session, world.workspace_id)

    result = _review(
        session,
        tree,
        source,
        ReviewNowIn(workspace_slugs=["blobert"], only_for=["blobert"]),
    )

    assert result.started, result.waiting
    assert result.workspaces_added == ["blobert"]
    survey_pass = session.exec(select(SurveyPass).where(SurveyPass.source_id == source.id)).one()
    assert json.loads(survey_pass.workspaces_json) == ["blobert"]
    assert "**For workspaces:** blobert —" in (_carrier(session, source).description or "")
    served = [w.slug for w, _ in target_workspaces(session, source.id)]
    assert served == [world.workspace_slug, "blobert"]


def test_a_review_naming_every_workspace_reads_for_all(ctx):
    session, world, _blobert, tree = ctx
    source = _source(session, world.workspace_id)

    _review(
        session,
        tree,
        source,
        ReviewNowIn(workspace_slugs=["blobert"], only_for=["blobert", world.workspace_slug]),
    )

    survey_pass = session.exec(select(SurveyPass).where(SurveyPass.source_id == source.id)).one()
    assert survey_pass.workspaces_json == "[]"
    assert f"**For workspaces:** {world.workspace_slug}, blobert" in (
        _carrier(session, source).description or ""
    )


def test_a_review_for_a_workspace_it_does_not_serve_is_refused_before_any_write(ctx):
    session, world, _blobert, tree = ctx
    source = _source(session, world.workspace_id)

    with pytest.raises(SurveyError, match="does not serve blobert"):
        _review(session, tree, source, ReviewNowIn(only_for=["blobert"]))

    served = [w.slug for w, _ in target_workspaces(session, source.id)]
    assert served == [world.workspace_slug]
    survey_pass = session.exec(select(SurveyPass).where(SurveyPass.source_id == source.id)).one()
    assert survey_pass.carrier_ticket_id is None
