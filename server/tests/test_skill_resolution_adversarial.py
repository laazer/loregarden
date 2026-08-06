from pathlib import Path

from loregarden.skills.registry import get_skill


def _write_skill(root: Path, name: str, body: str) -> None:
    skill_dir = root / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(body, encoding="utf-8")


def test_hyphen_and_underscore_skill_directories_resolve_exactly(tmp_path):
    agent_context = tmp_path / "agent_context"
    _write_skill(agent_context, "a-b", "hyphen body")
    _write_skill(agent_context, "a_b", "underscore body")

    assert get_skill("a-b", agent_context_dir=agent_context) == "hyphen body"
    assert get_skill("a_b", agent_context_dir=agent_context) == "underscore body"


def test_hyphen_name_does_not_resolve_underscore_directory(tmp_path):
    agent_context = tmp_path / "agent_context"
    _write_skill(agent_context, "a_b", "underscore body")

    assert get_skill("a-b", agent_context_dir=agent_context) is None


def test_underscore_name_does_not_resolve_hyphen_directory(tmp_path):
    agent_context = tmp_path / "agent_context"
    _write_skill(agent_context, "a-b", "hyphen body")

    assert get_skill("a_b", agent_context_dir=agent_context) is None


def test_untrusted_skill_names_never_construct_paths(tmp_path):
    agent_context = tmp_path / "agent_context"
    outside = tmp_path / "outside"
    _write_skill(outside, "secret", "do not read")
    _write_skill(agent_context, "REFACTOR", "uppercase is distinct")

    assert get_skill("../outside/skills/secret", agent_context_dir=agent_context) is None
    assert get_skill(str(outside / "skills" / "secret"), agent_context_dir=agent_context) is None
    assert get_skill("refactor", agent_context_dir=agent_context) is None
