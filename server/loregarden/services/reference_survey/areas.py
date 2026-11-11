"""Project areas: the parts of a workspace a reference can be read for.

An area is named by its label, `<workspace>/<slug>` (`loremaker/stratos`), or by
its id; both resolve here, and an unknown or archived one is refused with the
name it was given before anything is written. Linking a source to an area also
makes it serve the area's workspace (`bulk.enqueue_many` folds them in), so an
area can never be read for by a source that does not serve its workspace.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass

from loregarden.models.domain import Workspace
from loregarden.models.domain.enums import utcnow
from loregarden.models.domain.survey_area_tables import SurveyArea, SurveySourceArea
from loregarden.services.reference_survey.area_homes import home_external_ids
from loregarden.services.reference_survey.errors import SurveyError, SurveyNotFound
from loregarden.services.reference_survey.schemas import EntryAreaIn
from loregarden.services.ticket_ids import AmbiguousTicketReference, TicketNotFound, resolve_one
from pydantic import BaseModel, Field, field_validator
from sqlmodel import Session, col, select

_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")


class AreaView(BaseModel):
    id: str
    workspace_slug: str
    slug: str
    #: `<workspace>/<slug>`: how the area is named everywhere.
    label: str
    name: str
    description: str
    keywords: list[str]
    #: The home ticket's external id, or None for the workspace's findings home.
    home_ticket: str | None
    archived: bool


class AreaIn(BaseModel):
    workspace_slug: str = Field(min_length=1)
    slug: str
    name: str = Field(min_length=1)
    description: str = ""
    keywords: list[str] = Field(default_factory=list)
    #: External id of the ticket parentless findings for this area go under; "" for none.
    home_ticket: str = ""

    @field_validator("slug")
    @classmethod
    def _slug_shape(cls, value: str) -> str:
        if not _SLUG.match(value):
            raise ValueError(
                "a slug is lowercase letters, digits and dashes, at most 48 characters"
            )
        return value


class AreaUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    description: str | None = None
    keywords: list[str] | None = None
    #: External id; "" clears it.
    home_ticket: str | None = None
    archived: bool | None = None


def _keywords(raw: list[str]) -> str:
    return json.dumps(list(dict.fromkeys(k.strip() for k in raw if k.strip())))


def area_label(workspace_slug: str, slug: str) -> str:
    return f"{workspace_slug}/{slug}"


def _view(area: SurveyArea, workspace_slug: str, home: str | None) -> AreaView:
    return AreaView(
        id=area.id, workspace_slug=workspace_slug, slug=area.slug,
        label=area_label(workspace_slug, area.slug), name=area.name,
        description=area.description, keywords=json.loads(area.keywords_json),
        home_ticket=home, archived=area.archived_at is not None,
    )  # fmt: skip


def _homes(session: Session, areas: list[SurveyArea]) -> dict[str, str]:
    return home_external_ids(session, [a.home_ticket_id for a in areas if a.home_ticket_id])


def list_areas(session: Session, *, include_archived: bool = False) -> list[AreaView]:
    """Every area, by workspace then slug."""
    query = select(SurveyArea, Workspace.slug).join(
        Workspace, col(Workspace.id) == SurveyArea.workspace_id
    )
    if not include_archived:
        query = query.where(col(SurveyArea.archived_at).is_(None))
    rows = session.exec(query.where(col(Workspace.archived_at).is_(None))).all()
    homes = _homes(session, [a for a, _ in rows])
    views = [_view(a, ws, homes.get(a.home_ticket_id or "")) for a, ws in rows]
    return sorted(views, key=lambda v: (v.workspace_slug, v.slug))


def _workspace(session: Session, slug: str) -> Workspace:
    workspace = session.exec(select(Workspace).where(Workspace.slug == slug)).first()
    if workspace is None:
        raise SurveyNotFound(f"No workspace {slug!r}")
    return workspace


def _home_id(session: Session, workspace: Workspace, external_id: str) -> str | None:
    if not external_id.strip():
        return None
    try:
        return resolve_one(session, external_id.strip(), workspace_id=workspace.id).id
    except TicketNotFound as exc:
        raise SurveyNotFound(f"No ticket {external_id!r} in {workspace.slug}") from exc
    except AmbiguousTicketReference as exc:
        raise SurveyError(str(exc)) from exc


def create_area(session: Session, body: AreaIn) -> AreaView:
    workspace = _workspace(session, body.workspace_slug)
    clash = session.exec(
        select(SurveyArea).where(
            SurveyArea.workspace_id == workspace.id, SurveyArea.slug == body.slug
        )
    ).first()
    if clash is not None:
        raise SurveyError(f"{area_label(workspace.slug, body.slug)} already exists")
    area = SurveyArea(
        workspace_id=workspace.id, slug=body.slug, name=body.name.strip(),
        description=body.description.strip(), keywords_json=_keywords(body.keywords),
        home_ticket_id=_home_id(session, workspace, body.home_ticket),
    )  # fmt: skip
    session.add(area)
    session.commit()
    session.refresh(area)
    return _view(area, workspace.slug, body.home_ticket.strip() or None)


def update_area(session: Session, area_id: str, body: AreaUpdate) -> AreaView:
    area = session.get(SurveyArea, area_id)
    if area is None:
        raise SurveyNotFound(f"No area {area_id!r}")
    workspace = session.get(Workspace, area.workspace_id)
    if workspace is None:
        raise SurveyNotFound(f"Area {area_id!r} names a workspace that no longer exists")
    if body.name is not None:
        area.name = body.name.strip()
    if body.description is not None:
        area.description = body.description.strip()
    if body.keywords is not None:
        area.keywords_json = _keywords(body.keywords)
    if body.home_ticket is not None:
        area.home_ticket_id = _home_id(session, workspace, body.home_ticket)
    if body.archived is not None:
        area.archived_at = utcnow() if body.archived else None
    area.updated_at = utcnow()
    session.add(area)
    session.commit()
    session.refresh(area)
    return _view(area, workspace.slug, _homes(session, [area]).get(area.home_ticket_id or ""))


def resolve_areas(session: Session, refs: list[str]) -> list[tuple[SurveyArea, Workspace]]:
    """Live areas by label or id, in the order given; any unknown one refuses them all."""
    found: list[tuple[SurveyArea, Workspace]] = []
    for ref in dict.fromkeys(r.strip() for r in refs if r.strip()):
        if "/" in ref:
            workspace_slug, slug = ref.split("/", 1)
            row = session.exec(
                select(SurveyArea, Workspace)
                .join(Workspace, col(Workspace.id) == SurveyArea.workspace_id)
                .where(Workspace.slug == workspace_slug, SurveyArea.slug == slug)
            ).first()
        else:
            row = session.exec(
                select(SurveyArea, Workspace)
                .join(Workspace, col(Workspace.id) == SurveyArea.workspace_id)
                .where(SurveyArea.id == ref)
            ).first()
        if row is None:
            raise SurveyNotFound(f"No project area {ref!r}")
        area, workspace = row
        if area.archived_at is not None:
            raise SurveyError(f"Project area {ref!r} is archived")
        found.append((area, workspace))
    return found


def link_areas(session: Session, source_id: str, areas: list[tuple[SurveyArea, str]]) -> list[str]:
    """Tie a source to areas (each with its reason); returns the ids newly linked.

    An existing link keeps its first reason unless it had none. Commits.
    """
    have = {
        link.area_id: link
        for link in session.exec(
            select(SurveySourceArea).where(SurveySourceArea.source_id == source_id)
        ).all()
    }
    added = []
    for area, reason in areas:
        existing = have.get(area.id)
        if existing is None:
            session.add(
                SurveySourceArea(source_id=source_id, area_id=area.id, reason=reason.strip())
            )
            added.append(area.id)
        elif not existing.reason and reason.strip():
            existing.reason = reason.strip()
            session.add(existing)
    session.commit()
    return added


class SourceAreaContext(BaseModel):
    """An area a source serves, as a pass reads it."""

    label: str
    name: str
    description: str
    reason: str
    home_ticket: str | None


def source_area_context(session: Session, source_id: str) -> list[SourceAreaContext]:
    rows = session.exec(
        select(SurveyArea, Workspace.slug, SurveySourceArea.reason)
        .join(SurveySourceArea, col(SurveySourceArea.area_id) == SurveyArea.id)
        .join(Workspace, col(Workspace.id) == SurveyArea.workspace_id)
        .where(SurveySourceArea.source_id == source_id, col(SurveyArea.archived_at).is_(None))
        .order_by(col(Workspace.slug), col(SurveyArea.slug))
    ).all()
    homes = _homes(session, [a for a, _, _ in rows])
    return [
        SourceAreaContext(
            label=area_label(ws, a.slug),
            name=a.name,
            description=a.description,
            reason=reason,
            home_ticket=homes.get(a.home_ticket_id or ""),
        )  # fmt: skip
        for a, ws, reason in rows
    ]


def area_labels_by_source(session: Session, source_ids: list[str]) -> dict[str, list[str]]:
    """Each source's live area labels, sorted; sources with none are absent."""
    if not source_ids:
        return {}
    rows = session.exec(
        select(SurveySourceArea.source_id, Workspace.slug, SurveyArea.slug)
        .join(SurveyArea, col(SurveyArea.id) == SurveySourceArea.area_id)
        .join(Workspace, col(Workspace.id) == SurveyArea.workspace_id)
        .where(
            col(SurveySourceArea.source_id).in_(source_ids), col(SurveyArea.archived_at).is_(None)
        )
    ).all()
    labels: dict[str, list[str]] = defaultdict(list)
    for source_id, ws, slug in rows:
        labels[source_id].append(area_label(ws, slug))
    return {k: sorted(v) for k, v in labels.items()}


