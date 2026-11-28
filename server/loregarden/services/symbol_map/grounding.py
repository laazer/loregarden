"""Does this claim still describe the code it was written about? (368, R3)

The write-time half (`resolve_anchors`) stamps what a symbol looked like; the
read-time half (`verdicts_for`, `claim_verdicts`) compares that stamp against
the map built for the checkout the reader is standing in. No LLM and no
network: the verdict is two indexed lookups, and this module imports no HTTP
client and no agent module so that it stays that way.

**The per-anchor rule, in this order.**

1. freshness is not MATCHES -> UNVERIFIABLE ("no symbol map for this
   checkout", or "the map describes <indexed>, this checkout is <head>").
2. the stored fingerprint's scheme prefix is not `FINGERPRINT_SCHEME` ->
   UNVERIFIABLE. A deliberate scheme bump therefore makes an old corpus
   regroundable rather than reporting false drift on every claim in it.
3. the stored fingerprint is "" -> UNVERIFIABLE: that anchor's extractor tier
   produces none.
4. a row with that qualified_name whose fingerprint is EQUAL -> GROUNDED.
   Any such row: a name defined twice (an overload, a platform branch, an
   `if TYPE_CHECKING` block) grounds on whichever copy still matches.
5. a row with that qualified_name whose fingerprint differs -> CHANGED. This
   outranks step 6 deliberately: a symbol edited under its own name is
   CHANGED even when a byte-identical copy of the old body exists elsewhere.
6. no such qualified_name, but a row in the partition carries that
   fingerprint -> MOVED, located at the FIRST such row ordered by
   `(path, line)`. A trivial body (`def __init__(self): pass`) collides, so
   MOVED can name the wrong one of two sites — accepted (risk 3), because the
   verdict is still right and the `(path, line)` order makes the choice
   deterministic across rebuilds rather than a coin flip.
7. otherwise -> MISSING.

A claim's verdict is the most severe of its anchors':
MISSING > CHANGED > UNVERIFIABLE > MOVED > GROUNDED. UNVERIFIABLE above MOVED
is the fail-safe direction (D4): an anchor nothing could decide must not be
swallowed by a sibling that merely moved.
"""

from __future__ import annotations

import difflib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loregarden.models.domain.symbol_map_enums import (
    ExtractorTier,
    Freshness,
    GroundingVerdict,
)
from loregarden.models.domain.symbol_map_tables import (
    SymbolMapFile,
    SymbolMapPartition,
    SymbolMapSymbol,
)
from loregarden.services.symbol_map.fingerprint import FINGERPRINT_SCHEME
from loregarden.services.symbol_map.freshness import partition_for, status_for
from loregarden.services.symbol_map.sources import UnbornHeadError, head_sha
from loregarden.services.symbol_map.store import MapRead
from sqlmodel import Session, col, select

#: The one sentence every refusal ends with. An agent whose anchor cannot be
#: resolved still has a learning worth recording, and must be told how.
UNGROUNDED_FALLBACK = "write it without anchors to record it ungrounded"

#: Worst first. Index in this tuple IS the severity, so a claim's verdict is
#: the minimum index among its anchors'.
_SEVERITY = (
    GroundingVerdict.MISSING,
    GroundingVerdict.CHANGED,
    GroundingVerdict.UNVERIFIABLE,
    GroundingVerdict.MOVED,
    GroundingVerdict.GROUNDED,
)

#: How many existing names a refusal offers. Three: enough to recognise the
#: one that was meant, few enough that the message stays a sentence.
_MAX_CANDIDATES = 3

_ANCHOR_KEYS = ("qualified_name", "fingerprint", "path", "checkout_identity", "extractor_tier")


class SymbolAnchorError(Exception):
    """What an anchor could not be resolved to, at write time.

    The three subclasses are siblings and never one another's kinds: an agent
    told its correct anchor does not exist, when the truth is that no map has
    been built yet, learns to stop anchoring — which is the capability turned
    off by its own error copy.
    """


