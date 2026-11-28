"""The single-artifact read path: `GET /api/tickets/{id}/artifacts/{artifact_id}`.

lg-milestone-that-1424 AC5. This route did not exist when the ticket was filed —
`server/loregarden/api/tickets.py` carried exactly one artifact GET, the feed —
so bounding the feed without it would have left the operator looking at a cut
log with no control reaching the rest.

It is the other half of the bound: the feed row carries the head of the log so
the reader opens on something, and this route fills in the whole of it. So the
test that matters most here is the byte-for-byte one — lg-milestone-that-1419
AC9 requires the FULL output on the receipt, and that stays true only if some
read path hands it back whole.
"""

from __future__ import annotations

import json

from loregarden.models.domain import (
    Artifact,
    ArtifactKind,
    Ticket,
    TicketState,
    Workspace,
)
from loregarden.services.artifact_service import get_ticket_artifact
from sqlmodel import Session, select

#: >= 1 MiB, the size AC1 and AC5 both pin: a conftest-level break or a bad
#: refactor putting hundreds of tests red, which is the case the bound exists
#: for and therefore the case this route has to be able to return.
RED_SUITE_OUTPUT = "FAILED server/tests/test_thing.py::case — AssertionError\n" * 20_000


def _workspace(session: Session) -> Workspace:
    ws = session.exec(select(Workspace)).first()
    if ws:
        return ws
    ws = Workspace(slug="ws-detail", name="Detail WS", repo_path="/tmp")
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


