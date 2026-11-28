"""Asserted-not-proven, adversarially: the corners the happy-path suites leave.

Spec `6d686e57`. These do not restate R1–R12; they attack the places where an
implementation can satisfy every assertion in `test_asserted_not_proven.py` and
`test_asserted_not_proven_listing.py` and still be wrong in the one direction
this class cannot survive — **a count that lies**.

Why each group exists, in one line:

- **Anchoring.** `FC._TEST_REF` is used with `.search` at its source. A validator
  that inherits that habit accepts prose with a pointer buried in it, and AC12(b)'s
  refusal — the bar the whole enumeration rests on — silently stops holding.
- **Escaping the checkout.** `closable` is computed by opening the owed pointer
  under `repo_root`. A pointer with `..` or a leading `/` reads a file outside the
  tree, so a claim can be made `closable` by a file nobody reviewed.
- **Boundaries.** "Exceeded by measured load" is a strict comparison. Equal-to,
  zero, negative and non-finite are the four values that close a ceiling for free
  under the three most likely mis-codings.
- **Identity.** The count is over distinct claim identities, and identity is a
  slug. Two distinct statements that slug alike, or two long statements truncated
  to the same prefix, silently become one member — the count lies downward.
- **Counting under amendment.** Every count is over identities, never rows. A
  reader that applies the cap to *rows* passes the 250-unamended-claims test and
  under-reports the moment anything is amended.
- **Ordering before the cap.** `closable` first is the answer to the question.
  Sorting after a `LIMIT` drops exactly the members the operator came for.
- **Double-fire.** AC12(g) names the write *and* the close tool as idempotent.
  Only the write is pinned next door.
- **Determinism.** A count that is the feature must be the same count twice.
"""

from __future__ import annotations

import json

import pytest
from loregarden.models.domain import (
    AssertedClaimStatus,
    AssertedNotProvenKind,
    ClaimOutcome,
    Ticket,
    TicketState,
)
from loregarden.services.asserted_not_proven.listing import open_claims
from loregarden.services.asserted_not_proven.record import (
    amend_claim,
    claim_slug,
    close_claim,
    record_measurement,
    register_claim,
)
from loregarden.services.asserted_not_proven.schemas import parse_claim
from loregarden.services.ticket_documents import MAX_ARTIFACT_LIST_LIMIT
from sqlmodel import Session
from tests.asserted_not_proven_helpers import (
    BEHAVIOUR_CLAIM,
    CODE_SITE_CLAIM,
    DRAFTED_DEFAULT_CLAIM,
    claim_values,
    git,
    land_owed_test,
    listed_members,
    make_ticket,
    # Fixtures, discovered by pytest once they are in this namespace. They live
    # in the helpers so the suites cannot drift apart on the checkout their
    # answers are measured against.
    repo_fixture,  # noqa: F401
    ticket_fixture,  # noqa: F401
    workspace_fixture,  # noqa: F401
)


def _list(db_session, workspace, repo, **kwargs) -> dict:
    return open_claims(db_session, workspace_slug=workspace.slug, repo_root=repo, **kwargs)


def _refusal(values: dict) -> str:
    with pytest.raises(ValueError) as excinfo:
        parse_claim(values)
    return str(excinfo.value)


# =========================================================================== #
# ANCHORING — a pointer buried in prose is still prose (R2.3, AC12(b))
# =========================================================================== #


@pytest.mark.parametrize(
    "owed",
    [
        "a test proving the lock holds: server/tests/test_x.py::test_y, eventually",
        "server/tests/test_x.py::test_y is what this owes",
        "TODO server/tests/test_x.py::test_y",
        "see server/tests/test_x.py::test_y (once someone writes it)",
    ],
    ids=["trailing-prose", "leading-pointer", "todo-prefix", "parenthesised"],
)
def test_prose_with_a_pointer_buried_in_it_is_still_prose(owed):
    """`FC._TEST_REF` is a `.search` pattern at its source.

    A validator that reuses it verbatim with `.search` — the literal instruction
    in R2 is "reuse `FC._TEST_REF` verbatim" — accepts every string here, and
    AC12(b)'s refusal stops holding without a single assertion going red. The
    pattern must be anchored at the service boundary.
    """
    assert _refusal(claim_values(owed_artifact=owed))


