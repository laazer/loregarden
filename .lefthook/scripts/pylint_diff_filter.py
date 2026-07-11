#!/usr/bin/env python3
"""Diff-scope Pylint's too-many-statements: only fail when a touched
function's statement count actually *increased* versus the committed
(HEAD) version. Pre-existing long functions don't block unrelated edits
to them — including a one-line touch that doesn't grow the function at
all (e.g. adding `from e` to a `raise`) — same "don't make it worse"
policy as py_organization_check.py.

Invoked with cwd=server/ and server-relative file paths (see py-pylint.sh).
"""

import ast
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, Optional, Set, Tuple

_LEFTHOOK_SCRIPTS = Path(__file__).resolve().parent
if str(_LEFTHOOK_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_LEFTHOOK_SCRIPTS))

from precommit_git_diff import git_diff_cached, git_repo_root, parse_staged_additions

_COUNT_RE = re.compile(r"\((\d+)/(\d+)\)")


def _function_span(py_file: Path, lineno: int) -> Tuple[int, int]:
    try:
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return (lineno, lineno)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.lineno == lineno:
            return (node.lineno, node.end_lineno or node.lineno)
    return (lineno, lineno)


def _statement_count(message: str) -> Optional[int]:
    m = _COUNT_RE.search(message)
    return int(m.group(1)) if m else None


def _run_pylint_json(paths: list) -> list:
    if not paths:
        return []
    proc = subprocess.run(
        [sys.executable, "-m", "pylint", "--output-format=json", *paths],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        return json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return []


def _head_statement_counts(repo: Path, repo_rel_paths: Set[str]) -> Dict[str, Dict[str, int]]:
    """Map repo_rel path -> {obj_name: statement_count} at HEAD (pre-commit)."""
    counts: Dict[str, Dict[str, int]] = {}
    with tempfile.TemporaryDirectory(dir=".") as tmp:
        tmp_path_by_repo_rel: Dict[str, Path] = {}
        for repo_rel in repo_rel_paths:
            head_text = _head_text(repo, repo_rel)
            if head_text is None:
                continue  # new file — no baseline, any violation is new
            tmp_file = Path(tmp) / Path(repo_rel).name
            tmp_file.write_text(head_text, encoding="utf-8")
            tmp_path_by_repo_rel[repo_rel] = tmp_file

        if not tmp_path_by_repo_rel:
            return counts

        messages = _run_pylint_json([str(p) for p in tmp_path_by_repo_rel.values()])
        path_to_repo_rel = {str(p): rel for rel, p in tmp_path_by_repo_rel.items()}
        for msg in messages:
            if msg.get("symbol") != "too-many-statements":
                continue
            repo_rel = path_to_repo_rel.get(msg.get("path", ""))
            if repo_rel is None:
                continue
            count = _statement_count(msg.get("message", ""))
            if count is None:
                continue
            counts.setdefault(repo_rel, {})[msg.get("obj", "")] = count
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


def main(argv: list) -> int:
    server_rel_args = argv[1:]
    if not server_rel_args:
        return 0

    repo = git_repo_root()
    additions_map: Dict[str, Set[int]] = {}
    if repo is not None:
        additions_map = {
            path: {ln for ln, _ in items}
            for path, items in parse_staged_additions(git_diff_cached(repo)).items()
        }

    messages = _run_pylint_json(server_rel_args)

    candidates = []
    repo_rels_needed: Set[str] = set()
    for msg in messages:
        if msg.get("symbol") != "too-many-statements":
            continue
        server_rel = msg.get("path", "")
        repo_rel = f"server/{server_rel}"
        touched = additions_map.get(repo_rel, set())
        if not touched:
            continue
        start, end = _function_span(Path(server_rel), msg.get("line", 0))
        if not any(ln in touched for ln in range(start, end + 1)):
            continue
        candidates.append((msg, repo_rel))
        repo_rels_needed.add(repo_rel)

    head_counts = _head_statement_counts(repo, repo_rels_needed) if repo and repo_rels_needed else {}

    kept = []
    for msg, repo_rel in candidates:
        current_count = _statement_count(msg.get("message", ""))
        baseline_count = head_counts.get(repo_rel, {}).get(msg.get("obj", ""))
        # No baseline violation for this function (new function, or it was
        # under the cap before) -> any current violation is new debt.
        # Baseline violation exists -> only flag if this diff grew it further.
        if baseline_count is not None and current_count is not None and current_count <= baseline_count:
            continue
        kept.append(msg)

    if kept:
        print("pre-commit: Pylint too-many-statements grew on touched lines:")
        for msg in kept:
            print(f" - {msg['path']}:{msg['line']}:{msg['column']}: {msg['message']}")
        return 1

    print("pre-commit: Pylint (too-many-statements) — no growth on touched lines.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
