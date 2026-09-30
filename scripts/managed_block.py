"""Reading and rewriting a marker-delimited block in a file another repo owns.

Shared by install_workspace_hooks.py and install_workspace_agents_doc.py. Both
edit a file loregarden does not own, so both refuse rather than guess whenever
the file is not what they expect:

- **Unbalanced markers.** A BEGIN with no END (someone deleted it by hand) used
  to make everything after BEGIN look like the managed block, and a refresh
  deleted the workspace's own content — then reported the result as current.
- **A symlink.** Writing through one lands outside the repository the operator
  was told would be changed.
- **Not UTF-8.** Refused with a sentence the page can show, not a traceback.

Writes go through a temp file in the same directory and ``os.replace``, so a
killed installer leaves the old file or the new one, never half of either, and
keep the file's line endings so a CRLF repo does not see every line change.

Stdlib only — this runs against arbitrary workspaces, which may have no venv.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


class RefusedError(Exception):
    """The file is not safe to edit; the message says why, for the operator."""


def refuse_symlink(path: Path) -> None:
    """Refuse a symlink, dangling or not — writing through it leaves the repo."""
    if path.is_symlink():
        raise RefusedError(
            f"{path} is a symlink to {os.readlink(path)}; loregarden only writes files "
            "inside the workspace, so edit the file it points at yourself"
        )


def read_lines(path: Path) -> tuple[list[str], str]:
    """The file's lines and its line ending (the first one found; ``\\n`` if none)."""
    refuse_symlink(path)
    data = path.read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RefusedError(
            f"{path} is not UTF-8 text ({exc.reason} at byte {exc.start}); "
            "convert it to UTF-8 and install again"
        ) from exc
    first_newline = text.find("\n")
    newline = "\r\n" if first_newline > 0 and text[first_newline - 1] == "\r" else "\n"
    return text.splitlines(), newline


def split_block(
    lines: list[str], begin: str, end: str, *, path: Path
) -> tuple[list[str], list[str]]:
    """Split out the managed block. Returns (rest, block); block is [] when absent.

    Refuses anything but zero or exactly one BEGIN followed by one END: any other
    shape means the boundary of the managed block is unknown, and guessing it is
    how the workspace's own lines get deleted.
    """
    begins = [i for i, line in enumerate(lines) if line.strip() == begin]
    ends = [i for i, line in enumerate(lines) if line.strip() == end]
    if not begins and not ends:
        return list(lines), []
    if len(begins) != 1 or len(ends) != 1 or begins[0] > ends[0]:
        where = ", ".join(
            [f"begin marker on line {i + 1}" for i in begins]
            + [f"end marker on line {i + 1}" for i in ends]
        )
        raise RefusedError(
            f"{path} has an unmatched loregarden managed block ({where}); "
            "restore the missing marker or remove the partial block, then install again"
        )
    start, stop = begins[0], ends[0]
    return [*lines[:start], *lines[stop + 1 :]], lines[start : stop + 1]


def _new_file_mode() -> int:
    umask = os.umask(0)
    os.umask(umask)
    return 0o666 & ~umask


def write_lines(path: Path, lines: list[str], newline: str) -> None:
    """Replace ``path`` atomically, keeping its permissions when it exists."""
    refuse_symlink(path)
    mode = path.stat().st_mode & 0o777 if path.exists() else _new_file_mode()
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(newline.join(lines) + newline)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
