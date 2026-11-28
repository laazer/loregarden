"""Asserted-not-proven: the record, its validation, and its lifecycle.

Spec `6d686e57` R1, R2, R3, R8, R9, R10 (ticket AC1, AC2, AC3, AC5, AC6, AC7,
AC8, AC9, AC12(b)(g)). The enumeration, the MCP response shape, the bounded
scan and the PR section are pinned next door in
`test_asserted_not_proven_listing.py`; the `ceiling:` gate in
`test_ceiling_marker_gate.py`.

What these hold, in one line each:

- a claim observed once and not proven is a RECORDED state, distinct from true
  and from unrecorded, and recording one is the normal path;
- the artifact it is OWED is a resolvable pointer, never prose — the bar
  `services/fix_coverage.py` already holds, applied to a second record;
- the fallback is NAMED and says it is NOT BUILT;
- a guarantee is split into the part proven today and the part owed;
- every claim keeps its upstream identifier and its citation, a drafted default
  is frozen and challenged by an artifact class rather than a role, and a defect
  found in a document this one depends on is ROUTED;
- a retried write cannot inflate the count that is the feature, an amendment
  keeps its history, and the dependent-document obligation RAISES rather than
  warning.
"""

from __future__ import annotations

import json

import pytest
from loregarden.mcp.operation_tools import OPERATION_TOOLS
from loregarden.mcp.tool_ids import (
    AUTO_APPROVED_MCP_TOOLS,
    STAGE_DEFAULT_MCP_TOOLS,
    TICKET_SCOPED_MCP_TOOLS,
    McpTool,
)
from loregarden.mcp.tools import execute_tool
from loregarden.models.domain import (
    Artifact,
    ArtifactKind,
    AssertedClaimStatus,
    AssertedNotProvenKind,
    ClaimOperation,
    ClaimOutcome,
    Workspace,
)
from loregarden.services.asserted_not_proven.listing import open_claims
from loregarden.services.asserted_not_proven.record import (
    amend_claim,
    claim_slug,
    close_claim,
    record_measurement,
    register_claim,
)
from loregarden.services.asserted_not_proven.schemas import (
    AssertedNotProvenClaim,
    parse_claim,
)
from loregarden.services.fix_coverage import MIN_ANSWER, MIN_WAIVER
from sqlmodel import col, select
from tests.asserted_not_proven_helpers import (
    BEHAVIOUR_CLAIM,
    CODE_SITE_CLAIM,
    DOCUMENT_DEFECT_CLAIM,
    DRAFTED_DEFAULT_CLAIM,
    artifact_payload,
    claim_values,
    make_ticket,
    make_workspace,
    paths_this_change_adds,
)
from tests.repo_paths import REPO_ROOT

RECORD_TOOL = "loregarden_record_asserted_not_proven"


@pytest.fixture(name="ticket")
def ticket_fixture(db_session, tmp_path):
    workspace = make_workspace(db_session, str(tmp_path))
    return make_ticket(db_session, workspace)


def _refusal(values: dict) -> str:
    with pytest.raises(ValueError) as excinfo:
        parse_claim(values)
    return str(excinfo.value)


def _rows(session, ticket) -> list[Artifact]:
    return list(
        session.exec(
            select(Artifact)
            .where(
                Artifact.ticket_id == ticket.id,
                Artifact.kind == ArtifactKind.ASSERTED_NOT_PROVEN,
            )
            .order_by(col(Artifact.created_at).asc())
        ).all()
    )


# =========================================================================== #
# R1 — ASSERTED_NOT_PROVEN is a recorded state, distinct from true and from
#      unrecorded (AC1)
# =========================================================================== #


def test_the_artifact_kind_exists_with_its_settled_spelling():
    """R1.1. The spelling is what every reader and the Outputs filter match on."""
    assert ArtifactKind.ASSERTED_NOT_PROVEN == "asserted_not_proven"


def test_the_artifacts_kind_column_is_still_an_open_string():
    """R1.2. The enum covers one tier of an open column, so no migration is owed.

    A migration is the one irreversible part of this change and nothing needs
    it; this is the assertion that keeps a later hand from adding one.
    """
    assert Artifact.model_fields["kind"].annotation is str


