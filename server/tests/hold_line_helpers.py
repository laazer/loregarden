"""Parse the parent's hold line back into its parts, for lg-milestone-that-919.

The line is one string with two levels of elision, and the counts at each level
have to reconcile with the fixture's real counts (R5-AC4, R5-AC6). Asserting on
substrings cannot do that: `"+8 more prerequisites" in message` passes for a
line that named six and claims eight. So the tests parse the grammar in R3 and
assert on the parts.

Splitting is bracket-aware because a NOT_LANDED prerequisite's ``[…]`` group
contains the separator the next level up uses: ``[done, not landed on x; land
it: …]`` holds a ``"; "``. A naive ``message.split("; ")`` tears that clause in
half and the test then asserts nonsense.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: U+2014, the separator `builtin_orchestrator` already uses to re-wrap a
#: child's reason into its parent's. The prerequisite elision reuses it (R3).
EM_DASH = "—"
#: U+2026 then "[cut]", preceded by one space (R1-AC1).
CUT_MARKER = " …[cut]"

_PREFIX_RE = re.compile(r"^Child ticket waiting on a prerequisite \((\d+)\): ")
_CHILD_RE = re.compile(
    r"^(?P<title>.*?) \[(?P<external_id>[^\[\]]+)\] \(waits for (?P<prereqs>.*)\)$",
    re.DOTALL,
)
_PREREQ_RE = re.compile(
    r"^(?P<slug>[^:\[\]]+):(?P<external_id>[^ \[\]]+) \[(?P<why>.*)\]$", re.DOTALL
)
_PREREQ_ELISION_RE = re.compile(
    r"^\+(?P<count>\d+) more prerequisites " + EM_DASH + r" loregarden_get_ticket (?P<child>\S+)$"
)
_CHILDREN_ELISION_RE = re.compile(r"^\+(?P<count>\d+) more$")


@dataclass(frozen=True)
class ParsedPrerequisite:
    slug: str
    external_id: str
    why: str

    @property
    def key(self) -> str:
        """The R4 sort key's second element, as the line itself spells it."""
        return f"{self.slug}:{self.external_id}"

    @property
    def is_not_landed(self) -> bool:
        return ", not landed on " in self.why


@dataclass(frozen=True)
class ParsedChild:
    title: str
    external_id: str
    prerequisites: list[ParsedPrerequisite]
    #: `j` from R3 — 0 when this child named all of its prerequisites.
    elided_prerequisites: int
    #: The child id the prerequisite elision points `loregarden_get_ticket` at,
    #: or "" when nothing was elided. R7-AC4: an elision with no pointer is not
    #: acceptable, so the test asserts this equals `external_id`.
    elision_pointer: str


@dataclass(frozen=True)
class ParsedHoldLine:
    total: int
    children: list[ParsedChild]
    #: `k` from R3 — 0 when every held child was named.
    elided_children: int


def split_top_level(text: str, separator: str) -> list[str]:
    """Split on `separator` only where no ``[…]`` group is open."""
    parts: list[str] = []
    depth = 0
    start = 0
    index = 0
    while index < len(text):
        char = text[index]
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
        elif depth == 0 and text.startswith(separator, index):
            parts.append(text[start:index])
            index += len(separator)
            start = index
            continue
        index += 1
    parts.append(text[start:])
    return parts


def parse_hold_line(message: str) -> ParsedHoldLine:
    prefix = _PREFIX_RE.match(message)
    assert prefix, f"hold line must lead with its count (R3-AC1): {message[:120]!r}"
    body = message[prefix.end() :]

    elided_children = 0
    children_clauses = split_top_level(body, "; ")
    tail = _CHILDREN_ELISION_RE.match(children_clauses[-1])
    if tail:
        elided_children = int(tail.group("count"))
        children_clauses = children_clauses[:-1]

    children = [_parse_child(clause, message) for clause in children_clauses]
    return ParsedHoldLine(
        total=int(prefix.group(1)), children=children, elided_children=elided_children
    )


def _parse_child(clause: str, message: str) -> ParsedChild:
    match = _CHILD_RE.match(clause)
    assert match, f"child clause must read '<title> [<id>] (waits for …)' (R3-AC1): {clause!r}"
    prereq_clauses = split_top_level(match.group("prereqs"), ", ")

    elided = 0
    pointer = ""
    elision = _PREREQ_ELISION_RE.match(prereq_clauses[-1])
    if elision:
        elided = int(elision.group("count"))
        pointer = elision.group("child")
        prereq_clauses = prereq_clauses[:-1]

    assert prereq_clauses, (
        "a named child must carry at least one complete prerequisite clause "
        f"(R5-AC3): {clause!r} in {message!r}"
    )
    return ParsedChild(
        title=match.group("title"),
        external_id=match.group("external_id"),
        prerequisites=[_parse_prerequisite(part) for part in prereq_clauses],
        elided_prerequisites=elided,
        elision_pointer=pointer,
    )


def _parse_prerequisite(clause: str) -> ParsedPrerequisite:
    match = _PREREQ_RE.match(clause)
    assert match, f"prerequisite must read '<slug>:<id> [<why>]' (R3-AC1): {clause!r}"
    return ParsedPrerequisite(
        slug=match.group("slug"), external_id=match.group("external_id"), why=match.group("why")
    )


def assert_plain_single_line(message: str) -> None:
    """R3-AC2 / R3-AC4, asserted at every fixture in this ticket's suite."""
    for forbidden, name in (
        ("\n", "newline"),
        ("\r", "carriage return"),
        ("\t", "tab"),
        ("\x1b", "ANSI escape"),
        ("`", "backtick"),
        ("**", "markdown emphasis"),
        ("](", "markdown link"),
    ):
        assert forbidden not in message, f"hold line must carry no {name}: {message!r}"
    assert "+0 more" not in message, f"an empty elision is never emitted: {message!r}"
    assert "(waits for )" not in message, f"a named child with no prerequisite: {message!r}"
    assert ", )" not in message, f"a dangling prerequisite list: {message!r}"
    assert "for ," not in message, f"a dangling prerequisite list: {message!r}"


def assert_reconciles(
    parsed: ParsedHoldLine, *, total: int, prerequisite_counts: list[int]
) -> None:
    """R5-AC6. Named + elided == total, at both levels.

    `prerequisite_counts` is the real unmet count per *named* child, in the
    order the line names them.
    """
    assert parsed.total == total, f"the count must be every held child: {parsed}"
    assert len(parsed.children) + parsed.elided_children == total, (
        f"named children plus '+k more' must equal the total: {parsed}"
    )
    assert len(prerequisite_counts) == len(parsed.children), (
        f"expected {len(prerequisite_counts)} named children, got {len(parsed.children)}"
    )
    for child, owed in zip(parsed.children, prerequisite_counts, strict=True):
        assert len(child.prerequisites) + child.elided_prerequisites == owed, (
            f"{child.external_id}: named {len(child.prerequisites)} + elided "
            f"{child.elided_prerequisites} must equal its {owed} unmet prerequisites"
        )
        if child.elided_prerequisites:
            assert child.elision_pointer == child.external_id, (
                "R7-AC4: a prerequisite elision points loregarden_get_ticket at the "
                f"child whose prerequisites were dropped: {child}"
            )
