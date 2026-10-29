"""Shared setup for the reference-survey tests: a seeded workspace with a ticket
tree to land findings on, a carrier parent, and a running pass over a real tree."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from loregarden.models.domain import WorkItemType, Workspace
from loregarden.models.domain.survey_enums import (
    LicenceBasis,
    LicenceKind,
    LicenceUse,
    SurveyPassMode,
    SurveyPassStatus,
    SurveySourceKind,
    SurveySourceStatus,
)
from loregarden.models.domain.survey_tables import SurveyPass, SurveySource, SurveySourceWorkspace
from loregarden.services.reference_survey.coverage import map_tree
from loregarden.services.reference_survey.passes import store_map
from loregarden.services.seed import seed_database
from loregarden.services.ticket_tags import serialize_tags
from sqlmodel import Session, select
from tests.factories import make_ticket

MIT_TEXT = """MIT License

Copyright (c) 2026 Someone

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND.
"""


@dataclass
class SurveyWorld:
    workspace_id: str
    workspace_slug: str
    milestone_id: str
    feature_id: str
    feature_ext: str
    carrier_parent_id: str


def build_world(session: Session) -> SurveyWorld:
    seed_database(session)
    ws = session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()
    milestone = make_ticket(
        session, workspace_id=ws.id, external_id="lg-survey-m-1", title="Survey milestone",
        work_item_type=WorkItemType.MILESTONE,
    )  # fmt: skip
    feature = make_ticket(
        session, workspace_id=ws.id, external_id="lg-survey-f-2", title="Typed reference sources",
        work_item_type=WorkItemType.FEATURE, parent_ticket_id=milestone.id,
    )  # fmt: skip
    carrier = make_ticket(
        session, workspace_id=ws.id, external_id="lg-survey-f-3", title="Survey passes",
        work_item_type=WorkItemType.FEATURE, parent_ticket_id=milestone.id,
    )  # fmt: skip
    carrier.tags_json = serialize_tags(["reference-survey-carrier"])
    session.add(carrier)
    session.commit()
    return SurveyWorld(ws.id, ws.slug, milestone.id, feature.id, feature.external_id, carrier.id)


def write_tree(root: Path, files: dict[str, str]) -> Path:
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    return root


def serve(session: Session, source: SurveySource, workspace_id: str, question: str = "q") -> None:
    """Make `source` answer `workspace_id`'s backlog."""
    session.add(
        SurveySourceWorkspace(source_id=source.id, workspace_id=workspace_id, question=question)
    )
    session.commit()


def second_workspace(session: Session, world: SurveyWorld, slug: str = "blobert") -> Workspace:
    """Another workspace with the same workflow, so tickets can be created in it."""
    home = session.get(Workspace, world.workspace_id)
    other = Workspace(
        slug=slug,
        name=slug,
        repo_path=home.repo_path,
        workflow_template_id=home.workflow_template_id,
    )
    session.add(other)
    session.commit()
    session.refresh(other)
    return other


def running_pass(
    session: Session, world: SurveyWorld, tree: Path, *, url: str = "https://github.com/acme/widget"
) -> tuple[SurveySource, SurveyPass]:
    """An admitted MIT source with its map stored and one dispatched pass."""
    source = SurveySource(
        workspace_id=world.workspace_id, kind=SurveySourceKind.REPO, url=url, name="widget",
        question="How do they gate?", queued_reason="test", status=SurveySourceStatus.ACTIVE,
        licence_kind=LicenceKind.OPEN, licence_basis=LicenceBasis.FILE_AT_COMMIT, licence_use=LicenceUse.FULL,
        licence_name="MIT", licence_constraint="MIT: permissive.", pinned_commit="abc1234def",
    )  # fmt: skip
    session.add(source)
    session.commit()
    serve(session, source, world.workspace_id)
    store_map(session, source, map_tree(tree), "abc1234def")
    survey_pass = SurveyPass(
        source_id=source.id, ordinal=1, mode=SurveyPassMode.INITIAL, question=source.question,
        status=SurveyPassStatus.DISPATCHED,
    )  # fmt: skip
    session.add(survey_pass)
    session.commit()
    session.refresh(source)
    session.refresh(survey_pass)
    return source, survey_pass