class UnknownSymbolAnchorError(SymbolAnchorError):
    """No symbol of that qualified name is in this checkout's map."""


class SymbolMapUnavailableError(SymbolAnchorError):
    """This checkout has no symbol map at all yet (lg-code-knowledge-284)."""


class UnfingerprintableSymbolError(SymbolAnchorError):
    """The symbol exists, and its extractor tier produces no fingerprint."""


class CorruptAnchorsError(ValueError):
    """A stored `anchors_json` value this build cannot read back."""


@dataclass(frozen=True)
class SymbolAnchor:
    """What one claim depends on, and what it looked like when written."""

    qualified_name: str
    fingerprint: str
    path: str
    checkout_identity: str
    extractor_tier: ExtractorTier


@dataclass(frozen=True)
class GroundingCheck:
    """One anchor's verdict. Frozen: a read path must not rewrite it."""

    verdict: GroundingVerdict
    qualified_name: str
    #: `path:line` for GROUNDED and MOVED, "" otherwise.
    location: str
    #: Non-empty exactly when the verdict is UNVERIFIABLE. A reason on a
    #: decided verdict would read as a caveat on a fact.
    reason: str


@dataclass(frozen=True)
class ClaimGrounding:
    """One claim's verdict, and every anchor's behind it."""

    verdict: GroundingVerdict
    checks: tuple[GroundingCheck, ...]
    reason: str


def anchor_key(anchor: SymbolAnchor) -> str:
    """The key `verdicts_for` returns its checks under.

    The fingerprint is in it, so two claims anchoring one symbol at two
    stampings stay two facts: one may still hold while the other does not.
    """
    return f"{anchor.qualified_name}@{anchor.fingerprint}"


def encode_anchors(anchors: Sequence[SymbolAnchor]) -> str:
    """The column's only writer. Order is the author's and is preserved: the
    exported note renders these as a list a person reads."""
    return json.dumps(
        [
            {
                "qualified_name": anchor.qualified_name,
                "fingerprint": anchor.fingerprint,
                "path": anchor.path,
                "checkout_identity": anchor.checkout_identity,
                "extractor_tier": ExtractorTier(anchor.extractor_tier).value,
            }
            for anchor in anchors
        ]
    )


def decode_anchors(raw: str) -> list[SymbolAnchor]:
    """The column's only reader. Raises `CorruptAnchorsError` on anything else.

    The column is TEXT on a per-workspace SQLite file in a synced vault that
    operators edit by hand, so "whatever `encode_anchors` wrote" is not the
    only value this ever sees.
    """
    text = (raw or "").strip()
    if not text:
        return []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CorruptAnchorsError(f"anchors are not JSON: {text[:60]!r}") from exc
    if type(payload) is not list:
        raise CorruptAnchorsError(f"anchors are not a JSON list: {text[:60]!r}")
    anchors: list[SymbolAnchor] = []
    for item in payload:
        if type(item) is not dict or not all(key in item for key in _ANCHOR_KEYS):
            raise CorruptAnchorsError(f"an anchor record is malformed: {text[:60]!r}")
        try:
            tier = ExtractorTier(item["extractor_tier"])
        except ValueError as exc:
            raise CorruptAnchorsError(f"unknown extractor tier: {text[:60]!r}") from exc
        anchors.append(
            SymbolAnchor(
                qualified_name=str(item["qualified_name"]),
                fingerprint=str(item["fingerprint"]),
                path=str(item["path"]),
                checkout_identity=str(item["checkout_identity"]),
                extractor_tier=tier,
            )
        )
    return anchors


def anchor_payloads(anchors: Sequence[SymbolAnchor]) -> list[dict[str, Any]]:
    """The anchor records as plain JSON dicts, for a caller that stores them."""
    return json.loads(encode_anchors(anchors))


@dataclass(frozen=True)
class _Site:
    """One definition site, reduced to what a verdict needs."""

    path: str
    line: int
    fingerprint: str

    @property
    def location(self) -> str:
        return f"{self.path}:{self.line}"


