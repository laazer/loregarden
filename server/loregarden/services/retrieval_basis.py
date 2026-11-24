"""The basis an empty answer ships with.

Two retrieval surfaces return a bare empty list for several different facts —
the store is not configured, the query tokenised to nothing, nothing matched —
and a caller cannot tell them apart. Every one of them reads as "there is no
prior work", and the most expensive of them (a typo in a slug) reads as the
cheapest.

This module is pure policy. It imports `models/domain/enums` and nothing else:
no `sqlmodel`, no `Session`, no `Path`. The producers measure; this decides
what a measurement is allowed to claim.

The failure mode it is built against is its own: a basis that becomes a new
confident lie. The obvious field name is one `rows_in_scope` and the obvious
value is the scan limit, which would pass its own tests and tell every
downstream stage that an unexamined corpus was examined. Hence three separate
counts, arithmetic checked at construction, and a quantity nobody persists
typed as `None` rather than guessed at.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from loregarden.models.domain.enums import MemoryStoreState, RetrievalEmptiness

#: On every reply, empty or not. An empty hit list is a statement about the
#: scope that was queried, never about the world.
EMPTY_IS_NOT_ABSENCE = (
    "An empty hit list means nothing in the queried scope matched. "
    "It is not proof that nothing relevant exists."
)

#: The one case where an empty list carries no information at all.
ZERO_CORPUS_CARRIES_NO_INFORMATION = (
    "Where the corpus total is 0 there was nothing to match against, "
    "so an empty list carries no information either way."
)

#: How many zero-hit areas `untouched` will ever name. Beyond three the
#: sentence stops being a sentence and starts being a list nobody reads.
MAX_UNTOUCHED_AREAS = 3

#: The default character budget for that sentence, sized for a prompt line.
DEFAULT_UNTOUCHED_BUDGET = 240


@dataclass(frozen=True)
class Reduction:
    """What a reduced collection dropped, and why.

    `omitted` is a multiset difference over a stable key, never a tail slice:
    a projection picked for diversity is not a prefix of its source, so
    `everything[len(emitted):]` would name rows that were in fact emitted.

    The omitted set travels inline. A persisted recovery store with a
    reference into it was considered and rejected: inlining removes the write
    path, which makes a dead reference unreachable rather than handled.
    """

    total: int
    emitted: int
    truncated: int
    reason: str
    omitted: tuple[Any, ...]


def reduce_to(
    everything: Sequence[Any],
    emitted: Sequence[Any],
    *,
    reason: str,
    key: Callable[[Any], Any],
) -> Reduction:
    """The reduction from `everything` to `emitted`, keyed by `key`.

    `key` exists because rows here are dicts, which are unhashable, and
    because two distinct rows may legitimately share a key — two settled
    tickets can share a title. The difference is therefore a multiset
    difference: a key collision must not delete a row from the omitted set,
    because under-reporting what was dropped is the direction that reads as
    reassurance.
    """
    source_keys = Counter(key(item) for item in everything)
    emitted_keys = Counter(key(item) for item in emitted)
    extra = emitted_keys - source_keys
    if extra:
        raise ValueError(
            "a reduction describes one collection; "
            f"the projection holds {sum(extra.values())} item(s) the source never had"
        )

    remaining = source_keys - emitted_keys
    omitted: list[Any] = []
    budget = Counter(remaining)
    for item in everything:
        item_key = key(item)
        if budget[item_key] > 0:
            budget[item_key] -= 1
            omitted.append(item)

    truncated = len(omitted)
    if truncated and not reason.strip():
        raise ValueError("a reduction that dropped rows must name why; a silent cap is the defect")
    return Reduction(
        total=len(everything),
        emitted=len(emitted),
        truncated=truncated,
        reason=reason,
        omitted=tuple(omitted),
    )


def untouched(
    areas: Iterable[str],
    *,
    scope: str,
    budget: int = DEFAULT_UNTOUCHED_BUDGET,
) -> str:
    """One sentence naming at most three zero-hit areas, or `''`.

    The list shrinks until the sentence fits the budget; it is never cut
    mid-sentence, because half a sentence about what was not searched is worse
    than silence. `''` is returned when even one area will not fit.

    Areas arrive largest-first and are kept in that order: dropping from the
    front would name the three least useful.
    """
    ordered: list[str] = []
    for area in areas:
        text = str(area).strip()
        if text and text not in ordered:
            ordered.append(text)
    ordered = ordered[:MAX_UNTOUCHED_AREAS]

    while ordered:
        sentence = f"Nothing matched in {scope} under {_join(ordered)}."
        if len(sentence) <= budget:
            return sentence
        ordered.pop()
    return ""


def _join(areas: Sequence[str]) -> str:
    if len(areas) == 1:
        return areas[0]
    return f"{', '.join(areas[:-1])} or {areas[-1]}"


@dataclass(frozen=True)
class RetrievalBasis:
    """Why a retrieval answer is the size it is.

    `corpus_total`, `scanned` and `not_examined` are three fields and never
    one. `search_prior_work` scans at most 400 settled tickets with no
    `order_by`, so a single number would say the scope was examined on a
    corpus of any size.

    `exported_into_store` is the share of the workspace's knowledge actually
    exported into the queried store. **It is always `None`.** Nobody persists
    it: no row in this system records what a workspace knows against what
    reached a store, so there is no rate to report. `0` would say the store
    holds none of it, which is a different claim and one nothing supports.

    `legs` reuses `models/domain/enums.MemoryStoreState`, the vocabulary
    `agents/inherited_wisdom.py` already consumes. A parallel leg-state enum
    would let the two basis producers drift.
    """

    corpus_total: int
    scanned: int
    not_examined: int
    legs: Mapping[str, MemoryStoreState]
    emptiness: RetrievalEmptiness = RetrievalEmptiness.MATCHED
    #: Term -> why it was dropped, for the question an empty answer raises
    #: first: was my query even used?
    dropped_terms: Mapping[str, str] = field(default_factory=dict)
    #: Whether the discriminativeness ceiling ran at all. Load-bearing beside
    #: `dropped_terms`: an empty mapping means "nothing was too common" only if
    #: the ceiling ran, and means "nobody measured" if it did not. The memory
    #: graph leg has no corpus count, so it is always False there — which is
    #: the honest answer AC8 asks for rather than a zero nobody measured.
    ceiling_applied: bool = False
    #: One sentence naming zero-hit areas inside the scope that was queried.
    untouched_sentence: str = ""
    #: Always `None`; see the class docstring for `exported_into_store`.
    exported_into_store: int | None = None
    reductions: Mapping[str, Reduction] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name, value in (
            ("corpus_total", self.corpus_total),
            ("scanned", self.scanned),
            ("not_examined", self.not_examined),
        ):
            if value < 0:
                raise ValueError(f"{name} cannot be negative ({value})")
        if self.scanned > self.corpus_total:
            raise ValueError(
                f"scanned ({self.scanned}) cannot exceed corpus_total ({self.corpus_total}): "
                "reading more rows than exist is a number that cannot be true"
            )
        if self.not_examined != self.corpus_total - self.scanned:
            raise ValueError(
                f"not_examined ({self.not_examined}) must be corpus_total - scanned "
                f"({self.corpus_total - self.scanned})"
            )
        if self.exported_into_store is not None:
            raise ValueError(
                "exported_into_store is always None: nothing persists what share of a "
                "workspace's knowledge reached a store, and a rate here would be invented"
            )
        legs = dict(self.legs)
        for kind, state in legs.items():
            # By type, not by value: `MemoryStoreState` mixes in `str`, so a
            # bare `"read"` compares equal to a member and would slip a second
            # vocabulary through an equality check.
            if not issubclass(type(state), MemoryStoreState):
                raise TypeError(
                    f"leg {kind!r} must carry a MemoryStoreState, not {state!r}; "
                    "a parallel leg vocabulary is what this type exists to refuse"
                )
        object.__setattr__(self, "legs", MappingProxyType(legs))
        object.__setattr__(self, "dropped_terms", MappingProxyType(dict(self.dropped_terms)))
        object.__setattr__(self, "reductions", MappingProxyType(dict(self.reductions)))

    @property
    def notes(self) -> tuple[str, ...]:
        """The two fixed notes, on every reply rather than on empty ones."""
        return (EMPTY_IS_NOT_ABSENCE, ZERO_CORPUS_CARRIES_NO_INFORMATION)

    def as_dict(self) -> dict[str, Any]:
        """JSON-ready: the MCP envelope `json.dumps` this with no encoder."""
        return {
            "corpus_total": self.corpus_total,
            "scanned": self.scanned,
            "not_examined": self.not_examined,
            "legs": {kind: state.value for kind, state in self.legs.items()},
            "emptiness": self.emptiness.value,
            "dropped_terms": dict(self.dropped_terms),
            "ceiling_applied": self.ceiling_applied,
            "untouched_sentence": self.untouched_sentence,
            "exported_into_store": self.exported_into_store,
            "reductions": {
                name: {
                    "total": reduction.total,
                    "emitted": reduction.emitted,
                    "truncated": reduction.truncated,
                    "reason": reduction.reason,
                    # Inline, because that is the entire recovery path: AC10
                    # rejected a persisted store with a reference into it, so
                    # a reduction that reported only a count would leave the
                    # dropped rows unreachable rather than merely unfetched.
                    "omitted": list(reduction.omitted),
                }
                for name, reduction in self.reductions.items()
            },
            "notes": list(self.notes),
        }


def envelope_partial(
    basis: RetrievalBasis, *, extra: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """The whole `partial` field for an MCP reply, built in one place.

    `tool_registry` and `memory_tools` both answer the same question, and
    each used to spell the `dropped_terms -> partial["terms"]` branch
    separately. That is how two envelopes start disagreeing about what
    partiality looks like, so the shared shape lives here and each caller
    passes only what is genuinely its own (`extra`): a degraded leg for the
    memory tool, an unexamined-rows count for prior work.

    Key -> VALUE, never a key name: the producer already holds the answer, so
    naming only the key would discard it. Entries are absent rather than
    present-and-zero, because `partial` is a list of what is missing and a
    reduction that dropped nothing is not missing anything.
    """
    partial: dict[str, Any] = dict(extra or {})
    if basis.dropped_terms:
        partial["terms"] = dict(basis.dropped_terms)
    partial.update(partial_reductions(basis))
    return partial


def partial_reductions(basis: RetrievalBasis) -> dict[str, Any]:
    """The `partial` entries for every collection this reply truncated."""
    return {
        f"truncated_{name}": {
            "total": reduction.total,
            "emitted": reduction.emitted,
            "omitted": list(reduction.omitted),
            "reason": reduction.reason,
        }
        for name, reduction in basis.reductions.items()
        if reduction.truncated
    }
