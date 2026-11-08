"""Give every subprocess the cached `claude setup-token` credential, however the server started.

macOS Keychain will not release Claude's login to a background process, so a
`claude` subprocess the backend spawns reads `CLAUDE_CODE_OAUTH_TOKEN` instead.
The token is cached in `data/.claude-oauth-token` (gitignored), and `data/` is
per checkout: only the primary checkout has the file. A server started from a
worktree (`task sandbox` is the common case) looked only in its own `data/`,
found nothing, and every agent it ran failed within seconds with an empty
stderr: "Baxter unavailable: Agent run failed".

So, like `gh_account_env`, this looks in `repo_root` first and then in the
primary checkout. An explicit environment value always wins.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from loregarden.services.git_trees import main_working_tree

logger = logging.getLogger(__name__)

CLAUDE_OAUTH_TOKEN_ENV = "CLAUDE_CODE_OAUTH_TOKEN"
TOKEN_RELATIVE_PATH = Path("data") / ".claude-oauth-token"


def _read_token(path: Path) -> str | None:
    """The token cached at `path`, or None when there is no usable one."""
    try:
        token = path.read_text(encoding="utf-8").strip()
    except (
        FileNotFoundError
    ):  # silent-ok: no cached token is the default; the caller tries the next checkout
        return None
    except OSError:
        logger.warning("Could not read the cached Claude token at %s", path, exc_info=True)
        return None
    if token and token.isascii() and not any(ch.isspace() for ch in token):
        return token
    logger.warning("The cached Claude token at %s is malformed; ignored", path)
    return None


def prime_claude_oauth_token_env(*, repo_root: Path) -> Path | None:
    """Set `CLAUDE_CODE_OAUTH_TOKEN` from the cached token file, unless already set.

    Returns the file the token came from, or None when the environment already
    had one or no checkout caches one.
    """
    if os.environ.get(CLAUDE_OAUTH_TOKEN_ENV, "").strip():
        return None
    candidates = [repo_root / TOKEN_RELATIVE_PATH]
    primary = main_working_tree(repo_root)
    if primary is not None and primary.resolve() != repo_root.resolve():
        candidates.append(primary / TOKEN_RELATIVE_PATH)
    for candidate in candidates:
        token = _read_token(candidate)
        if token:
            os.environ[CLAUDE_OAUTH_TOKEN_ENV] = token
            return candidate
    return None