@pytest.mark.parametrize(
    "owed",
    [
        "the hot path is at server/loregarden/services/foo.py:412",
        "server/loregarden/services/foo.py:412 and also :500",
    ],
    ids=["leading-prose", "two-pointers"],
)
def test_the_code_site_form_is_anchored_too(owed):
    """R2.2's other half: the same hole, on the form a CODE_SITE member owes."""
    assert _refusal(claim_values(CODE_SITE_CLAIM, owed_artifact=owed))


@pytest.mark.parametrize(
    ("base", "owed"),
    [
        (BEHAVIOUR_CLAIM, "server/tests/test_x.py::test_y::test_z"),
        (BEHAVIOUR_CLAIM, "server/tests/test_x.txt::test_y"),
        (BEHAVIOUR_CLAIM, "server/tests/test_x.py::helper_y"),
        (BEHAVIOUR_CLAIM, "server/tests/test_x.py::"),
        (BEHAVIOUR_CLAIM, "::test_y"),
        (CODE_SITE_CLAIM, "server/loregarden/services/foo.py:412:9"),
        (CODE_SITE_CLAIM, "server/loregarden/services/foo.py:+5"),
        (CODE_SITE_CLAIM, "server/loregarden/services/foo.py: 5"),
        (CODE_SITE_CLAIM, "server/loregarden/services/foo.py:"),
        (CODE_SITE_CLAIM, "server/loregarden/services/foo.py:4.5"),
    ],
    ids=[
        "double-separator",
        "not-a-python-file",
        "not-a-test-name",
        "empty-test-name",
        "empty-path",
        "line-and-column",
        "signed-line",
        "spaced-line",
        "empty-line-number",
        "fractional-line",
    ],
)
def test_a_pointer_that_only_looks_resolvable_is_refused(base, owed):
    """R2.4's principle generalised: a pointer that resolves to nothing only
    looks resolvable, and the enumeration cannot act on it either way."""
    assert _refusal(claim_values(base, owed_artifact=owed))


@pytest.mark.parametrize(
    ("base", "owed"),
    [
        (BEHAVIOUR_CLAIM, "../../../other-repo/tests/test_x.py::test_y"),
        (BEHAVIOUR_CLAIM, "/tmp/evil/tests/test_x.py::test_y"),
        (BEHAVIOUR_CLAIM, "server/tests/../../../etc/test_x.py::test_y"),
        (CODE_SITE_CLAIM, "../../../etc/secrets.py:1"),
        (CODE_SITE_CLAIM, "/etc/secrets.py:1"),
    ],
    ids=["relative-escape", "absolute", "embedded-escape", "code-site-escape", "code-site-abs"],
)
def test_a_pointer_that_leaves_the_checkout_is_refused(base, owed):
    """`closable` is computed by opening the owed pointer under `repo_root`.

    A pointer carrying `..` or a leading `/` reads a file outside the tree, so a
    member can be made `closable` by a file nobody reviewed and no checkout
    contains. The pointer is resolvable *in this checkout* or it is not a
    pointer (R2, R5's checkout-dependence note).
    """
    assert _refusal(claim_values(base, owed_artifact=owed))


def test_surrounding_whitespace_is_stripped_not_refused():
    """`str_strip_whitespace=True` is on (R10's idempotency note depends on it),
    so a pasted pointer must normalise rather than fail the author."""
    claim = parse_claim(claim_values(owed_artifact="  server/tests/test_x.py::test_y  "))
    assert claim.owed_artifact == "server/tests/test_x.py::test_y"


# =========================================================================== #
# THE FALLBACK SUBSTRING, at its boundary (R3)
# =========================================================================== #


@pytest.mark.parametrize(
    "fallback",
    ["refuse every removal and report the cost — notbuilt", "not_built, fall back to the lock"],
    ids=["no-space", "underscored"],
)
def test_a_near_miss_of_the_not_built_literal_is_refused(fallback):
    """R3's check is a literal substring. A near miss must fail rather than
    pass: a reader scanning for "not built" would miss these too."""
    assert _refusal(claim_values(fallback=fallback))


def test_the_not_built_literal_alone_is_under_the_answer_floor():
    """R3.1 + `MIN_ANSWER`: "not built" is nine characters and names no
    alternative, so satisfying the substring must not satisfy the field."""
    assert _refusal(claim_values(fallback="not built"))


# =========================================================================== #
# THE CEILING COMPARISON, at its boundary (R5, R10.7)
# =========================================================================== #


