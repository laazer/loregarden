"""Composed views and the sidebar that ranks them.

The layout arrives typed: `ViewLayout` is validated by the request model, so a
malformed one is refused at the boundary and never reaches a write. That is the
whole guarantee this resource offers — a half-written layout is a view that
cannot be opened, and therefore cannot be repaired from the UI that fails to
open it.
"""

from fastapi import APIRouter, Depends, HTTPException
from loregarden.db.session import get_session
from loregarden.models.domain import SidebarEntry, View, Workspace
from loregarden.models.domain.view_layout import ViewLayout
from loregarden.models.domain.view_viewport import ViewViewport
from loregarden.services.view_service import (
    MAX_PAGE_KEY_LENGTH,
    SidebarContentionError,
    create_view,
    delete_entry,
    delete_view,
    entry_payload,
    get_entry,
    get_view,
    list_entries,
    list_views,
    pin_page,
    reorder_entries,
    set_entry_pinned,
    update_view,
    view_payload,
)
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, select

router = APIRouter(prefix="/workspaces", tags=["views"])


class ViewCreate(BaseModel):
    """The view's kind is not sent: it is the layout's kind.

    Two fields carrying one fact can disagree, and the reconciliation had to
    answer a question with no right answer — which of the two the caller meant.
    Extra keys are refused rather than dropped so a client still sending ``kind``
    is told, instead of having it silently ignored.
    """

    model_config = ConfigDict(extra="forbid")

    title: str = ""
    icon: str = ""
    layout: ViewLayout


