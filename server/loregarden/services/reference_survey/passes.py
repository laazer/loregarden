"""One pass: its slice of the map, what it read, how it ended, and the coverage query.

A pass is bounded by the directory map, not by "whatever fits in one context":
the feeder assigns a slice of unread leaves up to a file budget, the pass records
what it did with each, and finishing it states the outcome, where it stopped, and
what to read next. Coverage is then a query (`coverage_report`) whose every number
carries its denominator.
"""

from __future__ import annotations

import json

from loregarden.models.domain.enums import utcnow
from loregarden.models.domain.survey_enums import (
    SurveyPassOutcome,
    SurveyPassStatus,
    SurveyReadState,
    SurveySourceKind,
    SurveySourceStatus,
)
from loregarden.models.domain.survey_tables import (
    SurveyCoverage,
    SurveyPass,
    SurveyPassRead,
    SurveySource,
)
from loregarden.services.reference_survey.coverage import MapEntry, is_leaf
from loregarden.services.reference_survey.errors import SurveyError, SurveyNotFound
from loregarden.services.reference_survey.queue import open_passes, queue_continuation
from loregarden.services.reference_survey.record import open_unknowns
from loregarden.services.reference_survey.schemas import PassFinishIn, ReadIn
from sqlmodel import Session, col, select


def get_pass(session: Session, pass_id: str) -> tuple[SurveyPass, SurveySource]:
    survey_pass = session.get(SurveyPass, pass_id)
    if survey_pass is None:
        raise SurveyNotFound(f"No survey pass {pass_id!r}")
    source = session.get(SurveySource, survey_pass.source_id)
    if source is None:
        raise SurveyNotFound(f"Pass {pass_id} names a missing source")
    return survey_pass, source


def coverage_rows(session: Session, source_id: str) -> list[SurveyCoverage]:
    return list(
        session.exec(
            select(SurveyCoverage)
            .where(SurveyCoverage.source_id == source_id)
            .order_by(col(SurveyCoverage.path))
        )
    )


def map_leaves(rows: list[SurveyCoverage]) -> list[SurveyCoverage]:
    """The map's leaves. Their file counts sum to the tree at the mapped commit."""
    mapped = {r.path for r in rows if r.in_map}
    return [r for r in rows if r.in_map and is_leaf(r.path, mapped)]


def leaf_containing(path: str, leaves: list[SurveyCoverage]) -> SurveyCoverage | None:
    """The map leaf a finer path rolls up into, or None when it is outside the map."""
    for leaf in leaves:
        if leaf.path == path:
            return leaf
        if leaf.path == "." and "/" not in path:
            return leaf
        if leaf.path.endswith("/."):
            parent = leaf.path[:-1]
            if path.startswith(parent) and "/" not in path[len(parent) :]:
                return leaf
        elif path.startswith(leaf.path + "/"):
            return leaf
    return None


def store_map(
    session: Session, source: SurveySource, entries: list[MapEntry], commit_sha: str
) -> None:
    """Record the map at `commit_sha`. Entries already present keep their priority
    and note (which a person or an earlier pass set) and take the new counts."""
    existing = {row.path: row for row in coverage_rows(session, source.id)}
    for entry in entries:
        row = existing.get(entry.path) or SurveyCoverage(source_id=source.id, path=entry.path)
        row.in_map = True
        row.file_count = entry.file_count
        row.byte_count = entry.byte_count
        row.mapped_commit = commit_sha
        session.add(row)
    source.mapped_commit = commit_sha
    session.add(source)
    session.commit()


_RANK = {SurveyReadState.SKIPPED: 0, SurveyReadState.PARTIAL: 1, SurveyReadState.READ: 2}


def _read_rows(session: Session, source_id: str) -> list[tuple[str, SurveyReadState, int]]:
    return list(
        session.exec(
            select(SurveyCoverage.path, SurveyPassRead.state, SurveyPassRead.files_read)
            .join(SurveyPassRead, col(SurveyPassRead.coverage_id) == SurveyCoverage.id)
            .where(SurveyCoverage.source_id == source_id)
        ).all()
    )


def read_states(session: Session, source_id: str) -> dict[str, SurveyReadState]:
    """The best state any pass recorded for each coverage path, finer ones included."""
    best: dict[str, SurveyReadState] = {}
    for path, state, _files in _read_rows(session, source_id):
        if path not in best or _RANK[state] > _RANK[best[path]]:
            best[path] = state
    return best


def leaf_states(
    rows: list[SurveyCoverage],
    states: dict[str, SurveyReadState],
    finer_read: dict[str, int] | None = None,
) -> dict[str, SurveyReadState]:
    """Each map leaf's state. A leaf with no read of its own but finer reads under
    it is partial, unless those reads add up to every file it holds."""
    leaves = map_leaves(rows)
    result = {leaf.path: states[leaf.path] for leaf in leaves if leaf.path in states}
    covered: dict[str, int] = {}
    for row in rows:
        if row.in_map or row.path not in states or states[row.path] is SurveyReadState.SKIPPED:
            continue
        leaf = leaf_containing(row.path, leaves)
        if leaf is None or leaf.path in result and result[leaf.path] is not SurveyReadState.PARTIAL:
            continue
        covered[leaf.path] = covered.get(leaf.path, 0) + (finer_read or {}).get(row.path, 0)
        complete = leaf.file_count is not None and covered[leaf.path] >= leaf.file_count
        result[leaf.path] = SurveyReadState.READ if complete else SurveyReadState.PARTIAL
    return result