def _code_site_with_measurement(db_session, ticket, value, unit="artifacts"):
    claim = parse_claim(claim_values(CODE_SITE_CLAIM))
    register_claim(db_session, ticket, claim)
    slug = claim_slug(claim.claim)
    record_measurement(db_session, ticket, slug, value=value, unit=unit)
    return slug


@pytest.mark.parametrize(
    "value", [500.0, 499.999, 0.0, -1.0], ids=["equal", "just-under", "zero", "negative"]
)
def test_a_measurement_that_does_not_exceed_the_ceiling_cannot_close_it(db_session, ticket, value):
    """R5: the rule is *exceeds* `ceiling_quantity`, a strict comparison.

    Equal-to is the value that separates `>` from `>=`, and zero and negative
    are what a failed or unstarted measurement produces. All four close the
    member for free under the three most likely mis-codings.
    """
    slug = _code_site_with_measurement(db_session, ticket, value)
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


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_measurement_is_refused(db_session, ticket, value):
    """`nan > 500` is False and `inf > 500` is True, so one of these silently
    never closes and the other closes everything. Neither is a measurement."""
    claim = parse_claim(claim_values(CODE_SITE_CLAIM))
    register_claim(db_session, ticket, claim)
    with pytest.raises(ValueError):
        record_measurement(
            db_session, ticket, claim_slug(claim.claim), value=value, unit="artifacts"
        )


def test_a_nan_measurement_never_reads_as_closable(db_session, workspace, repo, ticket):
    """Belt and braces on the listing side: whatever the writer accepts, a
    member a reader cannot compare must stay `open`, never `closable`."""
    claim = parse_claim(claim_values(CODE_SITE_CLAIM))
    register_claim(db_session, ticket, claim)
    try:
        record_measurement(
            db_session, ticket, claim_slug(claim.claim), value=float("nan"), unit="artifacts"
        )
    except ValueError:
        return
    assert listed_members(_list(db_session, workspace, repo))[0]["status"] == "open"


# =========================================================================== #
# IDENTITY — the count is over slugs, so the slug must not collapse members
# =========================================================================== #


def _register_or_refuse(db_session, ticket, values):
    try:
        return register_claim(db_session, ticket, parse_claim(values))
    except ValueError:
        return None


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("the reaper keeps dirty checkouts", "The reaper keeps dirty checkouts!"),
        ("the reaper keeps dirty checkouts", "the  reaper  keeps  dirty  checkouts"),
        ("the reaper keeps dirty checkouts", "the reaper — keeps dirty checkouts"),
    ],
    ids=["case-and-punctuation", "repeated-spaces", "em-dash"],
)
def test_two_distinct_statements_never_silently_become_one_member(
    db_session, workspace, repo, ticket, first, second
):
    """Identity is `(ticket_id, slugified claim)`. Slugification folds case,
    punctuation and runs of whitespace, so these pairs collide.

    A colliding second write must either be refused or become its own member.
    What it must not do is return the first row's id with `amended: false` —
    that is a silent overwrite of a claim the operator believes is filed, and
    the count lies downward, which is the one direction it must not.
    """
    one = _register_or_refuse(db_session, ticket, claim_values(claim=first))
    assert one is not None
    two = _register_or_refuse(db_session, ticket, claim_values(claim=second))
    if two is None:
        return
    assert two.artifact_id != one.artifact_id, "a colliding claim silently overwrote the first"
    assert len(listed_members(_list(db_session, workspace, repo))) == 2


def test_two_long_statements_differing_only_at_the_end_stay_twomembers(
    db_session, workspace, repo, ticket
):
    """A slug truncated to a fixed prefix makes every long claim the same claim.

    Claim statements in this repo's own documents run to a sentence; truncation
    is the cheapest way to build the collision above and the hardest to notice,
    because the first write always looks correct.
    """
    stem = "the enumeration merges stored rows and scanned code sites " * 30
    one = _register_or_refuse(db_session, ticket, claim_values(claim=f"{stem} under load"))
    two = _register_or_refuse(db_session, ticket, claim_values(claim=f"{stem} under idle"))
    assert one is not None
    if two is None:
        return
    assert two.artifact_id != one.artifact_id
    assert len(listed_members(_list(db_session, workspace, repo))) == 2


