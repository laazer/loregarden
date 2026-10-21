"""Classify a fetched tree's licence before anything else in it is read.

The gate this feeds runs before a pass is dispatched, so the only files a refused
source ever has opened are the ones named in `LicenceVerdict.files_read`. Every
licence call the survey made by hand (serena GPL and itsaplan AGPL to
patterns-only; mellowyak PolyForm-NC and Ivy-Tendril FSL-1.1 excluded; tau flagged
because its only grant was a `package.json` field) is a rule here.

Pattern from CypherSisters/PuffPAIBuildBot @63c8a03 `run_licence.py` (Apache-2.0),
reimplemented: four kinds with the stricter reading winning, an identifier, an
expression and licence prose as three separate detection paths, and a manifest
claim kept apart from a licence file (lg-reference-sources-1376).

It never fetches and never executes anything in the tree.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import tomllib
from loregarden.models.domain.survey_enums import LicenceBasis, LicenceKind, LicenceUse
from pydantic import BaseModel, ConfigDict

#: Fail-closed ordering: when two readings disagree, the lower rank wins.
_RANK: dict[LicenceKind, int] = {
    LicenceKind.OPEN: 3,
    LicenceKind.LIMITED: 2,
    LicenceKind.CUSTOM: 1,
    LicenceKind.NONE: 0,
}

#: A licence file larger than this is not one we know; it is read no further.
MAX_LICENCE_BYTES = 128_000

LICENCE_FILE_RE = re.compile(r"^(licen[cs]e|unlicen[cs]e|copying)([._-][\w.-]*)?$", re.IGNORECASE)

#: Where licence files conventionally live, besides the root. Not recursive.
LICENCE_FOLDERS: tuple[str, ...] = (
    "",
    "licenses",
    "licences",
    "LICENSES",
    "legal",
    "docs",
    ".github",
)

_MANIFESTS: tuple[str, ...] = ("package.json", "Cargo.toml", "pyproject.toml")

OPEN_IDS: frozenset[str] = frozenset(
    {
        "0BSD", "Apache-2.0", "Artistic-2.0", "BlueOak-1.0.0", "BSD-2-Clause",
        "BSD-3-Clause", "BSL-1.0", "CC0-1.0", "CC-BY-4.0", "ISC", "MIT", "MIT-0",
        "PSF-2.0", "Python-2.0", "Unlicense", "UPL-1.0", "WTFPL", "X11", "Zlib",
    }
)  # fmt: skip

#: Copyleft and network copyleft: readable, but code is not copied.
COPYLEFT_PREFIXES: tuple[str, ...] = (
    "AGPL-", "CC-BY-SA-", "CDDL-", "EPL-", "EUPL-", "GPL-", "LGPL-", "MPL-", "OSL-",
)  # fmt: skip

#: Non-commercial and source-available: not read at all.
EXCLUDING_PREFIXES: tuple[str, ...] = (
    "BUSL-", "CC-BY-NC", "Commons-Clause", "Elastic-", "FSL-", "PolyForm-", "SSPL-",
)  # fmt: skip


@dataclass(frozen=True)
class _TextMarker:
    spdx: str
    needles: tuple[str, ...]
    #: Canonical texts are short; a "permissive" file far longer carries terms of its own.
    max_chars: int = 0
    #: A smaller sibling this marker's match makes redundant (BSD-3 over BSD-2).
    subsumes: str = ""


#: Order matters only for reporting; every marker that matches is applied.
_TEXT_MARKERS: tuple[_TextMarker, ...] = (
    _TextMarker("AGPL-3.0", ("gnu affero general public license",)),
    _TextMarker("LGPL-3.0", ("gnu lesser general public license",)),
    _TextMarker("GPL-3.0", ("gnu general public license", "version 3"), subsumes="GPL-2.0"),
    _TextMarker("GPL-2.0", ("gnu general public license", "version 2")),
    _TextMarker("MPL-2.0", ("mozilla public license", "2.0")),
    _TextMarker("PolyForm-Noncommercial-1.0.0", ("polyform noncommercial license",)),
    _TextMarker("PolyForm-Shield-1.0.0", ("polyform shield license",)),
    _TextMarker("FSL-1.1", ("functional source license",)),
    _TextMarker("BUSL-1.1", ("business source license",)),
    _TextMarker("SSPL-1.0", ("server side public license",)),
    _TextMarker("Elastic-2.0", ("elastic license 2.0",)),
    _TextMarker("CC-BY-NC-4.0", ("attribution-noncommercial",)),
    _TextMarker("MIT", ("permission is hereby granted, free of charge",), 2_500),
    _TextMarker(
        "ISC", ("permission to use, copy, modify, and/or distribute this software",), 1_500
    ),
    _TextMarker(
        "BSD-3-Clause",
        ("redistribution and use in source and binary forms", "endorse"),
        2_500,
        "BSD-2-Clause",
    ),
    _TextMarker("BSD-2-Clause", ("redistribution and use in source and binary forms",), 2_000),
    _TextMarker("Apache-2.0", ("apache license", "version 2.0"), 13_000),
    _TextMarker("Unlicense", ("this is free and unencumbered software",), 1_800),
)

#: Words that turn a permissive-looking text into terms of its own.
_RESTRICTION_RE = re.compile(
    r"non[- ]?commercial|commercial use|may not be (used|sold|redistributed)|"
    r"not permitted to|without (the )?prior written|all rights reserved",
    re.IGNORECASE,
)
_SPDX_LINE_RE = re.compile(r"SPDX-License-Identifier:\s*(?P<expr>[^\n*]+)")


@dataclass(frozen=True)
class LicenceVerdict:
    kind: LicenceKind
    basis: LicenceBasis | None
    #: The identifier or expression as read; "" when there is none.
    name: str
    use: LicenceUse
    #: What a reader may and may not do with the source, in words.
    constraint: str
    #: Tree-relative paths opened to reach this verdict, and nothing else.
    files_read: tuple[str, ...] = ()
    #: Files refused (outside the tree, oversized), named so a hostile input is visible.
    refused: tuple[str, ...] = field(default=())


@dataclass(frozen=True)
class _Reading:
    kind: LicenceKind
    name: str
    excluding: bool = False


def _id_reading(identifier: str) -> _Reading:
    ident = identifier.strip().removesuffix("+").strip()
    base = re.sub(r"-(only|or-later)$", "", ident)
    if ident in OPEN_IDS or base in OPEN_IDS:
        return _Reading(LicenceKind.OPEN, ident)
    if ident.startswith(EXCLUDING_PREFIXES):
        return _Reading(LicenceKind.LIMITED, ident, excluding=True)
    if ident.startswith(COPYLEFT_PREFIXES):
        return _Reading(LicenceKind.LIMITED, ident)
    return _Reading(LicenceKind.CUSTOM, ident)


def _stricter(a: _Reading, b: _Reading) -> _Reading:
    if _RANK[a.kind] != _RANK[b.kind]:
        return a if _RANK[a.kind] < _RANK[b.kind] else b
    return a if a.excluding or not b.excluding else b


def _looser(a: _Reading, b: _Reading) -> _Reading:
    return b if _stricter(a, b) is a else a


def _split_top(expr: str, op: str) -> list[str]:
    """Split on `op` where it stands outside every parenthesis."""
    parts, depth, start, pos = [], 0, 0, 0
    for tok in re.split(r"(\(|\)|\s+)", expr):
        if tok == "(":
            depth += 1
        elif tok == ")":
            depth -= 1
        elif depth == 0 and tok == op:
            parts.append(expr[start:pos])
            start = pos + len(tok)
        pos += len(tok)
    parts.append(expr[start:])
    return [p.strip() for p in parts if p.strip()]


def _wrapped(expr: str) -> bool:
    """Whether one pair of parentheses encloses the whole expression."""
    if not (expr.startswith("(") and expr.endswith(")")):
        return False
    depth = 0
    for i, char in enumerate(expr):
        depth += {"(": 1, ")": -1}.get(char, 0)
        if depth == 0 and i < len(expr) - 1:
            return False
    return depth == 0


def expression_reading(expr: str) -> _Reading:
    """Resolve an SPDX expression. A licensee may pick either side of OR, so OR
    takes the looser operand; AND binds both, so it takes the stricter one."""
    text = expr.strip()
    while _wrapped(text):
        text = text[1:-1].strip()
    ors = _split_top(text, "OR")
    if len(ors) > 1:
        result = expression_reading(ors[0])
        for part in ors[1:]:
            result = _looser(result, expression_reading(part))
        return _Reading(result.kind, text, result.excluding)
    ands = _split_top(text, "AND")
    if len(ands) > 1:
        result = expression_reading(ands[0])
        for part in ands[1:]:
            result = _stricter(result, expression_reading(part))
        return _Reading(result.kind, text, result.excluding)
    # `X WITH exception` keeps X's terms.
    return _id_reading(text.split(" WITH ", 1)[0])


def text_reading(body: str) -> _Reading:
    """Classify licence prose. Every marker that matches applies, the stricter wins,
    and a permissive text that is too long or carries restriction words is CUSTOM."""
    spdx = _SPDX_LINE_RE.search(body[:2_000])
    if spdx:
        return expression_reading(spdx.group("expr").strip())
    lowered = " ".join(body.lower().split())
    matched: list[_Reading] = []
    subsumed: set[str] = set()
    for marker in _TEXT_MARKERS:
        if marker.spdx in subsumed or not all(needle in lowered for needle in marker.needles):
            continue
        if marker.subsumes:
            subsumed.add(marker.subsumes)
        reading = _id_reading(marker.spdx)
        if reading.kind is LicenceKind.OPEN and (
            len(lowered) > marker.max_chars or _RESTRICTION_RE.search(lowered)
        ):
            reading = _Reading(LicenceKind.CUSTOM, f"{marker.spdx} with terms of its own")
        matched.append(reading)
    if not matched:
        return _Reading(LicenceKind.CUSTOM, "unrecognised licence text")
    result = matched[0]
    for reading in matched[1:]:
        result = _stricter(result, reading)
    return result


def _inside(root: Path, candidate: Path) -> bool:
    """The one containment predicate: the resolved path is under the resolved root."""
    return candidate.resolve().is_relative_to(root)


def _licence_files(root: Path) -> tuple[list[Path], list[str]]:
    found, refused = [], []
    for folder in LICENCE_FOLDERS:
        directory = root / folder if folder else root
        if not directory.is_dir() or not _inside(root, directory):
            continue
        for entry in sorted(directory.iterdir()):
            if not LICENCE_FILE_RE.match(entry.name) or not entry.is_file():
                continue
            rel = entry.relative_to(root).as_posix()
            if not _inside(root, entry):
                refused.append(f"{rel} (resolves outside the tree)")
            elif entry.stat().st_size > MAX_LICENCE_BYTES:
                refused.append(f"{rel} (over {MAX_LICENCE_BYTES} bytes)")
            else:
                found.append(entry)
    return found, refused


class _LicenceTable(BaseModel):
    """pyproject's `license = {text = ...}`, or npm's legacy `license: {type}`."""

    model_config = ConfigDict(extra="ignore")

    text: str = ""
    type: str = ""

    def __str__(self) -> str:
        return self.text or self.type


class _ManifestLicence(BaseModel):
    """The licence field of a package manifest. `str()` of it is the claim."""

    model_config = ConfigDict(extra="ignore")

    license: str | _LicenceTable = ""


def _manifest_claim(root: Path, name: str) -> str:
    path = root / name
    if not path.is_file() or not _inside(root, path) or path.stat().st_size > MAX_LICENCE_BYTES:
        return ""
    raw = path.read_text(encoding="utf-8", errors="replace")
    try:
        if name == "package.json":
            manifest = _ManifestLicence.model_validate(json.loads(raw))
        else:
            data = tomllib.loads(raw)
            manifest = _ManifestLicence.model_validate(
                data.get("package") or data.get("project") or {}
            )
    except ValueError:  # silent-ok: an unparseable manifest makes no claim, and the verdict falls to NONE, the stricter reading
        return ""
    return str(manifest.license).strip()


def _use_for(reading: _Reading, basis: LicenceBasis | None) -> tuple[LicenceUse, str]:
    name = reading.name or "no licence"
    if reading.kind is LicenceKind.NONE:
        return (
            LicenceUse.PATTERNS_ONLY,
            "No licence file: all rights reserved. Read for patterns; copy no code or text.",
        )
    if reading.kind is LicenceKind.CUSTOM:
        return (
            LicenceUse.EXCLUDED,
            f"Licence terms this classifier cannot read ({name}). Not surveyed until a "
            "person records the licence by hand.",
        )
    if reading.kind is LicenceKind.LIMITED and reading.excluding:
        return (
            LicenceUse.EXCLUDED,
            f"{name} restricts commercial or competing use. Not surveyed.",
        )
    if reading.kind is LicenceKind.LIMITED:
        return (
            LicenceUse.PATTERNS_ONLY,
            f"{name} is copyleft. Read for patterns and reimplement; copy no code.",
        )
    if basis is LicenceBasis.MANIFEST:
        return (
            LicenceUse.PATTERNS_ONLY,
            f"Only a manifest field claims {name}; no licence file was read. "
            "A reported licence is not a granted one: patterns only.",
        )
    return LicenceUse.FULL, f"{name}: permissive. Attribute what is quoted."


def classify_tree(root: Path) -> LicenceVerdict:
    """Classify the licence of the tree at `root`, reading only licence files and
    manifests, each bounded and contained."""
    root = root.resolve()
    files, refused = _licence_files(root)
    read: list[str] = []
    reading: _Reading | None = None
    for path in files:
        read.append(path.relative_to(root).as_posix())
        this = text_reading(path.read_text(encoding="utf-8", errors="replace"))
        reading = this if reading is None else _stricter(reading, this)
    if refused and reading is not None:
        reading = _stricter(reading, _Reading(LicenceKind.CUSTOM, "a licence file was refused"))
    if reading is not None:
        use, constraint = _use_for(reading, LicenceBasis.FILE_AT_COMMIT)
        return LicenceVerdict(
            reading.kind, LicenceBasis.FILE_AT_COMMIT, reading.name, use, constraint,
            tuple(read), tuple(refused),
        )  # fmt: skip
    for manifest in _MANIFESTS:
        if not (root / manifest).is_file():
            continue
        read.append(manifest)
        claim = _manifest_claim(root, manifest)
        if claim:
            manifest_reading = expression_reading(claim)
            use, constraint = _use_for(manifest_reading, LicenceBasis.MANIFEST)
            return LicenceVerdict(
                manifest_reading.kind, LicenceBasis.MANIFEST, claim, use, constraint,
                tuple(read), tuple(refused),
            )  # fmt: skip
    if refused:
        none = _Reading(LicenceKind.CUSTOM, "a licence file was refused")
    else:
        none = _Reading(LicenceKind.NONE, "")
    use, constraint = _use_for(none, None)
    return LicenceVerdict(none.kind, None, none.name, use, constraint, tuple(read), tuple(refused))


def recorded_verdict(name: str) -> LicenceVerdict:
    """A licence a person recorded by hand (the legacy survey's calls).

    Weaker than a file read at a pinned commit: the feeder classifies the file at
    the next dispatch and reports any disagreement rather than overwriting it.
    "none" records that the person found no licence file.
    """
    if name.strip().lower() in ("", LicenceKind.NONE.value):
        reading = _Reading(LicenceKind.NONE, "")
    else:
        reading = expression_reading(name)
    use, constraint = _use_for(reading, LicenceBasis.RECORDED_BY_HAND)
    return LicenceVerdict(
        reading.kind, LicenceBasis.RECORDED_BY_HAND, reading.name, use, constraint
    )
