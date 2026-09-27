"""Enums for durable memory: the outcome ladder (178) and the relation vocabulary.

Split out of `enums.py`, which had reached its size cap.
"""

from __future__ import annotations

from enum import Enum


class LearningOutcomeRung(str, Enum):
    """How a run a learning was surfaced into actually ended (lg-improved-memory-178).

    Derived from what the control plane recorded about the run — gate
    evaluations, reroute ledgers, block classifications — and never from what
    the run's agent said about itself. Ordered best to worst; when a run earns
    several, the worst one is its rung.
    """

    CLEAN_PASS = "clean_pass"
    PASSED_AFTER_AUTOFIX = "passed_after_autofix"
    REROUTED = "rerouted"
    BLOCKED = "blocked"


class MemoryRelationType(str, Enum):
    """What a `memory_relations` edge asserts. A closed vocabulary, deliberately small.

    Most edges should stay ``RELATED`` — a plain mention. Type an edge only when
    the relationship is the point, and only when the writer states it: an agent
    guessing at relationships produces a graph that looks rigorous and encodes
    its own assumptions. Every extra type is one more choice made wrongly on
    some edge, so this list grows only with a reader that uses the new type.

    ``SUPERSEDES`` means the source replaced the target because the target
    *stopped being true* (an API changed, a decision was revisited). It is not
    ``discredited``, which means the target *was recorded wrong*: a superseded
    learning stays visible, labelled and ranked below its successor; a
    discredited one is withdrawn from every agent read.
    """

    RELATED = "related"
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    EXTENDS = "extends"
    PART_OF = "part_of"
    APPLIES = "applies"
    SUPERSEDES = "supersedes"


class RelationDirection(str, Enum):
    """Which end of a `memory_relations` edge a surfaced node sits on (179)."""

    OUT = "out"  # the surfaced node is the edge's source
    IN = "in"  # the surfaced node is the edge's target