def _ticket(session: Session, workspace_id: str, external_id: str) -> Ticket:
    ticket = Ticket(
        external_id=external_id,
        title=f"Artifact detail {external_id}",
        state=TicketState.IN_PROGRESS,
        workspace_id=workspace_id,
        workflow_stage_key="done",
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def _receipt(session: Session, ticket_id: str, output: str) -> Artifact:
    """A row shaped like the land-verification receipt, with an unbounded output."""
    artifact = Artifact(
        ticket_id=ticket_id,
        kind=ArtifactKind.LAND_VERIFICATION,
        title="Land verification — failed — loregarden/lg-r-1",
        content_json=json.dumps(
            {
                "status": "failed",
                "stage_key": "done",
                "outcome": "failed",
                "branch": "loregarden/lg-r-1",
                "target": "integration/lg-ms",
                "candidate_sha": "a" * 40,
                "command": "uv run pytest -q",
                "exit_code": 1,
                "output": output,
            }
        ),
    )
    session.add(artifact)
    session.commit()
    session.refresh(artifact)
    return artifact


def test_the_whole_output_comes_back_byte_for_byte(client, db_session: Session):
    """AC5 — no `land_verification` row loses evidence to the feed's bound."""
    ws = _workspace(db_session)
    ticket = _ticket(db_session, ws.id, "det-whole")
    assert len(RED_SUITE_OUTPUT.encode("utf-8")) >= 1_048_576
    artifact = _receipt(db_session, ticket.id, RED_SUITE_OUTPUT)

    resp = client.get(f"/api/tickets/{ticket.id}/artifacts/{artifact.id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["content"]["output"] == RED_SUITE_OUTPUT
    assert body["content"]["exit_code"] == 1
    assert body["content"]["status"] == "failed"


def test_the_detail_row_is_never_marked_as_shortened(client, db_session: Session):
    """AC5 — `content_truncated`/`content_shown_bytes` are absent: this IS the whole of it.

    A `content_truncated: false` here would be the client's cue to keep saying
    "showing the first N", which is the one wrong answer about a complete read.
    """
    ws = _workspace(db_session)
    ticket = _ticket(db_session, ws.id, "det-marks")
    artifact = _receipt(db_session, ticket.id, RED_SUITE_OUTPUT)

    body = client.get(f"/api/tickets/{ticket.id}/artifacts/{artifact.id}").json()

    assert "content_truncated" not in body
    assert "content_shown_bytes" not in body
    assert body["content_bytes"] == len(artifact.content_json.encode("utf-8"))


def test_the_detail_row_has_the_same_shape_as_a_feed_row(client, db_session: Session):
    """AC5 — same item shape, so the client's `TicketArtifactItem` covers both."""
    ws = _workspace(db_session)
    ticket = _ticket(db_session, ws.id, "det-shape")
    artifact = _receipt(db_session, ticket.id, "a short output\n")

    feed = client.get(f"/api/tickets/{ticket.id}/artifacts").json()["items"][0]
    detail = client.get(f"/api/tickets/{ticket.id}/artifacts/{artifact.id}").json()

    assert set(detail) == set(feed)
    assert detail == feed, "an under-limit row reads the same through either path"


def test_the_service_returns_the_whole_content(db_session: Session):
    """AC5 — the route is backed by `artifact_service.get_ticket_artifact`."""
    ws = _workspace(db_session)
    ticket = _ticket(db_session, ws.id, "det-service")
    artifact = _receipt(db_session, ticket.id, RED_SUITE_OUTPUT)

    item = get_ticket_artifact(db_session, ticket.id, artifact.id)

    assert item is not None
    assert item["id"] == artifact.id
    assert item["content"]["output"] == RED_SUITE_OUTPUT


def test_404_when_the_ticket_does_not_exist(client, db_session: Session):
    """AC5, refusal 1 — never a 200 with an empty body."""
    ws = _workspace(db_session)
    ticket = _ticket(db_session, ws.id, "det-no-ticket")
    artifact = _receipt(db_session, ticket.id, "out\n")

    assert client.get(f"/api/tickets/nope/artifacts/{artifact.id}").status_code == 404


def test_404_when_the_artifact_does_not_exist(client, db_session: Session):
    """AC5, refusal 2 — never a 200 with a partial body."""
    ws = _workspace(db_session)
    ticket = _ticket(db_session, ws.id, "det-no-artifact")

    assert client.get(f"/api/tickets/{ticket.id}/artifacts/nope").status_code == 404


def test_404_when_the_artifact_belongs_to_another_ticket(client, db_session: Session):
    """AC5, refusal 3 — and never another ticket's artifact.

    The id alone is enough to fetch a row, so without this check the ticket in
    the path is decoration and any artifact id reads any ticket's evidence.
    """
    ws = _workspace(db_session)
    mine = _ticket(db_session, ws.id, "det-mine")
    theirs = _ticket(db_session, ws.id, "det-theirs")
    artifact = _receipt(db_session, theirs.id, "their output\n")

    resp = client.get(f"/api/tickets/{mine.id}/artifacts/{artifact.id}")

    assert resp.status_code == 404
    assert "their output" not in resp.text


# --- adversarial cases for the read path (test-break stage) ----------------
#
# The tests above prove the whole output comes back and the three refusals
# refuse. These are the ways the two halves can each pass their own tests and
# still not fit together: a prefix the whole output does not continue, a shape
# the route only handles for objects, and an id that reaches the handler as
# something other than a row that is missing.


def test_the_feed_prefix_is_the_leading_slice_of_what_the_route_returns(
    client, db_session: Session
):
    """The reader opens on the feed's prefix and grows into this response.

    If the prefix is not a true leading slice of the whole — a different
    truncation, a re-serialization, an ellipsis appended — then the fill-in
    makes the pane jump, and the first screen the operator read was not the
    first screen of the log. This is the one assertion neither half can make
    alone, and nothing else in the suite makes it.
    """
    ws = _workspace(db_session)
    ticket = _ticket(db_session, ws.id, "det-continuity")
    artifact = _receipt(db_session, ticket.id, RED_SUITE_OUTPUT)

    feed_row = client.get(f"/api/tickets/{ticket.id}/artifacts").json()["items"][0]
    whole = client.get(f"/api/tickets/{ticket.id}/artifacts/{artifact.id}").json()

    assert feed_row["content_truncated"] is True
    prefix = feed_row["content"]["output"]
    assert prefix, "the reader opens on the head of the log"
    assert whole["content"]["output"].startswith(prefix)
    assert len(whole["content"]["output"]) > len(prefix)
    assert whole["id"] == feed_row["id"]


def test_a_row_that_is_not_an_object_comes_back_whole(client, db_session: Session):
    """AC5 says "the WHOLE parsed content", whatever shape it parsed into.

    The feed replaces an oversized array with a `{"_raw": prefix}`; the route
    that exists to reach the rest must not do the same thing, or the rest is
    not reachable from anywhere.
    """
    ws = _workspace(db_session)
    ticket = _ticket(db_session, ws.id, "det-array")
    payload = [{"n": i, "pad": "p" * 400} for i in range(3000)]
    artifact = Artifact(
        ticket_id=ticket.id,
        kind=ArtifactKind.LOG,
        title="an oversized array",
        content_json=json.dumps(payload),
    )
    db_session.add(artifact)
    db_session.commit()
    db_session.refresh(artifact)

    body = client.get(f"/api/tickets/{ticket.id}/artifacts/{artifact.id}").json()

    assert body["content"] == payload
    assert "content_truncated" not in body


def test_unparseable_content_comes_back_whole_under_raw(client, db_session: Session):
    """The feed's decode fallback is `{"_raw": text}`; the route keeps all of it.

    A row an agent wrote with a broken serializer is exactly the row an operator
    opens to find out what happened, so a prefix of it is the wrong answer.
    """
    ws = _workspace(db_session)
    ticket = _ticket(db_session, ws.id, "det-raw")
    raw = "not json " * 20_000
    artifact = Artifact(
        ticket_id=ticket.id, kind=ArtifactKind.LOG, title="broken", content_json=raw
    )
    db_session.add(artifact)
    db_session.commit()
    db_session.refresh(artifact)

    body = client.get(f"/api/tickets/{ticket.id}/artifacts/{artifact.id}").json()

    assert body["content"] == {"_raw": raw}
    assert body["content_bytes"] == len(raw.encode("utf-8"))


def test_a_malformed_artifact_id_is_refused_and_not_an_error(client, db_session: Session):
    """Every id that is not a row on this ticket is a 404, not a 500.

    The ids reaching this handler come from a URL. A handler that assumes a
    well-formed id — or that interpolates one — answers these with a stack
    trace instead of a refusal, and "it broke" stops being distinguishable from
    "it is not here".
    """
    ws = _workspace(db_session)
    ticket = _ticket(db_session, ws.id, "det-malformed")

    # `..` is not in the list: httpx normalises it away before the request is
    # made, so it would test the client's path handling and not the handler's.
    for artifact_id in ("%20", "a" * 600, "' OR 1=1 --", "null", "0", "a%2Fb", "-1"):
        resp = client.get(f"/api/tickets/{ticket.id}/artifacts/{artifact_id}")
        assert resp.status_code == 404, f"{artifact_id!r} -> {resp.status_code}"


def test_the_refusal_does_not_depend_on_the_ticket_existing_first(client, db_session: Session):
    """Both halves of the path missing is still one refusal, with no body.

    Kills an ordering where a missing artifact on a missing ticket falls through
    to a 200, and an implementation that reads the artifact before checking the
    ticket owns it.
    """
    resp = client.get("/api/tickets/nope/artifacts/also-nope")

    assert resp.status_code == 404
    assert "content" not in resp.json()
