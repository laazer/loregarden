"""What inferred this package's derived signals, and where each heuristic is wired.

Two heuristics produce everything here that is not a direct measurement:

* `is_fix_subject` — a regex over commit subjects, behind `bugfix_commits` and
  `last_bugfix_at`;
* `classify_path` — behind the `PathKind` filtering those two are credited
  through (a fix touching a module and its doc credits the module only).

Each gets one frozen record with a stable id and its wiring site, and the
resolved triple is attached to the READ model (`FileSignals`) by
`store.signals_for_path` and `store.top_files_by`. No column and no migration:
`db/shared_database_guard.py` refuses a worktree migration `origin/main` does
not ship, and a field resolved at read time needs nothing stored.

ACCEPTED COST, recorded rather than solved: read-time resolution labels a row
computed by an older synthesizer with today's wiring site. Synthesizer
versioning belongs to lg-code-knowledge-2723.
"""

from __future__ import annotations

from dataclasses import dataclass

from loregarden.models.domain.provenance import Provenance

#: Stable ids. These are what a read model reports as `inferred_by`, and the
#: keys the registry resolves a site from — not the function objects, which move
#: between modules, and not a free-text label a caller types.
CLASSIFY_PATH_ID = "code_signals.classify_path"
IS_FIX_SUBJECT_ID = "code_signals.is_fix_subject"

_WIRED_IN = "loregarden.services.code_signals.compute.compute_signals"


@dataclass(frozen=True)
class SynthesizerRecord:
    """One heuristic: its stable id, where it is wired, and what it is."""

    synthesizer_id: str
    registered_at: str
    describes: str


RECORDS: tuple[SynthesizerRecord, ...] = (
    SynthesizerRecord(
        synthesizer_id=IS_FIX_SUBJECT_ID,
        registered_at=_WIRED_IN,
        describes="bugfix_commits and last_bugfix_at, from a regex over commit subjects",
    ),
    SynthesizerRecord(
        synthesizer_id=CLASSIFY_PATH_ID,
        registered_at=_WIRED_IN,
        describes="the PathKind a bug-fix commit is credited through",
    ),
)


@dataclass(frozen=True)
class SignalsRegistry:
    """The `SynthesizerRegistry` for this package's heuristics.

    Raises on an id it does not know rather than returning an empty site: a
    site invented for an unknown synthesizer is worse than no site, and a
    silently empty one would read as a recorded omission.
    """

    records: tuple[SynthesizerRecord, ...]

    def registered_at(self, synthesizer_id: str) -> str:
        for record in self.records:
            if record.synthesizer_id == synthesizer_id:
                return record.registered_at
        raise KeyError(f"no synthesizer registered under {synthesizer_id!r}")


REGISTRY = SignalsRegistry(RECORDS)


def resolved_provenance() -> tuple[Provenance, ...]:
    """The triple for every heuristic behind a `FileSignals` row, resolved now.

    `confidence` is left unset because nothing here derives one; shipping the
    field with no producer is honest, shipping a number nobody computed is not.
    """
    return tuple(
        Provenance(
            inferred_by=record.synthesizer_id,
            registered_at=REGISTRY.registered_at(record.synthesizer_id),
        )
        for record in RECORDS
    )


__all__ = [
    "CLASSIFY_PATH_ID",
    "IS_FIX_SUBJECT_ID",
    "RECORDS",
    "REGISTRY",
    "SignalsRegistry",
    "SynthesizerRecord",
    "resolved_provenance",
]
