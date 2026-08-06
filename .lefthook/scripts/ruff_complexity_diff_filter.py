#!/usr/bin/env python3
"""Diff-scope Ruff McCabe complexity (C901): only fail when a touched
function's complexity actually *increased* versus HEAD.

Pre-existing high-complexity functions don't block unrelated edits — same
"don't make it worse" policy as pylint_diff_filter.py / py_organization_check.py.

Invoked with cwd=<python project root> and project-relative file paths.
Repo-relative path for staged-addition lookup is ``{repo_prefix}/{path}``
(e.g. ``server/loregarden/...``); pass ``--repo-prefix server``.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

_LEFTHOOK_SCRIPTS = Path(__file__).resolve().parent
if str(_LEFTHOOK_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_LEFTHOOK_SCRIPTS))

from precommit_git_diff import git_diff_cached, git_repo_root, parse_staged_additions

# "`name` is too complex (14 > 10)"
_COMPLEXITY_RE = re.compile(r"is too complex \((\d+)\s*>\s*(\d+)\)")
_FN_NAME_RE = re.compile(r"`([^`]+)` is too complex")


def _function_span(py_file: Path, lineno: int) -> Tuple[int, int]:
    try:
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return (lineno, lineno)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.lineno == lineno:
            return (node.lineno, node.end_lineno or node.lineno)
    return (lineno, lineno)


def _complexity(message: str) -> Optional[int]:
    m = _COMPLEXITY_RE.search(message)
    return int(m.group(1)) if m else None


def _fn_name(message: str) -> str:
    m = _FN_NAME_RE.search(message)
    return m.group(1) if m else ""


def _project_rel(raw_path: str, cwd: Path) -> str:
    path_obj = Path(raw_path)
    if path_obj.is_absolute():
        try:
            return str(path_obj.relative_to(cwd))
        except ValueError:
            return path_obj.name
    return raw_path


def _run_ruff_c901(paths: list[str], *, config: Path | None) -> list[dict]:
    if not paths:
        return []
    cmd = [
        sys.executable,
        "-m",
        "ruff",
        "check",
        "--select",
        "C901",
        "--output-format",
        "json",
        "--no-cache",
    ]
    if config is not None and config.is_file():
        cmd.extend(["--config", str(config)])
    cmd.extend(paths)
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    try:
        data = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _head_complexities(
    repo: Path, repo_rel_paths: Set[str], *, config: Path
) -> Dict[str, Dict[str, int]]:
    """Map repo_rel -> {function_name: complexity} for C901 hits at HEAD."""
    counts: Dict[str, Dict[str, int]] = {}
    with tempfile.TemporaryDirectory(dir=".") as tmp:
        # Index by a unique token so we can map ruff's reported path back.
        token_to_repo_rel: Dict[str, str] = {}
        tmp_paths: list[str] = []
        for i, repo_rel in enumerate(sorted(repo_rel_paths)):
            head_text = _head_text(repo, repo_rel)
            if head_text is None:
                continue
            token = f"head_{i}.py"
            tmp_file = Path(tmp) / token
            tmp_file.write_text(head_text, encoding="utf-8")
            token_to_repo_rel[token] = repo_rel
            tmp_paths.append(str(tmp_file))

        if not tmp_paths:
            return counts

        for msg in _run_ruff_c901(tmp_paths, config=config):
            if msg.get("code") != "C901":
                continue
            token = Path(msg.get("filename") or "").name
            repo_rel = token_to_repo_rel.get(token)
            if repo_rel is None:
                continue
            score = _complexity(msg.get("message", ""))
            obj = _fn_name(msg.get("message", ""))
            if score is None or not obj:
                continue
            counts.setdefault(repo_rel, {})[obj] = score
    return counts


def _head_text(repo: Path, repo_rel: str) -> Optional[str]:
    proc = subprocess.run(
        ["git", "show", f"HEAD:{repo_rel}"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout


def _parse_args(argv: list[str]) -> tuple[str, list[str]]:
    repo_prefix = ""
    paths: list[str] = []
    i = 1
    while i < len(argv):
        if argv[i] == "--repo-prefix" and i + 1 < len(argv):
            repo_prefix = argv[i + 1].rstrip("/")
            i += 2
            continue
        paths.append(argv[i])
        i += 1
    return repo_prefix, paths


def main(argv: list[str]) -> int:
    repo_prefix, rel_args = _parse_args(argv)
    if not rel_args:
        return 0

    cwd = Path.cwd()
    config = cwd / "pyproject.toml"
    repo = git_repo_root()
    additions_map: Dict[str, Set[int]] = {}
    if repo is not None:
        additions_map = {
            path: {ln for ln, _ in items}
            for path, items in parse_staged_additions(git_diff_cached(repo)).items()
        }

    messages = _run_ruff_c901(rel_args, config=config if config.is_file() else None)

    candidates: List[tuple[dict, str, str]] = []
    repo_rels_needed: Set[str] = set()
    for msg in messages:
        if msg.get("code") != "C901":
            continue
        project_rel = _project_rel(msg.get("filename") or "", cwd)
        repo_rel = f"{repo_prefix}/{project_rel}" if repo_prefix else project_rel
        touched = additions_map.get(repo_rel, set())
        if not touched:
            continue
        row = int((msg.get("location") or {}).get("row") or 0)
        start, end = _function_span(Path(project_rel), row)
        if not any(ln in touched for ln in range(start, end + 1)):
            continue
        obj = _fn_name(msg.get("message", ""))
        candidates.append((msg, repo_rel, obj))
        repo_rels_needed.add(repo_rel)

    head_counts = (
        _head_complexities(repo, repo_rels_needed, config=config)
        if repo and repo_rels_needed
        else {}
    )

    kept: List[dict] = []
    for msg, repo_rel, obj in candidates:
        current = _complexity(msg.get("message", ""))
        baseline = head_counts.get(repo_rel, {}).get(obj) if obj else None
        if baseline is not None and current is not None and current <= baseline:
            continue
        kept.append(msg)

    if kept:
        print("pre-commit: Ruff C901 (McCabe) complexity grew on touched lines:")
        for msg in kept:
            loc = msg.get("location") or {}
            row = loc.get("row", "?")
            col = loc.get("column", "?")
            path = _project_rel(msg.get("filename") or "", cwd)
            print(f" - {path}:{row}:{col}: {msg.get('message', '')}")
        return 1

    print("pre-commit: Ruff C901 (McCabe) — no complexity growth on touched lines.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
