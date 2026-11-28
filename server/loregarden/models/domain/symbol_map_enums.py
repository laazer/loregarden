"""Vocabulary for the per-workspace symbol map (lg-code-knowledge-281).

Its own module rather than a corner of `enums`, following the
`code_signal_enums` / `merge_queue_enums` precedent. Eight closed sets. The
five stored as TEXT are read by the consumer tickets (282, 283, 284, 300,
304), so each member value is a wire format and not an implementation detail;
the three that are never stored (`ChangedPathsOutcome` and `RefreshFailure`
from lg-code-knowledge-284, `GroundingVerdict` from lg-code-knowledge-368)
are closed vocabularies all the same, and a string in their place is what
`py-organization` exists to stop.
"""

from __future__ import annotations

from enum import StrEnum


class SymbolKind(StrEnum):
    """What a recorded definition is.

    `METHOD` exists beside `FUNCTION` so a lookup can tell a method from a
    free function of the same name; `parent` on the row carries which class.
    """

    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    CONSTANT = "constant"
    INTERFACE = "interface"
    TYPE_ALIAS = "type_alias"
    ENUM = "enum"
    VARIABLE = "variable"


class SourceLanguage(StrEnum):
    """What a tracked path is, decided by extension and nothing else.

    `OTHER` is a recorded answer, not an absence: a path present-but-unsupported
    and a path missing from the map must not read the same way.
    """

    PYTHON = "python"
    TYPESCRIPT = "typescript"
    TSX = "tsx"
    OTHER = "other"


class ExtractorTier(StrEnum):
    """How strong a claim a row is.

    A 281-era TypeScript node comes from a line-anchored regex and is visibly
    a weaker claim than a parsed one; 386 adds `TREE_SITTER` beside these, and
    a new `StrEnum` member needs no migration.
    """

    PYTHON_AST = "python_ast"
    REGEX_FALLBACK = "regex_fallback"


class ParseStatus(StrEnum):
    """Why a file row holds the symbols it holds — or holds none.

    "We do not read this kind" and "we could not read it" are different facts.
    One counter for both hides whichever is smaller.
    """

    PARSED = "parsed"
    UNSUPPORTED_LANGUAGE = "unsupported_language"
    PARSE_FAILED = "parse_failed"


class Freshness(StrEnum):
    """Whether the stored line numbers describe the tree the caller is on.

    Four-valued and never a bool: no partition, a sha mismatch, a peer
    writing this index right now and a genuine zero hit would otherwise all
    arrive as an empty list. 304 adds `WORKING_TREE_DIRTY` here without
    changing a return type or a consumer signature.

    `BUILDING` is reachable rather than vocabulary: lg-code-knowledge-284
    stamps `symbol_map_partitions.refresh_started_at` before any row is
    written and clears it on commit, so a reader entering while a peer
    refreshes the same partition sees it.
    """

    MATCHES = "matches"
    SHA_MISMATCH = "sha_mismatch"
    NO_PARTITION = "no_partition"
    BUILDING = "building"


class GroundingVerdict(StrEnum):
    """Whether an anchored claim still describes the code in front of a reader.

    Five states, not four (D3). UNVERIFIABLE is not a fifth kind of drift: it
    means NO VERDICT WAS COMPUTED — no partition for this checkout, a sha
    mismatch, a stored scheme prefix this build does not compute, or an
    extractor tier that produces no fingerprint. Collapsing any of those into
    MISSING would report a deletion nobody observed.

    Claim-level severity, worst first:
    MISSING > CHANGED > UNVERIFIABLE > MOVED > GROUNDED. UNVERIFIABLE above
    MOVED is D4's fail-safe direction — an anchor nothing could decide must
    not be swallowed by a sibling that merely moved.
    """

    GROUNDED = "grounded"
    MOVED = "moved"
    CHANGED = "changed"
    MISSING = "missing"
    UNVERIFIABLE = "unverifiable"


class ChangedPathsOutcome(StrEnum):
    """Whether `sources.changed_paths` answered, and if not, why not.

    Closed and three-valued because the one answer it may never give is a
    failure wearing the shape of success. An empty changed-set under a failed
    `git diff` re-stamps the sha onto a map describing another tree, and the
    reader then answers MATCHES with every line number wrong — strictly worse
    than no map, because the designed grep fallback never fires.
    """

    OK = "ok"
    #: The stored base is not something this checkout can diff a tree against
    #: (force-pushed away, garbage-collected, a copied database). Recoverable:
    #: the refresh rebuilds and re-stamps a sha it has actually read.
    BASE_UNUSABLE = "base_unusable"
    #: git ran and failed for any other reason. The stored sha is left alone.
    GIT_FAILED = "git_failed"


class RefreshFailure(StrEnum):
    """Why a refresh declined, as a value a consumer can branch on.

    Never an exception class name and never a traceback string: the run-log
    line (lg-code-knowledge-2731) and the degrade path both have to switch on
    this, and a message is not a vocabulary.
    """

    GIT_FAILED = "git_failed"
    UNBORN_HEAD = "unborn_head"
    INTERPRETER_TOO_OLD = "interpreter_too_old"
    UNEXPECTED = "unexpected"


__all__ = [
    "ChangedPathsOutcome",
    "ExtractorTier",
    "Freshness",
    "GroundingVerdict",
    "ParseStatus",
    "RefreshFailure",
    "SourceLanguage",
    "SymbolKind",
]