def test_the_change_ships_no_migration():
    """R1.2's other half, checked against the tree rather than the model.

    Attributed to THIS ticket's commits, not to every commit the branch carries:
    the branch has merged `integration/lg-code-knowledge-273`, which brings
    lg-code-knowledge-299's `db/versions/code_signals.py` with it. Charging that
    to this change would fail an assertion about a migration this ticket did not
    write, and the fix would be to weaken the assertion. See
    `asserted_not_proven_helpers.paths_this_change_adds`, which is fail-closed.
    """
    root = REPO_ROOT
    offenders = paths_this_change_adds(root, ("server/loregarden/db/versions/",))
    if offenders is None:
        pytest.skip("no comparable origin/main in this checkout")
    assert offenders == [], f"this change must ship no migration; it adds {offenders}"


def test_the_two_vocabularies_are_enums_with_exactly_their_members():
    """R1.3. A bare `str` for either fails the py-organization gate."""
    assert [member.value for member in AssertedNotProvenKind] == [
        "behaviour",
        "code_site",
        "drafted_default",
        "document_defect",
    ]
    assert [member.value for member in ClaimOutcome] == [
        "proven",
        "falsified",
        "ceiling_reached",
    ]


def test_the_status_filter_is_a_vocabulary_too():
    """R5's `status` parameter is a closed set, so it is an enum, not a string.

    Named `AssertedClaimStatus` rather than the spec's `ClaimStatus`: that name
    is already taken in `models/domain/survey_enums.py` for how far a capability
    claim about an OUTSIDE project was checked. Recorded as a test-design
    correction on this ticket.
    """
    assert [member.value for member in AssertedClaimStatus] == [
        "open",
        "closable",
        "settled",
        "all",
    ]


def test_the_write_operations_are_a_vocabulary_too():
    assert [member.value for member in ClaimOperation] == [
        "record",
        "amend",
        "measure",
        "close",
    ]


def test_a_well_formed_claim_round_trips_every_field(db_session, ticket):
    """R1.4. A record that loses a field on the way back is not a record."""
    claim = parse_claim(claim_values())
    written = register_claim(db_session, ticket, claim)

    assert artifact_payload(db_session, written.artifact_id) == claim.model_dump(mode="json")


def test_three_states_are_distinguishable_for_the_same_statement(db_session, ticket):
    """R1.5. Unrecorded, asserted-not-proven, and settled are three answers.

    This is AC1's whole point: "we have not checked" must not read the same as
    "it is true", and neither may read the same as "nobody filed anything".
    """
    claim = parse_claim(claim_values())
    slug = claim_slug(claim.claim)

    assert _rows(db_session, ticket) == [], "unrecorded: there is no row"

    register_claim(db_session, ticket, claim)
    recorded = artifact_payload(db_session, _rows(db_session, ticket)[-1].id)
    assert recorded.get("outcome") in (None, ""), "asserted-not-proven: a row with no outcome"

    close_claim(
        db_session,
        ticket,
        slug,
        outcome=ClaimOutcome.PROVEN,
        evidence=BEHAVIOUR_CLAIM["owed_artifact"],
        dependent_document_answers={},
        evidence_exists=True,
    )
    settled = artifact_payload(db_session, _rows(db_session, ticket)[-1].id)
    assert settled["outcome"] == ClaimOutcome.PROVEN.value
    assert settled != recorded, "settled must not be equal to asserted-not-proven"


# =========================================================================== #
# R2 — the owed artifact is a resolvable pointer, never prose (AC2, AC12(b))
# =========================================================================== #


def test_a_test_reference_is_an_accepted_owed_artifact():
    """R2.1."""
    claim = parse_claim(claim_values(owed_artifact="server/tests/test_x.py::test_y"))
    assert claim.owed_artifact == "server/tests/test_x.py::test_y"


def test_a_code_site_line_is_an_accepted_owed_artifact():
    """R2.2."""
    claim = parse_claim(
        claim_values(CODE_SITE_CLAIM, owed_artifact="server/loregarden/services/foo.py:412")
    )
    assert claim.owed_artifact == "server/loregarden/services/foo.py:412"


