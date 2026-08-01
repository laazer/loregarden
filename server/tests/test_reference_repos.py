import shutil
import subprocess
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from loregarden.models.domain import TicketStudioSession, Workspace
from loregarden.services import reference_repo_service
from loregarden.services.reference_repo_service import (
    ParsedRepoUrl,
    ReferenceRepoError,
    clone_or_refresh,
    clone_path_for,
    is_cloned,
    parse_repo_url,
)
from loregarden.services.ticket_studio_service import (
    _assert_adapter_can_read_extra_dirs,
    build_studio_prompt,
)
from sqlmodel import select

SURVEY_STUB = """Here is what is worth taking.

```json
{
  "summary": "The reference repo's learning loop maps onto our memory layer.",
  "clarifying_questions": [],
  "findings": [
    {
      "ref": "find-1",
      "title": "Skill creation loop",
      "repo_slug": "github.com/nousresearch/hermes-agent",
      "source_paths": ["skills/", "agent/loop.py"],
      "what_it_gives": "Agents write their own reusable skills after a run",
      "fit": "Sits next to our learnings service",
      "risks": "Depends on their provider abstraction",
      "verdict": "adapt",
      "effort": "M"
    },
    {
      "ref": "find-2",
      "title": "Telegram gateway",
      "repo_slug": "github.com/nousresearch/hermes-agent",
      "source_paths": ["gateway/telegram.py"],
      "what_it_gives": "Chat over Telegram",
      "fit": "No local-first use for it",
      "risks": "",
      "verdict": "skip",
      "effort": "L"
    }
  ]
}
```
"""


