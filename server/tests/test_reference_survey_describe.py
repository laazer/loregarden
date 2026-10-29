"""A reference described from its own page: title and first real paragraph."""

from __future__ import annotations

from datetime import datetime
from unittest import mock

from loregarden.db.migration_utils import table_columns
from loregarden.db.versions.reference_survey_record import m_reference_survey_record
from loregarden.db.versions.survey_source_description import m_survey_source_description
from loregarden.models.domain.enums import ReferenceCacheOutcome
from loregarden.models.domain.survey_enums import SurveySourceKind
from loregarden.models.domain.survey_tables import SurveySource
from loregarden.services.reference_cache import ReferencePayload
from loregarden.services.reference_survey import describe
from loregarden.services.reference_survey.describe import SUMMARY_CHARS, describe_page, summarize
from sqlalchemy import create_engine, text
from sqlmodel import Session
from tests.survey_support import build_world

PAGE = """[![badge](https://x/b.svg)](https://x)

# Temporal docs

Short nav.

**Temporal** is a durable execution platform: your code runs to completion
even when [workers](https://x/w) crash.

More text."""


def _payload(**kw) -> ReferencePayload:
    base = {"url": "https://docs.x.dev/", "title": "", "markdown": "", "cache": ReferenceCacheOutcome.MISS,
            "fetched_at": datetime(2026, 10, 22), "total_chars": 0, "truncated": False, "error": ""}  # fmt: skip
    return ReferencePayload(**(base | kw))


def test_the_summary_is_the_title_and_the_first_paragraph_of_prose():
    assert summarize("Temporal", PAGE) == (
        "Temporal — Temporal is a durable execution platform: your code runs to completion even "
        "when workers crash."
    )


def test_a_long_summary_is_cut_to_a_line():
    summary = summarize("T", "word " * 200)
    assert len(summary) <= SUMMARY_CHARS
    assert summary.endswith("…")


def test_a_page_is_described_and_a_dead_one_says_why_without_being_read_every_tick(isolated_db):
    with Session(isolated_db) as session:
        world = build_world(session)
        site = SurveySource(
            workspace_id=world.workspace_id, kind=SurveySourceKind.SITE, url="https://docs.x.dev/"
        )
        session.add(site)
        session.commit()
        with mock.patch.object(
            describe, "fetch_reference", return_value=_payload(title="Temporal", markdown=PAGE)
        ):
            described = describe_page(session, site)
        assert described.description.startswith("Temporal — Temporal is a durable")
        assert described.describe_error == ""
        with mock.patch.object(
            describe, "fetch_reference", return_value=_payload(error="HTTP 404")
        ):
            failed = describe_page(session, site)
        assert failed.describe_error == "HTTP 404"
        assert failed.described_at is not None
        assert failed.description.startswith("Temporal")  # the last good reading stays


def test_the_description_columns_are_added_idempotently():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        for table in ("workspaces", "approvals", "tickets"):
            conn.execute(text(f"CREATE TABLE {table} (id VARCHAR PRIMARY KEY)"))
        m_reference_survey_record(conn)
        m_survey_source_description(conn)
        m_survey_source_description(conn)
        added = {"description", "language", "stars", "topics", "described_at", "describe_error"}
        assert added <= set(table_columns(conn, "survey_sources"))
