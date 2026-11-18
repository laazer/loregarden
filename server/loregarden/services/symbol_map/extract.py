"""The tier dispatcher: a path and its bytes in, a `FileExtraction` out.

Pure. No database, no git, no filesystem — reading happens in the build loop,
which hands this the bytes it already has. That boundary is what makes 386 a
one-module swap: a new tier changes what `extract` returns and nothing about
how the build loop calls it.

It NEVER raises. Every failure — invalid syntax, a null byte, bytes that are
not UTF-8, an empty file, an extension nothing claims — comes back as a
`ParseStatus` on the returned value, so one unreadable file costs one row's
symbols rather than the whole map.
"""

from __future__ import annotations

from loregarden.models.domain.symbol_map_enums import ParseStatus, SourceLanguage
from loregarden.services.symbol_map.python_ast import python_symbols
from loregarden.services.symbol_map.typescript_regex import typescript_symbols
from loregarden.services.symbol_map.values import FileExtraction, detect, tier_for

#: What a tier is allowed to fail with. `SyntaxError` and `ValueError` are
#: what `ast.parse` raises for source it cannot read (a null byte is the
#: second); `UnicodeDecodeError` is a `ValueError` and named anyway because it
#: is the regex tier's only failure; `RecursionError` is how a pathological
#: nesting depth arrives.
_TIER_FAILURES = (SyntaxError, ValueError, UnicodeDecodeError, RecursionError)


def extract(path: str, data: bytes) -> FileExtraction:
    """Dispatch *data* to the tier that claims *path*'s language."""
    language = detect(path)
    tier = tier_for(language)
    if language is SourceLanguage.OTHER:
        return FileExtraction(
            language=language,
            extractor_tier=tier,
            parse_status=ParseStatus.UNSUPPORTED_LANGUAGE,
        )
    reader = python_symbols if language is SourceLanguage.PYTHON else typescript_symbols
    try:
        symbols = reader(path, data)
    except _TIER_FAILURES as exc:
        return FileExtraction(
            language=language,
            extractor_tier=tier,
            parse_status=ParseStatus.PARSE_FAILED,
            parse_error=str(exc) or type(exc).__name__,
        )
    return FileExtraction(
        language=language,
        extractor_tier=tier,
        parse_status=ParseStatus.PARSED,
        symbols=symbols,
    )