def test_prose_is_refused_as_an_owed_artifact():
    """R2.3. Prose cannot be resolved, so AC4's enumeration cannot act on it.

    The message names BOTH accepted forms: a refusal that does not say what
    would be accepted costs the writer a round-trip they cannot win alone.
    """
    message = _refusal(claim_values(owed_artifact="a test proving the lock holds"))
    assert "path/to/test_file.py::test_name" in message
    assert "path/to/file.py:<line>" in message


@pytest.mark.parametrize("line", ["0", "-3"])
def test_a_non_positive_line_number_is_refused(line):
    """R2.4. `foo.py:0` resolves to nothing; it only looks resolvable."""
    assert _refusal(
        claim_values(CODE_SITE_CLAIM, owed_artifact=f"server/loregarden/services/foo.py:{line}")
    )


def test_a_code_site_member_may_not_owe_a_test_reference():
    """R2.5. The two forms are not interchangeable: each kind owes its own."""
    message = _refusal(
        claim_values(CODE_SITE_CLAIM, owed_artifact="server/tests/test_x.py::test_y")
    )
    assert AssertedNotProvenKind.CODE_SITE.value in message
    assert "path/to/file.py:<line>" in message


def test_a_behaviour_member_may_not_owe_a_code_site_line():
    """R2.5's other half."""
    message = _refusal(claim_values(owed_artifact="server/loregarden/services/foo.py:412"))
    assert AssertedNotProvenKind.BEHAVIOUR.value in message
    assert "path/to/test_file.py::test_name" in message


def test_prose_lives_in_owed_description_and_never_replaces_the_pointer():
    """R2 / AC2. A measurement or a reproduction that is not a file is described
    in a second field — which never substitutes for the resolvable pointer."""
    claim = parse_claim(
        claim_values(
            owed_description="a reproduction of the race under a 20-way parallel push",
        )
    )
    assert claim.owed_description
    assert claim.owed_artifact == BEHAVIOUR_CLAIM["owed_artifact"]

    assert _refusal(
        claim_values(
            owed_artifact="",
            owed_description="a reproduction of the race under a 20-way parallel push",
        )
    ), "owed_description must not stand in for the pointer"


def test_an_environment_it_needs_must_name_what_was_split_out_and_landed():
    """R2.6 / AC2. The portable subset is separated out and landed NOW — that is
    the half of the guarantee this record is able to prove today."""
    accepted = parse_claim(
        claim_values(
            owed_environment="linux, cgroup v2, a real container runtime",
            portable_subset="the path parsing and the refusal message, landed here",
            proven_today="the refusal message and the path parsing are covered on every platform",
        )
    )
    assert accepted.portable_subset

    message = _refusal(
        claim_values(
            owed_environment="linux, cgroup v2, a real container runtime",
            portable_subset="",
            proven_today="the refusal message is covered on every platform",
        )
    )
    assert "portable_subset" in message


def test_a_portable_subset_with_no_environment_is_refused():
    """R2.7. A fully portable claim has nothing to split, so the pair is a lie."""
    assert _refusal(
        claim_values(portable_subset="the path parsing and the refusal message, landed here")
    )


def test_a_placeholder_environment_is_refused():
    """R2.8. "linux" is five characters and tells the next reader nothing."""
    message = _refusal(claim_values(owed_environment="linux", proven_today="the parsing"))
    assert str(MIN_WAIVER) in message or "placeholder" in message.lower()


# =========================================================================== #
# R3 — the fallback is NAMED and explicitly NOT BUILT (AC3)
# =========================================================================== #


def test_a_fallback_that_says_it_is_not_built_is_accepted():
    """R3.1."""
    claim = parse_claim(
        claim_values(fallback="fall back to a per-ticket advisory lock — named, not built")
    )
    assert "not built" in claim.fallback


def test_a_fallback_that_does_not_assert_it_is_unbuilt_is_refused():
    """R3.2. A fallback already built is not a fallback; it is the implementation,
    and the claim should not have been filed."""
    message = _refusal(claim_values(fallback="fall back to a per-ticket advisory lock"))
    assert "not built" in message.lower()


@pytest.mark.parametrize(
    "base",
    [BEHAVIOUR_CLAIM, CODE_SITE_CLAIM, DRAFTED_DEFAULT_CLAIM, DOCUMENT_DEFECT_CLAIM],
    ids=["behaviour", "code_site", "drafted_default", "document_defect"],
)
def test_an_empty_fallback_is_refused_on_every_kind(base):
    """R3.3. Every member of the class names where it goes if the claim is false."""
    assert _refusal(claim_values(base, fallback=""))


