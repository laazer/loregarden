"""Give every entry point the `gh` account `.env` names, however it was launched.

`run_gh` acts as `LOREGARDEN_GH_USER` when it is set (#500). It was set in one
place, the primary checkout's `.env`, and read in one place, `dev-server.sh`,
which sources `$ROOT/.env` — its *own* checkout's. A server started from a
worktree, `loregarden-cli.sh mcp call …` (how terminal agents land and publish),
and the desktop app's `python -m loregarden` never saw it, so their `gh` ran as
whichever account was active. On this machine that is `whjake`, pull-only: four
tree publishes from 2026-10-29 to 10-30 pushed and then failed `open_pr` with
"whjake does not have the correct permissions", and each filed a card.

So the setting is primed here, at import of `loregarden.config`, like the other
credentials every subprocess needs. Only this one key is read: sourcing the
whole file would hand an agent's CLI call the operator's local overrides
(`LOREGARDEN_ALLOW_PERMISSION_BYPASS` among them).
"""

from __future__ import annotations

import logging
import os
import re
import shlex
from pathlib import Path

from loregarden.services.git_subprocess import GH_USER_ENV
from loregarden.services.git_trees import main_working_tree

logger = logging.getLogger(__name__)

_ASSIGNMENT = re.compile(rf"^\s*(?:export\s+)?{GH_USER_ENV}=(.*)$")


def gh_user_from_env_file(path: Path) -> str | None:
    """The value `path` assigns to `LOREGARDEN_GH_USER`, or None when it assigns none.

    Read as the shell would: an optional `export`, quotes removed, a trailing
    `# comment` dropped. The last assignment wins, as it does when sourced.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (
        FileNotFoundError
    ):  # silent-ok: no .env is the documented default; the caller tries the next file
        return None
    found: str | None = None
    for line in lines:
        match = _ASSIGNMENT.match(line)
        if match is None:
            continue
        try:
            words = shlex.split(match.group(1), comments=True)
        except ValueError:
            logger.warning(
                "%s assigns %s a value the shell could not parse; ignored", path, GH_USER_ENV
            )
            continue
        found = words[0] if words else ""
    return found


def prime_gh_user_env(*, repo_root: Path) -> str | None:
    """Set `LOREGARDEN_GH_USER` from `.env` when the environment does not already.

    `repo_root`'s own `.env` first, then the primary checkout's, which is where a
    worktree's live settings are. An explicit environment value always wins.
    Returns the account now in effect, or None when nothing names one.
    """
    current = os.environ.get(GH_USER_ENV, "").strip()
    if current:
        return current
    candidates = [repo_root / ".env"]
    primary = main_working_tree(repo_root)
    if primary is not None and primary.resolve() != repo_root.resolve():
        candidates.append(primary / ".env")
    for candidate in candidates:
        user = gh_user_from_env_file(candidate)
        if user:
            os.environ[GH_USER_ENV] = user
            return user
    return None
