"""Raw ticket artifact feed for the Artifacts tab."""

from __future__ import annotations

import json

from loregarden.models.domain import (
    Artifact,
    ArtifactKind,
    GateOutcome,
    StageBudgetArtifactKind,
    Ticket,
    TicketState,
    Workspace,
)
from loregarden.services.artifact_content_bound import ARTIFACT_FEED_CONTENT_LIMIT
from loregarden.services.artifact_records import (
    RUN_CONTEXT_ARTIFACT_TITLE,
    run_log_artifact_title,
)
from loregarden.services.artifact_service import list_ticket_artifacts
from loregarden.services.land_verification import VerifyOutcome, record_land_verification
from sqlalchemy import event
from sqlmodel import Session, select
from tests.factories import make_agent_run


def _workspace(session: Session) -> Workspace:
    ws = session.exec(select(Workspace)).first()
    if ws:
        return ws
    ws = Workspace(slug="ws-artifacts", name="Artifacts WS", repo_path="/tmp")
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


def _ticket(session: Session, workspace_id: str) -> Ticket:
    ticket = Ticket(
        external_id="art-1",
        title="Artifact feed",
        state=TicketState.IN_PROGRESS,
        workspace_id=workspace_id,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def test_list_ticket_artifacts_newest_first(db_session: Session):
    ws = _workspace(db_session)
    ticket = _ticket(db_session, ws.id)
    older = Artifact(
        ticket_id=ticket.id,
        kind="analysis",
        title="First note",
        content_json=json.dumps({"n": 1}),
    )
    newer = Artifact(
        ticket_id=ticket.id,
        kind="test_spec",
        title="Second note",
        content_json=json.dumps({"n": 2}),
    )
    db_session.add(older)
    db_session.commit()
    db_session.add(newer)
    db_session.commit()

    body = list_ticket_artifacts(db_session, ticket.id)
    assert body["total"] == 2
    assert [item["title"] for item in body["items"]] == ["Second note", "First note"]
    assert body["items"][0]["kind"] == "test_spec"
    assert body["items"][0]["content"] == {"n": 2}
    assert body["items"][0]["content_bytes"] > 0


def test_get_ticket_artifacts_endpoint(client, db_session: Session):
    ws = _workspace(db_session)
    ticket = _ticket(db_session, ws.id)
    db_session.add(
        Artifact(
            ticket_id=ticket.id,
            kind="source_analysis",
            title="Gate runner",
            content_json=json.dumps({"files": ["gate_runner.py"]}),
        )
    )
    db_session.commit()

    resp = client.get(f"/api/tickets/{ticket.id}/artifacts")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["kind"] == "source_analysis"
    assert body["items"][0]["content"]["files"] == ["gate_runner.py"]


def test_get_ticket_artifacts_404(client):
    assert client.get("/api/tickets/nope/artifacts").status_code == 404


def _by_title(body: dict) -> dict[str, dict]:
    return {item["title"]: item for item in body["items"]}


def test_feed_names_the_stage_from_the_run_then_from_the_content(db_session: Session):
    ws = _workspace(db_session)
    ticket = _ticket(db_session, ws.id)
    run = make_agent_run(
        db_session,
        workspace_id=ws.id,
        ticket_id=ticket.id,
        run_code="run_feed01",
        stage_key="implement",
    )
    db_session.add_all(
        [
            # The run wins over whatever the content claims.
            Artifact(
                ticket_id=ticket.id,
                run_id=run.id,
                kind="handoff",
                title="from the run",
                content_json=json.dumps({"stage_key": "plan"}),
            ),
            Artifact(
                ticket_id=ticket.id,
                kind="error",
                title="from the content",
                content_json=json.dumps({"stage_key": "gate"}),
            ),
            Artifact(ticket_id=ticket.id, kind="plan", title="no stage", content_json="{}"),
            Artifact(ticket_id=ticket.id, kind="data", title="a list", content_json="[1, 2]"),
            Artifact(
                ticket_id=ticket.id,
                kind="data",
                title="a non-string stage",
                content_json=json.dumps({"stage_key": 3}),
            ),
        ]
    )
    db_session.commit()

    items = _by_title(list_ticket_artifacts(db_session, ticket.id))
    assert items["from the run"]["stage_key"] == "implement"
    assert items["from the content"]["stage_key"] == "gate"
    assert items["no stage"]["stage_key"] is None
    assert items["a list"]["stage_key"] is None
    assert items["a non-string stage"]["stage_key"] is None


def test_feed_marks_the_platforms_bookkeeping_as_system(db_session: Session):
    ws = _workspace(db_session)
    ticket = _ticket(db_session, ws.id)
    run = make_agent_run(
        db_session,
        workspace_id=ws.id,
        ticket_id=ticket.id,
        run_code="run_feed02",
        stage_key="gate",
    )
    db_session.add_all(
        [
            Artifact(
                ticket_id=ticket.id,
                kind=StageBudgetArtifactKind.DISPATCH,
                title="stage-dispatch:gate",
            ),
            Artifact(
                ticket_id=ticket.id,
                run_id=run.id,
                kind=ArtifactKind.CONTEXT,
                title=RUN_CONTEXT_ARTIFACT_TITLE,
            ),
            Artifact(
                ticket_id=ticket.id,
                run_id=run.id,
                kind=ArtifactKind.LOG,
                title=run_log_artifact_title(run.run_code),
            ),
            # Work output, even where the shape is close to a system record.
            Artifact(ticket_id=ticket.id, run_id=run.id, kind=ArtifactKind.LOG, title="Run notes"),
            # Names a run, but no run of its own: an agent's title, not a pointer.
            Artifact(ticket_id=ticket.id, kind=ArtifactKind.LOG, title="Run run_elsewhere"),
            Artifact(ticket_id=ticket.id, kind=ArtifactKind.CONTEXT, title="Stage report — gate"),
        ]
    )
    db_session.commit()

    system = {
        item["title"]: item["system"]
        for item in list_ticket_artifacts(db_session, ticket.id)["items"]
    }
    assert system == {
        "stage-dispatch:gate": True,
        RUN_CONTEXT_ARTIFACT_TITLE: True,
        "Run run_feed02": True,
        "Run notes": False,
        "Run run_elsewhere": False,
        "Stage report — gate": False,
    }


def test_feed_reads_one_query_and_never_the_run_output(db_session: Session):
    """The feed joins each row's run for two short columns, not the run's output.

    Selecting the whole AgentRun read every run's stdout/stderr once per
    artifact row; on a 48-run ticket that was 274 MB and 4-16 s per request.
    """
    ws = _workspace(db_session)
    ticket = _ticket(db_session, ws.id)
    run = make_agent_run(
        db_session,
        workspace_id=ws.id,
        ticket_id=ticket.id,
        run_code="run_feed03",
        stage_key="implement",
    )
    run.stdout = "x" * 100_000
    run.stderr = "y" * 100_000
    db_session.add(run)
    db_session.add_all(
        Artifact(ticket_id=ticket.id, run_id=run.id, kind="handoff", title=f"h{i}")
        for i in range(5)
    )
    db_session.commit()
    ticket_id = ticket.id  # the commit expired it; refresh outside the measured window

    statements: list[str] = []

    def record(_conn, _cursor, statement, *_args) -> None:
        statements.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        body = list_ticket_artifacts(db_session, ticket_id)
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert body["total"] == 5
    assert {item["stage_key"] for item in body["items"]} == {"implement"}
    assert len(statements) == 1
    assert "agent_runs.stdout" not in statements[0]
    assert "agent_runs.stderr" not in statements[0]


# --- the per-row content bound (lg-milestone-that-1424) ---------------------
#
# The feed is polled every 2 s while the Outputs tab is open and returns the
# whole parsed content of up to 200 rows. The land-verification receipt's
# `content_json.output` is the concatenated stdout+stderr of every verify
# command, unbounded: a green 42-test run measured 869 bytes, seven failures
# 16,575 (~2.4 KB per failure), and a conftest-level break putting 500 tests red
# is ~1.2 MB in one row — served synchronously over SQLite on the event loop.
#
# The receipt is right as specified (lg-milestone-that-1419 AC9 requires the
# FULL output on the record), so the bound belongs here, on the read side.


def _receipt_ticket(session: Session, workspace_id: str) -> Ticket:
    """A ticket whose stage key is set, as a real landing's always is.

    `reportVerdict` needs `stage_key` as well as `status`, so a receipt written
    for a ticket with no stage key would not colour a pill even unbounded, and
    the verdict test would pass by checking nothing.
    """
    ticket = Ticket(
        external_id="art-receipt",
        title="Land verification feed",
        state=TicketState.IN_PROGRESS,
        workspace_id=workspace_id,
        workflow_stage_key="done",
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


#: The output of a broad red: a conftest-level break or a bad refactor putting
#: hundreds of tests red. AC1 pins the test at >= 1 MiB.
_RED_SUITE_OUTPUT = "FAILED server/tests/test_thing.py::case — AssertionError\n" * 20_000


def _only(body: dict) -> dict:
    assert body["total"] == 1, body
    return body["items"][0]


def _re_encoded_bytes(content: object) -> int:
    return len(json.dumps(content, ensure_ascii=False).encode("utf-8"))


def test_feed_row_content_is_bounded(db_session: Session):
    """AC1 — no feed row's content exceeds ARTIFACT_FEED_CONTENT_LIMIT.

    Asserts the measured size of what came back, not merely that a marker is
    present: a marker beside a megabyte is the same request.
    """
    ws = _workspace(db_session)
    ticket = _receipt_ticket(db_session, ws.id)
    assert len(_RED_SUITE_OUTPUT.encode("utf-8")) >= 1_048_576
    record_land_verification(
        db_session,
        ticket,
        branch=f"loregarden/{ticket.external_id}",
        target="integration/lg-ms",
        candidate_sha="0" * 40,
        outcome=VerifyOutcome(
            ok=False,
            outcome=GateOutcome.FAILED,
            command="uv run pytest -q",
            exit_code=1,
            output=_RED_SUITE_OUTPUT,
        ),
    )

    row = _only(list_ticket_artifacts(db_session, ticket.id))

    assert row["kind"] == ArtifactKind.LAND_VERIFICATION
    assert _re_encoded_bytes(row["content"]) <= ARTIFACT_FEED_CONTENT_LIMIT


def test_bounded_receipt_keeps_verdict_fields(db_session: Session):
    """AC3 — the row's verdict still renders from the feed row alone, with no fetch.

    The receipt shape specifically, not an oversized row in general: verified
    read-only 2026-10-29 that ZERO of the 2,139 currently-oversized rows carry
    top-level `status`+`stage_key` (largest verdict-bearing row 9,729 bytes), so
    a generic oversized-row test would pass while the one row shape this ticket
    exists for breaks. `status` and `stage_key` are what `reportVerdict`
    requires; `exit_code` is the field an operator checks first, and a bound
    that dropped it would leave the pill right and the row useless.
    """
    ws = _workspace(db_session)
    ticket = _receipt_ticket(db_session, ws.id)
    record_land_verification(
        db_session,
        ticket,
        branch="loregarden/lg-r-1",
        target="integration/lg-ms",
        candidate_sha="a" * 40,
        outcome=VerifyOutcome(
            ok=False,
            outcome=GateOutcome.FAILED,
            command="uv run pytest -q",
            exit_code=1,
            output=_RED_SUITE_OUTPUT,
        ),
    )

    content = _only(list_ticket_artifacts(db_session, ticket.id))["content"]

    assert content["status"] == "failed"
    assert content["stage_key"] == "done"
    assert content["exit_code"] == 1
    # The flat scalars of the receipt are all small; the bound has no reason to
    # reach any of them, and the prefix of `output` is what the reader opens on.
    assert content["branch"] == "loregarden/lg-r-1"
    assert content["command"] == "uv run pytest -q"
    assert content["output"], "the reader opens on the head of the log, not on nothing"
    assert _RED_SUITE_OUTPUT.startswith(content["output"])


def test_bounded_handoff_row_keeps_its_checklist_score(db_session: Session):
    """AC3, second case — `handoffScore` reads two numbers nested under `handoff`.

    The array is what made the row oversized, so the array is what goes; the two
    numbers the badge is built from are scalars and survive.
    """
    ws = _workspace(db_session)
    ticket = _ticket(db_session, ws.id)
    db_session.add(
        Artifact(
            ticket_id=ticket.id,
            kind=ArtifactKind.HANDOFF,
            title="handoff spec → test_designer",
            content_json=json.dumps(
                {
                    "handoff": {
                        "from_agent": "spec",
                        "to_agent": "test_designer",
                        "required_items_met": 5,
                        "total_required_items": 7,
                        "checklist": [
                            {"item_key": f"item_{i}", "evidence": "e" * 900} for i in range(40)
                        ],
                    }
                }
            ),
        )
    )
    db_session.commit()

    row = _only(list_ticket_artifacts(db_session, ticket.id))

    assert row["content_bytes"] > ARTIFACT_FEED_CONTENT_LIMIT
    assert _re_encoded_bytes(row["content"]) <= ARTIFACT_FEED_CONTENT_LIMIT
    assert row["content"]["handoff"]["required_items_met"] == 5
    assert row["content"]["handoff"]["total_required_items"] == 7
    assert "checklist" not in row["content"]["handoff"]


def test_only_shortened_rows_are_marked(db_session: Session):
    """AC4 — truncation is visible, and marked only where it happened.

    A universal `content_truncated: false` would restate all 177 prod-shape
    fixture rows for nothing, so the two keys are ABSENT on a row that was not
    shortened. `content_bytes` stays the FULL stored size either way, so the
    Size sort and the reader subtitle stay truthful.
    """
    ws = _workspace(db_session)
    ticket = _ticket(db_session, ws.id)
    small = json.dumps({"status": "pass", "stage_key": "gate", "confidence": 0.9})
    big = json.dumps({"status": "failed", "stage_key": "done", "output": _RED_SUITE_OUTPUT})
    db_session.add(Artifact(ticket_id=ticket.id, kind="context", title="small", content_json=small))
    db_session.commit()
    db_session.add(
        Artifact(
            ticket_id=ticket.id,
            kind=ArtifactKind.LAND_VERIFICATION,
            title="big",
            content_json=big,
        )
    )
    db_session.commit()

    body = list_ticket_artifacts(db_session, ticket.id)
    rows = _by_title(body)

    assert set(body) == {"items", "total"}, "no new top-level key on the feed envelope"
    assert "content_truncated" not in rows["small"]
    assert "content_shown_bytes" not in rows["small"]
    assert rows["small"]["content_bytes"] == len(small.encode("utf-8"))

    assert rows["big"]["content_truncated"] is True
    assert rows["big"]["content_shown_bytes"] == _re_encoded_bytes(rows["big"]["content"])
    assert rows["big"]["content_shown_bytes"] <= ARTIFACT_FEED_CONTENT_LIMIT
    assert rows["big"]["content_bytes"] == len(big.encode("utf-8"))
    assert rows["big"]["content_bytes"] > rows["big"]["content_shown_bytes"]


# --- adversarial cases for the bound on the feed (test-break stage) --------
#
# The tests above bound one row at a time, shaped the way the receipt is
# shaped. These are the ways the feed can be wrong with all of them passing:
# the bound applied to one row and not the next, the decision taken on the
# stored size rather than the returned size, an answer that changes between two
# polls of the same row, and content already in the database that makes the
# re-encoding raise where nothing re-encoded before.


def _add(session: Session, ticket_id: str, title: str, raw: str, kind: str = "context") -> None:
    session.add(Artifact(ticket_id=ticket_id, kind=kind, title=title, content_json=raw))
    session.commit()


def test_every_oversized_row_is_bounded_and_the_small_ones_are_untouched(db_session: Session):
    """AC1 is a property of the response, not of its first row.

    The cost this ticket exists for is the whole feed: 200 rows at 4.1 MB a poll
    on the worst ticket. An implementation that bounds `items[0]`, or bounds
    until it has saved enough, still serves megabytes every two seconds.
    """
    ws = _workspace(db_session)
    ticket = _ticket(db_session, ws.id)
    small = json.dumps({"status": "pass", "stage_key": "gate", "n": 1})
    for i in range(3):
        _add(
            db_session,
            ticket.id,
            f"big-{i}",
            json.dumps({"status": "failed", "output": "z" * 400_000}),
        )
        _add(db_session, ticket.id, f"small-{i}", small)

    body = list_ticket_artifacts(db_session, ticket.id)
    rows = _by_title(body)

    assert body["total"] == 6
    for item in body["items"]:
        assert _re_encoded_bytes(item["content"]) <= ARTIFACT_FEED_CONTENT_LIMIT, item["title"]
    for i in range(3):
        assert rows[f"big-{i}"]["content_truncated"] is True
        assert "content_truncated" not in rows[f"small-{i}"]
        assert rows[f"small-{i}"]["content"] == {"status": "pass", "stage_key": "gate", "n": 1}


def test_a_row_over_the_limit_only_as_stored_text_is_not_shortened(db_session: Session):
    """The bound is on what the response carries, not on what the row cost to store.

    Agents attach with `json.dumps(..., indent=2)`, so whitespace alone can put
    a row's stored text over 16 KiB while its re-encoded content is well under.
    Deciding on `content_bytes` marks that row partial and sends the operator to
    the reader for a rest that does not exist — AC4's "marked only where it
    happened", read from the other side.
    """
    ws = _workspace(db_session)
    ticket = _ticket(db_session, ws.id)
    payload = {"status": "pass", "stage_key": "plan", "items": [{"n": i} for i in range(700)]}
    raw = json.dumps(payload, indent=4)
    assert len(raw.encode("utf-8")) > ARTIFACT_FEED_CONTENT_LIMIT, "oversized as stored"
    assert _re_encoded_bytes(payload) <= ARTIFACT_FEED_CONTENT_LIMIT, "within it re-encoded"
    _add(db_session, ticket.id, "pretty", raw)

    row = _only(list_ticket_artifacts(db_session, ticket.id))

    assert row["content"] == payload
    assert "content_truncated" not in row
    assert "content_shown_bytes" not in row
    assert row["content_bytes"] == len(raw.encode("utf-8"))


def test_two_polls_of_the_same_feed_agree(db_session: Session):
    """The tab polls every 2 s; nothing about the bound may depend on the call.

    A row whose shortened content differs between polls reflows the table under
    the operator's cursor and makes `content_shown_bytes` a number that was
    true once.
    """
    ws = _workspace(db_session)
    ticket = _ticket(db_session, ws.id)
    _add(
        db_session,
        ticket.id,
        "wide",
        json.dumps(
            {
                "status": "failed",
                "stage_key": "done",
                "alpha": "A" * 40_000,
                "omega": "O" * 40_000,
                **{f"f{i}": "p" * 2000 for i in range(6)},
            }
        ),
    )

    first = list_ticket_artifacts(db_session, ticket.id)
    second = list_ticket_artifacts(db_session, ticket.id)

    assert first == second


def test_an_oversized_row_that_is_not_an_object_is_still_bounded(db_session: Session):
    """A stored array or a bare string is content too, and the feed carries both.

    `content_bytes` stays the full stored size whatever the shape, so Size still
    sorts and the reader's subtitle still says how much there is to read.
    """
    ws = _workspace(db_session)
    ticket = _ticket(db_session, ws.id)
    array = json.dumps([{"n": i, "pad": "p" * 400} for i in range(200)])
    string = json.dumps("S" * 200_000)
    _add(db_session, ticket.id, "array", array)
    _add(db_session, ticket.id, "string", string)

    rows = _by_title(list_ticket_artifacts(db_session, ticket.id))

    for title, raw in (("array", array), ("string", string)):
        assert _re_encoded_bytes(rows[title]["content"]) <= ARTIFACT_FEED_CONTENT_LIMIT
        assert rows[title]["content_truncated"] is True
        assert rows[title]["content_bytes"] == len(raw.encode("utf-8"))
        assert rows[title]["content_shown_bytes"] == _re_encoded_bytes(rows[title]["content"])


def test_an_empty_row_is_not_reported_as_shortened(db_session: Session):
    """A row with no content stored at all is `{}`, and nothing happened to it.

    "it is empty" and "the rest is elsewhere" are the two answers that must not
    collapse into one: a `content_truncated` here sends the operator to the
    reader to fetch nothing.
    """
    ws = _workspace(db_session)
    ticket = _ticket(db_session, ws.id)
    _add(db_session, ticket.id, "nothing", "")

    row = _only(list_ticket_artifacts(db_session, ticket.id))

    assert row["content"] == {}
    assert "content_truncated" not in row
    assert "content_shown_bytes" not in row


def test_an_oversized_row_holding_a_lone_surrogate_still_serves(client, db_session: Session):
    """A row already in the database must not become a 500 on the feed.

    `json.dumps` escapes a lone surrogate on the way in, so `"\\ud800"` stores
    cleanly and parses back as a character UTF-8 cannot encode. Nothing
    re-encoded a feed row before this change; measuring one in UTF-8 bytes is
    the first thing that can raise on it, and the feed is polled every 2 s.
    """
    ws = _workspace(db_session)
    ticket = _ticket(db_session, ws.id)
    _add(
        db_session,
        ticket.id,
        "surrogate",
        json.dumps({"status": "failed", "stage_key": "done", "output": "\ud800" + "x" * 400_000}),
    )

    resp = client.get(f"/api/tickets/{ticket.id}/artifacts")

    assert resp.status_code == 200, resp.text
    row = _only(resp.json())
    assert row["content"]["status"] == "failed"
    assert row["content"]["stage_key"] == "done"