def test_a_multi_line_field_is_refused():
    """R3.4. The PR section reads a field to the end of its line, as `FC` does."""
    assert _refusal(claim_values(fallback="not built\nand also something else"))


def test_the_not_built_check_is_case_insensitive():
    claim = parse_claim(claim_values(fallback="a per-ticket advisory lock: NAMED, NOT BUILT"))
    assert claim.fallback


# =========================================================================== #
# R8 — a guarantee is split into the proven part and the owed part (AC5)
# =========================================================================== #


def test_a_claim_that_cannot_be_proven_here_must_say_what_was_proven_here():
    """R8.1. Half a guarantee presented whole is the failure AC5 names."""
    message = _refusal(
        claim_values(
            owed_environment="linux, cgroup v2, a real container runtime",
            portable_subset="the path parsing and the refusal message, landed here",
            proven_today="",
        )
    )
    assert "proven_today" in message


def test_a_fully_portable_claim_may_leave_proven_today_empty():
    """R8.3. Nothing is owed to another environment, so nothing is split."""
    claim = parse_claim(claim_values(owed_environment="", proven_today=""))
    assert claim.proven_today == ""


def test_the_two_halves_are_separate_fields_and_never_one_sentence():
    """R8.2's model half: nothing concatenates the proven and the owed.

    The rendered half — the MCP response and the PR section — is asserted in
    `test_asserted_not_proven_listing.py`.
    """
    claim = parse_claim(
        claim_values(
            owed_environment="linux, cgroup v2, a real container runtime",
            portable_subset="the path parsing and the refusal message, landed here",
            proven_today="the refusal message and the path parsing hold on every platform",
        )
    )
    payload = claim.model_dump(mode="json")
    assert payload["proven_today"] not in payload["owed_artifact"]
    assert payload["owed_artifact"] not in payload["proven_today"]
    assert {"proven_today", "owed_artifact", "owed_environment", "portable_subset"} <= set(payload)


# =========================================================================== #
# R9 — upstream id, citation, frozen drafted defaults, routed defects
#      (AC6, AC7, AC9)
# =========================================================================== #


@pytest.mark.parametrize("field", ["upstream_id", "citation"])
@pytest.mark.parametrize(
    "base",
    [BEHAVIOUR_CLAIM, CODE_SITE_CLAIM, DRAFTED_DEFAULT_CLAIM, DOCUMENT_DEFECT_CLAIM],
    ids=["behaviour", "code_site", "drafted_default", "document_defect"],
)
def test_every_claim_keeps_its_upstream_identifier_and_its_citation(base, field):
    """R9.1 / AC6. A reader can ask who decided it, and against what, without
    leaving the record."""
    message = _refusal(claim_values(base, **{field: ""}))
    assert field in message


def test_a_drafted_default_that_is_not_frozen_is_not_a_member():
    """R9.2 / AC7. "Drafted" without "frozen" is just a number someone typed."""
    message = _refusal(claim_values(DRAFTED_DEFAULT_CLAIM, frozen=False))
    assert "frozen" in message


def test_a_role_is_refused_as_a_challenger():
    """R9.3 / AC7, verbatim: the challenger is an artifact class, not a role.

    Both halves of the check: a bare unbackticked token has the wrong shape, and
    a registered agent id is refused even when it is backticked.
    """
    assert _refusal(claim_values(DRAFTED_DEFAULT_CLAIM, challenger="backend_implementer"))
    assert _refusal(claim_values(DRAFTED_DEFAULT_CLAIM, challenger="`backend_implementer`"))


@pytest.mark.parametrize("challenger", ["`spec-review`", "`py_ceiling_marker_check`"])
def test_an_artifact_class_or_a_gate_is_accepted_as_a_challenger(challenger):
    """R9.3's accepted half."""
    claim = parse_claim(claim_values(DRAFTED_DEFAULT_CLAIM, challenger=challenger))
    assert claim.challenger == challenger


def test_a_drafted_default_with_no_challenger_is_refused():
    assert _refusal(claim_values(DRAFTED_DEFAULT_CLAIM, challenger=""))