def test_the_same_statement_on_two_tickets_is_twomembers(db_session, workspace, repo):
    """Identity is the pair. Two tickets can believe the same thing without
    proof, and collapsing them to one hides a ticket from its own debt."""
    first = make_ticket(db_session, workspace, number=1)
    second = make_ticket(db_session, workspace, number=2)
    register_claim(db_session, first, parse_claim(claim_values()))
    register_claim(db_session, second, parse_claim(claim_values()))

    members = listed_members(_list(db_session, workspace, repo))
    assert len(members) == 2
    assert {m["ticket_external_id"] for m in members} == {first.external_id, second.external_id}


# =========================================================================== #
# DOUBLE-FIRE — AC12(g) names the write AND the close tool
# =========================================================================== #


def test_a_superseded_payload_resent_after_an_amendment_is_not_a_no_op(db_session, ticket):
    """R10.1 compares against the **latest** stored payload.

    An implementation that compares against *any* stored revision treats a
    re-sent original as idempotent and quietly reverts nothing — the operator's
    correction is accepted, then lost on the next retry of the earlier call.
    """
    original = claim_values()
    register_claim(db_session, ticket, parse_claim(original))
    amend_claim(
        db_session,
        ticket,
        parse_claim(claim_values(qualifies_because="re-observed under a 20-way parallel push")),
        dependent_document_answers={},
    )
    third = amend_claim(db_session, ticket, parse_claim(original), dependent_document_answers={})
    assert third.amended is True, "a re-sent superseded payload is a change, not a no-op"


def test_closing_the_same_claim_twice_does_not_inflate_the_class(
    db_session, workspace, repo, ticket
):
    """AC12(g) verbatim: "the write and close tools are IDEMPOTENT on
    (ticket_id, claim)". Only the write half is pinned next door."""
    claim = parse_claim(claim_values())
    register_claim(db_session, ticket, claim)
    land_owed_test(repo, claim.owed_artifact)
    kwargs = {
        "outcome": ClaimOutcome.PROVEN,
        "evidence": claim.owed_artifact,
        "dependent_document_answers": {},
        "evidence_exists": True,
    }
    first = close_claim(db_session, ticket, claim_slug(claim.claim), **kwargs)
    second = close_claim(db_session, ticket, claim_slug(claim.claim), **kwargs)

    assert second.artifact_id == first.artifact_id
    response = _list(db_session, workspace, repo, status=AssertedClaimStatus.ALL)
    assert response["total"] == 1


def test_recording_the_same_measurement_twice_does_not_inflate_the_class(
    db_session, workspace, repo, ticket
):
    """A retried MCP call is the normal failure here, and a measurement is a
    write like any other."""
    claim = parse_claim(claim_values(CODE_SITE_CLAIM))
    register_claim(db_session, ticket, claim)
    slug = claim_slug(claim.claim)
    record_measurement(db_session, ticket, slug, value=612.0, unit="artifacts")
    record_measurement(db_session, ticket, slug, value=612.0, unit="artifacts")

    assert _list(db_session, workspace, repo, status=AssertedClaimStatus.ALL)["total"] == 1


def test_amending_one_claim_leaves_its_neighbour_untouched(db_session, workspace, repo, ticket):
    """Identity is per claim, not per ticket. An amendment keyed on the ticket
    alone rewrites whichever member happens to be newest."""
    first = parse_claim(claim_values(claim="the first unproven behaviour"))
    second = parse_claim(claim_values(claim="the second unproven behaviour"))
    register_claim(db_session, ticket, first)
    register_claim(db_session, ticket, second)
    amend_claim(
        db_session,
        ticket,
        parse_claim(
            claim_values(
                claim="the first unproven behaviour",
                qualifies_because="re-observed under a 20-way parallel push",
            )
        ),
        dependent_document_answers={},
    )

    reasons = {
        m["claim"]: m["qualifies_because"]
        for m in listed_members(_list(db_session, workspace, repo))
    }
    assert len(reasons) == 2
    assert reasons["the second unproven behaviour"] == BEHAVIOUR_CLAIM["qualifies_because"]


def test_two_sessions_racing_the_same_write_leave_one_member(
    db_session, workspace, repo, ticket, isolated_db
):
    """Two agents retrying the same MCP call is the live shape of double-fire.

    Whatever rows land, the *member* count is the feature, and it must be one.
    """
    register_claim(db_session, ticket, parse_claim(claim_values()))
    db_session.commit()

    with Session(isolated_db) as other:
        other_ticket = other.get(Ticket, ticket.id)
        try:
            register_claim(other, other_ticket, parse_claim(claim_values()))
        except ValueError:
            other.rollback()
        else:
            other.commit()

    db_session.expire_all()
    assert _list(db_session, workspace, repo)["total"] == 1


