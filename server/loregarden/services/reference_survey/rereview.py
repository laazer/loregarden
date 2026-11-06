"""Reviewing a reference again once its repository has moved.

One switch and two numbers for the whole library (`ReferenceRereviewSettings`):
a reviewed repository is queued for a re-review once the upstream check finds
it at least `min_new_commits` ahead of the commit we read, and that re-review
reads the newest `max_commits_per_review` of them. A person can take any
source out (`auto_rereview_excluded`); taking one out also withdraws a
re-review still waiting in line.

Clones are shallow (depth 1), so the commits come from GitHub: the newest N on
the default branch, then a compare from the commit before them to the head for
the files they touched. Those files, rolled up to map leaves, are the pass's
slice, and the commit list goes in its brief.

Paced: at most one re-review is queued or running at a time, most-moved first,
so turning it on over a library where dozens of repositories have moved does
not put dozens of agent runs in line at once. A re-review that could not read
GitHub is tried again for the same range after `RETRY_AFTER`, not every tick.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from loregarden.models.domain.enums import utcnow
from loregarden.models.domain.survey_enums import (
    SurveyPassMode,
    SurveyPassStatus,
    SurveySourceKind,
    SurveySourceStatus,
)
from loregarden.models.domain.survey_tables import (
    ReferenceRereviewSettings,
    SurveyPass,
    SurveySource,
)
from loregarden.services.reference_repo_service import ReferenceRepoError, parse_repo_url
from loregarden.services.reference_survey.errors import SurveyNotFound
from loregarden.services.reference_survey.passes import coverage_rows, leaf_containing, map_leaves
from loregarden.services.reference_survey.queue import request_pass
from loregarden.services.reference_survey.schemas import PassRequestIn
from loregarden.services.reference_survey.upstream import (
    GITHUB_HOST,
    UpstreamUnavailable,
    gh_api_json,
    parse_github,
)
from pydantic import BaseModel, Field, RootModel
from sqlmodel import Session, col, or_, select

logger = logging.getLogger(__name__)

SETTINGS_ID = "default"
#: GitHub lists at most 100 commits a page; one page is one read.
MAX_COMMITS_CEILING = 100
MIN_NEW_COMMITS_CEILING = 100_000
#: A re-review refused for a range is not queued for that range again sooner.
RETRY_AFTER = timedelta(hours=24)
#: Changed files named in the brief; the slice covers them all.
_FILES_LISTED = 80
_OPEN = (SurveyPassStatus.QUEUED, SurveyPassStatus.DISPATCHED)


class RereviewSettingsIn(BaseModel):
    enabled: bool
    min_new_commits: int = Field(ge=1, le=MIN_NEW_COMMITS_CEILING)
    max_commits_per_review: int = Field(ge=1, le=MAX_COMMITS_CEILING)


class ExcludedSource(BaseModel):
    id: str
    name: str


class RereviewInFlight(BaseModel):
    source_id: str
    name: str
    ordinal: int
    status: str


class RereviewSettingsView(BaseModel):
    enabled: bool
    min_new_commits: int
    max_commits_per_review: int
    max_commits_ceiling: int
    #: Reviewed repositories that would be queued, one at a time, while it is on.
    due: int
    #: The re-review queued or running now, if any.
    in_flight: RereviewInFlight | None
    excluded: list[ExcludedSource]
    updated_at: datetime | None


def load_settings(session: Session) -> ReferenceRereviewSettings:
    """The settings row, or the defaults (off) when nobody has changed them."""
    return session.get(ReferenceRereviewSettings, SETTINGS_ID) or ReferenceRereviewSettings(
        id=SETTINGS_ID
    )


def save_settings(session: Session, body: RereviewSettingsIn) -> RereviewSettingsView:
    row = session.get(ReferenceRereviewSettings, SETTINGS_ID) or ReferenceRereviewSettings(
        id=SETTINGS_ID
    )
    row.sqlmodel_update(body.model_dump())
    row.updated_at = utcnow()
    session.add(row)
    session.commit()
    return settings_view(session)


def settings_view(session: Session) -> RereviewSettingsView:
    cfg = load_settings(session)
    excluded = session.exec(
        select(SurveySource)
        .where(col(SurveySource.auto_rereview_excluded).is_(True))
        .order_by(col(SurveySource.name))
    ).all()
    persisted = session.get(ReferenceRereviewSettings, SETTINGS_ID)
    return RereviewSettingsView(
        enabled=cfg.enabled,
        min_new_commits=cfg.min_new_commits,
        max_commits_per_review=cfg.max_commits_per_review,
        max_commits_ceiling=MAX_COMMITS_CEILING,
        due=len(due_sources(session, cfg.min_new_commits)),
        in_flight=_in_flight(session),
        excluded=[ExcludedSource(id=s.id, name=s.name or s.url) for s in excluded],
        updated_at=persisted.updated_at if persisted else None,
    )


def _in_flight(session: Session) -> RereviewInFlight | None:
    row = session.exec(
        select(SurveyPass, SurveySource)
        .join(SurveySource, col(SurveySource.id) == SurveyPass.source_id)
        .where(SurveyPass.mode == SurveyPassMode.REREVIEW, col(SurveyPass.status).in_(_OPEN))
        .order_by(col(SurveyPass.queued_at))
    ).first()
    if row is None:
        return None
    survey_pass, source = row
    return RereviewInFlight(
        source_id=source.id,
        name=source.name or source.url,
        ordinal=survey_pass.ordinal,
        status=survey_pass.status.value,
    )


def set_excluded(session: Session, source_id: str, *, excluded: bool) -> SurveySource:
    """Take a source out of auto re-review, or put it back.

    Taking it out withdraws a re-review still queued on it; one already running
    finishes, since its agent has started reading.
    """
    source = session.get(SurveySource, source_id)
    if source is None:
        raise SurveyNotFound(f"No survey source {source_id!r}")
    source.auto_rereview_excluded = excluded
    source.updated_at = utcnow()
    session.add(source)
    if excluded:
        _withdraw_queued(session, source)
    session.commit()
    session.refresh(source)
    return source


def _withdraw_queued(session: Session, source: SurveySource) -> None:
    queued = session.exec(
        select(SurveyPass).where(
            SurveyPass.source_id == source.id,
            SurveyPass.mode == SurveyPassMode.REREVIEW,
            SurveyPass.status == SurveyPassStatus.QUEUED,
        )
    ).all()
    for survey_pass in queued:
        survey_pass.status = SurveyPassStatus.REFUSED
        survey_pass.refused_reason = "Withdrawn: a person took this source out of auto re-review."
        survey_pass.finished_at = utcnow()
        # A withdrawal is not a failed attempt: the retry wait (`due_sources`)
        # keys on the range, so a source put back is due again at once.
        survey_pass.since_commit = ""
        session.add(survey_pass)
    still_open = session.exec(
        select(SurveyPass.id).where(
            SurveyPass.source_id == source.id,
            col(SurveyPass.status).in_(_OPEN),
            col(SurveyPass.id).not_in([p.id for p in queued]),
        )
    ).first()
    if queued and still_open is None and source.status is SurveySourceStatus.ACTIVE:
        source.status = SurveySourceStatus.DONE


def due_sources(session: Session, min_new_commits: int) -> list[SurveySource]:
    """Reviewed repositories at least `min_new_commits` ahead, most-moved first.

    Only an answer read after our last read counts (`upstream_checked_at` later
    than `fetched_at`): the count a check took before a re-review dispatched
    still names the old commit until the next check.
    """
    candidates = session.exec(
        select(SurveySource)
        .where(
            SurveySource.kind == SurveySourceKind.REPO,
            SurveySource.status == SurveySourceStatus.DONE,
            col(SurveySource.auto_rereview_excluded).is_(False),
            SurveySource.pinned_commit != "",
            SurveySource.upstream_check_error == "",
            col(SurveySource.commits_since_review) >= min_new_commits,
            col(SurveySource.upstream_checked_at).is_not(None),
            or_(
                col(SurveySource.fetched_at).is_(None),
                col(SurveySource.upstream_checked_at) > col(SurveySource.fetched_at),
            ),
        )
        .order_by(col(SurveySource.commits_since_review).desc(), col(SurveySource.name))
    ).all()
    recently_refused = set(
        session.exec(
            select(SurveyPass.source_id, SurveyPass.since_commit).where(
                SurveyPass.mode == SurveyPassMode.REREVIEW,
                SurveyPass.status == SurveyPassStatus.REFUSED,
                col(SurveyPass.finished_at) > utcnow() - RETRY_AFTER,
            )
        ).all()
    )
    return [s for s in candidates if (s.id, s.pinned_commit) not in recently_refused]


def queue_due(session: Session) -> SurveyPass | None:
    """One feeder tick's share: queue the most-moved due repository, unless auto
    re-review is off or a re-review is already queued or running."""
    cfg = load_settings(session)
    if not cfg.enabled or _in_flight(session) is not None:
        return None
    due = due_sources(session, cfg.min_new_commits)
    if not due:
        return None
    source = due[0]
    behind = source.commits_since_review
    survey_pass = request_pass(
        session,
        PassRequestIn(
            source_id=source.id,
            mode=SurveyPassMode.REREVIEW,
            question=(
                f"What changed upstream since the commit we reviewed ({source.pinned_commit[:12]}, "
                f"{behind} commits behind at the last check)? Does any of it change what we "
                "concluded, or answer a question our workspaces or initiatives have?"
            ),
        ),
    )
    survey_pass.since_commit = source.pinned_commit
    session.add(survey_pass)
    session.commit()
    logger.info("Auto re-review queued %s (%s commits behind)", source.url, behind)
    return survey_pass


# --- What a re-review reads, fetched at dispatch ---------------------------------


class _CommitBody(BaseModel):
    message: str


class _ListedCommit(BaseModel):
    sha: str
    commit: _CommitBody


class _CommitPage(RootModel[list[_ListedCommit]]):
    pass


class _File(BaseModel):
    filename: str


class _CompareFiles(BaseModel):
    files: list[_File] = Field(default_factory=list)


class Changes(BaseModel):
    since: str
    #: The commit before the window, or `since` when the window reaches it.
    base: str
    head: str
    #: Newest first: (sha, subject).
    commits: list[tuple[str, str]]
    #: How many commits the upstream check counted since `since`; None if unknown.
    behind: int | None
    files: list[str]

    def render(self) -> str:
        older = (
            f" {self.behind - len(self.commits)} older ones are not part of this pass."
            if self.behind is not None and self.behind > len(self.commits)
            else ""
        )
        listed = self.files[:_FILES_LISTED]
        more = f" and {len(self.files) - len(listed)} more" if len(self.files) > len(listed) else ""
        lines = [
            f"**Changes since `{self.since[:12]}`:** reading the newest {len(self.commits)} commits "
            f"(`{self.base[:12]}...{self.head[:12]}`).{older}",
            "",
            *(f"- `{sha[:9]}` {subject}" for sha, subject in self.commits),
            "",
            f"**Files they touched ({len(self.files)}):** "
            + (", ".join(f"`{f}`" for f in listed) + more if listed else "none listed"),
        ]
        return "\n".join(lines)


def fetch_changes(source: SurveySource, survey_pass: SurveyPass, max_commits: int) -> Changes:
    """The newest `max_commits` commits since `survey_pass.since_commit`, and
    the files they touched. Raises UpstreamUnavailable when GitHub cannot say."""
    since = survey_pass.since_commit
    if not since:
        raise UpstreamUnavailable("The re-review names no commit to read changes since.")
    try:
        parsed = parse_repo_url(source.url)
    except ReferenceRepoError as exc:
        raise UpstreamUnavailable(f"Not a repository URL: {exc}") from exc
    if parsed.host != GITHUB_HOST:
        raise UpstreamUnavailable(
            f"Only GitHub repositories are re-reviewed; {parsed.host} is not one."
        )
    repo = f"repos/{parsed.owner}/{parsed.name}"
    page = parse_github(
        _CommitPage,
        gh_api_json(f"{repo}/commits?per_page={min(max_commits + 1, MAX_COMMITS_CEILING)}"),
    ).root
    if not page:
        raise UpstreamUnavailable("GitHub listed no commits on the default branch.")
    # `since` may be recorded short; match it as a prefix.
    reached = next((i for i, c in enumerate(page) if c.sha.startswith(since)), None)
    if reached is not None:
        window, base = page[:reached], since
    else:
        window = page[:max_commits]
        base = page[max_commits].sha if len(page) > max_commits else since
    if not window:
        raise UpstreamUnavailable(f"The default branch has not moved past {since[:12]}.")
    head = page[0].sha
    compare = parse_github(_CompareFiles, gh_api_json(f"{repo}/compare/{base}...{head}"))
    return Changes(
        since=since,
        base=base,
        head=head,
        commits=[
            (c.sha, c.commit.message.splitlines()[0] if c.commit.message else "") for c in window
        ],
        behind=source.commits_since_review,
        files=[f.filename for f in compare.files],
    )


def slice_for(session: Session, source_id: str, changes: Changes) -> list[str]:
    """The map leaves holding the changed files, in map order. Files outside the
    map (deleted since, or never mapped) have no leaf and are left out."""
    leaves = map_leaves(coverage_rows(session, source_id))
    touched = {leaf.path for f in changes.files if (leaf := leaf_containing(f, leaves))}
    return [leaf.path for leaf in leaves if leaf.path in touched]