def test_a_document_defect_must_be_routed_to_a_ticket(db_session, ticket):
    """R9.4 / AC9. A defect recorded and not routed is silently absorbed."""
    assert _refusal(claim_values(DOCUMENT_DEFECT_CLAIM, routed_ticket=""))

    with pytest.raises(ValueError) as excinfo:
        register_claim(
            db_session,
            ticket,
            parse_claim(claim_values(DOCUMENT_DEFECT_CLAIM, routed_ticket="137")),
        )
    assert "137" in str(excinfo.value), "resolve_one must refuse a bare number, naming candidates"

    written = register_claim(
        db_session,
        ticket,
        parse_claim(claim_values(DOCUMENT_DEFECT_CLAIM, routed_ticket=ticket.external_id)),
    )
    assert written.artifact_id


def test_shape_is_validated_in_the_model_and_resolution_at_the_service():
    """R9.4's seam. `resolve_one` performs I/O, so the bare model cannot run it;
    both refusals must exist or one of them is the only one anyone relies on."""
    parsed = parse_claim(claim_values(DOCUMENT_DEFECT_CLAIM, routed_ticket="137"))
    assert parsed.routed_ticket == "137", "the model validates shape, not resolution"


@pytest.mark.parametrize(
    "field,value",
    [("frozen", True), ("challenger", "`spec-review`"), ("routed_ticket", "lg-x-1")],
)
@pytest.mark.parametrize("base", [BEHAVIOUR_CLAIM, CODE_SITE_CLAIM], ids=["behaviour", "code_site"])
def test_a_kind_specific_field_on_the_wrong_kind_is_refused(base, field, value):
    """R9.5. Each kind carries its own required set; a stray field is a writer
    who has the wrong kind, and silently keeping it hides that."""
    message = _refusal(claim_values(base, **{field: value}))
    assert base["member_kind"] in message


def test_an_unknown_field_is_refused():
    """`extra="forbid"`, as `FC` has: a typo'd field name is not a record."""
    assert _refusal(claim_values(owed_artefact="server/tests/test_x.py::test_y"))


def test_a_code_site_member_must_name_its_ceiling_and_upgrade_path():
    """AC11. The ceiling is the field that makes AC4's enumeration useful:
    a quantity can be checked against a measurement and the member CLOSED, where
    "this is simplified" can only accumulate."""
    for field in ("ceiling", "ceiling_quantity", "upgrade_path"):
        blank = None if field == "ceiling_quantity" else ""
        message = _refusal(claim_values(CODE_SITE_CLAIM, **{field: blank}))
        assert field in message


# =========================================================================== #
# R10 — record / amend / measure / close, idempotency, and the AC8 refusal
# =========================================================================== #


def test_an_identical_rewrite_writes_nothing_and_says_so(db_session, ticket):
    """R10.1 / AC12(g). A retried MCP call must not inflate the count that is
    the feature. Double-fire is the failure mode of every MCP write here."""
    claim = parse_claim(claim_values())
    first = register_claim(db_session, ticket, claim)
    second = register_claim(db_session, ticket, parse_claim(claim_values()))

    assert second.artifact_id == first.artifact_id
    assert second.amended is False
    assert len(_rows(db_session, ticket)) == 1


def test_an_amendment_keeps_its_history_and_still_counts_once(db_session, ticket):
    """R10.2. The row count AND the member count together are the only pair that
    tells amendment-as-a-new-row apart from amendment-in-place.

    AC4 is explicit that the class grows by amendment, each new member joining
    with its qualifying reason — so the reasons must survive.
    """
    register_claim(db_session, ticket, parse_claim(claim_values()))
    second = amend_claim(
        db_session,
        ticket,
        parse_claim(claim_values(qualifies_because="re-observed under a 20-way parallel push")),
        dependent_document_answers={},
    )
    rows = _rows(db_session, ticket)
    assert len(rows) == 2
    assert second.amended is True
    assert artifact_payload(db_session, rows[1].id)["amends"] == rows[0].id

    amend_claim(
        db_session,
        ticket,
        parse_claim(claim_values(qualifies_because="re-observed a third time, same shape")),
        dependent_document_answers={},
    )
    assert len(_rows(db_session, ticket)) == 3

    listed = open_claims(
        db_session,
        workspace_slug=_slug(db_session, ticket),
        status=AssertedClaimStatus.ALL,
        repo_root=None,
    )
    members = [m for group in listed["groups"] for m in group["members"]]
    assert len(members) == 1, "amendment history must never inflate the count"
    assert members[0]["qualifies_because"] == "re-observed a third time, same shape"


