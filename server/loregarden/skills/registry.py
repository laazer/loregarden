from pathlib import Path

from loregarden.config import settings

#: How much of a skill reaches an agent. The prompt caps the skill block at this
#: same number, so loading more only ever produced text nothing would read —
#: truncation now happens once, here, instead of twice at two different sizes.
SKILL_PROMPT_CAP = 3000


class SkillNotFoundError(ValueError):
    """A declared skill name resolved nowhere in the search chain."""

    def __init__(
        self,
        skill_name: str,
        searched_dirs: list[Path],
        *,
        agent_id: str = "",
    ) -> None:
        self.skill_name = skill_name
        self.searched_dirs = [Path(path) for path in searched_dirs]
        self.agent_id = agent_id
        dirs = ", ".join(str(path) for path in self.searched_dirs)
        if agent_id:
            message = (
                f"Agent '{agent_id}' declares default skill '{skill_name}', "
                f"which is not registered (searched: {dirs})"
            )
        else:
            message = f"Skill '{skill_name}' is not registered (searched: {dirs})"
        super().__init__(message)


def skill_search_dirs(agent_context_dir: Path | None = None) -> list[Path]:
    """Workspace skills first, then the loregarden default (deduped)."""
    default = (settings.agent_context_dir / "skills").resolve()
    if agent_context_dir is None:
        return [default]
    workspace = (Path(agent_context_dir) / "skills").resolve()
    if workspace == default:
        return [default]
    return [workspace, default]


def _read_skill(skills_dir: Path, name: str) -> str | None:
    if not name or "/" in name or "\\" in name or name in {".", ".."}:
        return None
    # Exact directory-name match via iterdir so case-insensitive volumes do not
    # treat REFACTOR as refactor.
    if not skills_dir.is_dir():
        return None
    for child in skills_dir.iterdir():
        if child.name != name or not child.is_dir():
            continue
        skill_md = child / "SKILL.md"
        try:
            if not skill_md.is_file():
                return None
            body = skill_md.read_text(encoding="utf-8")[:SKILL_PROMPT_CAP]
        except (OSError, UnicodeDecodeError):
            return None
        return body if body.strip() else None
    return None


def get_skill(name: str, *, agent_context_dir: Path | None = None) -> str | None:
    for skills_dir in skill_search_dirs(agent_context_dir):
        body = _read_skill(skills_dir, name)
        if body is not None:
            return body
    return None


def list_skills() -> list[str]:
    skills_dir = settings.agent_context_dir / "skills"
    if not skills_dir.is_dir():
        return []
    return sorted(
        child.name
        for child in skills_dir.iterdir()
        if child.is_dir() and _read_skill(skills_dir, child.name) is not None
    )
