from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _find_repo_root() -> Path:
    env = __import__("os").environ.get("LOREGARDEN_REPO_ROOT")
    if env:
        return Path(env).resolve()
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / "agent_context").is_dir() and (parent / "server").is_dir():
            return parent
    return here.parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LOREGARDEN_")

    repo_root: Path = _find_repo_root()
    database_url: str = "sqlite:///data/loregarden.db"
    agent_context_dir: Path = Path("agent_context")
    project_board_dir: Path = Path("project_board")
    workflow_templates_dir: Path = Path("agent_context/workflows")
    cli_adapter: str = "local"
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]


settings = Settings()
settings.agent_context_dir = settings.repo_root / settings.agent_context_dir
settings.project_board_dir = settings.repo_root / settings.project_board_dir
settings.workflow_templates_dir = settings.repo_root / settings.workflow_templates_dir