# =========================================================================== #
# COUNTING UNDER AMENDMENT — every count is over identities, never rows
# =========================================================================== #


def test_amendment_history_does_not_push_the_class_over_the_cap(db_session, workspace, repo):
    """R10's counting rule, where it actually breaks.

    110 claims amended once is 220 artifact rows against a 200-row cap. A reader
    that applies `MAX_ARTIFACT_LIST_LIMIT` to rows passes the 250-unamended-claims
    test next door and reports 110 members as truncated here — the count that is
    the feature, wrong, with `truncated: true` to make it look deliberate.
    """
    ticket = make_ticket(db_session, workspace, number=1)
    claims = 110
    for index in range(claims):
        register_claim(
            db_session, ticket, parse_claim(claim_values(claim=f"unproven behaviour {index}"))
        )
        amend_claim(
            db_session,
            ticket,
            parse_claim(
                claim_values(
                    claim=f"unproven behaviour {index}",
                    qualifies_because="re-observed under a 20-way parallel push",
                )
            ),
            dependent_document_answers={},
        )

    response = _list(db_session, workspace, repo)
    assert response["total"] == claims
    assert response["returned"] == claims
    assert response["truncated"] is False
    assert response["groups"][0]["count"] == claims


# =========================================================================== #
# THE CAP — ordering is applied before it, and it is global
# =========================================================================== #


def test_the_closable_member_survives_the_cap(db_session, workspace, repo):
    """R5.7 and R5.2 together, which is where the implementation can split them.

    A reader that runs `LIMIT 200` and sorts the page it got back passes both
    tests separately and drops the one member the operator came for. "Which of
    those can I close now?" is the question; a cap that eats the answer leaves
    the tool answering the half nobody asked.
    """
    ticket = make_ticket(db_session, workspace, number=1)
    for index in range(MAX_ARTIFACT_LIST_LIMIT + 20):
        register_claim(
            db_session,
            ticket,
            parse_claim(
                claim_values(
                    claim=f"unproven behaviour {index}",
                    owed_artifact=f"server/tests/test_owed.py::test_owed_{index}",
                )
            ),
        )
    # Made closable LAST, so insertion order puts it beyond the cap.
    closable = parse_claim(
        claim_values(
            claim="the proof for this one has landed",
            owed_artifact="server/tests/test_landed.py::test_landed_proof",
        )
    )
    register_claim(db_session, ticket, closable)
    land_owed_test(repo, closable.owed_artifact)

    members = listed_members(_list(db_session, workspace, repo))
    assert members[0]["status"] == "closable"
    assert members[0]["claim"] == "the proof for this one has landed"


def test_the_cap_is_global_across_the_groups(db_session, workspace, repo):
    """R5: `returned` is members actually in `groups`, and it never exceeds the
    cap. A per-group cap returns 400 members under a limit of 200."""
    ticket = make_ticket(db_session, workspace, number=1)
    per_kind = MAX_ARTIFACT_LIST_LIMIT - 50
    for index in range(per_kind):
        register_claim(
            db_session, ticket, parse_claim(claim_values(claim=f"unproven behaviour {index}"))
        )
        register_claim(
            db_session,
            ticket,
            parse_claim(
                claim_values(
                    CODE_SITE_CLAIM,
                    claim=f"a knowingly insufficient read number {index}",
                    owed_artifact=f"server/loregarden/services/foo.py:{index + 1}",
                )
            ),
        )

    response = _list(db_session, workspace, repo)
    assert response["total"] == per_kind * 2
    assert response["returned"] == MAX_ARTIFACT_LIST_LIMIT
    assert len(listed_members(response)) == MAX_ARTIFACT_LIST_LIMIT
    assert {g["member_kind"]: g["count"] for g in response["groups"]} == {
        "behaviour": per_kind,
        "code_site": per_kind,
    }


# =========================================================================== #
# THE FILTERS, and the empty response that still says what it read
# =========================================================================== #


