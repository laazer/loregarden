"""The parent's hold line, composed against the width it is stored in.

One line of plain text answering, in one read and with no follow-up call, *why
has this subtree stopped, and what do I run to unstop it?* — how many children
are held, which ones, which prerequisite of each, why that prerequisite blocks,
and the command that clears it.

This module is a leaf, and that is the point. Both ends of one invariant live
here: the composer that must fit the column, and the cut that enforces it. The
orchestrator that composes imports the callback service that stores, so a leaf
imported by both is the only placement where the width exists once
(lg-milestone-that-919).

The defect it closes: the composer joined *every* unmet prerequisite and the
storage site then stored a blind character slice. Measured on the live maximum
(one dependent, 18 done-but-unlanded prerequisites) the 14th clause ended
``land it: loregarden_land_ticket lg-milestone-`` and four prerequisites
vanished with no indication at all. The remedy is what makes the hold
actionable, so the fix is not to drop it: the line is composed against the
budget, and every elision names the command that shows what it dropped.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from loregarden.models.domain import Ticket, Workspace
from loregarden.services.dependency_readiness import UnmetPrerequisite, UnmetReason
from sqlmodel import Session, select

#: The width ``OrchestrationRun.error_message`` (models/domain/tables.py:306) is
#: stored in. SQLite declares no length, so this is the budget this control
#: plane chose rather than one the column enforces — which is exactly why it
#: must exist once: the composer below spends it and `truncate_for_storage` is
#: the single place it is applied.
ERROR_MESSAGE_LIMIT = 2000

#: Appended by `truncate_for_storage` when it fires, so a stored message that
#: stops early says so instead of looking like the whole of what happened.
CUT_MARKER = " …[cut]"

#: How many held children the hold line names before it switches to a count.
#: A ceiling, not a target: the budget below may bind first (779).
WAITING_NAMES = 5

#: U+2014, the separator the orchestrator already uses to re-wrap a child's
#: reason into its parent's. The prerequisite elision reuses it.
_EM_DASH = "—"
_CHILD_SEPARATOR = "; "
_PREREQUISITE_SEPARATOR = ", "


def truncate_for_storage(message: str) -> str:
    """Bound `message` to the stored width without ending mid-token.

    The only place the width is applied. Reached by *every* terminal
    orchestration message — agent error text, gate output, a traceback — not
    only hold lines, which is what makes "no identifier is ever truncated
    mid-token" true rather than true-of-the-line-we-happened-to-fix.

    The cut lands on the last ``;``, ``,`` or whitespace that is not inside a
    ``[…]`` group, so a bracketed prerequisite clause is dropped whole rather
    than halved. A message with no such separator in the keep window is cut
    bluntly; nothing reaching this site has a 1993-character token, but the
    bound may not depend on that.
    """
    if len(message) <= ERROR_MESSAGE_LIMIT:
        return message
    keep = ERROR_MESSAGE_LIMIT - len(CUT_MARKER)
    index = _last_separator_outside_brackets(message, keep)
    head = message[:keep] if index is None else message[:index]
    return head.rstrip().rstrip(";,").rstrip() + CUT_MARKER


def _last_separator_outside_brackets(message: str, keep: int) -> int | None:
    """The largest index below `keep` holding a separator at bracket depth 0."""
    opened = 0
    closed = 0
    last: int | None = None
    for index in range(min(keep, len(message))):
        char = message[index]
        if opened == closed and (char in ";," or char.isspace()):
            last = index
        if char == "[":
            opened += 1
        elif char == "]":
            closed += 1
    return last


@dataclass(frozen=True)
class HeldChild:
    """A child that may not run, paired with what is holding it.

    The composer needs both: the child names itself, and its prerequisites are
    what the operator acts on. Deciding the hold and rendering it are separate
    jobs, so the partition hands this over rather than a pre-composed string.
    """

    ticket: Ticket
    unmet: list[UnmetPrerequisite]


def render_hold_line(session: Session, held: list[HeldChild]) -> str:
    """Render the hold line for `held`, within `ERROR_MESSAGE_LIMIT`.

    The budget is spent in three phases, so the line is bounded by composition
    and never by the cut:

    1. the prefix, plus an upper-bound reserve for the children elision;
    2. admission — each of the first `WAITING_NAMES` children is named only if
       its *minimum* clause fits (its first prerequisite plus its own elision
       reserve), and admission **stops** at the first that does not. Skipping
       would make the named set depend on how long a title happens to be,
       which an operator cannot predict, and the children already arrive in
       dependency order;
    3. growth — round-robin over the admitted children, one prerequisite clause
       at a time. Round-robin, not greedy-first, is what stops one child with
       18 prerequisites consuming the budget its siblings need.

    Empty when nothing is held: a hold line never reports a count of zero.
    """
    if not held:
        return ""
    total = len(held)
    prefix = f"Child ticket waiting on a prerequisite ({total}): "
    slugs = _workspace_slugs(session, held[:WAITING_NAMES])

    budget = ERROR_MESSAGE_LIMIT - len(prefix)
    if total > 1:
        # An upper bound: the digit count only shrinks as more children are
        # named, so the reserve is never short. A few unspent characters are
        # cheaper than a fixed point over digit widths.
        budget -= len(f"{_CHILD_SEPARATOR}+{total - 1} more")

    admitted: list[_Clause] = []
    for child in held[:WAITING_NAMES]:
        clause = _clause_for(child, slugs)
        if clause is None:
            break
        cost = clause.minimum_cost + (len(_CHILD_SEPARATOR) if admitted else 0)
        if cost > budget:
            break
        budget -= cost
        admitted.append(clause)
    _grow(admitted, budget)

    parts = [clause.render() for clause in admitted]
    elided = total - len(admitted)
    if elided:
        parts.append(f"+{elided} more")
    return prefix + _CHILD_SEPARATOR.join(parts)


def _grow(admitted: list[_Clause], budget: int) -> int:
    """Hand out one more prerequisite clause per child per round, while they fit.

    A child already naming all of its prerequisites is stepped over rather than
    ending the round: ending on it would leave its siblings at their admitted
    minimum, and not noticing it at all would never terminate.
    """
    while True:
        spent = 0
        for clause in admitted:
            cost = clause.growth_cost()
            if cost is not None and cost <= budget:
                budget -= cost
                spent += 1
                clause.grow()
        if not spent:
            return budget


@dataclass
class _Clause:
    """One child's clause, and how much its next prerequisite would cost."""

    head: str
    external_id: str
    prerequisites: list[str] = field(default_factory=list)
    named: int = 1

    @property
    def minimum_cost(self) -> int:
        """The clause with exactly its first prerequisite, plus its reserve."""
        return (
            len(self.head)
            + len(self.prerequisites[0])
            + 1
            + len(self._elision(len(self.prerequisites) - 1))
        )

    def growth_cost(self) -> int | None:
        """Cost of naming one more prerequisite, or None when none are left."""
        remaining = self._remaining
        if remaining <= 0:
            return None
        return (
            len(_PREREQUISITE_SEPARATOR)
            + len(self.prerequisites[self.named])
            + len(self._elision(remaining - 1))
            - len(self._elision(remaining))
        )

    def grow(self) -> None:
        self.named += 1

    def render(self) -> str:
        body = _PREREQUISITE_SEPARATOR.join(self.prerequisites[: self.named])
        return f"{self.head}{body}{self._elision(self._remaining)})"

    @property
    def _remaining(self) -> int:
        return len(self.prerequisites) - self.named

    def _elision(self, remaining: int) -> str:
        """Every elision names the command that shows what it dropped — the
        unelided view is `loregarden_get_ticket`'s `blocked_by`."""
        if remaining <= 0:
            return ""
        return (
            f"{_PREREQUISITE_SEPARATOR}+{remaining} more prerequisites "
            f"{_EM_DASH} loregarden_get_ticket {self.external_id}"
        )


