from pathlib import Path

from loregarden.config import settings
from loregarden.skills.registry import get_skill, list_skills, skill_search_dirs


def _write_skill(root: Path, name: str, body: str) -> None:
    skill_dir = root / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(body, encoding="utf-8")


def test_workspace_skill_overlay_does_not_shadow_database_skill(tmp_path, client):
    workspace_context = tmp_path / "workspace" / "agent_context"
    _write_skill(workspace_context, "plan", "workspace plan body")

    assert get_skill("plan", agent_context_dir=workspace_context) == get_skill("plan")
    assert get_skill("plan", agent_context_dir=workspace_context) != "workspace plan body"


def test_workspace_only_skill_resolves(tmp_path):
    workspace_context = tmp_path / "workspace" / "agent_context"
    _write_skill(workspace_context, "workspace-only", "workspace only body")

    assert get_skill("workspace-only", agent_context_dir=workspace_context) is None


def test_skill_search_dirs_are_resolved_and_ordered(tmp_path):
    workspace_context = tmp_path / "workspace" / "agent_context"

    # The default comes from settings, not from resolving a relative path against
    # the cwd — pytest run from server/ and from the repo root must agree.
    assert skill_search_dirs(workspace_context) == [
        (workspace_context / "skills").resolve(),
        (settings.agent_context_dir / "skills").resolve(),
    ]


def test_skill_search_dirs_dedupes_workspace_matching_default():
    assert skill_search_dirs(settings.agent_context_dir) == [
        (settings.agent_context_dir / "skills").resolve()
    ]


def test_skill_search_dirs_without_workspace_returns_default_only():
    assert skill_search_dirs() == [(settings.agent_context_dir / "skills").resolve()]


def test_list_skills_returns_seeded_database_slugs(client):
    assert list_skills() == [
        "absorb-adapt",
        "autopilot",
        "plan",
        "plan-risk",
        "plan-seams",
        "plan-simplest",
        "plan-synthesis",
        "refactor",
        "vulcan",
    ]
