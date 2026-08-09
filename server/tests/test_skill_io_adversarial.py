from pathlib import Path

from loregarden.skills.registry import get_skill


def _write_skill(root: Path, name: str, body: str) -> Path:
    skill_dir = root / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(body, encoding="utf-8")
    return skill_dir


def test_rewriting_skill_file_is_ignored_after_database_seeding(tmp_path, client):
    agent_context = tmp_path / "agent_context"
    skill_dir = _write_skill(agent_context, "volatile", "short body")

    assert get_skill("volatile", agent_context_dir=agent_context) is None
    (skill_dir / "SKILL.md").write_text("a much longer replacement body", encoding="utf-8")

    assert get_skill("volatile", agent_context_dir=agent_context) is None


def test_added_workspace_skill_directory_is_not_visible_after_first_miss(tmp_path, client):
    agent_context = tmp_path / "agent_context"

    assert get_skill("late-arrival", agent_context_dir=agent_context) is None
    _write_skill(agent_context, "late-arrival", "now present")

    assert get_skill("late-arrival", agent_context_dir=agent_context) is None
