"""Composed views and the one ordered sidebar they share with pinned pages.

Ordering lives on ``SidebarEntry`` alone. A view has no rank of its own, so
listing views means reading the sidebar's ranking rather than inventing a second
one — that is what lets a pinned built-in page sit between two views, and what
keeps the two kinds from drifting apart.

Every removal renumbers the surviving entries to 0..n-1, so the ordering is
normally dense. It is not an invariant, and nothing here relies on it: a removal
that overlaps an append renumbers only the rows it read, and the append lands one
past the highest rank it saw, which can leave a durable gap. That is harmless
because ordering is read as *relative* order — ``list_entries`` sorts by position
and every other path either appends past the maximum or rewrites the whole
permutation. What the database does guarantee is uniqueness, not density:
``UNIQUE (workspace_id, position)`` is what stops two entries sharing a rank. See
``_renumber`` for what rewriting a permutation costs.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from loregarden.models.domain import SidebarEntry, View
from loregarden.models.domain.enums import SidebarEntryKind, utcnow
from loregarden.models.domain.view_layout import CanvasLayout, FlexGridLayout, layout_payload
from loregarden.models.domain.view_viewport import ViewViewport, viewport_payload
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError
from sqlmodel import Session, col, select

ViewLayoutModel = FlexGridLayout | CanvasLayout

#: Appending an entry reads the highest position and writes one past it, and
#: ``UNIQUE (workspace_id, position)`` is what makes that read-then-write safe:
#: the loser of a race is refused by the database rather than silently seated on
#: a rank another entry already holds. Losing means re-reading and trying again.
#:
#: Every round has a winner — one insert commits and the rest are refused — so N
#: appends racing for one rank need at most N rounds, and a request only
#: exhausts this budget if that many were in flight at once on one workspace's
#: sidebar. Ten is well past what a person clicking tabs can produce.
_APPEND_ATTEMPTS = 10

#: The bounds in ``view_layout`` cap a layout's *cardinality* — how many
#: containers and nodes it holds — and cardinality is not size: 256 containers is
#: within every one of them while a single ``settings`` mapping carries a
#: megabyte. The stored column is returned whole by ``GET /views``, so the byte
#: cap is what stops one write from making every later read of that workspace
#: expensive. Generous against any real layout: 256 panes leave a kilobyte of
#: settings each.  Same shape as ``composer_note_service.MAX_NOTE_BYTES``.
MAX_LAYOUT_BYTES = 256_000

#: A page key names a built-in page from the client's ``AppPage`` union — a
#: handful of characters. It is stored, unique per workspace, and returned on
#: every sidebar read, so an unbounded one is a megabyte served on every request
#: for a value whose real vocabulary is short words.
MAX_PAGE_KEY_LENGTH = 200


class SidebarContentionError(ValueError):
    """A concurrent write kept this one from settling — the caller may retry.

    Distinct from the plain ``ValueError``s here, which mean the request itself
    is wrong and retrying it unchanged will fail again. The API maps this to 409
    and those to 400.
    """


def _serialized_layout(layout: ViewLayoutModel) -> str:
    """The layout as the string that goes in the column, refused if oversized.

    Encoding is fallible independently of validation, though no longer because
    of ``settings``: ``ViewContainer`` now refuses a value ``json`` cannot write
    (444). What still reaches here is depth — the dump of a *whole* layout runs
    deeper than the settings-only dump the validator did, and pydantic's
    serializer gives up around 250 levels, which is how a nesting the validator
    accepted still fails to encode. Both that and the byte cap are the request's
    fault, so both are a ``ValueError`` the API answers with a 4xx rather than a
    traceback.
    """
    try:
        encoded = json.dumps(layout_payload(layout))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Layout is not JSON-encodable: {exc}") from exc
    if len(encoded.encode("utf-8")) > MAX_LAYOUT_BYTES:
        raise ValueError(f"Layout is too large (limit {MAX_LAYOUT_BYTES} bytes)")
    return encoded


def _validated_page_key(page_key: str) -> str:
    if len(page_key) > MAX_PAGE_KEY_LENGTH:
        raise ValueError(f"Page key is too long (limit {MAX_PAGE_KEY_LENGTH} characters)")
    return page_key


def view_payload(view: View) -> dict:
    return {
        "id": view.id,
        "kind": view.kind.value,
        "title": view.title,
        "icon": view.icon,
        "layout": json.loads(view.layout_json),
        # An empty object is the absent viewport: this view has no stored
        # position, and the canvas opens at its default rather than at the
        # origin. Sent as itself rather than omitted, so a client never has to
        # tell "no viewport" from "a field this build does not serve".
        "viewport": json.loads(view.viewport_json),
        "created_at": view.created_at.isoformat(),
        "updated_at": view.updated_at.isoformat(),
    }


def entry_payload(entry: SidebarEntry) -> dict:
    """The wire shape, where the half this entry does not use is an empty string.

    The columns are nullable so that ``UNIQUE (workspace_id, page_key)`` and
    ``UNIQUE (workspace_id, view_id)`` ignore the half an entry does not use
    instead of colliding every entry of one kind on ``''``. A ``view_id`` that
    really names a view is the database's guarantee rather than this service's:
    the column declares a foreign key and ``PRAGMA foreign_keys`` is on. The wire
    keeps the flat shape, so a reader still never branches on which kind of entry
    it is holding.
    """
    return {
        "id": entry.id,
        "position": entry.position,
        "entry_kind": entry.entry_kind.value,
        "page_key": entry.page_key or "",
        "view_id": entry.view_id or "",
        "pinned": entry.pinned,
    }


def list_entries(session: Session, workspace_id: str) -> list[SidebarEntry]:
    return list(
        session.exec(
            select(SidebarEntry)
            .where(SidebarEntry.workspace_id == workspace_id)
            .order_by(col(SidebarEntry.position))
        ).all()
    )


def list_views(session: Session, workspace_id: str) -> list[View]:
    """Views in sidebar order — the one ranking, read rather than duplicated."""
    views = {
        view.id: view
        for view in session.exec(select(View).where(View.workspace_id == workspace_id)).all()
    }
    ordered = []
    for entry in list_entries(session, workspace_id):
        view = views.get(entry.view_id)
        if view is not None:
            ordered.append(view)
    return ordered


def get_view(session: Session, workspace_id: str, view_id: str) -> View | None:
    view = session.get(View, view_id)
    if not view or view.workspace_id != workspace_id:
        return None
    return view


def get_entry(session: Session, workspace_id: str, entry_id: str) -> SidebarEntry | None:
    entry = session.get(SidebarEntry, entry_id)
    if not entry or entry.workspace_id != workspace_id:
        return None
    return entry


def _page_entry(session: Session, workspace_id: str, page_key: str) -> SidebarEntry | None:
    return session.exec(
        select(SidebarEntry)
        .where(SidebarEntry.workspace_id == workspace_id)
        .where(SidebarEntry.entry_kind == SidebarEntryKind.PAGE)
        .where(SidebarEntry.page_key == page_key)
    ).first()


def _next_position(session: Session, workspace_id: str) -> int:
    """One past the highest rank in use, not the entry count.

    They agree only while positions are dense, which makes the count a fact
    derived from an invariant rather than from the column being appended to —
    and one gap, from any bug or hand-edit, turns an append into a collision.
    """
    highest = session.exec(
        select(func.max(col(SidebarEntry.position))).where(
            SidebarEntry.workspace_id == workspace_id
        )
    ).one()
    return 0 if highest is None else int(highest) + 1


def _renumber(session: Session, entries: list[SidebarEntry]) -> None:
    """Rank ``entries`` 0..n-1 in list order, in two passes.

    ``UNIQUE (workspace_id, position)`` is checked per statement, so writing the
    final ranks straight out fails the moment two entries trade places — the
    first UPDATE lands on a rank its partner still holds. Park every row on a
    negative rank first, a range no real entry occupies, and the second pass has
    an empty 0..n-1 to write into.
    """
    for index, entry in enumerate(entries):
        entry.position = -(index + 1)
        session.add(entry)
    session.flush()
    for index, entry in enumerate(entries):
        entry.position = index
        session.add(entry)
    session.flush()


def _close_gaps(session: Session, workspace_id: str) -> None:
    _renumber(session, list_entries(session, workspace_id))


def _commit_with_retry(session: Session, attempt: Callable[[], None]) -> None:
    """Run ``attempt`` and commit it, re-running it if a peer got there first.

    Every renumbering path reads the whole sidebar and then writes every row it
    read. A peer that commits in between invalidates that read two ways: a row
    that is gone makes the UPDATE match fewer rows than SQLAlchemy staged
    (``StaleDataError``), and a rank a peer took makes it collide
    (``IntegrityError``). Neither is corruption and neither is the caller's
    fault, so the answer is to discard the stale read and do the work again
    rather than to return a 500.

    Bounded for the same reason the append loop is: every round has a winner, so
    a request only exhausts the budget if that many writes were in flight at once
    on one workspace's sidebar. ``attempt`` must therefore re-read whatever it
    writes — it is called fresh on every pass.
    """
    for _ in range(_APPEND_ATTEMPTS):
        try:
            attempt()
            session.commit()
        except (IntegrityError, StaleDataError):
            session.rollback()
            continue
        return
    raise SidebarContentionError("The sidebar is being reordered by another request; try again")


def create_view(
    session: Session,
    workspace_id: str,
    *,
    title: str,
    icon: str,
    layout: ViewLayoutModel,
) -> View:
    """Create the view and append its sidebar entry, in one transaction.

    A view with no entry is unreachable from the sidebar that is the only way to
    open it, so the two writes commit together or not at all.

    The view's kind is the layout's kind. There is no second field to send it in,
    so there is nothing for it to disagree with.
    """
    layout_json = _serialized_layout(layout)
    for _ in range(_APPEND_ATTEMPTS):
        # Read the rank before anything is pending: a query with an unflushed
        # INSERT behind it autoflushes, which takes the write lock and turns
        # every concurrent create into a queue waiting on this transaction.
        position = _next_position(session, workspace_id)
        view = View(
            workspace_id=workspace_id,
            kind=layout.kind,
            title=title,
            icon=icon,
            layout_json=layout_json,
        )
        session.add(view)
        # Flush the view before its entry exists to reference it. Foreign keys
        # are enforced now (`PRAGMA foreign_keys=ON`), so an entry inserted
        # ahead of the row it points at fails the constraint — and this loop
        # would report that as sidebar contention, which it is not.
        session.flush()
        session.add(
            SidebarEntry(
                workspace_id=workspace_id,
                position=position,
                entry_kind=SidebarEntryKind.VIEW,
                view_id=view.id,
            )
        )
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            continue
        session.refresh(view)
        return view
    raise SidebarContentionError("The sidebar is being reordered by another request; try again")


def update_view(
    session: Session,
    view: View,
    *,
    title: str | None = None,
    icon: str | None = None,
    layout: ViewLayoutModel | None = None,
    viewport: ViewViewport | None = None,
) -> View | None:
    """Apply the fields that were sent, leaving the omitted ones alone.

    The layout and the viewport are independent halves of that: a pan writes the
    viewport and leaves the arrangement exactly as it was, and a split writes the
    arrangement and leaves the user looking where they were looking. That is why
    they are two columns and why neither branch below touches the other's.

    ``None`` when the view was deleted between the read and this write: the
    staged UPDATE then matches no rows and SQLAlchemy raises ``StaleDataError``.
    Unlike the renumbering paths, there is nothing here to retry — the row is
    gone and re-reading it will not bring it back — so this reports the absence
    the same way ``get_view`` does, and the API answers the 404 a request a
    millisecond later already gets.
    """
    if layout is not None:
        # Encoded before anything is assigned, so a layout that cannot be stored
        # leaves the view exactly as it was rather than half-updated.
        view.layout_json = _serialized_layout(layout)
        view.kind = layout.kind
    if viewport is not None:
        # Three bounded floats, so there is no size limit to enforce and nothing
        # here that `json` can refuse — unlike a layout, whose open `settings`
        # mapping is why `_serialized_layout` exists.
        view.viewport_json = json.dumps(viewport_payload(viewport))
    if title is not None:
        view.title = title
    if icon is not None:
        view.icon = icon
    view.updated_at = utcnow()
    session.add(view)
    try:
        session.commit()
    except StaleDataError:
        session.rollback()
        return None
    session.refresh(view)
    return view


def delete_view(session: Session, view: View) -> None:
    """Remove the view, its sidebar entry, and the gap the removal leaves.

    Retried as a unit: a rollback undoes the deletion along with the renumbering,
    so a pass that loses the race has to redo both. The view is re-fetched each
    pass because ``view`` itself is expired by the rollback, and because a peer
    may have deleted it in the meantime.
    """
    workspace_id = view.workspace_id
    view_id = view.id

    def attempt() -> None:
        target = session.get(View, view_id)
        for entry in list_entries(session, workspace_id):
            if entry.view_id == view_id:
                session.delete(entry)
        if target is not None:
            session.delete(target)
        session.flush()
        _close_gaps(session, workspace_id)

    _commit_with_retry(session, attempt)


def pin_page(session: Session, workspace_id: str, page_key: str) -> SidebarEntry:
    """Append an entry for a built-in page, or return the one already pinned.

    Pinning twice is the same request twice, not a second tab — and these
    endpoints run in FastAPI's threadpool, so "twice" includes two requests in
    flight at once. A select-then-insert cannot see the peer's uncommitted row,
    so the uniqueness lives in the database and the loser reads back the winner's
    entry instead of adding a duplicate tab.
    """
    page_key = _validated_page_key(page_key)
    for _ in range(_APPEND_ATTEMPTS):
        existing = _page_entry(session, workspace_id, page_key)
        if existing:
            return existing
        entry = SidebarEntry(
            workspace_id=workspace_id,
            position=_next_position(session, workspace_id),
            entry_kind=SidebarEntryKind.PAGE,
            page_key=page_key,
        )
        session.add(entry)
        try:
            session.commit()
        except IntegrityError:
            # Either a peer pinned this page first, or it took the rank we read.
            # The next pass tells them apart; both are resolved by re-reading.
            session.rollback()
            continue
        session.refresh(entry)
        return entry
    existing = _page_entry(session, workspace_id, page_key)
    if existing:
        return existing
    raise SidebarContentionError("The sidebar is being reordered by another request; try again")


def set_entry_pinned(session: Session, entry: SidebarEntry, pinned: bool) -> SidebarEntry:
    """Move a view's tab between the sidebar's Pinned section and Tabs.

    Only a view entry can be pinned. The built-in pages are no longer stored at
    all — the sidebar's Tools section is derived from the client's page catalog —
    so a page entry is a leftover row from before that change, and moving one
    between two sections that never draw it would be a write with no effect the
    caller could see.

    The rank is untouched: pinning is which section draws the tab, not where it
    sits in the ordering the two sections share. Re-pinning something already
    pinned is the same request twice, so it is not an error and it does not
    disturb the row.
    """
    if entry.entry_kind != SidebarEntryKind.VIEW:
        raise ValueError("Only a view's tab can be pinned or unpinned")
    if entry.pinned == pinned:
        return entry
    entry.pinned = pinned
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def delete_entry(session: Session, entry: SidebarEntry) -> None:
    """Unpin a built-in page.

    A view's entry is not unpinnable. It is the only thing that ranks the view,
    and nothing can recreate it — pinning takes a page key — so removing it would
    leave a stored view that no listing returns and no endpoint can reach again.
    Deleting the view is what removes a view's entry.
    """
    if entry.entry_kind != SidebarEntryKind.PAGE:
        raise ValueError("Only a pinned page can be unpinned; delete the view instead")
    workspace_id = entry.workspace_id
    entry_id = entry.id

    def attempt() -> None:
        target = session.get(SidebarEntry, entry_id)
        if target is not None:
            session.delete(target)
            session.flush()
        _close_gaps(session, workspace_id)

    _commit_with_retry(session, attempt)


def reorder_entries(
    session: Session, workspace_id: str, entry_ids: list[str]
) -> list[SidebarEntry]:
    """Rank the sidebar by an explicit permutation of its entry ids.

    Anything short of a permutation is refused rather than guessed: half a list
    cannot produce a total order, and a repeated id ranks one entry twice while
    leaving another at whatever rank it already held.

    The permutation is checked against a fresh read on every pass, because a
    retry exists precisely for the case where the sidebar changed underneath it.
    A list that named every entry on the first read and does not on the second
    was invalidated by a peer, not sent wrong — so that is contention, and the
    caller is told to retry rather than told its request was malformed.
    """
    if len(entry_ids) != len(set(entry_ids)):
        raise ValueError("Reorder repeats an entry")
    named = set(entry_ids)
    if named != {entry.id for entry in list_entries(session, workspace_id)}:
        raise ValueError("Reorder must name every entry of this workspace exactly once")

    def attempt() -> None:
        entries = {entry.id: entry for entry in list_entries(session, workspace_id)}
        if named != set(entries):
            raise SidebarContentionError(
                "The sidebar changed while it was being reordered; try again"
            )
        _renumber(session, [entries[entry_id] for entry_id in entry_ids])

    _commit_with_retry(session, attempt)
    return list_entries(session, workspace_id)