def _ordered(site: _Site) -> tuple[str, int]:
    """The tie-break the docstring promises, used by both indexes."""
    return (site.path, site.line)


@dataclass(frozen=True)
class _Index:
    """One call's lookups, built once and never mutated by the walk."""

    by_name: dict[str, list[_Site]]
    by_fingerprint: dict[str, list[_Site]]


def _sites(session: Session, rows: Sequence[SymbolMapSymbol]) -> _Index:
    """`(path, line)`-ordered sites for *rows*, indexed by name and by content.

    One file query for the whole batch: the briefing asks about up to five
    claims on the path that assembles every non-verify prompt in the system,
    and a query per anchor is how a read-time annotation becomes a per-prompt
    cost nobody budgeted.
    """
    file_ids = {row.file_id for row in rows}
    paths: dict[str, str] = {}
    if file_ids:
        paths = {
            file_row.id: file_row.path
            for file_row in session.exec(
                select(SymbolMapFile).where(col(SymbolMapFile.id).in_(file_ids))
            ).all()
        }
    by_name: dict[str, list[_Site]] = {}
    by_fingerprint: dict[str, list[_Site]] = {}
    for row in rows:
        site = _Site(path=paths.get(row.file_id, ""), line=row.line, fingerprint=row.fingerprint)
        by_name.setdefault(row.qualified_name, []).append(site)
        if row.fingerprint:
            by_fingerprint.setdefault(row.fingerprint, []).append(site)
    for sites in (*by_name.values(), *by_fingerprint.values()):
        sites.sort(key=_ordered)
    return _Index(by_name=by_name, by_fingerprint=by_fingerprint)


def _unverifiable(anchor: SymbolAnchor, reason: str) -> GroundingCheck:
    return GroundingCheck(
        verdict=GroundingVerdict.UNVERIFIABLE,
        qualified_name=anchor.qualified_name,
        location="",
        reason=reason,
    )


def _decided(anchor: SymbolAnchor, verdict: GroundingVerdict, location: str) -> GroundingCheck:
    return GroundingCheck(
        verdict=verdict, qualified_name=anchor.qualified_name, location=location, reason=""
    )


def _check(anchor: SymbolAnchor, index: _Index) -> GroundingCheck:
    """Steps 2 through 7 for one anchor, against an index built once per call."""
    if not anchor.fingerprint:
        return _unverifiable(anchor, "this anchor's extractor tier produces no fingerprint")
    scheme = anchor.fingerprint.partition(":")[0]
    if scheme != FINGERPRINT_SCHEME:
        return _unverifiable(anchor, f"unknown fingerprint scheme {scheme!r}")

    named: list[_Site] = index.by_name.get(anchor.qualified_name, [])
    if named:
        # ANY row with an equal fingerprint grounds it, not the first one read:
        # a qualified name defined twice is an ordinary shape in real trees.
        matching = [site for site in named if site.fingerprint == anchor.fingerprint]
        if matching:
            return _decided(anchor, GroundingVerdict.GROUNDED, matching[0].location)
        # Step 5 before step 6: the symbol is still here under its own name
        # with a different body, whatever survives elsewhere.
        return _decided(anchor, GroundingVerdict.CHANGED, "")

    moved: list[_Site] = index.by_fingerprint.get(anchor.fingerprint, [])
    if moved:
        return _decided(anchor, GroundingVerdict.MOVED, moved[0].location)
    return _decided(anchor, GroundingVerdict.MISSING, "")


