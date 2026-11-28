"""The pure vocabulary the extraction tiers share (lg-code-knowledge-281).

What a path's language is, and the shape of what an extractor returns. No
database, no git, no filesystem — this module is importable from the pure
dispatcher and from both tiers without any of them reaching the others.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath

from loregarden.models.domain.symbol_map_enums import (
    ExtractorTier,
    ParseStatus,
    SourceLanguage,
    SymbolKind,
)

#: Extension to language, first and only match. Anything absent is OTHER, and
#: an OTHER path is never opened — which is also why a binary path can never
#: raise a decode error.
_LANGUAGE_BY_SUFFIX = {
    ".py": SourceLanguage.PYTHON,
    ".pyi": SourceLanguage.PYTHON,
    ".ts": SourceLanguage.TYPESCRIPT,
    ".tsx": SourceLanguage.TSX,
}


def detect(path: str) -> SourceLanguage:
    """The language of a repo-relative path, by extension."""
    return _LANGUAGE_BY_SUFFIX.get(PurePosixPath(path).suffix, SourceLanguage.OTHER)


def tier_for(language: SourceLanguage) -> ExtractorTier:
    """Which tier claims a language.

    OTHER is answered by the regex tier declining it, so the row still records
    which extractor looked and said no.
    """
    if language is SourceLanguage.PYTHON:
        return ExtractorTier.PYTHON_AST
    return ExtractorTier.REGEX_FALLBACK


@dataclass(frozen=True)
class ExtractedSymbol:
    """One definition an extractor found, before it is a row."""

    name: str
    qualified_name: str
    kind: SymbolKind
    line: int
    parent: str = ""
    #: `sym1:<32 hex>` over this node's unparsed source (368), or "" for a
    #: tier that cannot bound a body. See `symbol_map.fingerprint`.
    fingerprint: str = ""


@dataclass(frozen=True)
class FileExtraction:
    """Everything one file contributes: its status, and its definitions.

    A failure is a value on this, never an exception out of `extract` — that
    is what lets 386 swap a tier without the build loop learning a new
    failure mode.
    """

    language: SourceLanguage
    extractor_tier: ExtractorTier
    parse_status: ParseStatus
    parse_error: str = ""
    symbols: list[ExtractedSymbol] = field(default_factory=list)