def finer_files_read(session: Session, source_id: str) -> dict[str, int]:
    """Most files any one read recorded for each path that was read in full."""
    most: dict[str, int] = {}
    for path, state, files in _read_rows(session, source_id):
        if state is SurveyReadState.READ:
            most[path] = max(most.get(path, 0), files)
    return most


def assign_slice(session: Session, survey_pass: SurveyPass, *, file_budget: int) -> list[str]:
    """Choose the pass's paths up to the budget: ranked unread entries first (the
    list a pass left behind, finer entries included), then unread map leaves.

    A pass given paths explicitly keeps them. At least one entry is assigned even
    when it alone is over budget; that pass then records it as partial.
    """
    survey_pass.file_budget = file_budget
    explicit = json.loads(survey_pass.slice_json)
    if explicit:
        session.add(survey_pass)
        return explicit
    rows = coverage_rows(session, survey_pass.source_id)
    states = read_states(session, survey_pass.source_id)
    leaves = leaf_states(rows, states, finer_files_read(session, survey_pass.source_id))
    ranked = [
        r for r in rows if r.priority is not None and states.get(r.path) is not SurveyReadState.READ
    ]
    ranked.sort(key=lambda r: (r.priority or 0, r.path))
    unread = [
        r
        for r in map_leaves(rows)
        if r.path not in leaves and r.priority is None and (r.file_count or 0) > 0
    ]
    chosen: list[str] = []
    used = 0
    for row in [*ranked, *unread]:
        size = row.file_count or 1
        if chosen and used + size > file_budget:
            continue
        chosen.append(row.path)
        used += size
        if used >= file_budget:
            break
    survey_pass.slice_json = json.dumps(chosen)
    session.add(survey_pass)
    return chosen


def _coverage_for_path(
    session: Session, source: SurveySource, body: ReadIn, by_path: dict[str, SurveyCoverage]
) -> SurveyCoverage:
    row = by_path.get(body.path)
    if row is not None:
        return row
    if source.kind is not SurveySourceKind.REPO:
        # A site or released app has no tree to map: the pages a pass names are
        # its map, and the report says so rather than inventing a denominator.
        row = SurveyCoverage(
            source_id=source.id, path=body.path, in_map=True, file_count=body.file_count or 1,
            mapped_commit=source.mapped_commit,
        )  # fmt: skip
        session.add(row)
        session.flush()
        by_path[body.path] = row
        return row
    if leaf_containing(body.path, map_leaves(list(by_path.values()))) is None:
        raise SurveyError(
            f"{body.path} is not under any path in the map taken at {source.mapped_commit or 'no commit'}; "
            "record reads against the map, so coverage has a denominator."
        )
    if body.file_count is None:
        raise SurveyError(
            f"{body.path} is not a map entry: give its own file_count (1 for a single file)"
        )
    row = SurveyCoverage(
        source_id=source.id,
        path=body.path,
        file_count=body.file_count,
        mapped_commit=source.mapped_commit,
    )
    session.add(row)
    session.flush()
    by_path[body.path] = row
    return row


def record_reads(session: Session, pass_id: str, reads: list[ReadIn]) -> list[SurveyPassRead]:
    """What the pass did with each path: read, partly read, or deliberately skipped."""
    survey_pass, source = get_pass(session, pass_id)
    if survey_pass.status is not SurveyPassStatus.DISPATCHED:
        raise SurveyError(
            f"Pass {pass_id} is {survey_pass.status.value}; reads are recorded while it runs"
        )
    mapped = {row.path: row for row in coverage_rows(session, source.id)}
    stored: list[SurveyPassRead] = []
    for body in reads:
        coverage = _coverage_for_path(session, source, body, mapped)
        if coverage.file_count is not None and body.files_read > coverage.file_count:
            raise SurveyError(
                f"{body.path} has {coverage.file_count} files; {body.files_read} cannot have been read"
            )
        if body.state is SurveyReadState.SKIPPED and not body.note:
            raise SurveyError(f"Skipping {body.path} deliberately needs the reason in note")
        row = session.exec(
            select(SurveyPassRead).where(
                SurveyPassRead.pass_id == pass_id, SurveyPassRead.coverage_id == coverage.id
            )
        ).first() or SurveyPassRead(pass_id=pass_id, coverage_id=coverage.id, state=body.state)
        row.state = body.state
        row.files_read = body.files_read
        row.note = body.note
        session.add(row)
        stored.append(row)
    session.commit()
    return stored


