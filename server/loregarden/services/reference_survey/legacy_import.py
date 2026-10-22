"""Import the survey's scratch files into the record, losslessly.

`scratch/ref_projects/` held the whole survey history in gitignored markdown:
`SURVEY_QUEUE.md` (pass records, coverage, unread lists), `ref_gh_links.md` (every
target and what became of it) and `UXBRIEF.md` (method addenda). This import:

1. Stores every section of every file verbatim (`SurveyLegacySection`), and proves
   the concatenation reproduces each file byte for byte. Nothing is lost even
   where the parse below is incomplete.
2. Indexes every target in `ref_gh_links.md` as a source, with its recorded
   licence (basis `recorded_by_hand`, which the feeder re-checks against the
   licence file at the next dispatch and reports any disagreement).
3. Parses the per-source sections of `SURVEY_QUEUE.md` into passes, the tickets
   each names, and the prioritised unread lists, and links the later lettered
   pass sections to sources by the pass label `ref_gh_links.md` records for them.

What the prose cannot settle is recorded as such, never guessed: a bare ticket
number is kept as `legacy_ref` and not resolved, because the files never say
which workspace it is in; a ticket whose action the prose does not state is
`mentioned`; an unread list's items keep their own words as the note.

Idempotent: re-running it against the same files changes nothing.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from loregarden.models.domain import Ticket, Workspace
from loregarden.models.domain.survey_enums import (
    LicenceBasis,
    SurveyPassMode,
    SurveyPassStatus,
    SurveySourceKind,
    SurveySourceStatus,
    SurveyTicketAction,
)
from loregarden.models.domain.survey_tables import (
    SurveyCoverage,
    SurveyLegacySection,
    SurveyPass,
    SurveyPassTicket,
    SurveySource,
    SurveySourceWorkspace,
)
from loregarden.services.reference_survey.licence import recorded_verdict
from sqlmodel import Session, select

LEGACY_FILES: tuple[str, ...] = ("SURVEY_QUEUE.md", "ref_gh_links.md", "UXBRIEF.md")

_HEADING_RE = re.compile(r"^#{1,2} ", re.MULTILINE)
_SOURCE_HEADING_RE = re.compile(r"^## (?P<n>\d+)\. (?P<name>.+?) — (?P<status>.+)$")
#: A lettered pass ("PASS J", "pass AI", "PASSES AA-AE"). Labels are capitals only,
#: so prose like "pass in" is not read as pass IN.
_PASS_HEADING_RE = re.compile(
    r"\b(?:PASS(?:ES)?|[Pp]ass(?:es)?)\s+(?P<label>[A-Z]{1,2}(?:-[A-Z]{1,2})?)\b"
)
_PASS_MARKER_RE = re.compile(
    r"(?:\[?PASS (?P<a>\d+)\b(?= DONE| \(|:)|\bPass (?P<b>\d+) \(|Read in pass (?P<c>\d+)\b)"
)
_OWNER_REPO_RE = re.compile(r"`?(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)`?")
_COMMIT_RE = re.compile(r"@(?P<sha>[0-9a-f]{7,40})\b")
_TICKET_RE = re.compile(r"\b(?:lg|lor)-[a-z0-9-]*?-?\d+\b|\blor-\d+\b")
_APPENDS_RE = re.compile(r"(?is)\bAppends? \(\d+\):(?P<ids>[^.\]]*)")
_BARE_ID_RE = re.compile(r"(?<![\w-])\d{2,4}(?![\w-])")
_LINK_RE = re.compile(
    r"^\s*[-*]?\s*(?:\((?P<ws>[A-Za-z-]+)\)\s*)?(?P<url>https?://\S+?)(?:\s+\((?P<note>.*)\))?\s*$"
)
_LICENCE_TOKEN_RE = re.compile(r"^(?P<id>[A-Za-z][A-Za-z0-9.+-]*(?:-\d[\w.]*)?)\b")
_DATE_RE = re.compile(r"(?P<d>20\d\d-\d\d-\d\d)")
#: How much of a link note states its outcome before the prose begins.
_NOTE_LEAD_CHARS = 160
#: A note this long is a finding someone wrote, so the source was read.
_WRITTEN_FINDING_CHARS = 120
_REFUSED_LEAD_RE = re.compile(r"\bNOT SURVEYED\b|\bEXCLUDED\b")


@dataclass
class ImportReport:
    sections: dict[str, int] = field(default_factory=dict)
    lossless: dict[str, bool] = field(default_factory=dict)
    sources_created: int = 0
    sources_seen: int = 0
    passes_created: int = 0
    ticket_links_resolved: int = 0
    ticket_refs_unresolved: int = 0
    unread_items: int = 0
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def split_sections(text: str) -> list[tuple[str, str]]:
    """(heading, body) per section; the bodies concatenate back to `text` exactly."""
    starts = [0, *[m.start() for m in _HEADING_RE.finditer(text) if m.start() > 0]]
    bounds = list(zip(starts, [*starts[1:], len(text)], strict=True))
    return [(text[a:b].split("\n", 1)[0].strip(), text[a:b]) for a, b in bounds if b > a]


def _store_sections(
    session: Session, origin: str, text: str, report: ImportReport
) -> list[SurveyLegacySection]:
    rows: list[SurveyLegacySection] = []
    for ordinal, (heading, body) in enumerate(split_sections(text)):
        digest = hashlib.sha256(body.encode()).hexdigest()
        row = session.exec(
            select(SurveyLegacySection).where(
                SurveyLegacySection.origin == origin, SurveyLegacySection.ordinal == ordinal
            )
        ).first()
        if row is not None and row.sha256 != digest:
            report.warnings.append(
                f"{origin} §{ordinal} changed since the last import; stored the new text"
            )
        row = row or SurveyLegacySection(
            origin=origin, ordinal=ordinal, heading=heading, body=body, sha256=digest
        )
        row.heading, row.body, row.sha256 = heading, body, digest
        session.add(row)
        rows.append(row)
    session.flush()
    report.sections[origin] = len(rows)
    report.lossless[origin] = "".join(r.body for r in rows) == text
    return rows


def _workspace(session: Session, slug: str) -> Workspace | None:
    return session.exec(select(Workspace).where(Workspace.slug == slug.lower())).first()


def _repo_url(owner: str, repo: str) -> str:
    return f"https://github.com/{owner}/{repo.removesuffix('.git')}"


def _licence_name(note: str) -> str:
    """The licence a legacy note opens with: "MIT — SURVEYED…", "**AGPL-3.0 — design…"."""
    text = note.strip().lstrip("*`( ")
    if re.match(r"(?i)no licen[cs]e", text):
        return "none"
    match = _LICENCE_TOKEN_RE.match(text)
    return match.group("id").rstrip(",") if match else ""


def _source_for(
    session: Session, workspace: Workspace, url: str, name: str, report: ImportReport
) -> SurveySource:
    source = session.exec(select(SurveySource).where(SurveySource.url == url)).first()
    report.sources_seen += 1
    if source is None:
        source = SurveySource(
            workspace_id=workspace.id,
            kind=SurveySourceKind.REPO if "github.com/" in url else SurveySourceKind.SITE,
            url=url,
            name=name,
            question="(not recorded in the legacy files)",
            queued_reason="imported from scratch/ref_projects",
        )
        session.add(source)
        session.flush()
        report.sources_created += 1
    if session.get(SurveySourceWorkspace, (source.id, workspace.id)) is None:
        session.add(
            SurveySourceWorkspace(
                source_id=source.id,
                workspace_id=workspace.id,
                question=source.question,
                queued_reason=source.queued_reason,
            )
        )
        session.flush()
    return source


def _apply_recorded_licence(source: SurveySource, name: str) -> None:
    if not name or source.licence_basis is LicenceBasis.FILE_AT_COMMIT:
        return
    verdict = recorded_verdict(name)
    source.licence_kind, source.licence_basis, source.licence_use = (
        verdict.kind,
        verdict.basis,
        verdict.use,
    )
    source.licence_name, source.licence_constraint = verdict.name, verdict.constraint


def import_links(
    session: Session, text: str, default_ws: Workspace, report: ImportReport
) -> dict[str, list[SurveySource]]:
    """Index every linked target. Returns sources by the pass label recorded for them."""
    by_label: dict[str, list[SurveySource]] = {}
    for line in text.splitlines():
        match = _LINK_RE.match(line)
        if not match:
            continue
        workspace = _workspace(session, match.group("ws") or "") or default_ws
        url = match.group("url").rstrip(").,")
        owner_repo = (
            _OWNER_REPO_RE.search(url.split("github.com/", 1)[-1]) if "github.com/" in url else None
        )
        if owner_repo:
            url = _repo_url(owner_repo.group("owner"), owner_repo.group("repo"))
        name = url.rstrip("/").rsplit("/", 1)[-1]
        source = _source_for(session, workspace, url, name, report)
        note = match.group("note") or ""
        source.notes = note or source.notes
        _apply_recorded_licence(source, _licence_name(note))
        # The outcome is stated in the note's opening clause ("MIT — SURVEYED…",
        # "AGPL-3.0 … — NOT SURVEYED…"); the rest is survey prose, where words
        # like "excluded" describe findings, not the source.
        upper = note[:_NOTE_LEAD_CHARS].upper()
        label = _PASS_HEADING_RE.search(note)
        if _REFUSED_LEAD_RE.search(upper):
            source.status = SurveySourceStatus.REFUSED
            source.refused_reason = note[:2000]
        elif "SURVEYED" in upper or label or len(note) >= _WRITTEN_FINDING_CHARS:
            # Inferred, not stated: a note naming a pass, or long enough to be a
            # written finding, records a survey. A bare or one-line link is a
            # candidate nobody has read.
            source.status = SurveySourceStatus.DONE
        if label:
            by_label.setdefault(label.group("label").upper(), []).append(source)
        session.add(source)
    session.flush()
    return by_label


def _resolve_ticket(session: Session, ref: str) -> Ticket | None:
    rows = session.exec(select(Ticket).where(Ticket.external_id == ref)).all()
    return rows[0] if len(rows) == 1 else None


def _link_tickets(
    session: Session, survey_pass: SurveyPass, text: str, report: ImportReport
) -> None:
    appended: set[str] = set()
    for block in _APPENDS_RE.finditer(text):
        appended.update(_TICKET_RE.findall(block.group("ids")))
        for bare in _BARE_ID_RE.findall(_TICKET_RE.sub("", block.group("ids"))):
            _add_link(session, survey_pass, None, SurveyTicketAction.APPENDED, bare, report)
    for ref in dict.fromkeys(_TICKET_RE.findall(text)):
        action = SurveyTicketAction.APPENDED if ref in appended else SurveyTicketAction.MENTIONED
        _add_link(session, survey_pass, _resolve_ticket(session, ref), action, ref, report)


def _add_link(
    session: Session,
    survey_pass: SurveyPass,
    ticket: Ticket | None,
    action: SurveyTicketAction,
    ref: str,
    report: ImportReport,
) -> None:
    exists = session.exec(
        select(SurveyPassTicket).where(
            SurveyPassTicket.pass_id == survey_pass.id, SurveyPassTicket.legacy_ref == ref
        )
    ).first()
    if exists is not None:
        return
    session.add(
        SurveyPassTicket(
            pass_id=survey_pass.id,
            ticket_id=ticket.id if ticket else None,
            action=action,
            legacy_ref=ref,
        )
    )
    if ticket is None:
        report.ticket_refs_unresolved += 1
    else:
        report.ticket_links_resolved += 1


def _pass(
    session: Session,
    source: SurveySource,
    ordinal: int,
    label: str,
    text: str,
    section: SurveyLegacySection,
    report: ImportReport,
) -> SurveyPass:
    row = session.exec(
        select(SurveyPass).where(SurveyPass.source_id == source.id, SurveyPass.label == label)
    ).first()
    if row is None:
        taken = {
            p.ordinal
            for p in session.exec(select(SurveyPass).where(SurveyPass.source_id == source.id)).all()
        }
        while ordinal in taken:
            ordinal += 1
        date = _DATE_RE.search(text) or _DATE_RE.search(section.heading)
        commit = _COMMIT_RE.search(text) or _COMMIT_RE.search(section.body[:600])
        row = SurveyPass(
            source_id=source.id,
            ordinal=ordinal,
            label=label,
            mode=SurveyPassMode.INITIAL if ordinal == 1 else SurveyPassMode.CONTINUATION,
            status=SurveyPassStatus.FINISHED,
            question="(not recorded in the legacy entry)",
            outcome_note="Imported from the legacy survey queue; the outcome was recorded as prose.",
            read_commit=commit.group("sha") if commit else "",
            legacy_section_id=section.id,
            legacy_text=text,
            finished_at=datetime.fromisoformat(date.group("d")) if date else None,
        )
        session.add(row)
        session.flush()
        report.passes_created += 1
    _link_tickets(session, row, text, report)
    return row


def _pass_segments(body: str) -> list[tuple[int, str]]:
    """(ordinal, text) per pass marker, in order; the text before the first is pass 1."""
    marks = []
    seen: set[int] = set()
    for m in _PASS_MARKER_RE.finditer(body):
        n = int(m.group("a") or m.group("b") or m.group("c"))
        if n not in seen:
            seen.add(n)
            marks.append((n, m.start()))
    if not marks:
        return [(1, body)]
    # An unread list is not part of the pass it follows; it is stored as coverage.
    stops = sorted(
        [s for _, s in marks[1:]] + [m.start() for m in re.finditer(r"STILL UNREAD", body)]
    )
    ends = [next((s for s in stops if s > start), len(body)) for _, start in marks]
    segments = [(n, body[start:end]) for (n, start), end in zip(marks, ends, strict=True)]
    if 1 not in seen:
        segments.insert(0, (1, body[: marks[0][1]]))
    return segments


def _unread_items(body: str) -> list[str]:
    """The first prioritised STILL UNREAD list in a section, one string per item."""
    lines = body.splitlines()
    start = next(
        (
            i
            for i, line in enumerate(lines)
            if "STILL UNREAD" in line and line.rstrip().endswith(":")
        ),
        None,
    )
    if start is None:
        return []
    items: list[str] = []
    indent: int | None = None
    for line in lines[start + 1 :]:
        stripped = line.strip()
        depth = len(line) - len(line.lstrip())
        if not stripped or stripped.startswith("["):
            break
        if indent is None:
            indent = depth
        if depth < indent:
            break
        if depth == indent:
            items.append(stripped)
        else:
            items[-1] += " " + stripped
    return items


def _store_unread(
    session: Session, source: SurveySource, items: list[str], report: ImportReport
) -> None:
    existing = session.exec(
        select(SurveyCoverage).where(SurveyCoverage.source_id == source.id)
    ).all()
    taken = {row.path for row in existing}
    notes = {row.note for row in existing}
    for rank, item in enumerate(items, start=1):
        note = f"Legacy unread list, item {rank}, as written (later passes may have read part of it): {item}"
        if note in notes:
            continue
        path = item.split(" ", 1)[0].rstrip(",") or f"unread-{rank}"
        if path in taken:
            path = f"{path}#{rank}"
        taken.add(path)
        session.add(SurveyCoverage(source_id=source.id, path=path, priority=rank, note=note))
        report.unread_items += 1


def import_queue(
    session: Session, sections: list[SurveyLegacySection], default_ws: Workspace,
    by_label: dict[str, list[SurveySource]], report: ImportReport,
) -> None:  # fmt: skip
    for section in sections:
        label = _PASS_HEADING_RE.search(section.heading)
        heading = None if label else _SOURCE_HEADING_RE.match(section.heading)
        if heading:
            head_lines = "\n".join(section.body.splitlines()[:4])
            repo = _OWNER_REPO_RE.search(head_lines)
            if repo is None:
                report.warnings.append(
                    f"{section.heading}: no owner/repo in its first lines; kept as text only"
                )
                continue
            source = _source_for(
                session,
                default_ws,
                _repo_url(repo.group("owner"), repo.group("repo")),
                heading.group("name"),
                report,
            )
            _apply_recorded_licence(
                source, _licence_name(head_lines.split(repo.group(0), 1)[-1].lstrip(" `·,"))
            )
            commit = _COMMIT_RE.search(section.body[:600])
            source.pinned_commit = source.pinned_commit or (commit.group("sha") if commit else "")
            # A source with its own section was surveyed, whatever a link note says.
            source.status = SurveySourceStatus.DONE
            source.refused_reason = ""
            for ordinal, text in _pass_segments(section.body):
                _pass(session, source, ordinal, f"PASS {ordinal}", text, section, report)
            _store_unread(session, source, _unread_items(section.body), report)
            last_unread = section.body.rfind("STILL UNREAD")
            if last_unread >= 0:
                source.notes = section.body[last_unread : last_unread + 2000]
            session.add(source)
            continue
        if label is None:
            continue
        for key in _expand_labels(label.group("label").upper()):
            for source in by_label.get(key, []):
                _pass(session, source, 1, f"PASS {key}", "", section, report)
                session.add(source)


def _label_index(label: str) -> int:
    value = 0
    for char in label:
        value = value * 26 + (ord(char) - 64)
    return value


def _expand_labels(label: str) -> list[str]:
    """`AA-AE` names five passes."""
    if "-" not in label:
        return [label]
    first, last = label.split("-", 1)
    names = []
    for i in range(_label_index(first), _label_index(last) + 1):
        name, n = "", i
        while n:
            n, r = divmod(n - 1, 26)
            name = chr(65 + r) + name
        names.append(name)
    return names


def import_legacy(
    session: Session, directory: Path, *, workspace_slug: str, commit: bool = True
) -> ImportReport:
    """Import the three files. `commit=False` parses and reports, then rolls back."""
    report = ImportReport()
    default_ws = _workspace(session, workspace_slug)
    if default_ws is None:
        raise ValueError(f"No workspace {workspace_slug!r}")
    texts = {}
    for name in LEGACY_FILES:
        path = directory / name
        if not path.is_file():
            report.warnings.append(f"{name} not found in {directory}")
            continue
        texts[name] = path.read_text(encoding="utf-8")
    stored = {name: _store_sections(session, name, text, report) for name, text in texts.items()}
    by_label = import_links(session, texts.get("ref_gh_links.md", ""), default_ws, report)
    import_queue(session, stored.get("SURVEY_QUEUE.md", []), default_ws, by_label, report)
    if commit and all(report.lossless.values()):
        session.commit()
    else:
        # A file whose sections do not reassemble into it is not imported at all.
        session.rollback()
    return report
