import json

import pytest
from loregarden.models.domain import Artifact, ArtifactKind, Workspace
from loregarden.services.diff_artifact_repair import DIFFS_SHOW_NO_DELETED_FILE_HEADER
from loregarden.services.invariants import run_invariant
from sqlmodel import Session, select
from tests.worktree_helpers import make_ticket


@pytest.fixture
def ticket(db_session: Session):
    workspace = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()
    return make_ticket(db_session, workspace, external_id="LG-DIFF-1")


def _deleted(path: str, *, header_row: bool) -> dict:
    lines = [{"type": "h", "ln": "", "text": "@@ -1,2 +0,0 @@"}]
    lines += [{"type": "d", "ln": "", "text": "a"}, {"type": "d", "ln": "", "text": "b"}]
    if header_row:
        lines.insert(0, {"type": "a", "ln": "", "text": "++ /dev/null"})
    return {"path": path, "add": 1 if header_row else 0, "del": 2, "lines": lines}


def _added_line_reading_the_header() -> dict:
    """A real added line with that text sits after a hunk header, not before it."""
    lines = [
        {"type": "h", "ln": "", "text": "@@ -0,0 +1 @@"},
        {"type": "a", "ln": "", "text": "++ /dev/null"},
    ]
    return {"path": "notes.txt", "add": 1, "del": 0, "lines": lines}


def _lookalikes() -> list[dict]:
    """Each section matches the bad row in all but one respect."""
    hunk = {"type": "h", "ln": "", "text": "@@ -0,0 +1 @@"}

    def first(row_type: str, text: str, then: dict) -> dict:
        return {
            "path": f"{row_type}-{then['type']}.txt",
            "add": 1,
            "del": 0,
            "lines": [{"type": row_type, "ln": "", "text": text}, then],
        }

    return [
        first("c", "++ /dev/null", hunk),
        first("a", "++ /dev/nul", hunk),
        first("a", "++ /dev/null", {"type": "a", "ln": "2", "text": "next"}),
    ]


def _diff(session: Session, ticket, sections: list[dict]) -> Artifact:
    artifact = Artifact(
        ticket_id=ticket.id,
        kind=ArtifactKind.DIFF,
        title="git diff",
        content_json=json.dumps({"file": "x", "sections": sections}),
    )
    session.add(artifact)
    session.commit()
    session.refresh(artifact)
    return artifact


def test_a_stored_diff_loses_the_header_row_and_keeps_everything_else(db_session: Session, ticket):
    bad = _diff(
        db_session,
        ticket,
        [_deleted("gone.txt", header_row=True), _added_line_reading_the_header()],
    )
    good = _diff(db_session, ticket, [_deleted("also-gone.txt", header_row=False), *_lookalikes()])
    good_before = good.content_json

    violations = DIFFS_SHOW_NO_DELETED_FILE_HEADER.detect(db_session)
    assert [v.subject_id for v in violations] == [bad.id]

    assert DIFFS_SHOW_NO_DELETED_FILE_HEADER.repair(db_session, violations) == 1

    db_session.refresh(bad)
    db_session.refresh(good)
    sections = json.loads(bad.content_json)["sections"]
    assert sections == [_deleted("gone.txt", header_row=False), _added_line_reading_the_header()]
    assert good.content_json == good_before
    assert DIFFS_SHOW_NO_DELETED_FILE_HEADER.detect(db_session) == []


def test_the_reconcile_pass_repairs_backlog(db_session: Session, ticket):
    bad = _diff(db_session, ticket, [_deleted("gone.txt", header_row=True)])

    check = run_invariant(db_session, DIFFS_SHOW_NO_DELETED_FILE_HEADER)

    assert (check.found, check.repaired) == (1, 1)
    db_session.refresh(bad)
    assert json.loads(bad.content_json)["sections"] == [_deleted("gone.txt", header_row=False)]