def pass_counts(session: Session, pass_id: str) -> tuple[int, int]:
    """(files read, files under the paths the pass recorded)."""
    rows = session.exec(
        select(SurveyPassRead.files_read, SurveyCoverage.file_count)
        .join(SurveyCoverage, col(SurveyCoverage.id) == SurveyPassRead.coverage_id)
        .where(SurveyPassRead.pass_id == pass_id)
    ).all()
    return sum(r for r, _ in rows), sum(c or 0 for _, c in rows)


def finish_pass(session: Session, body: PassFinishIn) -> SurveyPass:
    """Close a pass with its outcome, where it stopped, and what to read next.

    Refuses `answered` while a question this pass opened is still open, and any
    finish at all when the pass recorded no reads: a pass that cannot say what it
    examined has no denominator for anything it found.
    """
    survey_pass, source = get_pass(session, body.pass_id)
    if survey_pass.status is not SurveyPassStatus.DISPATCHED:
        raise SurveyError(f"Pass {body.pass_id} is {survey_pass.status.value}, not running")
    still_open = open_unknowns(session, source_id=source.id, pass_id=survey_pass.id)
    if body.outcome is SurveyPassOutcome.ANSWERED and still_open:
        raise SurveyError(
            f"{len(still_open)} question(s) this pass opened are still open "
            f"({', '.join(u.id for u in still_open)}). Resolve them with observed evidence, or "
            "finish as partial or unresolved."
        )
    read, examined = pass_counts(session, survey_pass.id)
    if examined == 0:
        raise SurveyError("Record what the pass read (record_reads) before finishing it")
    mapped = {row.path: row for row in coverage_rows(session, source.id)}
    unknown_paths = [p for p in body.unread_priority if p not in mapped]
    if unknown_paths:
        raise SurveyError(
            f"Not in the map: {', '.join(unknown_paths)}. Name map paths, or record a read for them first."
        )
    for rank, path in enumerate(body.unread_priority, start=1):
        mapped[path].priority = rank
        session.add(mapped[path])
    survey_pass.status = SurveyPassStatus.FINISHED
    survey_pass.outcome = body.outcome
    survey_pass.outcome_note = body.outcome_note
    survey_pass.stopped_at = body.stopped_at
    survey_pass.finished_at = utcnow()
    session.add(survey_pass)
    session.flush()
    follow = None
    if body.outcome is not SurveyPassOutcome.ANSWERED and body.unread_priority:
        follow = queue_continuation(session, source, survey_pass.question)
    if follow is None and not [
        p for p in open_passes(session, source.id) if p.id != survey_pass.id
    ]:
        source.status = SurveySourceStatus.DONE
        source.updated_at = utcnow()
        session.add(source)
    session.commit()
    session.refresh(survey_pass)
    return survey_pass


def coverage_report(session: Session, source_id: str) -> dict:
    """The map with what every pass did to it. Every count has its denominator."""
    source = session.get(SurveySource, source_id)
    if source is None:
        raise SurveyNotFound(f"No survey source {source_id!r}")
    rows = coverage_rows(session, source_id)
    states = read_states(session, source_id)
    leaves = map_leaves(rows)
    per_leaf = leaf_states(rows, states, finer_files_read(session, source_id))
    total = sum(r.file_count or 0 for r in leaves)
    by_state = {
        state: sum(r.file_count or 0 for r in leaves if per_leaf.get(r.path) is state)
        for state in SurveyReadState
    }
    unopened = sorted(
        (r for r in leaves if r.path not in per_leaf),
        key=lambda r: (r.priority is None, r.priority or 0, -(r.file_count or 0)),
    )
    finer = [r for r in rows if not r.in_map]
    return {
        "source_id": source.id,
        "url": source.url,
        "mapped_commit": source.mapped_commit,
        "tree_files": total,
        "files_in_read_leaves": by_state[SurveyReadState.READ],
        "files_in_partial_leaves": by_state[SurveyReadState.PARTIAL],
        "files_in_skipped_leaves": by_state[SurveyReadState.SKIPPED],
        "files_in_unopened_leaves": sum(r.file_count or 0 for r in unopened),
        "denominator_note": (
            f"Files under the map's leaves at {source.mapped_commit or 'no commit (not mapped)'}: "
            f"{total} in all. A leaf counts as read only when a pass read all of it; one with "
            "some files opened is partial, and its files are not all read."
        ),
        "unopened": [
            {"path": r.path, "file_count": r.file_count, "priority": r.priority, "note": r.note}
            for r in unopened
        ],
        "finer_entries": [
            {
                "path": r.path,
                "file_count": r.file_count,
                "priority": r.priority,
                "state": states[r.path].value if r.path in states else None,
                "note": r.note,
            }
            for r in finer
        ],
    }


def pass_counts_line(session: Session, survey_pass: SurveyPass) -> str:
    read, examined = pass_counts(session, survey_pass.id)
    slice_paths = json.loads(survey_pass.slice_json)
    return f"read {read} of {examined} files in the paths it recorded; slice was {len(slice_paths)} path(s)"
