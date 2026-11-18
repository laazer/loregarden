"""Vocabulary for the per-workspace symbol map (lg-code-knowledge-281).

Its own module rather than a corner of `enums`, following the
`code_signal_enums` / `merge_queue_enums` precedent. Five closed sets, all
stored as TEXT and all read by the consumer tickets (282, 283, 284, 300, 304),
so each member value is a wire format and not an implementation detail.
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

    Three-valued and never a bool: no partition, a sha mismatch and a genuine
    zero hit would otherwise all arrive as an empty list. 304 adds
    `WORKING_TREE_DIRTY` here without changing a return type or a consumer
    signature.
    """

    MATCHES = "matches"
    SHA_MISMATCH = "sha_mismatch"
    NO_PARTITION = "no_partition"


__all__ = ["ExtractorTier", "Freshness", "ParseStatus", "SourceLanguage", "SymbolKind"]
