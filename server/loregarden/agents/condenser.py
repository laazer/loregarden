"""Shrinking a conversation without losing the task that started it.

The LM Studio runner grows one `messages` list across its tool-call loop with no
bound: every round appends an assistant turn plus a tool result of up to 8,000
characters. `MAX_TOOL_ROUNDS` bounds the number of rounds, not the size of what
they accumulate, so a long stage arrives at the model carrying everything it has
ever said (lg-workflow-integrity-380).

Sibling 163 handles the same axis by throwing the conversation away and starting
fresh with a cap — cheapest to build, loses everything the run learned. This is
the graduated version: keep the head, keep a recent tail, replace the middle with
a summary.

WHY HEAD AND TAIL. The first message carries the task and its constraints, and
losing it is how a run forgets what it was asked to do while still sounding
busy. The tail is where the model is actually working. The middle is the part
that can usually be said more briefly.

WHAT THIS DELIBERATELY DOES NOT DO. It writes no durable record. `lg-agent-prompt-379`
owns condensation-as-an-event — what was removed, the summary that replaced it,
and where the summary belongs — and inventing a second scheme here is what that
ticket exists to prevent. `CondensationResult` carries those facts back to the
caller so 379 can persist them; this module just does not decide how.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

logger = logging.getLogger(__name__)

#: Head turns kept verbatim. One is enough to hold the task statement, which is
#: the thing that must never fall out; more would be guessing.
DEFAULT_HEAD = 1

#: Recent turns kept verbatim. An assistant turn and its tool results travel
#: together, so a tail shorter than a few messages can split a tool_call from
#: its result and leave the model reading a reply to a question it cannot see.
DEFAULT_TAIL = 6

#: Total characters across all messages, above which condensation triggers.
DEFAULT_MAX_CHARS = 120_000

#: A summariser calls a model, so it is a cost and a failure point of its own.
DEFAULT_SUMMARY_TIMEOUT_SECONDS = 60


def _size(messages: Sequence[dict]) -> int:
    """Total characters across message contents.

    Characters rather than tokens on purpose: this runs in the subprocess, which
    has no tokenizer for whichever local model is loaded, and a threshold that
    is roughly right and always available beats one that is exact and sometimes
    absent.
    """
    return sum(len(str(message.get("content") or "")) for message in messages)


@dataclass(frozen=True)
class CondensationResult:
    """What a condensation did, in enough detail for someone else to record it.

    Deliberately a value rather than a side effect: `lg-agent-prompt-379` will
    persist this as a condensation event, and a strategy that had already
    written its own log would have to be unpicked first.
    """

    messages: list[dict]
    dropped: int = 0
    summary: str = ""
    #: True when the middle was discarded without a summary — the degraded path.
    degraded: bool = False
    original_chars: int = 0
    condensed_chars: int = 0

    @property
    def condensed(self) -> bool:
        return self.dropped > 0

    def describe(self) -> str:
        how = "dropped" if self.degraded else "summarised"
        return (
            f"condensed {self.dropped} message(s), {how}: "
            f"{self.original_chars} -> {self.condensed_chars} chars"
        )


class Condenser(Protocol):
    """A strategy for shrinking a conversation."""

    def should_condense(self, messages: Sequence[dict]) -> bool: ...

    def condense(self, messages: Sequence[dict]) -> CondensationResult: ...


@dataclass(frozen=True)
class NoOpCondenser:
    """Does nothing, and is the default.

    Every runner opts in explicitly. Being able to prove the inert case is inert
    is why this exists as a real class rather than as a `None` check scattered
    through the loop.
    """

    def should_condense(self, messages: Sequence[dict]) -> bool:
        return False

    def condense(self, messages: Sequence[dict]) -> CondensationResult:
        size = _size(messages)
        return CondensationResult(
            messages=list(messages), original_chars=size, condensed_chars=size
        )


@dataclass
class SummarisingCondenser:
    """Keep the head and the tail; replace the middle with a summary.

    `summarise` is injected rather than built here: the caller owns the model
    client, and a strategy that reached for its own would be untestable without
    one. It is handed the messages being removed and must return prose, or raise
    — and raising is a supported outcome, not an error path nobody walks.
    """

    summarise: Callable[[list[dict]], str] | None = None
    head: int = DEFAULT_HEAD
    tail: int = DEFAULT_TAIL
    max_chars: int = DEFAULT_MAX_CHARS
    _events: list[CondensationResult] = field(default_factory=list, repr=False)

    def should_condense(self, messages: Sequence[dict]) -> bool:
        """Whether this conversation is over budget *and* can usefully shrink.

        Both halves matter. A conversation of head + tail messages is already as
        small as this strategy can make it, and returning True for it would spin
        the caller through a condensation that removes nothing every round.
        """
        return len(messages) > self.head + self.tail and _size(messages) > self.max_chars

    def condense(self, messages: Sequence[dict]) -> CondensationResult:
        """Shrink `messages`, degrading to a plain drop if summarising fails."""
        original = _size(messages)
        if len(messages) <= self.head + self.tail:
            # Nothing in the middle to remove. Returned unchanged rather than
            # raising: a caller reacting to a provider overflow signal has no
            # better option to fall back to, and failing here would turn a
            # recoverable step into a dead run.
            return CondensationResult(
                messages=list(messages), original_chars=original, condensed_chars=original
            )

        head = list(messages[: self.head])
        tail = list(messages[len(messages) - self.tail :])
        middle = list(messages[self.head : len(messages) - self.tail])

        summary, degraded = self._summarise_middle(middle)
        kept = head + ([{"role": "user", "content": summary}] if summary else []) + tail
        result = CondensationResult(
            messages=kept,
            dropped=len(middle),
            summary=summary,
            degraded=degraded,
            original_chars=original,
            condensed_chars=_size(kept),
        )
        self._events.append(result)
        logger.info("Condensed conversation — %s", result.describe())
        return result

    def _summarise_middle(self, middle: list[dict]) -> tuple[str, bool]:
        """Prose standing in for `middle`, and whether we had to degrade.

        A summariser that calls a model is a run cost and a failure point. If it
        raises — timeout, a model that will not load, a provider error — the
        middle is dropped and said so, because losing the middle is survivable
        and failing the parent run over a compression step is not.
        """
        if self.summarise is None:
            return _dropped_marker(len(middle)), True
        try:
            text = (self.summarise(middle) or "").strip()
        except Exception as exc:  # noqa: BLE001 - degrade, and say why
            logger.warning("Summariser failed, dropping the middle instead: %s", exc)
            return _dropped_marker(len(middle), reason=str(exc)), True
        if not text:
            logger.warning("Summariser returned nothing, dropping the middle instead")
            return _dropped_marker(len(middle)), True
        return (
            f"[Earlier conversation condensed — {len(middle)} messages replaced "
            f"by this summary]\n{text}",
            False,
        )

    @property
    def events(self) -> list[CondensationResult]:
        """Every condensation this instance performed, oldest first.

        Held so the caller can hand them to whatever records them — see the
        module docstring on why this module does not.
        """
        return list(self._events)


def _dropped_marker(count: int, *, reason: str = "") -> str:
    """The stand-in for a middle nobody could summarise.

    Still a message rather than a silent gap: the model should know it is
    missing something, or it will treat the head and tail as adjacent and
    reason about a conversation that never happened.
    """
    because = f" ({reason})" if reason else ""
    return (
        f"[Earlier conversation dropped — {count} messages removed without a "
        f"summary{because}. Ask again if you need something from them.]"
    )