def _make_origin(tmp_path):
    """A real local repo to clone from, so the git plumbing is actually exercised."""
    origin = tmp_path / "origin"
    origin.mkdir()

    def git(*args):
        subprocess.run(["git", *args], cwd=origin, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "Test")
    (origin / "README.md").write_text("# reference\n", encoding="utf-8")
    (origin / "skills").mkdir()
    (origin / "skills" / "loop.py").write_text("# skill loop\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "init")
    return origin


@pytest.fixture(name="cache_dir")
def cache_dir_fixture(tmp_path, monkeypatch):
    root = tmp_path / "reference-cache"
    monkeypatch.setattr(
        reference_repo_service.settings, "reference_repo_cache_dir", str(root), raising=False
    )
    return root


def test_parse_repo_url_accepts_https_and_ssh():
    https = parse_repo_url("https://github.com/nousresearch/hermes-agent")
    assert (https.host, https.owner, https.name) == ("github.com", "nousresearch", "hermes-agent")
    assert https.slug == "github.com/nousresearch/hermes-agent"

    suffixed = parse_repo_url("https://github.com/nousresearch/hermes-agent.git/")
    assert suffixed.name == "hermes-agent"

    ssh = parse_repo_url("git@github.com:nousresearch/hermes-agent.git")
    assert ssh.slug == "github.com/nousresearch/hermes-agent"


def test_parse_repo_url_collapses_subgroups():
    parsed = parse_repo_url("https://gitlab.com/group/subgroup/project")
    assert parsed.owner == "group-subgroup"
    assert parsed.name == "project"


@pytest.mark.parametrize(
    "url",
    [
        "",
        "not-a-url",
        "file:///etc/passwd",
        "https://github.com/onlyowner",
        "https://user:token@github.com/owner/repo",
        "https://github.com/owner/../../etc",
        "--upload-pack=touch /tmp/pwned",
    ],
)
def test_parse_repo_url_rejects_unsafe_input(url):
    with pytest.raises(ReferenceRepoError):
        parse_repo_url(url)


def test_clone_path_stays_inside_the_cache(cache_dir):
    path = clone_path_for(parse_repo_url("https://github.com/owner/repo"))
    assert str(path).startswith(str(cache_dir.resolve()))


def test_clone_then_refresh_tracks_the_origin(tmp_path, cache_dir):
    origin = _make_origin(tmp_path)
    parsed = ParsedRepoUrl(url=f"file://{origin}", host="local", owner="o", name="reference")

    path, branch, head = clone_or_refresh(parsed)
    assert is_cloned(path)
    assert (path / "skills" / "loop.py").exists()
    assert branch == "main"

    (origin / "skills" / "new.py").write_text("# new\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=origin, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "second"], cwd=origin, check=True, capture_output=True)

    path, _, head_after = clone_or_refresh(parsed)
    assert head_after != head
    assert (path / "skills" / "new.py").exists()


def _add_repo(client: TestClient, tmp_path, cache_dir) -> dict:
    """Register a reference repo, cloning from a local origin instead of the network."""
    origin = _make_origin(tmp_path)
    real_parse = parse_repo_url

    def fake_parse(url: str):
        parsed = real_parse(url)
        return ParsedRepoUrl(
            url=f"file://{origin}", host=parsed.host, owner=parsed.owner, name=parsed.name
        )

    with mock.patch.object(reference_repo_service, "parse_repo_url", side_effect=fake_parse):
        response = client.post(
            "/api/reference-repos",
            json={
                "workspace_slug": "loregarden",
                "url": "https://github.com/nousresearch/hermes-agent",
                "notes": "self-improving agent platform",
            },
        )
    assert response.status_code == 200, response.text
    return response.json()


def test_reference_repo_crud(client: TestClient, tmp_path, cache_dir):
    repo = _add_repo(client, tmp_path, cache_dir)
    assert repo["slug"] == "github.com/nousresearch/hermes-agent"
    assert repo["cloned"] is True
    assert repo["head_sha"]

    listed = client.get("/api/reference-repos?workspace=loregarden").json()
    assert [item["id"] for item in listed] == [repo["id"]]

    deleted = client.delete(f"/api/reference-repos/{repo['id']}?remove_clone=true")
    assert deleted.status_code == 200
    assert client.get("/api/reference-repos?workspace=loregarden").json() == []
    assert not is_cloned(repo["local_path"])


def test_reference_repo_rejects_a_bad_url(client: TestClient):
    response = client.post(
        "/api/reference-repos",
        json={"workspace_slug": "loregarden", "url": "file:///etc"},
    )
    assert response.status_code == 400


def test_survey_then_scope_uses_selected_findings(
    client: TestClient, tmp_path, cache_dir, monkeypatch
):
    repo = _add_repo(client, tmp_path, cache_dir)
    create = client.post(
        "/api/ticket-studio/sessions",
        json={
            "workspace_slug": "loregarden",
            "title": "Adopt a learning loop",
            "brief": "Take what is useful from the reference agent platform.",
            "reference_repo_ids": [repo["id"]],
        },
    )
    assert create.status_code == 200, create.text
    session_id = create.json()["id"]
    assert [item["id"] for item in create.json()["reference_repos"]] == [repo["id"]]

    monkeypatch.setenv("LOREGARDEN_TICKET_STUDIO_STUB_RESPONSE", SURVEY_STUB)
    survey = client.post(f"/api/ticket-studio/sessions/{session_id}/survey")
    assert survey.status_code == 200, survey.text
    findings = survey.json()["survey"]
    assert [f["ref"] for f in findings] == ["find-1", "find-2"]
    # A "skip" verdict arrives unchecked so the operator prunes rather than re-picks.
    assert [f["selected"] for f in findings] == [True, False]

    findings[0]["selected"] = False
    findings[1]["selected"] = True
    saved = client.patch(
        f"/api/ticket-studio/sessions/{session_id}/survey", json={"findings": findings}
    )
    assert saved.status_code == 200, saved.text
    assert [f["selected"] for f in saved.json()["survey"]] == [False, True]


def test_prompt_exposes_the_clone_path_and_guards_the_adapter(
    client: TestClient, db_session, tmp_path, cache_dir, monkeypatch
):
    repo = _add_repo(client, tmp_path, cache_dir)
    create = client.post(
        "/api/ticket-studio/sessions",
        json={
            "workspace_slug": "loregarden",
            "title": "Adopt a learning loop",
            "brief": "Take what is useful.",
            "reference_repo_ids": [repo["id"]],
        },
    )
    row = db_session.get(TicketStudioSession, create.json()["id"])
    workspace = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()

    prompt = build_studio_prompt(row, workspace, [], "go", session=db_session, mode="survey")
    assert "## Reference repos" in prompt
    assert repo["local_path"] in prompt
    assert "self-improving agent platform" in prompt

    # A clone the operator deleted from disk must not be advertised as readable.
    shutil.rmtree(repo["local_path"])
    assert "## Reference repos" not in build_studio_prompt(
        row, workspace, [], "go", session=db_session, mode="survey"
    )

    # The seeded workspace runs the local adapter, which cannot take --add-dir.
    with pytest.raises(ValueError, match="claude CLI adapter"):
        _assert_adapter_can_read_extra_dirs(workspace)


def test_survey_requires_an_attached_repo(client: TestClient):
    create = client.post(
        "/api/ticket-studio/sessions",
        json={"workspace_slug": "loregarden", "title": "No repos", "brief": "Nothing attached."},
    )
    session_id = create.json()["id"]
    response = client.post(f"/api/ticket-studio/sessions/{session_id}/survey")
    assert response.status_code == 400
    assert "reference repo" in response.json()["detail"]


def test_attaching_a_repo_from_another_workspace_is_rejected(
    client: TestClient, tmp_path, cache_dir
):
    repo = _add_repo(client, tmp_path, cache_dir)
    other = client.post(
        "/api/workspaces",
        json={"slug": "other-project", "name": "Other", "repo_path": str(tmp_path)},
    )
    assert other.status_code in (200, 201), other.text

    create = client.post(
        "/api/ticket-studio/sessions",
        json={"workspace_slug": "other-project", "title": "Cross", "brief": "x"},
    )
    session_id = create.json()["id"]
    response = client.patch(
        f"/api/ticket-studio/sessions/{session_id}/reference-repos",
        json={"reference_repo_ids": [repo["id"]]},
    )
    assert response.status_code == 400
