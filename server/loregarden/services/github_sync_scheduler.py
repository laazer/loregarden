"""Background GitHub issue sync, per workspace, off by default.

A workspace opts in through its `GithubSyncSettings` row: an interval, and
optionally a parent to import unlinked open issues under. One loop wakes every
`settings.github_sync_tick_seconds` and runs `sync_workspace` for each workspace
whose interval has elapsed. The interval is per workspace; the tick is only the
resolution.

Nothing here is silent. A run that fails outright, or whose links failed, is
logged at warning and recorded on the settings row (`last_error`), which the
sync modal shows. Conflicts are not failures: they are waiting on a person, and
the ticket card is where they are resolved.

Not started in a sandboxed branch server (see `main.lifespan`): that server runs
on a snapshot of main's database, and a snapshot pushing its stale tickets to
GitHub would overwrite real edits.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from loregarden.config import settings
from loregarden.db.session import engine
from loregarden.models.domain import (
    GithubSyncSettings,
    GithubSyncSettingsView,
    Ticket,
    UpdateGithubSyncSettings,
    Workspace,
    WorkspaceSyncResult,
)
from loregarden.models.domain.enums import utcnow
from loregarden.services.github_issue_sync import sync_workspace
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)


def _row(session: Session, workspace: Workspace) -> GithubSyncSettings | None:
    return session.get(GithubSyncSettings, workspace.id)


def _view(workspace: Workspace, row: GithubSyncSettings | None) -> GithubSyncSettingsView:
    if row is None:
        return GithubSyncSettingsView(workspace_slug=workspace.slug)
    return GithubSyncSettingsView(
        workspace_slug=workspace.slug,
        **row.model_dump(exclude={"workspace_id"}),
    )


def get_sync_settings(session: Session, workspace: Workspace) -> GithubSyncSettingsView:
    return _view(workspace, _row(session, workspace))


def update_sync_settings(
    session: Session, workspace: Workspace, body: UpdateGithubSyncSettings
) -> GithubSyncSettingsView:
    """Save the workspace's settings. Raises if the import parent is not in it."""
    parent_id = body.import_parent_ticket_id.strip()
    if parent_id:
        parent = session.get(Ticket, parent_id)
        if parent is None or parent.workspace_id != workspace.id:
            raise ValueError("The import parent must be a work item in this workspace")
    row = _row(session, workspace) or GithubSyncSettings(workspace_id=workspace.id)
    row.enabled = body.enabled
    row.interval_minutes = body.interval_minutes
    row.push_on_edit = body.push_on_edit
    row.import_parent_ticket_id = parent_id
    row.import_label = body.import_label.strip()
    session.add(row)
    session.commit()
    session.refresh(row)
    return _view(workspace, row)


def _as_utc(value: datetime) -> datetime:
    # SQLite hands datetimes back naive; they were written as UTC.
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def is_due(row: GithubSyncSettings, now: datetime) -> bool:
    if not row.enabled:
        return False
    if row.last_run_at is None:
        return True
    return _as_utc(row.last_run_at) + timedelta(minutes=row.interval_minutes) <= now


def _failure_summary(result: WorkspaceSyncResult) -> str:
    failed = [link for link in result.links if link.error]
    if not failed:
        return ""
    first = failed[0]
    return (
        f"{len(failed)} of {len(result.links)} linked tickets failed to sync; "
        f"first: {first.external_id} (#{first.issue_number}): {first.error}"
    )


def run_workspace_sync(session: Session, row: GithubSyncSettings, now: datetime) -> str:
    """One scheduled run for one workspace. Returns the recorded error, blank on success."""
    workspace = session.get(Workspace, row.workspace_id)
    if workspace is None:
        raise LookupError(f"GitHub sync settings point at a missing workspace {row.workspace_id}")
    try:
        result = sync_workspace(
            session,
            workspace,
            import_parent_ticket_id=row.import_parent_ticket_id,
            import_label=row.import_label,
        )
        error = _failure_summary(result)
    except Exception as exc:  # noqa: BLE001 — recorded on the row and logged below
        logger.exception("Background GitHub sync failed for workspace %s", workspace.slug)
        session.rollback()
        error = str(exc) or type(exc).__name__
    else:
        if error:
            logger.warning("Background GitHub sync for %s: %s", workspace.slug, error)
    row = session.get(GithubSyncSettings, workspace.id) or row
    row.last_run_at = now
    row.last_error = error
    session.add(row)
    session.commit()
    return error


def run_due_syncs(session: Session, now: datetime | None = None) -> dict[str, str]:
    """Run every due workspace's sync. Returns slug -> recorded error (blank = ok)."""
    now = now or utcnow()
    outcomes: dict[str, str] = {}
    rows = session.exec(
        select(GithubSyncSettings).where(col(GithubSyncSettings.enabled).is_(True))
    ).all()
    for row in rows:
        if not is_due(row, now):
            continue
        workspace = session.get(Workspace, row.workspace_id)
        slug = workspace.slug if workspace else row.workspace_id
        outcomes[slug] = run_workspace_sync(session, row, now)
    return outcomes


def _tick() -> dict[str, str]:
    with Session(engine) as session:
        return run_due_syncs(session)


async def run_github_sync_loop(tick_seconds: float) -> None:
    """Run due workspace syncs every `tick_seconds` until cancelled."""
    while True:
        try:
            await asyncio.sleep(tick_seconds)
            await asyncio.to_thread(_tick)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — the loop outliving a bad tick is the point
            logger.exception("GitHub sync scheduler tick failed; continuing")


def start_github_sync_loop(tick_seconds: float | None = None) -> asyncio.Task | None:
    """Start the scheduler, or return None when the tick disables it."""
    if tick_seconds is None:
        tick_seconds = settings.github_sync_tick_seconds
    if tick_seconds <= 0:
        logger.info("GitHub sync scheduler disabled (tick=%s)", tick_seconds)
        return None
    return asyncio.create_task(run_github_sync_loop(tick_seconds))
