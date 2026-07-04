from pathlib import Path

from loregarden.config import settings

_SKILL_CACHE: dict[str, str] | None = None
_SKILL_DIR_CACHE: dict[str, dict[str, str]] = {}


def _load_skills_from_dir(skills_dir: Path) -> dict[str, str]:
    skills: dict[str, str] = {}
    if skills_dir.is_dir():
        for skill_md in skills_dir.glob("*/SKILL.md"):
            name = skill_md.parent.name.replace("-", "_")
            skills[name] = skill_md.read_text(encoding="utf-8")[:4000]
            skills[skill_md.parent.name] = skills[name]
    return skills


def _load_skills() -> dict[str, str]:
    global _SKILL_CACHE
    if _SKILL_CACHE is not None:
        return _SKILL_CACHE
    _SKILL_CACHE = _load_skills_from_dir(settings.agent_context_dir / "skills")
    return _SKILL_CACHE


def _skills_for_context_dir(agent_context_dir: Path) -> dict[str, str]:
    key = str(agent_context_dir.resolve())
    cached = _SKILL_DIR_CACHE.get(key)
    if cached is not None:
        return cached
    loaded = _load_skills_from_dir(agent_context_dir / "skills")
    _SKILL_DIR_CACHE[key] = loaded
    return loaded


def get_skill(name: str, *, agent_context_dir: Path | None = None) -> str | None:
    if not name:
        return None
    if agent_context_dir is not None:
        skills = _skills_for_context_dir(agent_context_dir)
    else:
        skills = _load_skills()
    return skills.get(name) or skills.get(name.replace("-", "_"))


def list_skills() -> list[str]:
    return sorted(_load_skills().keys())
