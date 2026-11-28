"""One asserted-not-proven claim, and everything refused at write time.

Shaped on `services.fix_coverage`: a Pydantic v2 model with `extra="forbid"`,
`str_strip_whitespace=True`, one line per field, and per-field minimum-substance
floors reused from there (`MIN_ANSWER`, `MIN_WAIVER`) so the two records cannot
drift apart on what counts as an answer.

Three refusals carry most of the weight, and each exists because the opposite
failure is silent:

- **The owed artifact is a resolvable pointer, never prose.** The enumeration
  computes `closable` by opening it; prose cannot be opened, so a prose pointer
  degrades the class to a list nobody can act on. Both accepted forms are
  anchored with `fullmatch`, not `search`: `FC._TEST_REF` is a `.search` pattern
  at its source, and reusing it that way accepts ``TODO path.py::test_y`` and
  the refusal stops holding without an assertion going red.
- **A pointer may not leave the checkout.** `..` or a leading `/` reads a file
  outside the tree, so a member could be made `closable` by a file no checkout
  contains and nobody reviewed.
- **The fallback says it is NOT BUILT.** A fallback already built is not a
  fallback for an unproven claim; it is the implementation, and the claim should
  not have been filed.

Shape is validated here. Resolution — does `routed_ticket` name a real ticket —
needs a session and lives in `record`, because `ticket_ids.resolve_one` performs
I/O. Both refusals exist, or one of them is the only one anyone relies on.
"""

from __future__ import annotations

import re

from loregarden.agents.registry import AGENTS
from loregarden.models.domain import AssertedNotProvenKind
from loregarden.services.fix_coverage import MIN_ANSWER, MIN_WAIVER
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

#: The two forms an owed artifact may take, anchored. The named groups mirror
#: `fix_coverage._TEST_REF`; the anchoring is the difference that matters.
TEST_REF_FORM = "path/to/test_file.py::test_name"
CODE_SITE_FORM = "path/to/file.py:<line>"
_TEST_REF = re.compile(r"(?P<path>[\w./-]+\.py)::(?P<name>test_\w+)")
_CODE_SITE = re.compile(r"(?P<path>[\w./-]+\.py):(?P<line>[1-9][0-9]*)")

#: Wider than `fix_coverage._BACKTICKED`, which is `[a-z][a-z0-9_]*` and so does
#: not match `spec-review` — the spec's own accepted challenger. The hyphen is
#: the only addition; the refusal that does the real work is the agent-id check.
_BACKTICKED = re.compile(r"`([a-z][a-z0-9_-]*)`")

#: The literal a fallback must carry, so the reader can scan for it.
NOT_BUILT = "not built"

#: Every free-text field, swept for the newline that would break the PR section.
#: Named rather than derived from `model_dump`, so a new field is a deliberate
#: entry here instead of silently exempt.
_TEXT_FIELDS = (
    "claim",
    "qualifies_because",
    "owed_artifact",
    "owed_description",
    "owed_environment",
    "portable_subset",
    "proven_today",
    "fallback",
    "upstream_id",
    "citation",
    "ceiling",
    "ceiling_unit",
    "upgrade_path",
    "challenger",
    "routed_ticket",
)

#: Kinds whose owed artifact is a checked-in test, not a code site.
_DOCUMENT_SHAPED = frozenset(
    {
        AssertedNotProvenKind.BEHAVIOUR,
        AssertedNotProvenKind.DRAFTED_DEFAULT,
        AssertedNotProvenKind.DOCUMENT_DEFECT,
    }
)

#: Fields only a DRAFTED_DEFAULT carries, and only a DOCUMENT_DEFECT carries,
#: and only a CODE_SITE carries. A field on the wrong kind is a writer who has
#: the wrong kind, and keeping it silently hides that.
_KIND_FIELDS: dict[AssertedNotProvenKind, frozenset[str]] = {
    AssertedNotProvenKind.DRAFTED_DEFAULT: frozenset({"frozen", "challenger"}),
    AssertedNotProvenKind.DOCUMENT_DEFECT: frozenset({"routed_ticket"}),
    AssertedNotProvenKind.CODE_SITE: frozenset(
        {"ceiling", "ceiling_quantity", "ceiling_unit", "upgrade_path"}
    ),
}
_ALL_KIND_FIELDS = frozenset().union(*_KIND_FIELDS.values())


