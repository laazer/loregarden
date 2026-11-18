"""TypeScript and TSX extraction by line-anchored regex (281, tier two).

Stamped `ExtractorTier.REGEX_FALLBACK` on every row it produces, so a reader
can tell a 281-era TypeScript node from a parsed one and 386 can swap this
module out without touching anything downstream.

Three rules carry most of the correctness:

- **Comments and string bodies are blanked first, line numbering preserved.**
  A line-oriented pattern that skips this reports every commented-out
  declaration as a definition — and a commented-out declaration at column
  zero is the shape of any file whose old implementation is still sitting
  above the new one. `suggest_names` would then offer an identifier that
  exists nowhere, which is the confidently-wrong answer this map exists to
  prevent.
- **A re-export is not a declaration.** `export { X } from "./y"` and
  `export * from "./y"` produce no node, so a barrel contributes nothing and
  a lookup returns the definition site once instead of one indistinguishable
  hit per barrel in the chain.
- **Overloads collapse.** A repeated `(name, kind)` in one file keeps the
  LAST occurrence, which in TypeScript is the implementation signature. A
  caller asking where something is defined wants the body.

Class methods are deliberately NOT extracted: this tier cannot bound a class
body without a parser, and guessing by indentation ships exactly the
plausible-looking wrongness the map is built to avoid. 386 closes it.
"""

from __future__ import annotations

import re

from loregarden.models.domain.symbol_map_enums import SymbolKind
from loregarden.services.symbol_map.values import ExtractedSymbol

#: Source keyword to kind. `const`/`let`/`var` are decided by the identifier,
#: not the keyword: TypeScript's `const` is a binding rule and not a
#: constant-ness claim, and the all-caps convention is what a caller
#: searching for a constant actually means.
_KIND_BY_KEYWORD = {
    "class": SymbolKind.CLASS,
    "function": SymbolKind.FUNCTION,
    "interface": SymbolKind.INTERFACE,
    "type": SymbolKind.TYPE_ALIAS,
    "enum": SymbolKind.ENUM,
}

_BINDING_KEYWORDS = frozenset({"const", "let", "var"})

_ALL_CAPS = re.compile(r"^[A-Z0-9_]+$")

#: Anchored at the start of a line, after the modifiers a declaration may
#: carry. An indented declaration is inside a body and is not this tier's to
#: claim. The shapes counted in this repository at HEAD a4d45f9f8f —
#: `export async function` (15 files), `export function f<T>` (18),
#: `export default function` (2), `export abstract class` (1) — all go
#: through the same modifier run.
_DECLARATION = re.compile(
    r"^(?:export[ \t]+default[ \t]+|export[ \t]+)?"
    r"(?:declare[ \t]+)?"
    r"(?:abstract[ \t]+)?"
    r"(?:async[ \t]+)?"
    r"(?P<keyword>class|function|interface|type|enum|const|let|var)"
    r"(?:[ \t]*\*)?"
    r"[ \t]+"
    r"(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)",
    re.MULTILINE,
)


def _end_of_line_comment(text: str, index: int) -> int:
    newline = text.find("\n", index)
    return len(text) if newline == -1 else newline


def _end_of_block_comment(text: str, index: int) -> int:
    close = text.find("*/", index + 2)
    return len(text) if close == -1 else close + 2


def _end_of_string(text: str, index: int, quote: str) -> int:
    """Where the string opened at *index* ends.

    An unterminated quote ends at the newline, except in a template literal —
    the one string form that legitimately spans lines.
    """
    cursor = index + 1
    while cursor < len(text):
        char = text[cursor]
        if char == "\\":
            cursor += 2
        elif char == quote:
            return cursor + 1
        elif char == "\n" and quote != "`":
            return cursor
        else:
            cursor += 1
    return len(text)


def _blank(out: list[str], text: str, start: int, stop: int) -> None:
    """Replace `text[start:stop]` with spaces in *out*, keeping newlines."""
    for position in range(start, stop):
        if text[position] != "\n":
            out[position] = " "


def _blank_comments_and_strings(text: str) -> str:
    """*text* with comment and string bodies replaced by spaces.

    Spaces rather than deletions, and newlines kept, so every surviving
    character stays at the offset — and therefore on the line — it started
    on. A line counter that walks the file by hand is where an off-by-one in
    this tier would come from, so the offsets are never disturbed.
    """
    out = list(text)
    index = 0
    while index < len(text):
        if text.startswith("//", index):
            stop = _end_of_line_comment(text, index)
        elif text.startswith("/*", index):
            stop = _end_of_block_comment(text, index)
        elif text[index] in "\"'`":
            stop = _end_of_string(text, index, text[index])
        else:
            index += 1
            continue
        _blank(out, text, index, stop)
        index = stop
    return "".join(out)


def _kind_for(keyword: str, name: str) -> SymbolKind:
    if keyword in _BINDING_KEYWORDS:
        return SymbolKind.CONSTANT if _ALL_CAPS.match(name) else SymbolKind.VARIABLE
    return _KIND_BY_KEYWORD[keyword]


def typescript_symbols(path: str, data: bytes) -> list[ExtractedSymbol]:
    """Every top-level declaration in *data*, one node per (name, kind).

    Raises `UnicodeDecodeError` for bytes that are not UTF-8; `extract` turns
    that into a recorded status like any other parse failure.
    """
    text = data.decode("utf-8")
    scanned = _blank_comments_and_strings(text)
    #: Keyed by (name, kind) so overload signatures collapse onto the
    #: implementation, and insertion-ordered so the result is deterministic.
    found: dict[tuple[str, SymbolKind], ExtractedSymbol] = {}
    for match in _DECLARATION.finditer(scanned):
        name = match.group("name")
        kind = _kind_for(match.group("keyword"), name)
        line = scanned.count("\n", 0, match.start()) + 1
        found[(name, kind)] = ExtractedSymbol(
            name=name,
            qualified_name=f"{path}#{name}",
            kind=kind,
            line=line,
        )
    return list(found.values())
