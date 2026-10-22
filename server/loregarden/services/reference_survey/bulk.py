"""Proposing several references at once.

Each URL is enqueued on its own, exactly as one added alone: a new one files its
own admission card, a known one gains the workspaces. One URL failing (a bad
URL, a refused source) does not stop the rest; it is reported with its reason
and leaves nothing behind. A URL already in the library for these workspaces is
reported as known, not as a failure.
"""

from __future__ import annotations

from loregarden.models.domain.survey_enums import BulkAddOutcome, SurveySourceKind
from loregarden.models.domain.survey_tables import SurveySource
from loregarden.services.reference_repo_service import ReferenceRepoError, parse_repo_url
from loregarden.services.reference_survey.errors import AlreadyServes, SurveyError, SurveyNotFound
from loregarden.services.reference_survey.library import LibraryRow, library_row
from loregarden.services.reference_survey.queue import enqueue_source
from loregarden.services.reference_survey.schemas import BulkSourceIn, SourceIn
from pydantic import BaseModel
from sqlmodel import Session

#: Hosts whose URLs are repositories. Any other https URL is read as a site,
#: since `parse_repo_url` would accept a docs page's owner/name-shaped path too.
CODE_HOSTS = frozenset({"github.com", "gitlab.com", "bitbucket.org", "codeberg.org"})


def infer_kind(url: str) -> SurveySourceKind:
    """A repository on a known code host, or reached over ssh; otherwise a site."""
    try:
        parsed = parse_repo_url(url)
    except ReferenceRepoError:
        return SurveySourceKind.SITE
    if parsed.host in CODE_HOSTS or url.strip().startswith("git@"):
        return SurveySourceKind.REPO
    return SurveySourceKind.SITE


class BulkAddItem(BaseModel):
    url: str
    outcome: BulkAddOutcome
    kind: SurveySourceKind
    #: The source as it now stands; None when the URL failed.
    source: LibraryRow | None
    #: Why it failed; "" otherwise.
    detail: str


def enqueue_many(session: Session, body: BulkSourceIn) -> list[BulkAddItem]:
    items = []
    for url in dict.fromkeys(u.strip() for u in body.urls if u.strip()):
        kind = body.kind or infer_kind(url)
        one = SourceIn(
            workspace_slugs=body.workspace_slugs, kind=kind, url=url, question=body.question,
            queued_reason=body.queued_reason, priority=body.priority,
        )  # fmt: skip
        try:
            added = enqueue_source(session, one)
        except AlreadyServes as exc:
            session.rollback()
            known = session.get(SurveySource, exc.source_id)
            items.append(
                BulkAddItem(
                    url=url, outcome=BulkAddOutcome.ALREADY, kind=known.kind if known else kind,
                    source=library_row(session, known) if known else None, detail="",
                )
            )  # fmt: skip
            continue
        except (SurveyError, SurveyNotFound) as exc:
            # Whatever this URL flushed before failing goes; the URLs before it
            # were committed by their own enqueue and stay.
            session.rollback()
            failed = BulkAddItem(
                url=url, outcome=BulkAddOutcome.FAILED, kind=kind, source=None, detail=str(exc)
            )  # fmt: skip
            items.append(failed)
            continue
        row = library_row(session, added.source)
        if added.scoped_pass is not None:
            outcome = BulkAddOutcome.RESCOPED
        elif added.proposed:
            outcome = BulkAddOutcome.PROPOSED
        else:
            outcome = BulkAddOutcome.ATTACHED
        items.append(BulkAddItem(url=url, outcome=outcome, kind=kind, source=row, detail=""))
    return items
