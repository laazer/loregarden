"""A reviewer's name for a criterion resolves to the one stored criterion it means, or to none."""

from __future__ import annotations

import pytest
from loregarden.services.criterion_names import resolve_criterion

# The shape of lg-code-knowledge-1113's criteria: a label, a heading sentence, then the body.
CRITERIA = [
    "AC1 — THE BASIS MODULE, AND IT NAMES WHAT IT CANNOT KNOW. New `retrieval_basis.py`, …",
    "AC2 — BOTH RETRIEVAL SURFACES RETURN THE BASIS. (a) `search_prior_work` returns …",
    "AC10 — TRUNCATION IS REVERSIBLE, AND THE OMITTED SET IS A STABLE MULTISET DIFFERENCE. "
    "In `services/retrieval_basis.py`, a uniform field family …",
    "AC11 — PARTIALITY IS SWEPT INTO ONE PLACE IN THE MCP REPLY ENVELOPE. (a) The MCP …",
]


@pytest.mark.parametrize(
    ("name", "meant"),
    [
        # Word for word, and with its whitespace reflowed.
        (CRITERIA[0], 0),
        ("  " + CRITERIA[1].replace(" ", "\n  ", 3), 1),
        # As 1113's reviewer wrote them: the heading, then why it failed.
        (
            "AC10 — TRUNCATION IS REVERSIBLE, AND THE OMITTED SET IS A STABLE MULTISET "
            "DIFFERENCE (the uniform field family is absent on the vault leg, and the graph "
            "leg's `total` is the scan cap rather than the real total)",
            2,
        ),
        (
            "AC1 — THE BASIS MODULE, AND IT NAMES WHAT IT CANNOT KNOW (not_examined=0 asserts "
            "a scope was fully examined when it was not)",
            0,
        ),
        # The whole criterion with a reason after it.
        (CRITERIA[3] + " — still unmet: the vault leg never reaches `partial`", 3),
        # The label alone, or with words of the reviewer's own.
        ("AC11", 3),
        ("AC1", 0),
        ("ac10: the omitted set is not stable", 2),
    ],
)
def test_a_name_resolves_to_the_criterion_it_means(name, meant):
    assert resolve_criterion(CRITERIA, name) == CRITERIA[meant]


@pytest.mark.parametrize(
    "name",
    [
        "",
        "   ",
        "AC7",  # no such criterion
        "THE OMITTED SET IS STABLE",  # neither a beginning nor a label
        "AC",  # begins every criterion: abstains, and is no label
    ],
)
def test_a_name_that_fits_no_single_criterion_resolves_to_none(name):
    assert resolve_criterion(CRITERIA, name) is None


def test_criteria_without_labels_resolve_by_how_they_begin():
    criteria = ["Errors name the arm", "Width is read on every arm"]

    assert (
        resolve_criterion(criteria, "Errors name the arm (the parse error drops it)")
        == (criteria[0])
    )
    assert resolve_criterion(criteria, "Errors name") == criteria[0]
    assert resolve_criterion(criteria, "Errors") == criteria[0]
    assert resolve_criterion(criteria + ["Errors are logged"], "Errors") is None
