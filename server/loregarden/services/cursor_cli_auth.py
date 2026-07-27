"""Cursor CLI credentials for headless subprocesses (Baxter, stage runs).

Priority for ``CURSOR_API_KEY``:
1. Already set in the process environment
2. ``data/.cursor-api-key`` (explicit User API key from ``task cursor:setup-key``)
3. Cursor IDE session token from ``state.vscdb`` (same store the Usage modal reads)

Browser ``agent login`` alone does not unlock ``cursor-agent -p``; the IDE token
or a User API key must be present in the environment the control plane exports
to that subprocess.
"""

from __future__ import annotations

import logging
import os
import platform
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

CURSOR_ACCESS_KEY = "cursorAuth/accessToken"


def cursor_state_db() -> Path:
    home = Path.home()
    if platform.system() == "Darwin":
        return home / "Library/Application Support/Cursor/User/globalStorage/state.vscdb"
    if platform.system() == "Windows":
        appdata = os.environ.get("APPDATA", "")
        return Path(appdata) / "Cursor/User/globalStorage/state.vscdb"
    return home / ".config/Cursor/User/globalStorage/state.vscdb"


def read_cursor_ide_access_token() -> str | None:
    """Read the IDE's stored access token, if Cursor is signed in on this machine."""
    db_path = cursor_state_db()
    if not db_path.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT value FROM ItemTable WHERE key = ? LIMIT 1",
                (CURSOR_ACCESS_KEY,),
            ).fetchone()
        finally:
            conn.close()
        if not row or not row[0]:
            return None
        raw = row[0]
        if isinstance(raw, bytes):
            token = raw.decode("utf-8")
        else:
            token = str(raw)
        token = token.strip()
        return token or None
    except sqlite3.Error as exc:
        logger.debug("cursor IDE credential sqlite read failed: %s", exc)
        return None


def _valid_env_secret(value: str) -> bool:
    return bool(value) and value.isascii() and not any(ch.isspace() for ch in value)


def prime_cursor_api_key_env(*, repo_root: Path) -> str | None:
    """Ensure ``CURSOR_API_KEY`` is set for child ``cursor-agent`` processes.

    Returns the source used (``env``, ``file``, ``ide``) or ``None`` if unset.
    Never logs the secret.
    """
    if os.environ.get("CURSOR_API_KEY", "").strip():
        return "env"

    key_path = repo_root / "data" / ".cursor-api-key"
    if key_path.is_file():
        try:
            key = key_path.read_text(encoding="utf-8").strip()
        except OSError:
            key = ""
        if _valid_env_secret(key):
            os.environ["CURSOR_API_KEY"] = key
            return "file"

    ide_token = read_cursor_ide_access_token()
    if ide_token and _valid_env_secret(ide_token):
        os.environ["CURSOR_API_KEY"] = ide_token
        return "ide"

    return None
