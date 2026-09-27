"""Two-way links between tickets and GitHub issues.

One row per linked ticket. Besides the address of the issue, the row stores the
content both sides agreed on at the last sync — the *base* of a three-way merge.
Comparing each side against the base is what tells a local edit from a remote
one: without it, "the ticket and the issue differ" cannot say which side moved,
and every sync would have to pick a winner blindly.

Re-exported from ``models.domain``; its own module because ``tables`` and
``enums`` both sit at their size caps.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from loregarden.models.domain.enums import str_enum_column, utcnow
from pydantic import BaseModel
from pydantic import Field as PydanticField
from sqlmodel import Field, SQLModel


class IssueClosure(StrEnum):
    """Whether a work item is open, and if not, why it closed.

    The shared vocabulary between `TicketState` and GitHub's issue state plus
    `stateReason`: done is ``completed``, wont_do is ``not_planned``, and every
    other ticket state is open.
    """

    OPEN = "open"
    COMPLETED = "completed"
    NOT_PLANNED = "not_planned"


class ConflictPolicy(StrEnum):
    """What a sync does with a field both sides changed since the last sync."""

    #: Leave both sides alone and report the conflict. The base is not advanced,
    #: so the conflict is reported again until someone resolves it.
    REPORT = "report"
    #: The ticket's value wins and is pushed to the issue.
    LOCAL = "local"
    #: The issue's value wins and is pulled into the ticket.
    REMOTE = "remote"


class SyncField(StrEnum):
    TITLE = "title"
    BODY = "body"
    CLOSURE = "closure"


class GithubIssueLink(SQLModel, table=True):
    __tablename__ = "github_issue_links"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    ticket_id: str = Field(foreign_key="tickets.id", unique=True, index=True)
    workspace_id: str = Field(foreign_key="workspaces.id", index=True)
    #: ``owner/name``, as `gh repo view --json nameWithOwner` spells it.
    repo: str = Field(index=True)
    issue_number: int = Field(index=True)
    issue_url: str = ""
    # The merge base: what both sides held after the last successful sync.
    synced_title: str = ""
    synced_body: str = ""
    synced_closure: IssueClosure = Field(
        default=IssueClosure.OPEN,
        sa_column=str_enum_column(IssueClosure, IssueClosure.OPEN),
    )
    last_synced_at: datetime = Field(default_factory=utcnow)
    #: The last sync's failure, verbatim, or blank. Recorded rather than only
    #: raised, so a webhook-driven sync that failed is visible on the ticket.
    last_error: str = ""
    created_at: datetime = Field(default_factory=utcnow)


#: Bounds on the background sync interval. The floor keeps a busy workspace
#: from spending its GitHub rate limit on a sync every minute.
MIN_SYNC_INTERVAL_MINUTES = 5
MAX_SYNC_INTERVAL_MINUTES = 24 * 60
DEFAULT_SYNC_INTERVAL_MINUTES = 15


class GithubSyncSettings(SQLModel, table=True):
    """Per-workspace background sync. No row means off, the default."""

    __tablename__ = "github_sync_settings"

    workspace_id: str = Field(foreign_key="workspaces.id", primary_key=True)
    enabled: bool = Field(default=False)
    interval_minutes: int = Field(default=DEFAULT_SYNC_INTERVAL_MINUTES)
    #: Import unlinked open issues under this ticket on each run. Blank: links only.
    import_parent_ticket_id: str = ""
    import_label: str = ""
    last_run_at: datetime | None = Field(default=None)
    #: The last run's failure — the run itself, or a count of links that failed.
    #: Blank when it succeeded.
    last_error: str = ""


class GithubSyncSettingsView(BaseModel):
    workspace_slug: str
    enabled: bool = False
    interval_minutes: int = DEFAULT_SYNC_INTERVAL_MINUTES
    import_parent_ticket_id: str = ""
    import_label: str = ""
    last_run_at: datetime | None = None
    last_error: str = ""


class UpdateGithubSyncSettings(BaseModel):
    enabled: bool
    interval_minutes: int = PydanticField(
        default=DEFAULT_SYNC_INTERVAL_MINUTES,
        ge=MIN_SYNC_INTERVAL_MINUTES,
        le=MAX_SYNC_INTERVAL_MINUTES,
    )
    import_parent_ticket_id: str = ""
    import_label: str = ""


class IssueSnapshot(BaseModel):
    """The synced content of one side — a ticket, an issue, or the base."""

    title: str
    body: str
    closure: IssueClosure


class SyncConflict(BaseModel):
    field: SyncField
    base: str
    local: str
    remote: str


class GithubIssueLinkView(BaseModel):
    ticket_id: str
    repo: str
    issue_number: int
    issue_url: str
    last_synced_at: datetime
    last_error: str


class LinkSyncResult(BaseModel):
    """What one link's sync did, field by field."""

    ticket_id: str
    external_id: str
    issue_number: int
    issue_url: str
    pushed: list[SyncField] = []
    pulled: list[SyncField] = []
    conflicts: list[SyncConflict] = []
    #: Blank on success. A failed link does not stop a workspace sync; it is
    #: reported here and recorded on the link.
    error: str = ""


class WorkspaceSyncResult(BaseModel):
    workspace_slug: str
    repo: str
    links: list[LinkSyncResult] = []
    imported: list[LinkSyncResult] = []


__all__ = [
    "ConflictPolicy",
    "GithubIssueLink",
    "GithubIssueLinkView",
    "GithubSyncSettings",
    "GithubSyncSettingsView",
    "UpdateGithubSyncSettings",
    "MIN_SYNC_INTERVAL_MINUTES",
    "MAX_SYNC_INTERVAL_MINUTES",
    "DEFAULT_SYNC_INTERVAL_MINUTES",
    "IssueClosure",
    "IssueSnapshot",
    "LinkSyncResult",
    "SyncConflict",
    "SyncField",
    "WorkspaceSyncResult",
]