def _slug(session, ticket) -> str:
    workspace = session.get(Workspace, ticket.workspace_id)
    return workspace.slug


def test_closing_as_proven_needs_evidence_that_exists(db_session, ticket):
    """R10.3. "Proven" against a test nobody wrote is the state AC1 exists to
    make unavailable."""
    register_claim(db_session, ticket, parse_claim(claim_values()))
    with pytest.raises(ValueError) as excinfo:
        close_claim(
            db_session,
            ticket,
            claim_slug(BEHAVIOUR_CLAIM["claim"]),
            outcome=ClaimOutcome.PROVEN,
            evidence="server/tests/test_x.py::test_missing",
            dependent_document_answers={},
            evidence_exists=False,
        )
    assert "test_missing" in str(excinfo.value)


def test_closing_raises_on_an_unanswered_dependent_document(db_session, ticket):
    """R10.4 / AC8. Updating a dependent document is an IMPLEMENTATION
    OBLIGATION tracked as work, not a convention.

    This asserts it RAISES. A logged warning fails this criterion: a warning in
    an unattended run is a failure nobody sees, which is the repo's own rule.
    """
    register_claim(
        db_session,
        ticket,
        parse_claim(claim_values(dependent_documents=["AGENTS.md §WHERE TO LOOK"])),
    )
    with pytest.raises(ValueError) as excinfo:
        close_claim(
            db_session,
            ticket,
            claim_slug(BEHAVIOUR_CLAIM["claim"]),
            outcome=ClaimOutcome.PROVEN,
            evidence=BEHAVIOUR_CLAIM["owed_artifact"],
            dependent_document_answers={},
            evidence_exists=True,
        )
    assert "AGENTS.md §WHERE TO LOOK" in str(excinfo.value)


def test_amending_enforces_the_same_dependent_document_refusal(db_session, ticket):
    """R10.5. The obligation attaches to the change, not to the closing."""
    register_claim(
        db_session,
        ticket,
        parse_claim(claim_values(dependent_documents=["AGENTS.md §WHERE TO LOOK"])),
    )
    with pytest.raises(ValueError) as excinfo:
        amend_claim(
            db_session,
            ticket,
            parse_claim(
                claim_values(
                    dependent_documents=["AGENTS.md §WHERE TO LOOK"],
                    qualifies_because="re-observed under a 20-way parallel push",
                )
            ),
            dependent_document_answers={},
        )
    assert "AGENTS.md §WHERE TO LOOK" in str(excinfo.value)


@pytest.mark.parametrize(
    "answer",
    [
        "the WHERE TO LOOK row now names services/asserted_not_proven",
        "lg-code-knowledge-1055",
    ],
    ids=["an edit that was made", "a ticket that was filed"],
)
def test_an_answered_dependent_document_lets_the_change_through(db_session, ticket, answer):
    """AC8's accepted half: either the edit was made, or the work is filed."""
    document = "AGENTS.md §WHERE TO LOOK"
    register_claim(
        db_session,
        ticket,
        parse_claim(claim_values(dependent_documents=[document])),
    )
    if answer == "lg-code-knowledge-1055":
        answer = ticket.external_id
    written = amend_claim(
        db_session,
        ticket,
        parse_claim(
            claim_values(
                dependent_documents=[document],
                qualifies_because="re-observed under a 20-way parallel push",
            )
        ),
        dependent_document_answers={document: answer},
    )
    assert written.amended is True


def test_a_thin_dependent_document_answer_is_refused(db_session, ticket):
    """The MIN_ANSWER floor, as every other answer field here has: "done" is not
    a record that the dependent document was updated."""
    document = "AGENTS.md §WHERE TO LOOK"
    register_claim(db_session, ticket, parse_claim(claim_values(dependent_documents=[document])))
    with pytest.raises(ValueError) as excinfo:
        amend_claim(
            db_session,
            ticket,
            parse_claim(
                claim_values(
                    dependent_documents=[document],
                    qualifies_because="re-observed under a 20-way parallel push",
                )
            ),
            dependent_document_answers={document: "done"},
        )
    assert str(MIN_ANSWER) in str(excinfo.value) or "placeholder" in str(excinfo.value).lower()


