import shutil
from pathlib import Path

from loregarden.skills.registry import get_skill


def _write_skill(root: Path, name: str, body: str) -> Path:
    skill_dir = root / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(body, encoding="utf-8")
    return skill_dir


def test_rewriting_skill_file_is_visible_without_restart(tmp_path):
    agent_context = tmp_path / "agent_context"
    skill_dir = _write_skill(agent_context, "volatile", "short body")

    assert get_skill("volatile", agent_context_dir=agent_context) == "short body"
    (skill_dir / "SKILL.md").write_text("a much longer replacement body", encoding="utf-8")

    assert (
        get_skill("volatile", agent_context_dir=agent_context) == "a much longer replacement body"
    )


def test_added_skill_directory_is_visible_after_first_miss(tmp_path):
    agent_context = tmp_path / "agent_context"

    assert get_skill("late-arrival", agent_context_dir=agent_context) is None
    _write_skill(agent_context, "late-arrival", "now present")

    assert get_skill("late-arrival", agent_context_dir=agent_context) == "now present"


def test_removed_skill_directory_resolves_to_none_after_first_hit(tmp_path):
    agent_context = tmp_path / "agent_context"
    skill_dir = _write_skill(agent_context, "removed", "present")

    assert get_skill("removed", agent_context_dir=agent_context) == "present"
    shutil.rmtree(skill_dir)

    assert get_skill("removed", agent_context_dir=agent_context) is None
