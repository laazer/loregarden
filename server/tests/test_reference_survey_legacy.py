"""The legacy import, on an excerpt shaped like the real scratch files.

The real files are gitignored, so they cannot be a fixture. This excerpt keeps
their shapes: a per-source section with numbered passes, an `Appends (n):` list
mixing external ids and bare numbers, a prioritised STILL UNREAD list with a
bracketed later pass inside it, a lettered multi-source pass section, and link
notes recording licence, outcome and pass label.
"""

from __future__ import annotations

import pytest
from loregarden.models.domain.survey_enums import (
    LicenceBasis,
    LicenceUse,
    SurveySourceStatus,
    SurveyTicketAction,
)
from loregarden.models.domain.survey_tables import (
    SurveyCoverage,
    SurveyLegacySection,
    SurveyPass,
    SurveyPassTicket,
    SurveySource,
)
from loregarden.services.reference_survey.legacy_import import import_legacy, split_sections
from sqlmodel import Session, col, select
from tests.factories import make_ticket
from tests.survey_support import build_world

QUEUE = """# Reference-survey queue — set of 2026-10-12

Standing drill per repo: map the whole tree before reading any of it.

---

## 9. openharness — PASS 1 DONE, MULTIPLE PASSES REMAIN
   autonomous-ai/openharness · MIT · TypeScript · 16,869 files · @0ad6af54
   The same product as loregarden at ~10x the size.
   Pass 1 (2026-10-13) read: AGENTS.md, CLAUDE.md.
   Pass 1 appends (2): lg-legacy-a-1 267.
   Pass 2 (2026-10-13) read docs/design/engine-interface.md. Appends (1): lg-legacy-a-2.
   STILL UNREAD — in rough priority order:
     provider/reference-provider/src/{server,store}.ts,
       example-provider/src/{jsonl,recap}.ts
     docs/design/2026-10-08-other-engines-out-of-core.md (22k)
     docs/design/2026-10-03-harnessd.md §§ Services
     [PASS 3 DONE 2026-10-13: council.md. Appends (1): lg-legacy-a-3.]

## 12. ThinkTerm — PASS 1 DONE, MULTIPLE PASSES REMAIN
RoversX/ThinkTerm, GPL-3.0-only (WezTerm parts stay MIT) — PATTERNS ONLY, DO NOT COPY CODE.
Read in pass 1: README (full). Appends (1): lg-legacy-a-1.
[PASS 2 DONE 2026-10-13: the agent_status source. Appends (1): lg-legacy-a-2.]

## 27 — PASS P: the host recomputes the verdict (2026-10-16)

One repository, MIT, nothing executed. Mentions lg-legacy-a-3.
"""

LINKS = """# Ref Projects and Brainstorming

## Ref GitHub Links 8/24/2026
- https://github.com/nextlevelbuilder/ui-ux-pro-max-skill (MIT — SURVEYED 2026-10-16, pass P. A UI/UX knowledge base.)
- https://github.com/mikjee/warpdrv (AGPL-3.0, resolved at source — NOT SURVEYED 2026-10-16, pass Q, declined.)
- (blobert) https://github.com/VAST-AI-Research/TripoSplat
- https://github.com/acme/oneliner (the survey drill itself, as a skill)
- https://github.com/acme/written (Apache-2.0, Python; the find is a REVERSAL: v1 reconciled ADD/UPDATE/DELETE, v3 is ADD-only with a dedupe on read, and the migration note says why: updates destroyed provenance faster than they fixed errors.)
- https://github.com/autonomous-ai/openharness (MIT, TypeScript, 16,869 files, @0ad6af54. The same product as ours at ten times the size: a value then AGGREGATES DIFFERENTLY IN TWO PLACES, excluded from total() because it would double count, and EXCLUDED ITEMS STAY REACHABLE SOMEWHERE.)
- https://github.com/acme/lean (MIT — SURVEYED 2026-10-17, pass Q. Long prose follows here for a while so that the word appears only well past the opening clause of the note, as the real notes do: three candidates excluded for failing.)
"""

BRIEF = "# UX brief\n\n## Addendum 1\n\nRead the instruments first.\n"


@pytest.fixture(name="ctx")
def ctx_fixture(isolated_db, tmp_path):
    session = Session(isolated_db)
    world = build_world(session)
    for n in (1, 2, 3):
        make_ticket(
            session,
            workspace_id=world.workspace_id,
            external_id=f"lg-legacy-a-{n}",
            title=f"legacy {n}",
        )
    directory = tmp_path / "ref_projects"
    directory.mkdir()
    (directory / "SURVEY_QUEUE.md").write_text(QUEUE)
    (directory / "ref_gh_links.md").write_text(LINKS)
    (directory / "UXBRIEF.md").write_text(BRIEF)
    yield session, directory
    session.close()


def _source(session, name):
    return session.exec(select(SurveySource).where(SurveySource.name == name)).one()


def _passes(session, source):
    return session.exec(
        select(SurveyPass)
        .where(SurveyPass.source_id == source.id)
        .order_by(col(SurveyPass.ordinal))
    ).all()


def _links(session, survey_pass):
    return {
        (link.legacy_ref, link.action, link.ticket_id is not None)
        for link in session.exec(
            select(SurveyPassTicket).where(SurveyPassTicket.pass_id == survey_pass.id)
        )
    }