def test_closing_as_falsified_records_the_fallback_and_builds_nothing(db_session, ticket):
    """R10.6 / AC3. The alternative is recorded without being speculatively
    implemented — closing says the fallback now applies, and that is all."""
    claim = parse_claim(claim_values())
    register_claim(db_session, ticket, claim)
    written = close_claim(
        db_session,
        ticket,
        claim_slug(claim.claim),
        outcome=ClaimOutcome.FALSIFIED,
        evidence="",
        dependent_document_answers={},
        evidence_exists=False,
    )
    payload = artifact_payload(db_session, written.artifact_id)
    assert payload["outcome"] == ClaimOutcome.FALSIFIED.value
    assert payload["fallback"] == claim.fallback


def test_ceiling_reached_needs_a_recorded_measurement(db_session, ticket):
    """R10.7 / AC12(a). Nothing in this repo measures production load, so
    "exceeded by measured load" is a measurement RECORDED against the claim.
    With none, the member stays open; no live probe is built."""
    claim = parse_claim(claim_values(CODE_SITE_CLAIM))
    register_claim(db_session, ticket, claim)
    slug = claim_slug(claim.claim)

    with pytest.raises(ValueError) as excinfo:
        close_claim(
            db_session,
            ticket,
            slug,
            outcome=ClaimOutcome.CEILING_REACHED,
            evidence="",
            dependent_document_answers={},
            evidence_exists=False,
        )
    assert "measurement" in str(excinfo.value).lower()

    record_measurement(db_session, ticket, slug, value=612.0, unit="artifacts")
    written = close_claim(
        db_session,
        ticket,
        slug,
        outcome=ClaimOutcome.CEILING_REACHED,
        evidence="",
        dependent_document_answers={},
        evidence_exists=False,
    )
    assert artifact_payload(db_session, written.artifact_id)["outcome"] == "ceiling_reached"


def test_a_measurement_below_the_ceiling_does_not_close_it(db_session, ticket):
    """The ceiling is a quantity, not a mood: 400 of 500 is still holding."""
    claim = parse_claim(claim_values(CODE_SITE_CLAIM))
    register_claim(db_session, ticket, claim)
    slug = claim_slug(claim.claim)
    record_measurement(db_session, ticket, slug, value=400.0, unit="artifacts")

    with pytest.raises(ValueError):
        close_claim(
            db_session,
            ticket,
            slug,
            outcome=ClaimOutcome.CEILING_REACHED,
            evidence="",
            dependent_document_answers={},
            evidence_exists=False,
        )


def test_a_measurement_in_another_unit_cannot_close_a_ceiling(db_session, ticket):
    """612 milliseconds does not exceed 500 artifacts. Comparing a quantity to a
    quantity in another unit is how a ceiling gets closed for free."""
    claim = parse_claim(claim_values(CODE_SITE_CLAIM))
    register_claim(db_session, ticket, claim)
    slug = claim_slug(claim.claim)
    with pytest.raises(ValueError) as excinfo:
        record_measurement(db_session, ticket, slug, value=612.0, unit="milliseconds")
    assert "artifacts" in str(excinfo.value)


def test_a_measurement_on_a_document_shaped_member_is_refused(db_session, ticket):
    """Only a CODE_SITE member has a ceiling to measure against (R5)."""
    claim = parse_claim(claim_values())
    register_claim(db_session, ticket, claim)
    with pytest.raises(ValueError):
        record_measurement(db_session, ticket, claim_slug(claim.claim), value=1.0, unit="x")


def test_a_write_against_an_unrecorded_claim_is_refused(db_session, ticket):
    """Amend, measure and close all name an existing identity; a typo in the
    slug must not silently create a second member."""
    for call in (
        lambda: record_measurement(db_session, ticket, "no-such-claim", value=1.0, unit="x"),
        lambda: close_claim(
            db_session,
            ticket,
            "no-such-claim",
            outcome=ClaimOutcome.FALSIFIED,
            evidence="",
            dependent_document_answers={},
            evidence_exists=False,
        ),
    ):
        with pytest.raises(ValueError) as excinfo:
            call()
        assert "no-such-claim" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# R10.9, R5.9 — the two tools are registered everywhere a tool must be
