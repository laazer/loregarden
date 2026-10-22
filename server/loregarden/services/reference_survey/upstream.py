"""Has a reference repository changed since we reviewed it?

For a GitHub repository, three reads through the configured `gh` account
(`git_subprocess.run_gh`, the one place loregarden calls GitHub from): the
repository's default branch, that branch's newest commit and its date, and how
many commits it is ahead of the commit we reviewed (`pinned_commit`). Checked in
the background a few sources at a time, at most once a day each, and on demand.

A check that cannot answer says why (`upstream_check_error`) and keeps its
time, so "not checked", "could not check" and "no change" never read alike.
Only GitHub repositories are checked; any other host says so.
"""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime, timedelta
from typing import TypeVar

from loregarden.config import settings
from loregarden.models.domain.enums import utcnow
from loregarden.models.domain.survey_enums import SurveySourceKind
from loregarden.models.domain.survey_tables import SurveySource
from loregarden.services.git_subprocess import run_gh
from loregarden.services.reference_repo_service import ReferenceRepoError, parse_repo_url
from loregarden.services.reference_survey.errors import SurveyNotFound
from pydantic import BaseModel, ValidationError
from sqlmodel import Session, col, or_, select

logger = logging.getLogger(__name__)

#: A source is checked again once its last check is this old.
STALE_AFTER = timedelta(hours=24)
#: Sources checked per background tick: three API calls each, kept well inside
#: GitHub's hourly limit at the feeder's two-minute cadence.
BATCH = 5
_TIMEOUT_SECONDS = 30
GITHUB_HOST = "github.com"


class _Repo(BaseModel):
    default_branch: str


class _Committer(BaseModel):
    date: datetime


class _CommitBody(BaseModel):
    committer: _Committer


class _Commit(BaseModel):
    sha: str
    commit: _CommitBody


class _Compare(BaseModel):
    ahead_by: int


class UpstreamUnavailable(Exception):
    """The check could not answer; the message says why, in words."""


def _gh_json(path: str) -> str:
    try:
        result = run_gh(["api", path], cwd=settings.repo_root, timeout=_TIMEOUT_SECONDS)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        raise UpstreamUnavailable(f"gh api {path}: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        raise UpstreamUnavailable(
            f"gh api {path}: {detail[-1] if detail else f'exit {result.returncode}'}"
        )
    return result.stdout


M = TypeVar("M", bound=BaseModel)


def _parse(model: type[M], raw: str) -> M:
    try:
        return model.model_validate(json.loads(raw))
    except (ValueError, ValidationError) as exc:
        raise UpstreamUnavailable(
            f"GitHub answered in a shape this check does not know: {exc}"
        ) from exc


def check_upstream(session: Session, source: SurveySource) -> SurveySource:
    """Read the source's upstream state now and store it, answer or reason."""
    source.upstream_checked_at = utcnow()
    try:
        _check(source)
        source.upstream_check_error = ""
    except UpstreamUnavailable as exc:
        logger.warning("Upstream check for %s could not answer: %s", source.url, exc)
        source.upstream_check_error = str(exc)
    session.add(source)
    session.commit()
    session.refresh(source)
    return source


def _check(source: SurveySource) -> None:
    if source.kind is not SurveySourceKind.REPO:
        raise UpstreamUnavailable(f"A {source.kind.value} has no commits to check.")
    try:
        parsed = parse_repo_url(source.url)
    except ReferenceRepoError as exc:
        raise UpstreamUnavailable(f"Not a repository URL: {exc}") from exc
    if parsed.host != GITHUB_HOST:
        raise UpstreamUnavailable(
            f"Only GitHub repositories are checked; {parsed.host} is not one."
        )
    repo = f"repos/{parsed.owner}/{parsed.name}"
    branch = _parse(_Repo, _gh_json(repo)).default_branch
    head = _parse(_Commit, _gh_json(f"{repo}/commits/{branch}"))
    source.upstream_branch = branch
    source.upstream_head = head.sha
    source.upstream_committed_at = head.commit.committer.date.replace(tzinfo=None)
    source.commits_since_review = None
    if source.pinned_commit:
        # The reviewed commit may be gone (a force-push) or recorded too short to
        # resolve; the head and its date still answer, so this part alone fails.
        try:
            compare = _parse(
                _Compare, _gh_json(f"{repo}/compare/{source.pinned_commit}...{head.sha}")
            )
            source.commits_since_review = compare.ahead_by
        except UpstreamUnavailable as exc:
            raise UpstreamUnavailable(
                f"Newest commit read, but the reviewed commit {source.pinned_commit[:12]} could not be "
                f"compared with it: {exc}"
            ) from exc


def check_upstream_by_id(session: Session, source_id: str) -> SurveySource:
    source = session.get(SurveySource, source_id)
    if source is None:
        raise SurveyNotFound(f"No survey source {source_id!r}")
    return check_upstream(session, source)


def refresh_stale(session: Session, *, limit: int = BATCH) -> int:
    """Check the repositories checked longest ago (never first). Returns how many."""
    cutoff = utcnow() - STALE_AFTER
    due = session.exec(
        select(SurveySource)
        .where(
            SurveySource.kind == SurveySourceKind.REPO,
            or_(
                col(SurveySource.upstream_checked_at).is_(None),
                col(SurveySource.upstream_checked_at) < cutoff,
            ),
        )
        .order_by(
            col(SurveySource.upstream_checked_at).is_not(None),
            col(SurveySource.upstream_checked_at),
        )
        .limit(limit)
    ).all()
    for source in due:
        check_upstream(session, source)
    return len(due)