def test_each_status_filter_narrows_to_exactly_its_ownmembers(db_session, workspace, repo):
    """R5.5's vocabulary is four values, and R5.1 pins only two of them."""
    ticket = make_ticket(db_session, workspace, number=1)
    closable = parse_claim(
        claim_values(
            claim="the proof for this one has landed",
            owed_artifact="server/tests/test_landed.py::test_landed_proof",
        )
    )
    register_claim(db_session, ticket, closable)
    land_owed_test(repo, closable.owed_artifact)

    plain = parse_claim(
        claim_values(
            claim="this one is plainly open", owed_artifact="server/tests/test_o.py::test_o"
        )
    )
    register_claim(db_session, ticket, plain)

    settled = parse_claim(
        claim_values(
            claim="this one is finished", owed_artifact="server/tests/test_s.py::test_settled"
        )
    )
    register_claim(db_session, ticket, settled)
    land_owed_test(repo, settled.owed_artifact)
    close_claim(
        db_session,
        ticket,
        claim_slug(settled.claim),
        outcome=ClaimOutcome.PROVEN,
        evidence=settled.owed_artifact,
        dependent_document_answers={},
        evidence_exists=True,
    )

    only_closable = listed_members(
        _list(db_session, workspace, repo, status=AssertedClaimStatus.CLOSABLE)
    )
    assert [m["claim"] for m in only_closable] == ["the proof for this one has landed"]

    only_settled = listed_members(
        _list(db_session, workspace, repo, status=AssertedClaimStatus.SETTLED)
    )
    assert [m["claim"] for m in only_settled] == ["this one is finished"]

    assert (
        len(listed_members(_list(db_session, workspace, repo, status=AssertedClaimStatus.ALL))) == 3
    )


def test_a_kind_filter_excludes_scanned_members_of_the_other_kind(db_session, workspace, repo):
    """R4.4. The scan only ever produces CODE_SITE members, so a BEHAVIOUR
    filter that still returns them is reporting a kind nobody asked for."""
    ticket = make_ticket(db_session, workspace, number=1)
    register_claim(db_session, ticket, parse_claim(claim_values()))
    (repo / "server" / "loregarden" / "services" / "scanned.py").write_text(
        "# ceiling: 500 tickets — upgrade: paginate the join and merge per page\nx = 1\n",
        encoding="utf-8",
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "a marked site")

    members = listed_members(
        _list(db_session, workspace, repo, member_kind=AssertedNotProvenKind.BEHAVIOUR)
    )
    assert members, "the behaviour member should still be there"
    assert {m["member_kind"] for m in members} == {AssertedNotProvenKind.BEHAVIOUR.value}


def test_the_empty_response_still_says_how_much_of_the_tree_it_read(db_session, workspace, repo):
    """R5.3 spells the empty body as three keys; R7.1 says `scanned_files` is on
    **every** response. R7.1 wins, and this is why.

    `total == 0` with no `scanned_files` cannot be told apart from a scan that
    covered nothing — the zero-may-be-the-detector failure, on the one surface
    this ticket ships.
    """
    response = _list(db_session, workspace, repo)
    assert response["total"] == 0
    assert "scanned_files" in response
    assert "scan_truncated" in response
    assert response["question"]


# =========================================================================== #
# DETERMINISM — a count that is the feature is the same count twice
# =========================================================================== #


def test_the_same_question_asked_twice_gets_the_same_answer(db_session, workspace, repo):
    """Ordering within a bucket must be total, not incidental. A response that
    reshuffles between calls makes every operator comparison a false diff."""
    ticket = make_ticket(db_session, workspace, number=1)
    for index in range(12):
        register_claim(
            db_session,
            ticket,
            parse_claim(
                claim_values(
                    claim=f"unproven behaviour {index}",
                    owed_artifact=f"server/tests/test_owed.py::test_owed_{index}",
                )
            ),
        )
        register_claim(
            db_session,
            ticket,
            parse_claim(
                claim_values(
                    CODE_SITE_CLAIM,
                    claim=f"a knowingly insufficient read {index}",
                    owed_artifact=f"server/loregarden/services/foo.py:{index + 1}",
                )
            ),
        )

    answers = [
        json.dumps(_list(db_session, workspace, repo, status=AssertedClaimStatus.ALL))
        for _ in range(3)
    ]
    assert answers[0] == answers[1] == answers[2]


# =========================================================================== #
# AC8 — the dependent-document refusal, at its edges
# =========================================================================== #