# --------------------------------------------------------------------------- #


def test_the_write_tool_is_classified_in_all_four_places():
    """R10.9. `TI` classifies a tool in four places; a tool missing from one of
    them is unreachable, or reachable and ungated."""
    tool = McpTool.RECORD_ASSERTED_NOT_PROVEN
    assert tool.value == RECORD_TOOL
    assert tool in STAGE_DEFAULT_MCP_TOOLS
    assert tool in AUTO_APPROVED_MCP_TOOLS
    assert tool in TICKET_SCOPED_MCP_TOOLS
    assert tool.value in OPERATION_TOOLS


def test_the_read_tool_is_registered_and_auto_approves():
    """R5.9. The enumeration is the only operator-readable surface; a read that
    needs an approval card is a surface nobody will use."""
    tool = McpTool.OPEN_CLAIMS
    assert tool.value == "loregarden_open_claims"
    assert tool in AUTO_APPROVED_MCP_TOOLS
    assert tool.value in OPERATION_TOOLS


def test_both_tools_are_operations():
    """A tool declared outside the operations seam carries a second copy of its
    own arguments; `test_operations.py` refuses one, and so does this."""
    assert {McpTool.RECORD_ASSERTED_NOT_PROVEN.value, McpTool.OPEN_CLAIMS.value} <= set(
        OPERATION_TOOLS
    )


# --------------------------------------------------------------------------- #
# The write tool, through the surface an agent reaches
# --------------------------------------------------------------------------- #


def _write_tool(session, arguments: dict) -> str:
    """The tool as MCP calls it: through `execute_tool` into its operation."""
    return execute_tool(session, McpTool.RECORD_ASSERTED_NOT_PROVEN.value, dict(arguments))


def test_the_write_tool_records_a_claim_and_is_idempotent(db_session, ticket):
    """R10.1 through the surface an agent actually calls."""
    handler = _write_tool
    arguments = {
        "ticket_id": ticket.external_id,
        "operation": ClaimOperation.RECORD.value,
        **claim_values(),
    }
    first = json.loads(handler(db_session, dict(arguments)))
    second = json.loads(handler(db_session, dict(arguments)))

    assert first["ok"] is True
    assert second["artifact_id"] == first["artifact_id"]
    assert second["amended"] is False
    assert len(_rows(db_session, ticket)) == 1


def test_the_write_tool_refuses_prose_as_an_owed_artifact(db_session, ticket):
    """AC12(b) at the boundary an agent reaches, not only in the model."""
    handler = _write_tool
    with pytest.raises(ValueError) as excinfo:
        handler(
            db_session,
            {
                "ticket_id": ticket.external_id,
                "operation": ClaimOperation.RECORD.value,
                **claim_values(owed_artifact="a test proving the lock holds"),
            },
        )
    assert ticket.external_id in str(excinfo.value)


def test_recording_a_claim_is_the_normal_path_not_a_failure_to_file(db_session, ticket):
    """AC1. No approval, no waiver, no gate: a well-formed claim is accepted."""
    handler = _write_tool
    result = json.loads(
        handler(
            db_session,
            {
                "ticket_id": ticket.external_id,
                "operation": ClaimOperation.RECORD.value,
                **claim_values(),
            },
        )
    )
    assert result["ok"] is True
    assert result["ticket"] == ticket.external_id


def test_the_model_is_a_pydantic_v2_model_with_stripped_strings():
    """`FC`'s shape, reused: `str_strip_whitespace` is what makes the idempotency
    digest stable against trailing noise (R10's recorded caveat)."""
    assert AssertedNotProvenClaim.model_config.get("extra") == "forbid"
    assert AssertedNotProvenClaim.model_config.get("str_strip_whitespace") is True
    assert (
        parse_claim(claim_values(claim=f"  {BEHAVIOUR_CLAIM['claim']}  ")).claim
        == (BEHAVIOUR_CLAIM["claim"])
    )
