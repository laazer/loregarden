"""The system prompt a Claude stage run's session started under, kept for its resume.

A rework round resumes the earlier run's session (`rework_resume`). Claude's
prompt cache is a prefix: tools, then the system prompt, then the history. The
stage prompt rides in the system prompt (`--append-system-prompt-file`), and a
freshly rendered one differs from the earlier run's (the rework feedback, the
unresolved ledger, the memory briefing all moved), so every resume re-wrote the
whole session history to the cache. On the live database 71 of the first 72
resumes ran under a changed prompt (by about 7k of 101k chars), and resumes
within five minutes of the earlier run still wrote about 190k cache tokens.

So each Claude run keeps the system prompt it ran under, and a resume passes
the earlier run's prompt back unchanged. What changed since travels in the
resumed turn's message instead (`stage_context.turn_context_lines`).

Only the newest run at a ticket's stage can be resumed (`rework_resume._prior_run`),
so keeping one run's prompt removes the older runs' at that stage: at most one
file per ticket stage stays, rather than one per run in a directory nothing
sweeps (`run_resupervise._delete_output_files`).
"""

from __future__ import annotations

import logging
from pathlib import Path

from loregarden.config import settings
from loregarden.models.domain import AgentRun
from loregarden.services.run_output_files import run_file_stem
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

SESSION_PROMPT_DIR = "session-prompts"


def session_prompt_path(run: AgentRun) -> Path:
    stem = run_file_stem(run.run_code, run.id)
    return Path(settings.run_log_dir) / SESSION_PROMPT_DIR / f"{stem}.md"


def read_session_prompt(run: AgentRun) -> str:
    """The system prompt `run`'s session started under, or "" when none was kept.

    Runs from before this module, and runs whose keep failed, have none; their
    resume re-caches the history, as every resume did before.
    """
    path = session_prompt_path(run)
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:  # silent-ok: no kept prompt is the normal state of a pre-existing or non-Claude run; the caller notes the costlier resume on the run's log
        return ""
    except OSError:
        logger.warning("Could not read the kept session prompt %s", path, exc_info=True)
        return ""


def keep_session_prompt(session: Session, run: AgentRun, prompt: str) -> Path | None:
    """Keep `prompt` as the system prompt `run`'s session runs under.

    Returns the kept file, or None when it could not be written: the run still
    proceeds (from its own temporary prompt file), and only a later resume of
    its session pays to re-cache the history.
    """
    path = session_prompt_path(run)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(prompt, encoding="utf-8")
    except OSError:
        logger.warning(
            "Could not keep run %s's session prompt at %s; a resume of its session "
            "will re-cache the history",
            run.run_code,
            path,
            exc_info=True,
        )
        return None
    _drop_older_prompts(session, run)
    return path


def _drop_older_prompts(session: Session, run: AgentRun) -> None:
    """Remove the kept prompts of runs at `run`'s stage that no resume can reach."""
    older = session.exec(
        select(AgentRun)
        .where(AgentRun.ticket_id == run.ticket_id)
        .where(AgentRun.stage_key == run.stage_key)
        .where(AgentRun.id != run.id)
        .where(col(AgentRun.created_at) <= run.created_at)
    ).all()
    for prior in older:
        path = session_prompt_path(prior)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove the superseded session prompt %s", path)
