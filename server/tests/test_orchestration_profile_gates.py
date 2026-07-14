"""Writing a workspace's GatesConfig (enabled/commands/transition_script)
back to its orchestration profile YAML — the write path backing the Studio
"Gates" editor.

Orchestration profiles always resolve from loregarden's own repo tree
(settings.repo_root), never from the workspace's own repo_path — so every
test here redirects settings.repo_root to a throwaway tmp_path rather than
writing into the real agent_context/orchestration/ directory.
"""

import yaml
from fastapi.testclient import TestClient
from loregarden.config import settings
from loregarden.models.domain import Workspace
from loregarden.services.orchestration_profile import (
    GatesConfig,
    orchestration_dir,
    resolve_orchestration_profile,
    update_gates_config,
)
from sqlmodel import Session


def _workspace(slug="gates-write-test", repo_path=".") -> Workspace:
    return Workspace(slug=slug, name="Gates Write Test", repo_path=repo_path)


def test_writes_new_profile_file_when_none_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "repo_root", tmp_path)
    ws = _workspace()
    profile = update_gates_config(
        ws, GatesConfig(enabled=True, commands=["echo hi"], transition_script="ci/gate.py")
    )
    assert profile.gates.enabled is True
    assert profile.gates.commands == ["echo hi"]
    assert profile.gates.transition_script == "ci/gate.py"

    path = orchestration_dir() / f"{ws.slug}.yaml"
    assert path.is_file()
    raw = yaml.safe_load(path.read_text())
    assert raw["gates"] == {
        "enabled": True,
        "commands": ["echo hi"],
        "transition_script": "ci/gate.py",
    }


def test_preserves_other_fields_in_existing_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "repo_root", tmp_path)
    ws = _workspace()
    path = orchestration_dir() / f"{ws.slug}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "slug: gates-write-test\n"
        "name: Custom Name\n"
        "driver: builtin_autopilot\n"
        "workflow_template: blobert-tdd\n"
        "gates:\n"
        "  enabled: false\n",
        encoding="utf-8",
    )

    update_gates_config(ws, GatesConfig(enabled=True, commands=["true"], transition_script=""))

    raw = yaml.safe_load(path.read_text())
    assert raw["name"] == "Custom Name"
    assert raw["workflow_template"] == "blobert-tdd"
    assert raw["gates"] == {"enabled": True, "commands": ["true"], "transition_script": ""}

    profile = resolve_orchestration_profile(ws)
    assert profile.workflow_template == "blobert-tdd"
    assert profile.gates.enabled is True


def test_resolves_by_slug_regardless_of_workspace_repo_path(tmp_path, monkeypatch):
    """The bug this guards against: a workspace whose repo_path points at some
    other checkout (e.g. a workspace orchestrating an external repo) must
    still resolve its profile from loregarden's own repo tree, not fall back
    to a different workspace's profile because its own repo has no
    agent_context/orchestration/ directory."""
    monkeypatch.setattr(settings, "repo_root", tmp_path)
    root = orchestration_dir()
    root.mkdir(parents=True, exist_ok=True)
    (root / "external-project.yaml").write_text(
        "slug: external-project\nworkflow_template: blobert-tdd\ngates:\n  enabled: true\n",
        encoding="utf-8",
    )

    ws = _workspace(slug="external-project", repo_path="/some/other/checkout/entirely")
    profile = resolve_orchestration_profile(ws)
    assert profile.workflow_template == "blobert-tdd"
    assert profile.gates.enabled is True


def test_update_workspace_gates_endpoint(
    client: TestClient, db_session: Session, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "repo_root", tmp_path)
    ws = Workspace(slug="gates-api-test", name="Gates API Test", repo_path=".")
    db_session.add(ws)
    db_session.commit()

    res = client.put(
        "/api/orchestration/workspaces/gates-api-test/profile/gates",
        json={"enabled": True, "commands": ["lefthook run pre-commit"], "transition_script": ""},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["gates_enabled"] is True
    assert body["gates_commands"] == ["lefthook run pre-commit"]

    followup = client.get("/api/orchestration/workspaces/gates-api-test/profile")
    assert followup.json()["gates_commands"] == ["lefthook run pre-commit"]


def test_update_workspace_gates_endpoint_unknown_workspace(client: TestClient):
    res = client.put(
        "/api/orchestration/workspaces/does-not-exist/profile/gates",
        json={"enabled": True, "commands": [], "transition_script": ""},
    )
    assert res.status_code == 404