def test_sections_reassemble_into_the_file():
    assert "".join(body for _, body in split_sections(QUEUE)) == QUEUE


def test_import_is_lossless_and_parses_the_deep_entries(ctx):
    session, directory = ctx
    report = import_legacy(session, directory, workspace_slug="loregarden")
    assert report.lossless == {"SURVEY_QUEUE.md": True, "ref_gh_links.md": True, "UXBRIEF.md": True}
    stored = session.exec(
        select(SurveyLegacySection)
        .where(SurveyLegacySection.origin == "SURVEY_QUEUE.md")
        .order_by(col(SurveyLegacySection.ordinal))
    ).all()
    assert "".join(row.body for row in stored) == QUEUE

    harness = _source(session, "openharness")
    assert (harness.url, harness.pinned_commit, harness.licence_name) == (
        "https://github.com/autonomous-ai/openharness", "0ad6af54", "MIT",
    )  # fmt: skip
    assert (harness.licence_basis, harness.licence_use) == (
        LicenceBasis.RECORDED_BY_HAND,
        LicenceUse.FULL,
    )
    passes = _passes(session, harness)
    assert [p.label for p in passes] == ["PASS 1", "PASS 2", "PASS 3"]
    assert _links(session, passes[0]) == {
        ("lg-legacy-a-1", SurveyTicketAction.APPENDED, True),
        ("267", SurveyTicketAction.APPENDED, False),  # a bare number is kept, never guessed
    }
    assert _links(session, passes[1]) == {("lg-legacy-a-2", SurveyTicketAction.APPENDED, True)}
    assert _links(session, passes[2]) == {("lg-legacy-a-3", SurveyTicketAction.APPENDED, True)}
    unread = session.exec(
        select(SurveyCoverage)
        .where(SurveyCoverage.source_id == harness.id)
        .order_by(col(SurveyCoverage.priority))
    ).all()
    assert [(u.path, u.priority) for u in unread] == [
        ("provider/reference-provider/src/{server,store}.ts", 1),
        ("docs/design/2026-10-08-other-engines-out-of-core.md", 2),
        ("docs/design/2026-10-03-harnessd.md", 3),
    ]
    assert "example-provider/src/{jsonl,recap}.ts" in unread[0].note
    assert not any(u.in_map for u in unread)

    thinkterm = _source(session, "ThinkTerm")
    assert (thinkterm.licence_name, thinkterm.licence_use) == (
        "GPL-3.0-only",
        LicenceUse.PATTERNS_ONLY,
    )
    assert [p.label for p in _passes(session, thinkterm)] == ["PASS 1", "PASS 2"]
    assert report.ticket_refs_unresolved == 1


def test_link_notes_set_outcome_and_tie_lettered_passes_to_sources(ctx):
    session, directory = ctx
    import_legacy(session, directory, workspace_slug="loregarden")
    surveyed = _source(session, "ui-ux-pro-max-skill")
    assert surveyed.status is SurveySourceStatus.DONE
    assert [p.label for p in _passes(session, surveyed)] == ["PASS P"]
    declined = _source(session, "warpdrv")
    assert declined.status is SurveySourceStatus.REFUSED
    assert "NOT SURVEYED" in declined.refused_reason
    assert declined.licence_use is LicenceUse.PATTERNS_ONLY
    candidate = _source(session, "TripoSplat")
    assert (candidate.status, candidate.approval_id) == (SurveySourceStatus.PROPOSED, None)


def test_a_second_import_changes_nothing(ctx):
    session, directory = ctx
    import_legacy(session, directory, workspace_slug="loregarden")
    counts = [
        len(session.exec(select(model)).all())
        for model in (
            SurveySource,
            SurveyPass,
            SurveyPassTicket,
            SurveyCoverage,
            SurveyLegacySection,
        )
    ]
    again = import_legacy(session, directory, workspace_slug="loregarden")
    assert (again.sources_created, again.passes_created, again.unread_items) == (0, 0, 0)
    assert [
        len(session.exec(select(model)).all())
        for model in (
            SurveySource,
            SurveyPass,
            SurveyPassTicket,
            SurveyCoverage,
            SurveyLegacySection,
        )
    ] == counts


def test_a_dry_run_writes_nothing(ctx):
    session, directory = ctx
    report = import_legacy(session, directory, workspace_slug="loregarden", commit=False)
    assert report.passes_created > 0
    assert session.exec(select(SurveySource)).all() == []
    assert session.exec(select(SurveyLegacySection)).all() == []


def test_a_word_in_the_survey_prose_does_not_refuse_a_surveyed_source(ctx):
    """Five surveyed sources were imported as refused: their notes say "excluded"
    about findings. Only the note's opening clause states the outcome."""
    session, directory = ctx
    import_legacy(session, directory, workspace_slug="loregarden")
    harness = _source(session, "openharness")
    assert (harness.status, harness.refused_reason) == (SurveySourceStatus.DONE, "")
    assert _source(session, "lean").status is SurveySourceStatus.DONE
    assert _source(session, "warpdrv").status is SurveySourceStatus.REFUSED


def test_a_written_finding_marks_a_source_surveyed_and_a_one_liner_does_not(ctx):
    session, directory = ctx
    import_legacy(session, directory, workspace_slug="loregarden")
    assert _source(session, "written").status is SurveySourceStatus.DONE
    assert _source(session, "oneliner").status is SurveySourceStatus.PROPOSED
