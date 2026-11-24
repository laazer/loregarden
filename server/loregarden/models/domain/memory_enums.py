"""Enums for durable memory: the outcome ladder (178), the relation vocabulary,
and what one retrieval over the memory layer was able to read.

Split out of `enums.py`, which had reached its size cap. The briefing/retrieval
family joined them for the same reason, and because they belong together:
`MemoryStoreState` is the one leg vocabulary both basis producers read, and
`RetrievalEmptiness` is why an answer is the size it is. `enums.py` re-exports
them, so every existing importer is unaffected.
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


class MemoryOriginKind(str, Enum):
    """Who or what produced a memory node (766). Stored as its value; NULL is "unknown".

    NULL is a state of its own, not a default for any member: nodes written
    before provenance was recorded, and writes whose caller the control plane
    cannot identify, have no origin — and a browser must say "origin unknown"
    rather than guess one.

    ``AGENT``  a Loregarden-supervised agent run; `origin_ref` is its run id when
               the write path carries one.
    ``HUMAN``  a person; `origin_ref` names them.
    ``IMPORT`` brought in from elsewhere; `origin_ref` is the source path.
    """

    AGENT = "agent"
    HUMAN = "human"
    IMPORT = "import"


class MemoryNodeType(str, Enum):
    """The kinds of node the memory graph records (see `memory_authority`)."""

    MEMORY = "memory"
    LEARNING = "learning"


class InferredGroupKind(str, Enum):
    """Why the knowledge browser shows records together without a recorded edge.

    Derived on read from facts the records already carry, never written back as
    `memory_relations`: an inferred group is a *reading aid*, and storing it as
    an edge would make a guess look like something an agent asserted. The
    fact-vs-inference rule itself lives in `models.domain.provenance`, which is
    where an inference says what produced it; a third restatement of it here
    would be a third thing that can drift.
    """

    SAME_TICKET = "same_ticket"  # written while working the same ticket
    SAME_MILESTONE = "same_milestone"  # different tickets under one milestone
    SHARED_TAG = "shared_tag"  # a tag specific enough to mean something


class KnowledgeGraphSource(str, Enum):
    """Which reader produced a knowledge-graph response, so the UI labels it honestly."""

    LIST = "list"  # `list_nodes`: newest first, no text match
    SEARCH = "search"  # `search`: substring match over title, body, tags and aliases


class MemoryBriefingOutcome(str, Enum):
    """What one inherited-wisdom assembly actually did.

    The distinction the whole briefing-telemetry ticket turns on is
    ``STORE_ERROR``/``NO_STORE`` versus ``EMPTY``: a vault that would not open
    and a vault that opened and held nothing produce the same empty prompt
    section, and collapsing them is how retrieval stayed dead for a month
    without anyone noticing.
    """

    BUILT = "built"
    EMPTY = "empty"
    STORE_ERROR = "store_error"
    NO_STORE = "no_store"
    SKIPPED = "skipped"


class MemoryStoreKind(str, Enum):
    """The stores a briefing reads, plus the factory that builds them.

    ``CHECKPOINTS``, ``VAULT`` and ``GRAPH`` are real stores and are the only
    keys ever present in ``store_states_json``. ``SERVICE`` is the store-factory
    pseudo-store: it appears only in ``store_errors``, when
    ``AgentMemoryService.from_settings()`` itself raised and no store was ever
    reached.
    """

    CHECKPOINTS = "checkpoints"
    VAULT = "vault"
    GRAPH = "graph"
    SERVICE = "service"


class MemoryStoreState(str, Enum):
    """What one store *was* during an assembly — never what its row count implies.

    ``NOT_QUERIED`` is load-bearing. ``recall_related`` returns before touching
    either recall store when the query tokenises to no terms, so neither READ
    (nothing was read) nor UNCONFIGURED (the store exists) is true of it. It
    never contributes to ``STORE_ERROR`` and never counts as READ.
    """

    READ = "read"
    UNCONFIGURED = "unconfigured"
    ERRORED = "errored"
    NOT_QUERIED = "not_queried"


class RetrievalEmptiness(str, Enum):
    """Why a retrieval answer holds the hits it holds.

    ``search_prior_work`` returned a bare ``[]`` for three different facts —
    the query tokenised to no terms, the workspace slug did not resolve, and
    the scan matched nothing. One value cannot carry three facts, and the
    most expensive of them (a typo in a slug) read as the cheapest ("nothing
    like this has been tried").

    ``MATCHED`` is on every non-empty reply, so a reader never has to infer
    the emptiness from the length of a list.
    """

    MATCHED = "matched"
    NO_TERMS = "no_terms"
    UNRESOLVED_SCOPE = "unresolved_scope"
    NO_OVERLAP = "no_overlap"
    STORE_UNAVAILABLE = "store_unavailable"


class MemoryBriefingAssembly(str, Enum):
    """Which prompt-assembly path built a briefing.

    ``DISPATCH`` is supervised dispatch; ``RENDER`` is ``render_stage_prompt``,
    which the terminal handoff also goes through. Two assemblies for one run is
    a live path, and without this a legitimate pair is indistinguishable from a
    double write.
    """

    DISPATCH = "dispatch"
    RENDER = "render"