def finding_area(
    session: Session, source_id: str, workspace: Workspace, named: str
) -> SurveyArea | None:
    """The area a parentless finding in `workspace` is filed for.

    The one the pass named (by slug or label), which must be in that workspace;
    else, when the source serves exactly one area there, that one; else none.
    """
    if named.strip():
        ref = named.strip() if "/" in named else area_label(workspace.slug, named.strip())
        (area, area_ws), = resolve_areas(session, [ref])  # fmt: skip
        if area_ws.id != workspace.id:
            raise SurveyError(
                f"Area {ref!r} is not in {workspace.slug}, where this ticket is filed"
            )
        return area
    linked = session.exec(
        select(SurveyArea)
        .join(SurveySourceArea, col(SurveySourceArea.area_id) == SurveyArea.id)
        .where(
            SurveySourceArea.source_id == source_id,
            SurveyArea.workspace_id == workspace.id,
            col(SurveyArea.archived_at).is_(None),
        )
    ).all()
    return linked[0] if len(linked) == 1 else None


@dataclass(frozen=True)
class ResolvedArea:
    """A named area, resolved, with its workspace and the reason given for it."""

    area: SurveyArea
    workspace: Workspace
    reason: str


def resolve_picks(session: Session, picks: list[EntryAreaIn]) -> list[ResolvedArea]:
    """Each named area once (first reason wins), resolved; any unknown one refuses them all."""
    reasons: dict[str, str] = {}
    for pick in picks:
        if pick.area.strip():
            reasons.setdefault(pick.area.strip(), pick.reason)
    resolved = resolve_areas(session, list(reasons))
    return [
        ResolvedArea(area=area, workspace=ws, reason=reasons[ref])
        for ref, (area, ws) in zip(reasons, resolved, strict=True)
    ]


def link_picks(session: Session, source_id: str, picks: list[ResolvedArea]) -> list[str]:
    return link_areas(session, source_id, [(p.area, p.reason) for p in picks])


def with_area_workspaces(workspace_slugs: list[str], picks: list[ResolvedArea]) -> list[str]:
    """The given workspaces, then each area's workspace not already among them."""
    return list(dict.fromkeys([*workspace_slugs, *(p.workspace.slug for p in picks)]))
