from pathlib import Path

from loregarden.config import settings
from loregarden.db import session as db_session
from loregarden.services.skill_service import SkillService, seed_builtin_skills

#: How much of a skill reaches an agent. Storage and lookup return full bodies;
#: prompt rendering is the only place this cap may be applied.
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
    """Legacy diagnostic paths; DB lookup no longer reads these directories."""
    default = (settings.agent_context_dir / "skills").resolve()
    if agent_context_dir is None:
        return [default]
    workspace = (Path(agent_context_dir) / "skills").resolve()
    if workspace == default:
        return [default]
    return [workspace, default]


def get_skill(name: str, *, agent_context_dir: Path | None = None) -> str | None:
    del agent_context_dir
    if not name:
        return None
    with db_session.Session(db_session.engine) as session:
        service = SkillService(session)
        skill = service.get_skill(name)
        if skill is None:
            seed_builtin_skills(session)
            skill = service.get_skill(name)
        return skill.body if skill else None


def list_skills() -> list[str]:
    with db_session.Session(db_session.engine) as session:
        service = SkillService(session)
        slugs = service.list_skill_slugs()
        if not slugs:
            seed_builtin_skills(session)
            slugs = service.list_skill_slugs()
        return slugs