def _no_comparison_reason(
    freshness: Freshness, partition: SymbolMapPartition | None, repo_root: Path
) -> str:
    """Why no verdict could be computed, in the reader's words.

    An UNVERIFIABLE with no reason is a defect rather than a terse reply, so
    every branch of the four-valued verdict names its own cause. The sha the
    checkout is on is reported when git can still be asked for it, and its
    absence is itself the reason when it cannot.
    """
    if freshness is Freshness.BUILDING:
        return "the symbol map for this checkout is being built right now"
    if partition is None or freshness is Freshness.NO_PARTITION:
        return "no symbol map for this checkout"
    try:
        current = head_sha(repo_root)
    except (UnbornHeadError, OSError) as exc:
        return (
            f"the map describes {partition.head_sha}, and this checkout's own "
            f"sha could not be read: {exc}"
        )
    return f"the map describes {partition.head_sha}, this checkout is {current}"


def verdicts_for(
    session: Session,
    workspace_id: str,
    repo_root: Path,
    anchors: Sequence[SymbolAnchor],
) -> MapRead[dict[str, GroundingCheck]]:
    """A verdict per anchor, keyed `qualified_name@fingerprint`.

    BATCHED: one partition and freshness derivation, one query by qualified
    name, one by fingerprint and one for the file paths — per CALL, never per
    anchor. The walk never mutates the index it reads, so the order anchors
    arrive in cannot decide them.
    """
    partition = partition_for(session, workspace_id, repo_root)
    status = status_for(session, repo_root, partition)

    if status.freshness is not Freshness.MATCHES:
        # Step 1 precedes every other: with nothing to compare against, "no
        # verdict was computed" is the honest answer even for an anchor whose
        # scheme this build also does not know.
        reason = _no_comparison_reason(status.freshness, partition, repo_root)
        return MapRead(
            rows={anchor_key(anchor): _unverifiable(anchor, reason) for anchor in anchors},
            status=status,
        )
    if partition is None or not anchors:
        return MapRead(rows={}, status=status)

    names = {anchor.qualified_name for anchor in anchors}
    fingerprints = {anchor.fingerprint for anchor in anchors if anchor.fingerprint}
    rows = list(
        session.exec(
            select(SymbolMapSymbol).where(
                SymbolMapSymbol.partition_id == partition.id,
                col(SymbolMapSymbol.qualified_name).in_(names),
            )
        ).all()
    )
    if fingerprints:
        rows += session.exec(
            select(SymbolMapSymbol).where(
                SymbolMapSymbol.partition_id == partition.id,
                col(SymbolMapSymbol.fingerprint).in_(fingerprints),
            )
        ).all()
    index = _sites(session, rows)
    return MapRead(
        rows={anchor_key(anchor): _check(anchor, index) for anchor in anchors},
        status=status,
    )


def _claim_verdict(checks: Sequence[GroundingCheck]) -> GroundingVerdict:
    return min((check.verdict for check in checks), key=_SEVERITY.index)


def claim_verdicts(
    session: Session,
    workspace_id: str,
    repo_root: Path,
    claims: Sequence[tuple[str, str]],
) -> Mapping[str, ClaimGrounding]:
    """The read paths' entry point: `(claim_id, anchors_json)` in, verdicts out.

    A claim with no anchors gets NO entry — not a GROUNDED one and not an
    UNVERIFIABLE one. It was never grounded, so it has nothing to say, and
    every read path must be able to tell that from a claim whose anchors all
    passed.

    A claim whose stored anchors cannot be read gets an UNVERIFIABLE entry and
    takes nothing else down with it. Letting it raise out of here and relying
    on the briefing's never-raising guard costs the whole capability for every
    claim in the call rather than one row.
    """
    decoded: dict[str, list[SymbolAnchor]] = {}
    corrupt: dict[str, str] = {}
    for claim_id, raw in claims:
        try:
            anchors = decode_anchors(raw)
        except CorruptAnchorsError as exc:
            corrupt[claim_id] = str(exc)
            continue
        if anchors:
            decoded[claim_id] = anchors

    # One lookup for every claim's anchors together: twenty claims cost what
    # one costs, which is S2's stated page size.
    batch = {anchor_key(anchor): anchor for anchors in decoded.values() for anchor in anchors}
    checks = verdicts_for(session, workspace_id, repo_root, list(batch.values())).rows

    graded: dict[str, ClaimGrounding] = {}
    for claim_id, reason in corrupt.items():
        graded[claim_id] = ClaimGrounding(
            verdict=GroundingVerdict.UNVERIFIABLE, checks=(), reason=reason
        )
    for claim_id, anchors in decoded.items():
        found = tuple(checks[anchor_key(anchor)] for anchor in anchors)
        verdict = _claim_verdict(found)
        worst = next(check for check in found if check.verdict is verdict)
        graded[claim_id] = ClaimGrounding(verdict=verdict, checks=found, reason=worst.reason)
    return graded


