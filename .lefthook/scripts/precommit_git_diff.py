"""Parse `git diff --cached` output: added lines per file (for pre-commit policy hooks)."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def git_repo_root() -> Optional[Path]:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    top = proc.stdout.strip()
    return Path(top) if top else None


def git_diff_cached(repo: Path) -> str:
    proc = subprocess.run(
        ["git", "diff", "--cached", "--no-color", "-U0"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return ""
    return proc.stdout


def parse_staged_additions(diff: str) -> Dict[str, List[Tuple[int, str]]]:
    """Map relpath (as in diff, posix) -> [(new_line_no, added_line_without_leading_plus)]."""
    result: Dict[str, List[Tuple[int, str]]] = {}
    current_file: Optional[str] = None
    lines = diff.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("diff --git "):
            current_file = None
            i += 1
            continue
        if line.startswith("+++ b/"):
            name = line[6:].strip()
            current_file = None if name == "/dev/null" else name
            i += 1
            continue
        if line.startswith("@@ "):
            m = HUNK_HEADER_RE.match(line)
            i += 1
            if not m or current_file is None:
                continue
            new_line = int(m.group(3))
            while i < len(lines):
                l = lines[i]
                if l.startswith("@@") or l.startswith("diff --git"):
                    break
                if l.startswith("\\"):
                    i += 1
                    continue
                if not l:
                    i += 1
                    continue
                prefix = l[0]
                body = l[1:]
                if prefix == "+":
                    lst = result.setdefault(current_file, [])
                    lst.append((new_line, body))
                    new_line += 1
                elif prefix == " ":
                    new_line += 1
                elif prefix == "-":
                    pass
                i += 1
            continue
        i += 1
    return result


def git_diff_numstat(repo: Path) -> Dict[str, Tuple[int, int]]:
    """Map relpath -> (added_lines, deleted_lines) for staged changes.

    Used for "don't make it worse" checks (e.g. file-length caps) that should
    fire on net growth, not on any touch to an already-oversized file —
    otherwise a pure cleanup/shrink of a long file would itself get blocked.
    """
    proc = subprocess.run(
        ["git", "diff", "--cached", "--numstat"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return {}
    result: Dict[str, Tuple[int, int]] = {}
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        added, deleted, path = parts
        try:
            result[path] = (int(added), int(deleted))
        except ValueError:
            continue  # binary file ("-\t-\tpath")
    return result


def staged_file_text(repo: Path, relpath: str) -> Optional[str]:
    proc = subprocess.run(
        ["git", "show", f":0:{relpath}"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout
