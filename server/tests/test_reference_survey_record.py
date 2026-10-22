"""Evidence, findings, retractions and unknowns.

The two cases at the top are the claims the survey filed and had to retract on
one day in October 2026, replayed against the record that now refuses them.
"""

from __future__ import annotations

import pytest
from loregarden.models.domain.survey_enums import (
    SurveyEpistemic,
    SurveyFindingClaim,
    SurveyFindingStatus,
    SurveyUnknownStatus,
)
from loregarden.services.reference_survey.errors import StaleRevision, SurveyError
from loregarden.services.reference_survey.record import (
    finding_evidence,
    open_unknown,
    record_evidence,
    record_finding,
    render_finding,
    retract_finding,
    unknown_evidence,
    update_unknown,
)
from loregarden.services.reference_survey.schemas import (
    EvidenceIn,
    FindingIn,
    RetractIn,
    UnknownOpenIn,
    UnknownUpdateIn,
)
from pydantic import ValidationError
from sqlmodel import Session
from tests.survey_support import build_world, running_pass, write_tree


@pytest.fixture(name="ctx")
def ctx_fixture(isolated_db, tmp_path):
    session = Session(isolated_db)
    world = build_world(session)
    tree = write_tree(
        tmp_path / "clone", {"client/src/a.ts": "a", "client/scripts/visual-qa.mjs": "v"}
    )
    source, survey_pass = running_pass(session, world, tree)
    yield session, source, survey_pass
    session.close()


def _evidence(session, source, survey_pass, **kw):
    fields = {
        "source_id": source.id,
        "pass_id": survey_pass.id,
        "epistemic": SurveyEpistemic.OBSERVED,
        "operation": "grep -rn visual client/src",
    } | kw
    return record_evidence(session, EvidenceIn(**fields))


def test_absence_from_an_incomplete_corpus_is_refused(ctx):
    """The visual-QA claim: grep of client/src, runner lived in client/scripts."""
    session, source, survey_pass = ctx
    grep = _evidence(
        session,
        source,
        survey_pass,
        result="no match",
        corpus=["client/src"],
        examined_count=412,
        total_count=412,
    )
    with pytest.raises(SurveyError, match="does not establish absence"):
        record_finding(
            session,
            FindingIn(
                source_id=source.id, pass_id=survey_pass.id, claim=SurveyFindingClaim.ABSENCE,
                epistemic=SurveyEpistemic.OBSERVED, statement="No visual or accessibility coverage",
                evidence_ids=[grep.id],
            ),
        )  # fmt: skip
    found = record_finding(
        session,
        FindingIn(
            source_id=source.id, pass_id=survey_pass.id, claim=SurveyFindingClaim.NOT_FOUND,
            epistemic=SurveyEpistemic.OBSERVED, statement="No visual-QA runner", evidence_ids=[grep.id],
        ),
    )  # fmt: skip
    line = render_finding(found, finding_evidence(session, found.id))
    assert line.startswith("**Not found** in `client/src` (412 of 412 examined)")
    assert "does not establish absence" in line


def test_absence_from_an_accessibility_read_of_partial_output_is_refused(ctx):
    """The nav-links claim: the tree omitted clipped labels the DOM carried."""
    session, source, survey_pass = ctx
    a11y = _evidence(
        session, source, survey_pass, operation="read accessibility tree of /", corpus=["accessibility tree"],
        limitations="visually-hidden labels may not surface",
    )  # fmt: skip
    with pytest.raises(SurveyError):
        record_finding(
            session,
            FindingIn(
                source_id=source.id, claim=SurveyFindingClaim.ABSENCE, epistemic=SurveyEpistemic.OBSERVED,
                statement="12 nav destinations have no accessible name", evidence_ids=[a11y.id],
            ),
        )  # fmt: skip


def test_absence_needs_a_complete_corpus_with_a_basis(ctx):
    session, source, survey_pass = ctx
    with pytest.raises(ValidationError, match="completeness_basis"):
        EvidenceIn(
            source_id=source.id,
            epistemic=SurveyEpistemic.OBSERVED,
            operation="x",
            corpus=["."],
            corpus_complete=True,
        )
    with pytest.raises(ValidationError, match="Exhausting a page is not completeness"):
        EvidenceIn(
            source_id=source.id, epistemic=SurveyEpistemic.OBSERVED, operation="x", corpus=["."],
            corpus_complete=True, completeness_basis="whole tree", examined_count=23, total_count=200,
        )  # fmt: skip
    full = _evidence(
        session, source, survey_pass, operation="grep -rn visual .", corpus=["."], corpus_complete=True,
        completeness_basis="the whole tree at the pinned commit", examined_count=2, total_count=2,
    )  # fmt: skip
    absent = record_finding(
        session,
        FindingIn(
            source_id=source.id, claim=SurveyFindingClaim.ABSENCE, epistemic=SurveyEpistemic.OBSERVED,
            statement="No telemetry", evidence_ids=[full.id],
        ),
    )  # fmt: skip
    assert render_finding(absent, [full]).startswith("**Absent** (searched a complete corpus")


def test_a_count_without_its_denominator_is_refused():
    with pytest.raises(ValidationError, match="denominator"):
        EvidenceIn(
            source_id="s", epistemic=SurveyEpistemic.OBSERVED, operation="x", examined_count=23
        )


def test_an_observed_finding_cannot_rest_on_an_inference(ctx):
    session, source, survey_pass = ctx
    guess = _evidence(
        session, source, survey_pass, epistemic=SurveyEpistemic.INFERRED, operation="read dir names"
    )
    with pytest.raises(SurveyError, match="inferences"):
        record_finding(
            session,
            FindingIn(
                source_id=source.id, claim=SurveyFindingClaim.ASSERTION, epistemic=SurveyEpistemic.OBSERVED,
                statement="rules live in rules/", evidence_ids=[guess.id],
            ),
        )  # fmt: skip
    inferred = record_finding(
        session,
        FindingIn(
            source_id=source.id, claim=SurveyFindingClaim.ASSERTION, epistemic=SurveyEpistemic.INFERRED,
            statement="rules live in rules/", evidence_ids=[guess.id],
        ),
    )  # fmt: skip
    assert render_finding(inferred, [guess]).startswith("**Inferred, not observed**")


def test_retraction_keeps_the_row_with_why_and_what_replaced_it(ctx):
    session, source, survey_pass = ctx
    ev = _evidence(session, source, survey_pass, corpus=["client/src"])

    def finding(claim):
        return record_finding(
            session,
            FindingIn(
                source_id=source.id,
                claim=claim,
                epistemic=SurveyEpistemic.OBSERVED,
                statement="s",
                evidence_ids=[ev.id],
            ),
        )

    wrong = finding(SurveyFindingClaim.NOT_FOUND)
    right = finding(SurveyFindingClaim.ASSERTION)
    retracted = retract_finding(
        session,
        RetractIn(
            finding_id=wrong.id, reason="runner is in client/scripts", replaced_by_id=right.id
        ),
    )
    assert retracted.status is SurveyFindingStatus.RETRACTED
    assert (retracted.retracted_reason, retracted.replaced_by_id) == (
        "runner is in client/scripts",
        right.id,
    )
    assert retracted.retracted_at is not None
    assert render_finding(retracted, [ev]).startswith("~~")
    with pytest.raises(SurveyError, match="already retracted"):
        retract_finding(session, RetractIn(finding_id=wrong.id, reason="again"))


def test_unknowns_keep_contradicting_evidence_and_resolve_only_on_observation(ctx):
    session, source, survey_pass = ctx
    one = _evidence(session, source, survey_pass, operation="instrument A says 18")
    two = _evidence(session, source, survey_pass, operation="instrument B says 0")
    guess = _evidence(
        session, source, survey_pass, epistemic=SurveyEpistemic.INFERRED, operation="probably 18"
    )
    unknown = open_unknown(
        session,
        UnknownOpenIn(
            source_id=source.id, pass_id=survey_pass.id, question="How many rules?",
            authority_required="their test run", supporting=[one.id], contradicting=[two.id],
        ),
    )  # fmt: skip
    stances = {
        link.evidence_id: link.stance.value for link in unknown_evidence(session, unknown.id)
    }
    assert stances == {one.id: "supporting", two.id: "contradicting"}
    with pytest.raises(SurveyError, match="Only observed evidence"):
        update_unknown(
            session,
            UnknownUpdateIn(
                unknown_id=unknown.id,
                revision=1,
                resolve={"evidence_id": guess.id, "resolution": "18"},
            ),
        )
    resolved = update_unknown(
        session,
        UnknownUpdateIn(
            unknown_id=unknown.id, revision=1, resolve={"evidence_id": one.id, "resolution": "18"}
        ),
    )
    assert (resolved.status, resolved.revision, resolved.resolving_evidence_id) == (
        SurveyUnknownStatus.RESOLVED,
        2,
        one.id,
    )
    assert len(unknown_evidence(session, unknown.id)) == 2


def test_a_stale_revision_is_refused_so_the_writer_rereads(ctx, isolated_db):
    session, source, survey_pass = ctx
    ev = _evidence(session, source, survey_pass)
    unknown = open_unknown(
        session, UnknownOpenIn(source_id=source.id, question="q", authority_required="a")
    )
    with Session(isolated_db) as other:
        update_unknown(
            other, UnknownUpdateIn(unknown_id=unknown.id, revision=1, supporting=[ev.id])
        )
    with pytest.raises(StaleRevision) as caught:
        update_unknown(
            session, UnknownUpdateIn(unknown_id=unknown.id, revision=1, contradicting=[ev.id])
        )
    assert caught.value.current == 2
    assert [link.stance.value for link in unknown_evidence(session, unknown.id)] == ["supporting"]