def _answered(value: str, minimum: int) -> bool:
    return len(value) >= minimum and not value.startswith("<")


def _field_given(values: dict, name: str) -> bool:
    """True when this kind-specific field was actually answered.

    `ceiling_quantity` is the one field whose zero is a real answer, so it is
    tested for presence rather than truth.
    """
    value = values[name]
    if name == "ceiling_quantity":
        return value is not None
    return bool(value)


def _escapes_checkout(path: str) -> bool:
    """True when the pointer reads a file the checkout does not contain."""
    return path.startswith("/") or ".." in path.split("/")


class AssertedNotProvenClaim(BaseModel):
    """One claim believed without proof. One line per field, as `FC` has: the
    PR section reads a field to the end of its line."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    #: The statement itself. Slugified with its digest, this is the identity.
    claim: str = ""
    member_kind: AssertedNotProvenKind = AssertedNotProvenKind.BEHAVIOUR
    #: Why this one joins the class. AC4: the class grows by amendment, each
    #: member joining with its qualifying reason, so the reason is required and
    #: is returned on every member.
    qualifies_because: str = ""
    #: The resolvable pointer. Never prose.
    owed_artifact: str = ""
    #: A measurement or a reproduction that is not itself a file. Never a
    #: substitute for the pointer.
    owed_description: str = ""
    #: The environment the proof needs, when it cannot be proven where we develop.
    owed_environment: str = ""
    #: What was split out of it and landed now.
    portable_subset: str = ""
    #: The half of the guarantee that is proven today.
    proven_today: str = ""
    #: Where this goes if the claim turns out false — named, and NOT BUILT.
    fallback: str = ""
    #: The identifier the claim keeps from wherever it was decided.
    upstream_id: str = ""
    #: The source locator it is traceable to.
    citation: str = ""
    #: Documents a change to this claim obliges an edit to. AC8.
    dependent_documents: list[str] = []

    # CODE_SITE only.
    ceiling: str = ""
    ceiling_quantity: float | None = None
    ceiling_unit: str = ""
    upgrade_path: str = ""

    # DRAFTED_DEFAULT only.
    frozen: bool = False
    challenger: str = ""

    # DOCUMENT_DEFECT only.
    routed_ticket: str = ""

    @model_validator(mode="after")
    def _validated(self) -> AssertedNotProvenClaim:
        # One dump, read by key throughout: the fields are checked by NAME, and
        # a `getattr` table is a structured API written by hand.
        values = self.model_dump(mode="json")
        self._one_line_each(values)
        self._required_everywhere(values)
        self._owed_pointer()
        self._the_split()
        self._per_kind(values)
        return self

    def _one_line_each(self, values: dict) -> None:
        written = [values[name] for name in _TEXT_FIELDS]
        written.extend(self.dependent_documents)
        if any("\n" in value for value in written):
            raise ValueError("each field is one line; the PR section reads a field to its line end")

    def _required_everywhere(self, values: dict) -> None:
        for name in ("claim", "qualifies_because", "upstream_id", "citation"):
            if not _answered(values[name], MIN_ANSWER):
                raise ValueError(
                    f"{name} is missing or a placeholder ({MIN_ANSWER}+ characters); every "
                    "claim keeps its upstream identifier and its citation so a reader can "
                    "ask who decided it without leaving the record"
                )
        if not _answered(self.fallback, MIN_ANSWER):
            raise ValueError(
                f"fallback is missing or a placeholder ({MIN_ANSWER}+ characters): name where "
                f"this goes if the claim turns out false, and say it is {NOT_BUILT!r}"
            )
        if NOT_BUILT not in self.fallback.lower():
            raise ValueError(
                f"fallback must assert it is {NOT_BUILT!r}: a fallback already built is not a "
                "fallback for an unproven claim, it is the implementation"
            )

    def _owed_pointer(self) -> None:
        pointer = self.owed_artifact
        if not pointer:
            raise ValueError(
                f"owed_artifact is required and is a resolvable pointer — {TEST_REF_FORM} or "
                f"{CODE_SITE_FORM}. Prose describing a measurement or a reproduction goes in "
                "owed_description, which never substitutes for the pointer"
            )
        test_ref = _TEST_REF.fullmatch(pointer)
        code_site = _CODE_SITE.fullmatch(pointer)
        if test_ref is None and code_site is None:
            raise ValueError(
                f"owed_artifact {pointer!r} is not a resolvable pointer: it must be exactly "
                f"{TEST_REF_FORM} or {CODE_SITE_FORM}, with nothing around it"
            )
        matched = test_ref if test_ref is not None else code_site
        if _escapes_checkout(matched.group("path")):
            raise ValueError(
                f"owed_artifact {pointer!r} leaves the checkout: a pointer carrying '..' or a "
                "leading '/' reads a file no checkout contains, so it could make this member "
                "closable from a file nobody reviewed"
            )
        wants_test = self.member_kind in _DOCUMENT_SHAPED
        if wants_test and test_ref is None:
            raise ValueError(
                f"a {self.member_kind.value} member owes a checked-in test, as {TEST_REF_FORM}; "
                f"{CODE_SITE_FORM} is the form a {AssertedNotProvenKind.CODE_SITE.value} "
                "member owes"
            )
        if not wants_test and code_site is None:
            raise ValueError(
                f"a {self.member_kind.value} member owes its marked line, as {CODE_SITE_FORM}; "
                f"{TEST_REF_FORM} is the form a document-shaped member owes"
            )

    def _the_split(self) -> None:
        """AC5 / R2: the guarantee splits, and the two halves stay apart."""
        if self.owed_environment:
            if not _answered(self.owed_environment, MIN_WAIVER):
                raise ValueError(
                    f"owed_environment is a placeholder ({MIN_WAIVER}+ characters): name the "
                    "environment the proof needs, not just its platform"
                )
            if not _answered(self.portable_subset, MIN_ANSWER):
                raise ValueError(
                    "a claim that needs another environment must name its portable_subset — "
                    f"what was split out and landed now ({MIN_ANSWER}+ characters)"
                )
            if not _answered(self.proven_today, MIN_ANSWER):
                raise ValueError(
                    "a claim that cannot be proven here must say what WAS proven here: "
                    f"proven_today is missing or a placeholder ({MIN_ANSWER}+ characters)"
                )
        elif self.portable_subset:
            raise ValueError(
                "portable_subset with an empty owed_environment: a fully portable claim has "
                "nothing to split out, so the pair is a lie"
            )

    def _per_kind(self, values: dict) -> None:
        kind = self.member_kind
        mine = _KIND_FIELDS.get(kind, frozenset())
        for name in sorted(_ALL_KIND_FIELDS - mine):
            if _field_given(values, name):
                raise ValueError(
                    f"{name} is not a field a {kind.value} member carries; it belongs to "
                    f"{self._owner_of(name)}, so this is a member filed under the wrong kind"
                )
        for name in sorted(mine):
            if not _field_given(values, name):
                raise ValueError(
                    f"a {kind.value} member must name its {name}"
                    + (
                        " — the quantity at which this stops holding is what lets the member "
                        "be CLOSED rather than only accumulate"
                        if name.startswith("ceiling")
                        else ""
                    )
                )
        if kind is AssertedNotProvenKind.DRAFTED_DEFAULT:
            self._challenged()

    @staticmethod
    def _owner_of(name: str) -> str:
        return ", ".join(
            sorted(kind.value for kind, fields in _KIND_FIELDS.items() if name in fields)
        )

    def _challenged(self) -> None:
        """AC7, verbatim: the challenger is an artifact class, not a role."""
        named = _BACKTICKED.findall(self.challenger)
        if not named:
            raise ValueError(
                "challenger names no artifact class: put the artifact class or gate that owns "
                "challenging this number in backticks (`spec-review`). AC7 is verbatim that "
                "the challenger is an artifact class, not a role"
            )
        roles = sorted(token for token in named if token in AGENTS)
        if roles:
            raise ValueError(
                f"challenger {', '.join(roles)} is a registered agent id, and backticks around "
                "a role do not make it an artifact class: name the artifact class or gate that "
                "challenges the number"
            )


def parse_claim(values: dict) -> AssertedNotProvenClaim:
    """Validate a claim, raising ``ValueError`` naming every reason it was refused."""
    try:
        return AssertedNotProvenClaim.model_validate(values)
    except ValidationError as exc:
        reasons = "; ".join(str(error["msg"]) for error in exc.errors())
        raise ValueError(reasons) from exc