def _candidates(session: Session, partition_id: str, name: str) -> list[str]:
    """The nearest qualified names the map actually holds, at most three."""
    known = sorted(
        set(
            session.exec(
                select(SymbolMapSymbol.qualified_name).where(
                    SymbolMapSymbol.partition_id == partition_id
                )
            ).all()
        )
    )
    return difflib.get_close_matches(name, known, _MAX_CANDIDATES, 0.6)


def resolve_anchors(
    session: Session,
    workspace_id: str,
    repo_root: Path,
    names: Sequence[str],
) -> list[SymbolAnchor]:
    """Stamp one anchor per name, in the order asked, or refuse the lot.

    The fingerprint is read off the map and never typed, so no
    non-deterministic producer can mint one. Every refusal is worded as the
    environment's state, names the ticket that closes it where there is one,
    and ends with the ungrounded fallback: a claim stored ungrounded when its
    author asked for grounding is the silent failure this exists to remove.
    """
    partition = partition_for(session, workspace_id, repo_root)
    if partition is None:
        raise SymbolMapUnavailableError(
            "this checkout has no symbol map yet, so an anchor cannot be resolved "
            "against it; lg-code-knowledge-284 is the ticket that builds one in "
            f"production. Until it lands, {UNGROUNDED_FALLBACK}"
        )

    wanted = list(names)
    rows = session.exec(
        select(SymbolMapSymbol).where(
            SymbolMapSymbol.partition_id == partition.id,
            col(SymbolMapSymbol.qualified_name).in_(set(wanted)),
        )
    ).all()
    files = {
        file_row.id: file_row
        for file_row in session.exec(
            select(SymbolMapFile).where(col(SymbolMapFile.id).in_({row.file_id for row in rows}))
        ).all()
    }
    by_name: dict[str, list[SymbolMapSymbol]] = {}
    for row in rows:
        by_name.setdefault(row.qualified_name, []).append(row)
    for found in by_name.values():
        found.sort(key=lambda row: (files[row.file_id].path, row.line))

    sha = head_sha(repo_root)
    anchors: list[SymbolAnchor] = []
    for name in wanted:
        found = by_name.get(name, [])
        if not found:
            offered = _candidates(session, partition.id, name)
            nearest = (
                "; the map has " + ", ".join(offered)
                if offered
                else "; the map has nothing close to it"
            )
            raise UnknownSymbolAnchorError(
                f"no symbol named {name!r} is in the symbol map for this workspace "
                f"at {sha}{nearest}. To record the claim as it stands, "
                f"{UNGROUNDED_FALLBACK}"
            )
        row = found[0]
        if not row.fingerprint:
            raise UnfingerprintableSymbolError(
                f"the symbol {name!r} is in the map, but its extractor tier "
                f"({files[row.file_id].extractor_tier.value}) produces no fingerprint, so "
                "there is nothing to ground the claim against; lg-code-knowledge-386 "
                f"(tree-sitter) is the ticket that closes it. Until it lands, "
                f"{UNGROUNDED_FALLBACK}"
            )
        anchors.append(
            SymbolAnchor(
                qualified_name=name,
                fingerprint=row.fingerprint,
                path=files[row.file_id].path,
                checkout_identity=partition.checkout_identity,
                extractor_tier=files[row.file_id].extractor_tier,
            )
        )
    return anchors
