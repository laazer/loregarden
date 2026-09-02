"""Whose code a failing transition gate is complaining about.

The orchestrator had one vocabulary for a stage that did not advance — "the
agent needs rework" — so a gate failing on somebody else's uncommitted file
reached the implementer as an instruction to fix code it had never written. On
the blobert milestone 14 run (2026-08-15) that happened to eight faults of nine.
Ticket 22's implementer diagnosed it correctly, wrote a checkpoint saying the
failing paths did not intersect its own diff, and was rerouted twice more.

The discriminator is a set intersection, and the work is getting the two sets.

WHY THIS DOES NOT PARSE FIVE FORMATS. Six gate commands are configured, and
three of them are this repo's own scripts which already emit `path:line: message`
— one shape, not three. Of the rest, `ruff check` and `oxlint` both have native
JSON, and `ruff format --check` prints one fixed line per file. So the extraction
below is small on purpose, and a gate nobody has taught it to read produces no
paths, which is a stated outcome rather than a wrong one.

WHY UNKNOWN IS THE COMMON ANSWER. The ticket's side of the comparison comes from
`changed_paths_json`, and 91% of succeeded runs record none (see
lg-workflow-integrity-406). A classifier that read an empty left-hand side as
"none of these are mine" would call every failure foreign and stop rerouting real
rework — a worse defect than the one this module exists to fix, and one that
would look like the feature working. So an empty side is UNKNOWN, and UNKNOWN
routes exactly as before.
"""

from __future__ import annotations

import json
import re
from pathlib import PurePosixPath

from loregarden.models.domain import GateFaultAttribution
from pydantic import BaseModel, ConfigDict, ValidationError

#: `path:line: message`, the shape this repo's own gate scripts emit
#: (py_organization_check, py_silent_except_check, ts_organization_check).
#:
#: The path must contain a `.` or `/`, and be followed by a line number. The line
#: number alone was not enough: `note:12: something` matched and yielded a path
#: of "note". A false path is not cosmetic here — it lands on one side of a set
#: intersection and can turn a ticket's own failure into FOREIGN.
_PATH_LINE_RE = re.compile(
    r"^\s*(?P<path>[^\s:][^:]*?[./][^:]*?):(?P<line>\d+):",
    re.MULTILINE,
)

#: `ruff format --check` names files it would rewrite and nothing else.
_WOULD_REFORMAT_RE = re.compile(r"^\s*Would reformat:\s*(?P<path>.+?)\s*$", re.MULTILINE)


class _Diagnostic(BaseModel):
    """One entry of a native JSON gate report, modelled rather than sniffed.

    `ruff --output-format=json` and `oxlint --format=json` both name the file and
    disagree about the key, so all three spellings are accepted and the first
    populated one wins. Everything else in the record is ignored: this module
    only ever asks which file a finding is about.

    Modelled at the boundary because the alternative is hand-written shape
    checks on third-party JSON, which is what the organization gate exists to
    stop. `extra="ignore"` is doing the work a chain of `isinstance` would.
    """

    model_config = ConfigDict(extra="ignore")

    filename: str = ""
    file: str = ""
    path: str = ""

    def named_path(self) -> str:
        return next(
            (value.strip() for value in (self.filename, self.file, self.path) if value.strip()),
            "",
        )


class _DiagnosticReport(BaseModel):
    """The object form some tools wrap their diagnostics in."""

    model_config = ConfigDict(extra="ignore")

    diagnostics: list[_Diagnostic] = []


def _json_paths(text: str) -> set[str]:
    """Paths from a native JSON gate report (ruff `--output-format=json`, oxlint
    `--format=json`).

    Answers nothing rather than raising for output that is not JSON, or is JSON
    of a shape this does not model — most gate output is neither, and a gate
    nobody has taught this to read must produce UNKNOWN rather than a wrong
    answer.
    """
    stripped = text.strip()
    if not stripped.startswith(("[", "{")):
        return set()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return set()

    try:
        records = (
            [_Diagnostic.model_validate(entry) for entry in payload]
            if stripped.startswith("[")
            else _DiagnosticReport.model_validate(payload).diagnostics
        )
    except ValidationError:
        return set()
    return {named for named in (record.named_path() for record in records) if named}


def gate_output_paths(output: str) -> set[str]:
    """Every path the gate output names, as written.

    An empty result means "this output named no paths we can read", which the
    caller must treat as UNKNOWN rather than as "no paths were involved".
    """
    if not output:
        return set()
    found = _json_paths(output)
    found.update(match.group("path").strip() for match in _PATH_LINE_RE.finditer(output))
    found.update(match.group("path").strip() for match in _WOULD_REFORMAT_RE.finditer(output))
    return {path for path in found if path}


def _comparable(path: str) -> str:
    """A path reduced to what two sources can agree on.

    Gate output is relative to wherever the gate ran — `server/` for ruff,
    `client/` for oxlint, the repo root for the organization scripts — while
    `changed_paths_json` is relative to the repository. Comparing the tails
    avoids inventing a mapping between them, at the cost of matching a same-named
    file in two directories. That trade is deliberate: a false TICKET keeps
    today's behaviour, while a false FOREIGN would swallow a real reroute.
    """
    return PurePosixPath(path.replace("\\", "/")).name


def _roots(paths: set[str]) -> set[str]:
    """Top-level directory of each path, or the path itself when it has none."""
    roots: set[str] = set()
    for path in paths:
        parts = PurePosixPath(path.replace("\\", "/")).parts
        if parts:
            roots.add(parts[0])
    return roots


def attribute_gate_failure(
    *, gate_output: str, ticket_paths: set[str] | list[str]
) -> tuple[GateFaultAttribution, set[str]]:
    """Whose code the failure names, and the gate paths it was decided from.

    Returns UNKNOWN whenever either side is empty, so the only case that changes
    the orchestrator's behaviour is a confident disagreement: the gate named
    paths, the ticket recorded paths, and they do not overlap.
    """
    named = gate_output_paths(gate_output)
    owned = {path for path in ticket_paths if path}
    if not named or not owned:
        return GateFaultAttribution.UNKNOWN, named

    owned_tails = {_comparable(path) for path in owned}
    if any(_comparable(path) in owned_tails for path in named):
        return GateFaultAttribution.TICKET, named

    # Absence from the recorded list is weak evidence on its own. The list is
    # whatever the ticket's runs happened to record, and it is empty 91% of the
    # time — so a list that is merely INCOMPLETE (a rename recorded under one
    # name, a file a mechanical fixer touched, a hand-staged edit) would make the
    # ticket's own failure look foreign, and blocking for a human on that is
    # worse than the reroute this module exists to prevent.
    #
    # So FOREIGN also requires the failure to be somewhere the ticket is not
    # working at all. The incident this was built for reads that way —
    # `creature_store/store.py` against a ticket editing `server/` — while an
    # unrecorded file in a directory the ticket is already changing stays
    # UNKNOWN and routes as before.
    if _roots(named) & _roots(owned):
        return GateFaultAttribution.UNKNOWN, named
    return GateFaultAttribution.FOREIGN, named
