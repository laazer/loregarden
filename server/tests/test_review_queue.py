"""The review page's server half: queue grouping, review kits, captures, try-it."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from loregarden.models.domain import Approval, ApprovalKind, ApprovalStatus, ArtifactKind
from loregarden.services.review_kit import ReviewKitError, ingest_review_kit
from loregarden.services.review_try import TryRequest, send_try_request
from loregarden.testing.factories import make_artifact, make_ticket, make_workspace
from pydantic import ValidationError
from sqlmodel import Session

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _gate(session: Session, ticket, workspace, *, status=ApprovalStatus.PENDING, kind=None):
    approval = Approval(
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        kind=kind or ApprovalKind.WORKFLOW_GATE,
        title="Resolve Playtest exit actions",
        stage_key="playtest",
        status=status,
    )
    session.add(approval)
    session.commit()
    return approval


@pytest.fixture
def review_tree():
    """Two tickets landed on one integration branch, one unlanded, plus noise."""
    from loregarden.db.session import engine

    with Session(engine) as session:
        workspace = make_workspace(session, slug="review-ws")
        other = make_workspace(session, slug="review-other")
        landed = []
        for n in (1, 2):
            ticket = make_ticket(
                session,
                workspace_id=workspace.id,
                external_id=f"rev-landed-{n}",
                acceptance_criteria=[f"criterion {n}"],
            )
            ticket.landed_sha = f"{n:040d}"
            ticket.landed_branch = "integration/rev-root-1"
            session.add(ticket)
            landed.append(ticket)
        loose = make_ticket(session, workspace_id=workspace.id, external_id="rev-loose")
        loose.branch = "loregarden/rev-loose"
        session.add(loose)
        session.commit()
        for ticket in [*landed, loose]:
            _gate(session, ticket, workspace)
        # Not in the queue: resolved, not a gate, other workspace's handled by filter.
        _gate(session, landed[0], workspace, status=ApprovalStatus.APPROVED)
        _gate(session, loose, workspace, kind=ApprovalKind.CLI_PERMISSION)
        elsewhere = make_ticket(session, workspace_id=other.id, external_id="rev-elsewhere")
        _gate(session, elsewhere, other)
        return [t.id for t in landed], loose.id


def test_queue_groups_landed_tickets_into_one_combined_build(client: TestClient, review_tree):
    landed_ids, loose_id = review_tree

    body = client.get("/api/review/queue?workspace_slug=review-ws").json()

    assert body["total"] == 3
    combined, single = body["builds"]
    assert combined["ref"] == "integration/rev-root-1"
    assert combined["combined"] is True
    assert sorted(item["ticket"]["id"] for item in combined["items"]) == sorted(landed_ids)
    assert (single["ref"], single["combined"]) == ("loregarden/rev-loose", False)
    assert [item["ticket"]["id"] for item in single["items"]] == [loose_id]
    first = combined["items"][0]
    assert first["approval"]["kind"] == "workflow_gate"
    assert first["ticket"]["acceptance_criteria"] in (["criterion 1"], ["criterion 2"])


def test_queue_without_a_filter_spans_workspaces(client: TestClient, review_tree):
    body = client.get("/api/review/queue").json()

    slugs = {build["workspace_slug"] for build in body["builds"]}
    assert {"review-ws", "review-other"} <= slugs


def test_ingest_stores_screenshot_and_replaces_its_path(tmp_path: Path):
    shot = tmp_path / "shot.png"
    shot.write_bytes(PNG)

    content = ingest_review_kit(
        "0f0f0f0f-aaaa",
        {
            "summary": "kept",
            "review": {
                "screenshots": [{"path": str(shot), "caption": "marker"}],
                "links": [{"url": "http://127.0.0.1:5173/x", "label": "page"}],
                "requests": [{"name": "list", "path": "/api/x", "expect_status": 200}],
            },
        },
    )

    assert content["summary"] == "kept"
    (screenshot,) = content["review"]["screenshots"]
    assert screenshot["caption"] == "marker"
    assert screenshot["capture_id"].endswith(".png")
    assert "path" not in screenshot
    assert content["review"]["requests"][0]["method"] == "GET"


def test_a_link_may_be_a_path_on_whichever_instance_runs_the_build():
    content = ingest_review_kit(
        "0f0f0f0f-aaaa",
        {"review": {"links": [{"path": "/work-items?filter=mine", "label": "Work items"}]}},
    )

    assert content["review"]["links"] == [
        {"url": "", "path": "/work-items?filter=mine", "label": "Work items"}
    ]


def test_content_without_a_review_block_is_untouched():
    content = {"status": 201}
    assert ingest_review_kit("0f0f0f0f-aaaa", content) is content


@pytest.mark.parametrize(
    ("review", "message"),
    [
        ({"screenshots": [{"path": "/no/such/file.png"}]}, "does not exist"),
        ({"links": [{"url": "file:///etc/passwd"}]}, r"http\(s\) URL"),
        ({"requests": [{"name": "x", "path": "http://evil.example/"}]}, "single '/'"),
        ({"requests": [{"name": "x", "path": "//evil.example/"}]}, "single '/'"),
        ({"links": [{"path": "//evil.example/"}]}, "single '/'"),
        ({"links": [{"path": "http://evil.example/x"}]}, "single '/'"),
        ({"links": [{"label": "neither"}]}, "exactly one"),
        ({"links": [{"path": "/x", "url": "http://127.0.0.1:5173/x"}]}, "exactly one"),
        ({"bogus": []}, "malformed"),
    ],
)
def test_ingest_refuses_a_bad_kit_whole(review: dict, message: str):
    with pytest.raises(ReviewKitError, match=message):
        ingest_review_kit("0f0f0f0f-aaaa", {"review": review})


def test_ingest_refuses_a_file_that_is_not_an_image(tmp_path: Path):
    fake = tmp_path / "shot.png"
    fake.write_text("not an image")

    with pytest.raises(ReviewKitError, match="not a PNG"):
        ingest_review_kit("0f0f0f0f-aaaa", {"review": {"screenshots": [{"path": str(fake)}]}})


def test_attached_kit_reaches_the_queue_and_its_capture_is_served(
    client: TestClient, review_tree, tmp_path: Path
):
    from loregarden.db.session import engine

    landed_ids, _ = review_tree
    shot = tmp_path / "shot.png"
    shot.write_bytes(PNG)
    content = ingest_review_kit(
        landed_ids[0], {"review": {"screenshots": [{"path": str(shot), "caption": "c"}]}}
    )
    with Session(engine) as session:
        artifact = make_artifact(
            session,
            ticket_id=landed_ids[0],
            kind=ArtifactKind.EVIDENCE,
            title="shots",
            content=content,
        )
        assert json.loads(artifact.content_json)["review"]

    body = client.get("/api/review/queue?workspace_slug=review-ws").json()
    items = {item["ticket"]["id"]: item for b in body["builds"] for item in b["items"]}
    (screenshot,) = items[landed_ids[0]]["kit"]["screenshots"]

    served = client.get(f"/api/review/captures/{landed_ids[0]}/{screenshot['capture_id']}")
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/png"
    assert served.content == PNG


@pytest.mark.parametrize("capture_id", ["..%2F..%2Fetc%2Fpasswd", "x.png", "a" * 32 + ".exe"])
def test_capture_endpoint_refuses_anything_but_a_stored_id(client: TestClient, capture_id: str):
    response = client.get(f"/api/review/captures/0f0f0f0f-aaaa/{capture_id}")
    assert response.status_code == 404


@pytest.mark.parametrize(
    "base_url",
    [
        "http://example.com",
        "http://10.0.0.5:8000",
        "ftp://127.0.0.1",
        "http://127.0.0.1:8000/api",
        "http://user@127.0.0.1:8000",
    ],
)
def test_try_refuses_anything_but_a_loopback_origin(client: TestClient, base_url: str):
    response = client.post("/api/review/try", json={"base_url": base_url, "path": "/x"})
    assert response.status_code == 422


def test_an_operation_route_refuses_a_bad_argument_with_a_422_not_a_500(client: TestClient):
    """A validator's ValueError rides in the error's context, which JSON cannot encode;
    run_rest_operation used to answer it with a bare 500 Internal Server Error."""
    response = client.post(
        "/api/review/capture/loregarden",
        json={"ref": "main", "ticket_id": "t", "paths": ["//evil.example/"]},
    )

    assert response.status_code == 422
    assert "single '/'" in response.json()["detail"][0]["msg"]


def test_try_sends_the_request_and_reports_the_response():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={"id": 7})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        result = send_try_request(
            TryRequest(
                base_url="http://127.0.0.1:8000/",
                method="POST",
                path="/api/items",
                body='{"a": 1}',
            ),
            client=http,
        )

    assert result.ok and result.status == 201
    assert json.loads(result.body) == {"id": 7}
    assert str(seen[0].url) == "http://127.0.0.1:8000/api/items"
    assert seen[0].content == b'{"a": 1}'


def test_try_reports_a_refused_connection_as_a_result():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        result = send_try_request(TryRequest(base_url="http://127.0.0.1:9", path="/"), client=http)

    assert not result.ok
    assert result.status is None
    assert "ConnectError" in result.error


def test_try_request_rejects_an_unknown_method():
    with pytest.raises(ValidationError):
        TryRequest(base_url="http://127.0.0.1:1", path="/", method="TRACE")


def _git(cwd: Path, *args: str) -> str:
    from loregarden.services.git_subprocess import run_git

    return run_git(list(args), cwd=str(cwd), capture_output=True, text=True, check=True).stdout


def test_landed_scenes_come_from_the_landing_merge_even_once_main_holds_it(tmp_path: Path):
    """The branch diff goes empty when the integration branch reaches main; the
    landing merge still names exactly the ticket's scenes, not a sibling's."""
    from loregarden.db.session import engine
    from loregarden.services.gate_checklist import resolve_review_scenes

    repo = tmp_path / "game"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "base.tscn").write_text("base")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "checkout", "-q", "-b", "integration/root")
    _git(repo, "checkout", "-q", "-b", "loregarden/sibling")
    (repo / "sibling.tscn").write_text("s")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "sibling")
    _git(repo, "checkout", "-q", "integration/root")
    _git(repo, "merge", "-q", "--no-ff", "-m", "Land sibling", "loregarden/sibling")
    _git(repo, "checkout", "-q", "-b", "loregarden/mine")
    (repo / "mine.tscn").write_text("m")
    (repo / "notes.txt").write_text("n")
    (repo / "base.tscn").unlink()  # a scene the ticket deletes cannot be opened
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "mine")
    _git(repo, "checkout", "-q", "integration/root")
    _git(repo, "merge", "-q", "--no-ff", "-m", "Land mine", "loregarden/mine")
    landed = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--ff-only", "integration/root")

    with Session(engine) as session:
        workspace = make_workspace(session, slug="scenes-ws", repo_path=str(repo))
        ticket = make_ticket(session, workspace_id=workspace.id, external_id="scenes-mine")
        ticket.branch = "loregarden/mine"
        ticket.landed_sha = landed
        ticket.landed_branch = "integration/root"
        session.add(ticket)
        session.commit()

        assert resolve_review_scenes(session, ticket) == ["mine.tscn"]