class ViewUpdate(BaseModel):
    """PATCH semantics: an omitted field is untouched, not reset.

    Extra keys are refused here for the same reason ``ViewCreate`` refuses them,
    and refusing them on only one of the two would be worse than refusing them
    on neither: a client sending ``kind`` would be told on POST and silently
    ignored on PATCH, which reads as the field being accepted.
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    icon: str | None = None
    layout: ViewLayout | None = None
    #: Where the view is being looked at. Independently settable: a viewport-only
    #: PATCH leaves the layout alone, which is what lets a pan be written at
    #: gesture rate without racing a deliberate layout edit through one column.
    viewport: ViewViewport | None = None


class SidebarPin(BaseModel):
    #: A built-in page from the client's `AppPage` union, whose vocabulary the
    #: frontend owns. Bounded because the value is stored and returned on every
    #: sidebar read, and the vocabulary it comes from is short words.
    #: # py-org: allow-string
    page_key: str = Field(min_length=1, max_length=MAX_PAGE_KEY_LENGTH)


class SidebarReorder(BaseModel):
    entry_ids: list[str]


class SidebarEntryUpdate(BaseModel):
    """Which section draws this tab. Extra keys are refused, as on the view routes."""

    model_config = ConfigDict(extra="forbid")

    pinned: bool


def _workspace(session: Session, slug: str) -> Workspace:
    workspace = session.exec(select(Workspace).where(Workspace.slug == slug)).first()
    if not workspace:
        raise HTTPException(404, "Workspace not found")
    return workspace


def _view(session: Session, workspace_id: str, view_id: str) -> View:
    view = get_view(session, workspace_id, view_id)
    if not view:
        raise HTTPException(404, "View not found")
    return view


def _entry(session: Session, workspace_id: str, entry_id: str) -> SidebarEntry:
    entry = get_entry(session, workspace_id, entry_id)
    if not entry:
        raise HTTPException(404, "Sidebar entry not found")
    return entry


@router.get("/{slug}/views")
def get_views(slug: str, session: Session = Depends(get_session)) -> list[dict]:
    workspace = _workspace(session, slug)
    return [view_payload(view) for view in list_views(session, workspace.id)]


@router.post("/{slug}/views", status_code=201)
def post_view(slug: str, body: ViewCreate, session: Session = Depends(get_session)) -> dict:
    workspace = _workspace(session, slug)
    try:
        view = create_view(
            session,
            workspace.id,
            title=body.title,
            icon=body.icon,
            layout=body.layout,
        )
    except SidebarContentionError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        # Structure was checked by the request model; what is left is the
        # store's own limits — a layout too large, or one `json` cannot write.
        raise HTTPException(400, str(exc)) from exc
    return view_payload(view)


@router.get("/{slug}/views/{view_id}")
def get_one_view(slug: str, view_id: str, session: Session = Depends(get_session)) -> dict:
    workspace = _workspace(session, slug)
    return view_payload(_view(session, workspace.id, view_id))


@router.patch("/{slug}/views/{view_id}")
def patch_view(
    slug: str, view_id: str, body: ViewUpdate, session: Session = Depends(get_session)
) -> dict:
    workspace = _workspace(session, slug)
    view = _view(session, workspace.id, view_id)
    try:
        updated = update_view(
            session,
            view,
            title=body.title,
            icon=body.icon,
            layout=body.layout,
            viewport=body.viewport,
        )
    except ValueError as exc:
        # The same limits POST answers with a 400. A body refused on create and
        # accepted — or 500ing — on update teaches the client the wrong lesson.
        raise HTTPException(400, str(exc)) from exc
    if updated is None:
        # A peer deleted the view between the read above and the write. Nothing
        # to retry: it is the same 404 the next request gets on its own.
        raise HTTPException(404, "View not found")
    return view_payload(updated)


@router.delete("/{slug}/views/{view_id}")
def remove_view(slug: str, view_id: str, session: Session = Depends(get_session)) -> dict:
    workspace = _workspace(session, slug)
    try:
        delete_view(session, _view(session, workspace.id, view_id))
    except SidebarContentionError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"deleted": view_id}


@router.get("/{slug}/sidebar-entries")
def get_sidebar_entries(slug: str, session: Session = Depends(get_session)) -> list[dict]:
    workspace = _workspace(session, slug)
    return [entry_payload(entry) for entry in list_entries(session, workspace.id)]


@router.post("/{slug}/sidebar-entries", status_code=201)
def post_sidebar_entry(
    slug: str, body: SidebarPin, session: Session = Depends(get_session)
) -> dict:
    """Pin a built-in page. Pinning one already pinned returns its entry.

    Nothing in the app calls this any more: the sidebar's Tools section is
    derived from the client's page catalog rather than stored, so a `page` entry
    is drawn nowhere. The route stays because databases seeded before that
    change still hold those rows and still have to read and rank them, and
    because pinning a deep link is the use it was always shaped for.
    """
    workspace = _workspace(session, slug)
    try:
        entry = pin_page(session, workspace.id, body.page_key)
    except SidebarContentionError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return entry_payload(entry)


@router.patch("/{slug}/sidebar-entries")
def patch_sidebar_order(
    slug: str, body: SidebarReorder, session: Session = Depends(get_session)
) -> list[dict]:
    workspace = _workspace(session, slug)
    try:
        entries = reorder_entries(session, workspace.id, body.entry_ids)
    except SidebarContentionError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return [entry_payload(entry) for entry in entries]


@router.patch("/{slug}/sidebar-entries/{entry_id}")
def patch_sidebar_entry(
    slug: str,
    entry_id: str,
    body: SidebarEntryUpdate,
    session: Session = Depends(get_session),
) -> dict:
    """Move a view's tab between the Pinned section and Tabs.

    Separate from the collection PATCH, which ranks the whole sidebar: this
    one changes a single entry and leaves the ranking alone.
    """
    workspace = _workspace(session, slug)
    entry = _entry(session, workspace.id, entry_id)
    try:
        updated = set_entry_pinned(session, entry, body.pinned)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return entry_payload(updated)


@router.delete("/{slug}/sidebar-entries/{entry_id}")
def remove_sidebar_entry(slug: str, entry_id: str, session: Session = Depends(get_session)) -> dict:
    """Unpin a built-in page. A view's entry is not unpinnable — delete the view.

    Removing it would unrank the view without deleting it, and nothing can put it
    back: pinning takes a page key, so there is no request that recreates a view
    entry. The view would be stored, unlisted, and unreachable.
    """
    workspace = _workspace(session, slug)
    try:
        delete_entry(session, _entry(session, workspace.id, entry_id))
    except SidebarContentionError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"deleted": entry_id}
