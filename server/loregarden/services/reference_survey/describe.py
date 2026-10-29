"""What a reference is, in a line, read from the page itself.

GitHub repositories are described by the upstream check (`upstream.py`), from
the same API read it already makes. Everything else (a site, a released app, a
repository on another host) is described from its page: the title and the
first real paragraph, fetched through `reference_cache.fetch_reference`, which
refuses private addresses and caches what it reads. A read that fails says why
in `describe_error` and still stamps `described_at`, so a dead page is not
fetched again on every tick; "Check now" reads it again.
"""

from __future__ import annotations

import logging
import re

from loregarden.models.domain.enums import utcnow
from loregarden.models.domain.survey_tables import SurveySource
from loregarden.services.reference_cache import fetch_reference
from sqlmodel import Session

logger = logging.getLogger(__name__)

#: Long enough for "what is this", short enough to sit under a name.
SUMMARY_CHARS = 280
#: Shorter lines are navigation, badges and headings, not a description.
_MIN_PARAGRAPH = 40
_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_MARKUP = re.compile(r"[*_`>#|]+")


def summarize(title: str, markdown: str) -> str:
    """The page's title and its first paragraph of prose, without markup."""
    opening = ""
    for block in re.split(r"\n\s*\n", markdown):
        text = _MARKUP.sub("", _LINK.sub(r"\1", block)).strip()
        text = " ".join(text.split())
        if len(text) >= _MIN_PARAGRAPH and not text.startswith(("-", "+", "=")):
            opening = text
            break
    parts = [p for p in (title.strip(), opening) if p]
    summary = " — ".join(parts)
    return summary if len(summary) <= SUMMARY_CHARS else summary[: SUMMARY_CHARS - 1].rstrip() + "…"


def describe_page(session: Session, source: SurveySource) -> SurveySource:
    """Read the source's page and keep a one-line description, or why it could not."""
    payload = fetch_reference(session, source.url, max_chars=6000)
    source.described_at = utcnow()
    if payload.markdown or payload.title:
        source.description = summarize(payload.title, payload.markdown)
        source.describe_error = ""
    else:
        logger.warning("Could not describe %s: %s", source.url, payload.error)
        source.describe_error = payload.error or "the page returned nothing to read"
    session.add(source)
    session.commit()
    session.refresh(source)
    return source
