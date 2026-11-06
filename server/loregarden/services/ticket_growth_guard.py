"""The write-time size caps: the predicates, the refusals, and nothing else.

Pure by construction — scalars and strings only, no ORM model, no ``Session``,
no service import — so it sits below ``acceptance_criteria``,
``ticket_size_limits``, ``ticket_service`` and ``orchestration`` alike. The
alternative considered and rejected was putting the description guard in
``ticket_size_limits``, which is the 29-line read-side describer and imports the
``Ticket`` model: calling it from a write seam inverts the dependency it exists
to avoid.

**Two caps, two rules, deliberately different** (lg-milestone-that-2250):

* Acceptance criteria refuse **growth** past the cap — ``after > cap and after >
  before``. Their writers have an append mode whose ``merge_criteria`` dedupes,
  so a re-run stage re-sending criteria it already filed must stay legal, and
  growth is the thing to refuse.
* A description refuses only the **crossing** — ``before <= cap and after >
  cap``. No writer has a description append mode; every write is a full replace.
  A growth rule would refuse a rewrite of a 19,532-character description that
  landed at 19,533 — including a shortening attempt that overshot — and the
  agent's text is its only copy. 34 live tickets were over the cap when this
  landed, and all of them stay editable.

The refusal reaches two audiences from one place, so a reword cannot drift
between them: ``str(exc)`` is the **agent** wording naming ``depends_on``
(``mcp/protocol.py`` renders a raised exception as text, which is MCP's only
channel), and ``payload`` is the structured 400 detail whose ``message`` is
phrased for a person at the screen — the shape ``PATCH /api/tickets/{id}``
already returns for its 409 (``TicketRevisionConflict.payload``).
"""

from __future__ import annotations

#: The most characters a ticket's description may be written to. The read-side
#: split trigger imports this rather than respelling it
#: (``ticket_size_limits.SPLIT_AT_DESCRIPTION_CHARS``): one number, two names.
#: Measured 2026-11-05 on the live database: 34 of 2,733 descriptions are over
#: it, the largest 159,960 characters.
MAX_DESCRIPTION_CHARS = 15_000


def would_exceed(before: int, after: int, cap: int) -> bool:
    """Would this write take a value from at or under ``cap`` to over it?

    The crossing rule. Takes lengths, not text, because it is the predicate a
    caller reaches for when it already has counts — ``chat_feature_loop`` has a
    queue depth, not a list.
    """
    return before <= cap and after > cap


def would_grow_past(before: int, after: int, cap: int) -> bool:
    """Would this write leave a value over ``cap`` *and* larger than it was?

    The growth rule, which is the criteria half. Separate from
    :func:`would_exceed` rather than a flag: they disagree at ``(cap + 1, cap +
    2)``, which is exactly the case the two fields answer differently.
    """
    return after > cap and after > before


def agent_refusal(
    *, what: str, cap_text: str, before_text: str, after_text: str, instead: str
) -> str:
    """The wording an agent reads, naming the resulting count and the next action.

    Every refusal in this feature is built here, so none of them can be reworded
    into "nothing happened" or lose the ``depends_on`` route a stage agent's next
    move depends on.
    """
    return (
        f"A ticket's {what} holds at most {cap_text}; this write would take it from "
        f"{before_text} to {after_text}. File the overflow as a sibling ticket under the same "
        "parent with loregarden_create_ticket and depends_on set to this ticket, rather than "
        f"{instead}."
    )


def operator_refusal(*, subject: str, after_text: str, cap_text: str, remedy: str) -> str:
    """The wording a person at the screen reads: no tool names, a next action, a draft kept."""
    return (
        f"{subject} would {after_text}; the cap is {cap_text}. {remedy}, or open a new ticket "
        "under this one's parent for the extra work. Nothing was saved and your draft is "
        "still here."
    )


class SizeCapError(ValueError):
    """A write refused for taking a ticket past a write-time size cap.

    A ``ValueError`` so every existing writer's error handling is unchanged —
    which is why ``api/tickets.py`` must catch this *before* its generic
    ``except ValueError`` arm, or the structured payload flattens to prose.

    ``str(self)`` stays the agent wording rather than becoming the JSON payload
    (the shape ``TicketRevisionConflict`` chose), because MCP's only channel is
    that text and an agent needs the sentence, not a dict.
    """

    #: The ``error`` field of :attr:`payload`; the client switches on it.
    error_code = ""

    def __init__(
        self, message: str, *, cap: int, before: int, after: int, operator_message: str
    ) -> None:
        super().__init__(message)
        self.cap = cap
        self.before = before
        self.after = after
        self.operator_message = operator_message

    @property
    def payload(self) -> dict[str, object]:
        """The 400 detail: one fact and a person's next action, structured."""
        return {
            "error": self.error_code,
            "cap": self.cap,
            "before": self.before,
            "after": self.after,
            "message": self.operator_message,
        }


class DescriptionCapError(SizeCapError):
    """A write would take a description from at or under the cap to over it."""

    error_code = "description_cap"


def guard_description_growth(before: str | None, after: str | None) -> None:
    """Refuse a description write that crosses :data:`MAX_DESCRIPTION_CHARS`.

    Takes the **text** rather than two lengths: every caller has the text and
    none of them has a length, and an absent field is ``None`` on both bodies —
    so ``None`` has to mean "no description", not "zero characters I should
    compare against". A description already over the cap may be rewritten
    freely, longer included.
    """
    before_chars = len(before or "")
    after_chars = len(after or "")
    if not would_exceed(before_chars, after_chars, MAX_DESCRIPTION_CHARS):
        return
    raise DescriptionCapError(
        agent_refusal(
            what="description",
            cap_text=f"{MAX_DESCRIPTION_CHARS:,} characters",
            before_text=f"{before_chars:,}",
            after_text=f"{after_chars:,}",
            instead="cutting this one to fit",
        ),
        cap=MAX_DESCRIPTION_CHARS,
        before=before_chars,
        after=after_chars,
        operator_message=operator_refusal(
            subject="This description",
            after_text=f"be {after_chars:,} characters",
            cap_text=f"{MAX_DESCRIPTION_CHARS:,}",
            remedy="Shorten it",
        ),
    )
