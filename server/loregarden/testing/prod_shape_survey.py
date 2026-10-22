"""The scenario's reference survey: the record the reference library shows.

Calibrated from the legacy import (`loregarden survey import-legacy`) of
`scratch/ref_projects/` into a copy of the live database, 2026-10-21. The live
tables are empty until that import runs after merge, so this is the shape the
library will first open on: 113 sources (99 surveyed, 5 candidates, 9 refused),
115 passes, and ticket links concentrated on three deep sources — the largest
with 24 passes, 71 linked tickets and 41 legacy references no workspace could
resolve. Most surveyed sources have one pass and no linked ticket, because the
link list recorded only an outcome for them.

Linked tickets are drawn with the same index rule `prod_shape._memory` uses for
learnings, so the deep sources carry learnings as live ones do.

Split from `prod_shape` (near its size cap); `build_prod_shape` calls it.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from pydantic import BaseModel
from sqlmodel import Session

from loregarden.models.domain.survey_enums import (
    LicenceBasis,
    LicenceKind,
    LicenceUse,
    SurveyPassMode,
    SurveyPassStatus,
    SurveySourceKind,
    SurveySourceStatus,
    SurveyTicketAction,
)
from loregarden.models.domain.survey_tables import (
    SurveyPass,
    SurveyPassTicket,
    SurveySource,
    SurveySourceWorkspace,
)


class DeepSource(BaseModel):
    name: str
    passes: int
    tickets: int
    unresolved: int
    licence: str


class SurveyCalibration(BaseModel):
    measured: str
    surveyed: int
    candidates: int
    refused: int
    sites: int
    deep: list[DeepSource]
    #: Linked-ticket counts of the other sources that have any, largest first.
    linked_tail: list[int]


#: The workspace the legacy record served; every imported source serves it.
SURVEY_WORKSPACE = "loregarden"

SURVEY_CALIBRATION = SurveyCalibration(
    measured="2026-10-21",
    surveyed=99,
    candidates=5,
    refused=9,
    sites=3,
    deep=[
        DeepSource(name="openharness", passes=24, tickets=71, unresolved=41, licence="MIT"),
        DeepSource(name="superpowers", passes=1, tickets=69, unresolved=0, licence="MIT"),
        DeepSource(name="ThinkTerm", passes=9, tickets=40, unresolved=0, licence="GPL-3.0-only"),
    ],
    linked_tail=[9, 8, 7, 6, 5, 4, 4, 4, 3, 3, 2, 1, 1],
)

_BASE = datetime(2026, 10, 12, 9, 0, 0)

# Live sources are distinct projects ("perspica", "open-notebook", "skills-manager"),
# so the scenario's names are too: a repeated template would read as a wall of
# identical rows that production does not have.
_FIRST = ["open", "agent", "quiet", "atomic", "swift", "lumen", "hollow", "patch", "drift", "ember",
          "north", "tiny", "civic", "pixel", "rapid", "kindle", "stone", "relay", "maple", "orbit"]  # fmt: skip
_SECOND = ["notebook", "harness", "term", "flow", "forge", "ledger", "deck", "atlas", "bench", "garden",
           "kit", "lens", "mux", "pilot", "scope", "shelf", "vault", "weave", "yard", "board"]  # fmt: skip
_OWNERS = [
    "acme-labs",
    "northwind",
    "fieldnotes",
    "openwork",
    "tinyco",
    "lambda-house",
    "sourcecraft",
]


def _name(n: int) -> str:
    return f"{_FIRST[n % len(_FIRST)]}-{_SECOND[(n * 7 + n // len(_FIRST)) % len(_SECOND)]}"


_LICENCES: list[tuple[str, LicenceKind, LicenceUse]] = [
    ("MIT", LicenceKind.OPEN, LicenceUse.FULL),
    ("Apache-2.0", LicenceKind.OPEN, LicenceUse.FULL),
    ("GPL-3.0-only", LicenceKind.LIMITED, LicenceUse.PATTERNS_ONLY),
    ("AGPL-3.0", LicenceKind.LIMITED, LicenceUse.PATTERNS_ONLY),
]


def _licence(name: str) -> tuple[str, LicenceKind, LicenceUse]:
    return next((entry for entry in _LICENCES if entry[0] == name), _LICENCES[0])


def _source(
    session: Session,
    workspace_id: str,
    n: int,
    name: str,
    status: SurveySourceStatus,
    licence: str,
    id_fn: Callable[[str, int], str],
) -> SurveySource:
    licence_name, kind, use = _licence(licence)
    cal = SURVEY_CALIBRATION
    # The three sites are surveyed sources with no linked ticket, as live.
    is_site = cal.surveyed - cal.sites <= n < cal.surveyed
    source = SurveySource(
        id=id_fn("survey-source", n),
        workspace_id=workspace_id,
        kind=SurveySourceKind.SITE if is_site else SurveySourceKind.REPO,
        url=f"https://{name}.dev/docs"
        if is_site
        else f"https://github.com/{_OWNERS[n % len(_OWNERS)]}/{name}",
        name=name,
        question="(not recorded in the legacy files)",
        queued_reason="imported from scratch/ref_projects",
        status=status,
        licence_name="" if status is SurveySourceStatus.PROPOSED else licence_name,
        licence_kind=None if status is SurveySourceStatus.PROPOSED else kind,
        licence_basis=None
        if status is SurveySourceStatus.PROPOSED
        else LicenceBasis.RECORDED_BY_HAND,
        licence_use=None if status is SurveySourceStatus.PROPOSED else use,
        refused_reason="NOT SURVEYED: declined on its measurement"
        if status is SurveySourceStatus.REFUSED
        else "",
        created_at=_BASE,
        updated_at=_BASE,
    )
    # Parents flush first: these tables join by bare foreign keys, with no
    # relationship for the unit of work to order inserts by.
    session.add(source)
    session.flush()
    session.add(
        SurveySourceWorkspace(source_id=source.id, workspace_id=workspace_id, added_at=_BASE)
    )
    return source


def _passes(
    session: Session, source: SurveySource, count: int, id_fn: Callable[[str, int], str]
) -> list[SurveyPass]:
    rows = []
    for ordinal in range(1, count + 1):
        row = SurveyPass(
            id=id_fn(f"survey-pass/{source.name}", ordinal),
            source_id=source.id,
            ordinal=ordinal,
            label=f"PASS {ordinal}",
            mode=SurveyPassMode.INITIAL if ordinal == 1 else SurveyPassMode.CONTINUATION,
            status=SurveyPassStatus.FINISHED,
            question="(not recorded in the legacy entry)",
            outcome_note="Imported from the legacy survey queue; the outcome was recorded as prose.",
            queued_at=_BASE,
            finished_at=_BASE + timedelta(hours=ordinal),
        )
        session.add(row)
        rows.append(row)
    session.flush()
    return rows


def _link(
    session: Session,
    passes: list[SurveyPass],
    tickets: list[tuple[str, str]],
    count: int,
    unresolved: int,
    offset: int,
) -> int:
    links = 0
    for k in range(count):
        ticket_id, ext = tickets[((k + offset) * 7) % len(tickets)]
        survey_pass = passes[k % len(passes)]
        session.add(
            SurveyPassTicket(
                pass_id=survey_pass.id, ticket_id=ticket_id, action=SurveyTicketAction.APPENDED,
                legacy_ref=ext, verified=False, created_at=_BASE,
            )
        )  # fmt: skip
        links += 1
    for k in range(unresolved):
        session.add(
            SurveyPassTicket(
                pass_id=passes[k % len(passes)].id, ticket_id=None, action=SurveyTicketAction.APPENDED,
                legacy_ref=str(100 + k * 7), created_at=_BASE,
            )
        )  # fmt: skip
    return links


def add_survey_shape(
    session: Session,
    workspace_id: str,
    tickets: list[tuple[str, str]],
    id_fn: Callable[[str, int], str],
) -> int:
    """Seed the calibrated survey record on `workspace_id`. Returns sources added.

    `tickets` is that workspace's (id, external_id) in creation order, the list
    the memory graph draws learnings from.
    """
    cal = SURVEY_CALIBRATION
    n = 0
    for offset, deep in enumerate(cal.deep):
        source = _source(
            session, workspace_id, n, deep.name, SurveySourceStatus.DONE, deep.licence, id_fn
        )
        passes = _passes(session, source, deep.passes, id_fn)
        _link(session, passes, tickets, deep.tickets, deep.unresolved, offset * 3)
        n += 1
    for k, count in enumerate(cal.linked_tail):
        source = _source(
            session,
            workspace_id,
            n,
            _name(n),
            SurveySourceStatus.DONE,
            _LICENCES[k % 4][0],
            id_fn,
        )
        _link(
            session,
            _passes(session, source, 1 + (k % 3 == 0), id_fn),
            tickets,
            count,
            0,
            40 + k * 5,
        )
        n += 1
    while n < cal.surveyed:
        source = _source(
            session,
            workspace_id,
            n,
            _name(n),
            SurveySourceStatus.DONE,
            _LICENCES[n % 4][0],
            id_fn,
        )
        _passes(session, source, 1, id_fn)
        n += 1
    for _ in range(cal.candidates):
        _source(session, workspace_id, n, _name(n), SurveySourceStatus.PROPOSED, "MIT", id_fn)
        n += 1
    for _ in range(cal.refused):
        _source(session, workspace_id, n, _name(n), SurveySourceStatus.REFUSED, "AGPL-3.0", id_fn)
        n += 1
    session.commit()
    return n