def _clause_for(child: HeldChild, slugs: dict[str, str]) -> _Clause | None:
    """None when there is nothing to name — a clause with an empty
    ``(waits for )`` says less than the count already did."""
    if not child.unmet:
        return None
    ordered = sorted(child.unmet, key=lambda entry: _sort_key(entry, slugs))
    return _Clause(
        head=f"{child.ticket.title} [{child.ticket.external_id}] (waits for ",
        external_id=child.ticket.external_id,
        prerequisites=[_prerequisite_clause(entry, slugs) for entry in ordered],
    )


def _sort_key(entry: UnmetPrerequisite, slugs: dict[str, str]) -> tuple[int, str]:
    """Actionable first, then plain code-point order on the name as rendered.

    NOT_LANDED leads because an operator clears it with one command, while
    NOT_DONE clears itself when ordering runs the prerequisite.
    """
    actionable = 0 if entry.reason is UnmetReason.NOT_LANDED else 1
    return actionable, _name(entry, slugs)


def _name(entry: UnmetPrerequisite, slugs: dict[str, str]) -> str:
    """The workspace is not decoration. A prerequisite in another workspace is
    invisible from the board the reader is looking at, so "waits for
    lor-extract-lore-35" sends them looking in the wrong place; "lore-eden:
    lor-extract-lore-35" does not (770)."""
    return f"{slugs.get(entry.ticket.workspace_id, '?')}:{entry.ticket.external_id}"


def _prerequisite_clause(entry: UnmetPrerequisite, slugs: dict[str, str]) -> str:
    return f"{_name(entry, slugs)} [{_why(entry)}]"


def _why(entry: UnmetPrerequisite) -> str:
    """Why this prerequisite blocks, and for the actionable reason, the remedy.

    "done, not landed on integration/x" and "in_progress" are different things
    to go and fix (770). The remedy rides along because the first does not
    clear itself: landing runs only on a terminal-stage transition, so a
    prerequisite already `done` never re-lands on its own (779). Named on the
    prerequisite — landing the held child delivers nothing. One id per clause,
    because `loregarden_land_ticket` takes exactly one `ticket_id`, so a
    multi-id remedy would not be a command anyone could copy.
    """
    if entry.reason is UnmetReason.NOT_LANDED:
        return (
            f"{entry.ticket.state.value}, not landed on {entry.target}; "
            f"land it: loregarden_land_ticket {entry.ticket.external_id}"
        )
    return entry.ticket.state.value


def _workspace_slugs(session: Session, held: list[HeldChild]) -> dict[str, str]:
    workspace_ids = {entry.ticket.workspace_id for child in held for entry in child.unmet}
    if not workspace_ids:
        return {}
    rows = session.exec(select(Workspace).where(Workspace.id.in_(workspace_ids))).all()
    return {row.id: row.slug for row in rows}