def test_an_answer_for_a_different_document_does_not_discharge_the_obligation(db_session, ticket):
    """AC8 is per document. A truthy answers mapping that happens to be
    non-empty must not be read as "answered"."""
    owed = "AGENTS.md §WHERE TO LOOK"
    register_claim(db_session, ticket, parse_claim(claim_values(dependent_documents=[owed])))
    with pytest.raises(ValueError) as excinfo:
        amend_claim(
            db_session,
            ticket,
            parse_claim(
                claim_values(
                    dependent_documents=[owed],
                    qualifies_because="re-observed under a 20-way parallel push",
                )
            ),
            dependent_document_answers={"CLAUDE.md §Anti-patterns": "the table now names it"},
        )
    assert owed in str(excinfo.value)


def test_a_ticket_answer_that_does_not_resolve_is_refused(db_session, ticket):
    """R10: the answer is "the edit that was made" or "a filed ticket external
    id **that resolves**". An id nobody can open is the same as no answer."""
    owed = "AGENTS.md §WHERE TO LOOK"
    register_claim(db_session, ticket, parse_claim(claim_values(dependent_documents=[owed])))
    with pytest.raises(ValueError):
        amend_claim(
            db_session,
            ticket,
            parse_claim(
                claim_values(
                    dependent_documents=[owed],
                    qualifies_because="re-observed under a 20-way parallel push",
                )
            ),
            dependent_document_answers={owed: "lg-no-such-ticket-99999"},
        )


def test_every_unanswered_document_is_named_not_only_the_first(db_session, ticket):
    """A refusal naming one of three costs the author three round trips."""
    owed = ["AGENTS.md §WHERE TO LOOK", "CLAUDE.md §Anti-patterns", "AGENTS.md §STRUCTURE"]
    register_claim(db_session, ticket, parse_claim(claim_values(dependent_documents=owed)))
    with pytest.raises(ValueError) as excinfo:
        amend_claim(
            db_session,
            ticket,
            parse_claim(
                claim_values(
                    dependent_documents=owed,
                    qualifies_because="re-observed under a 20-way parallel push",
                )
            ),
            dependent_document_answers={},
        )
    message = str(excinfo.value)
    assert all(document in message for document in owed)


# =========================================================================== #
# AC7 — the challenger is an artifact class, and backticks are not a bypass
# =========================================================================== #


@pytest.mark.parametrize(
    "challenger",
    ["`backend_implementer`", "the `backend_implementer` agent", "`test_breaker`"],
    ids=["backticked-agent", "agent-in-prose", "another-agent"],
)
def test_backticks_around_a_role_do_not_make_it_an_artifact_class(challenger):
    """R9.3 names two halves: the shape check AND an explicit refusal of any
    token in `agents.registry`. The shape check alone is satisfied by putting
    the role in backticks, which is the first thing an author will try."""
    assert _refusal(claim_values(DRAFTED_DEFAULT_CLAIM, challenger=challenger))


@pytest.mark.parametrize(
    "challenger", ["``", "`  `", "spec-review"], ids=["empty", "blank", "bare"]
)
def test_a_challenger_with_nothing_in_the_backticks_is_refused(challenger):
    """AC7 is verbatim that the challenger is a named artifact class."""
    assert _refusal(claim_values(DRAFTED_DEFAULT_CLAIM, challenger=challenger))


# =========================================================================== #
# A landed ticket's debt is never dropped (AC12 d)
# =========================================================================== #


def test_unpaid_after_landing_members_are_not_hidden_by_the_cap(db_session, workspace, repo):
    """AC12(d): "neither drop them nor list them indistinguishably". The cap is
    the third way to drop them, and it is the one no assertion next door covers.

    The live shape is the reason: 899 of 3,165 tickets are done, so at real
    volume the unpaid-after-landing members are the *majority* of a long list.
    """
    live = make_ticket(db_session, workspace, number=1, state=TicketState.IN_PROGRESS)
    landed = make_ticket(db_session, workspace, number=2, state=TicketState.DONE)
    for index in range(MAX_ARTIFACT_LIST_LIMIT + 20):
        register_claim(
            db_session,
            live,
            parse_claim(
                claim_values(
                    claim=f"unproven behaviour {index}",
                    owed_artifact=f"server/tests/test_owed.py::test_owed_{index}",
                )
            ),
        )
    register_claim(
        db_session,
        landed,
        parse_claim(
            claim_values(
                claim="this one shipped and its proof never did",
                owed_artifact="server/tests/test_never.py::test_never_written",
            )
        ),
    )

    members = listed_members(_list(db_session, workspace, repo))
    assert members[0]["unpaid_after_landing"] is True
    assert members[0]["claim"] == "this one shipped and its proof never did"
