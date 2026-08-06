from pathlib import Path

from loregarden.config import settings
from loregarden.skills.registry import SKILL_PROMPT_CAP, get_skill, list_skills, skill_search_dirs


def _write_skill(root: Path, name: str, body: str) -> None:
    skill_dir = root / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(body, encoding="utf-8")


def test_workspace_without_skills_falls_back_to_default(tmp_path):
    workspace_context = tmp_path / "workspace" / "agent_context"
    expected = (settings.agent_context_dir / "skills" / "plan" / "SKILL.md").read_text(
        encoding="utf-8"
    )[:SKILL_PROMPT_CAP]

    assert get_skill("plan", agent_context_dir=workspace_context) == expected


def test_workspace_skill_wins_over_default(tmp_path):
    workspace_context = tmp_path / "workspace" / "agent_context"
    _write_skill(workspace_context, "plan", "workspace plan body")

    assert get_skill("plan", agent_context_dir=workspace_context) == "workspace plan body"


def test_workspace_only_skill_resolves(tmp_path):
    workspace_context = tmp_path / "workspace" / "agent_context"
    _write_skill(workspace_context, "workspace-only", "workspace only body")

    assert get_skill("workspace-only", agent_context_dir=workspace_context) == "workspace only body"


def test_empty_workspace_skill_does_not_shadow_default(tmp_path):
    workspace_context = tmp_path / "workspace" / "agent_context"
    _write_skill(workspace_context, "plan", "  \n\t")
    expected = (settings.agent_context_dir / "skills" / "plan" / "SKILL.md").read_text(
        encoding="utf-8"
    )[:SKILL_PROMPT_CAP]

    assert get_skill("plan", agent_context_dir=workspace_context) == expected


def test_skill_search_dirs_are_resolved_ordered_and_deduped(tmp_path):
    workspace_context = tmp_path / "workspace" / "agent_context"

    assert skill_search_dirs(workspace_context) == [
        (workspace_context / "skills").resolve(),
        (settings.agent_context_dir / "skills").resolve(),
    ]
    assert skill_search_dirs(settings.agent_context_dir) == [
        (settings.agent_context_dir / "skills").resolve()
    ]


def test_list_skills_uses_literal_default_directory_names_only():
    assert list_skills() == [
        "autopilot",
        "plan",
        "plan-risk",
        "plan-seams",
        "plan-simplest",
        "plan-synthesis",
        "refactor",
    ]
