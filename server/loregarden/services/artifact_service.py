"""Build diff and test artifacts for the IDE truth-layer tabs."""

from __future__ import annotations

import json
import re
import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loregarden.models.domain import AgentRun, Artifact, Ticket, Workspace
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session, select

MAX_DIFF_LINES = 400
MAX_DIFF_LINE_CHARS = 500
MAX_UNTRACKED_FILE_BYTES = 512_000
MAX_UNTRACKED_FILES = 20

_artifact_upsert_lock = threading.Lock()


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _git_base_ref(cwd: Path) -> str | None:
    for ref in ("main", "master", "origin/main", "origin/master"):
        if _git(cwd, "rev-parse", "--verify", ref).returncode == 0:
            return ref
    if _git(cwd, "rev-parse", "--verify", "HEAD~1").returncode == 0:
        return "HEAD~1"
    return None


@dataclass(frozen=True)
class BranchDiffContext:
    git_cwd: Path
    branch: str
    base: str
    range_label: str
    stat_args: list[str]
    patch_args: list[str]
    mode: str


def _parse_numstat(text: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for line in (text or "").splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        add_raw, del_raw, path = parts
        path = path.strip()
        if not path:
            continue
        if add_raw == "-" or del_raw == "-":
            entries.append({"path": path, "add": 0, "del": 0, "binary": True})
        else:
            entries.append(
                {"path": path, "add": int(add_raw), "del": int(del_raw), "binary": False}
            )
    return entries


def _manifest_from_entries(
    entries: list[dict[str, Any]],
    *,
    branch: str,
    base: str,
    range_label: str,
) -> dict[str, Any]:
    total_add = sum(int(item.get("add") or 0) for item in entries)
    total_del = sum(int(item.get("del") or 0) for item in entries)
    file_count = len(entries)
    summary = (
        f"{file_count} file{'s' if file_count != 1 else ''} changed, "
        f"{total_add} insertion{'s' if total_add != 1 else ''}(+), "
        f"{total_del} deletion{'s' if total_del != 1 else ''}(-)"
    )
    return {
        "file": entries[0]["path"] if entries else "changes",
        "add": f"+{total_add}",
        "del": f"−{total_del}",
        "files": summary,
        "range": range_label,
        "branch": branch,
        "base": base,
        "file_entries": [
            {
                "path": item["path"],
                "add": int(item.get("add") or 0),
                "del": int(item.get("del") or 0),
            }
            for item in entries
        ],
        "sections": [],
    }


def _current_branch(repo_root: Path) -> str:
    proc = _git(repo_root, "branch", "--show-current")
    if proc.returncode != 0:
        return ""
    return (proc.stdout or "").strip()


def _branch_checkout_root(repo_root: Path, branch: str) -> Path | None:
    from loregarden.services.file_editor import _parse_worktrees

    if _current_branch(repo_root) == branch:
        return repo_root
    for item in _parse_worktrees(repo_root):
        if item.get("branch") == branch:
            path = (item.get("path") or "").strip()
            if path:
                return Path(path)
    return None


def _resolve_upstream_ref(repo_root: Path, branch: str) -> str | None:
    proc = _git(repo_root, "rev-parse", "--abbrev-ref", "--verify", f"{branch}@{{upstream}}")
    if proc.returncode == 0:
        upstream = (proc.stdout or "").strip()
        if upstream and upstream != "HEAD":
            return upstream
    for prefix in ("origin", "upstream"):
        candidate = f"{prefix}/{branch}"
        if _git(repo_root, "rev-parse", "--verify", candidate).returncode == 0:
            return candidate
    return None


def _branch_diff_context(
    workspace: Workspace,
    branch: str,
    *,
    base: str | None = None,
    mode: str = "base",
) -> BranchDiffContext | None:
    repo_root = resolve_workspace_root(workspace)
    if not (repo_root / ".git").exists():
        return None

    branch = branch.strip()
    if not branch:
        return None

    allowed = {"base", "remote", "unstaged", "uncommitted"}
    if mode not in allowed:
        return None

    if _git(repo_root, "rev-parse", "--verify", branch).returncode != 0:
        return None

    if mode in {"unstaged", "uncommitted"}:
        git_cwd = _branch_checkout_root(repo_root, branch)
        if not git_cwd or _current_branch(git_cwd) != branch:
            return None
        if mode == "unstaged":
            return BranchDiffContext(
                git_cwd=git_cwd,
                branch=branch,
                base="working tree",
                range_label="unstaged changes",
                stat_args=[],
                patch_args=[],
                mode=mode,
            )
        return BranchDiffContext(
            git_cwd=git_cwd,
            branch=branch,
            base="HEAD",
            range_label="uncommitted changes",
            stat_args=["HEAD"],
            patch_args=["HEAD"],
            mode=mode,
        )

    if mode == "remote":
        upstream = _resolve_upstream_ref(repo_root, branch)
        if not upstream:
            return None
        diff_spec = f"{upstream}..{branch}"
        return BranchDiffContext(
            git_cwd=repo_root,
            branch=branch,
            base=upstream,
            range_label=diff_spec,
            stat_args=[diff_spec],
            patch_args=[diff_spec],
            mode=mode,
        )

    base_ref = base.strip() if base else None
    if not base_ref:
        base_ref = _git_base_ref(repo_root)
    if not base_ref:
        return None

    diff_spec = f"{base_ref}...{branch}"
    return BranchDiffContext(
        git_cwd=repo_root,
        branch=branch,
        base=base_ref,
        range_label=diff_spec,
        stat_args=[diff_spec],
        patch_args=[diff_spec],
        mode=mode,
    )


def _untracked_manifest_entries(cwd: Path) -> list[dict[str, Any]]:
    proc = _git(cwd, "ls-files", "--others", "--exclude-standard")
    entries: list[dict[str, Any]] = []
    for raw_path in (proc.stdout or "").splitlines():
        path = raw_path.strip()
        if not path or len(entries) >= MAX_UNTRACKED_FILES:
            continue
        full_path = cwd / path
        if not full_path.is_file():
            continue
        try:
            size = full_path.stat().st_size
        except OSError:
            continue
        if size > MAX_UNTRACKED_FILE_BYTES:
            entries.append({"path": path, "add": 1, "del": 0})
            continue
        try:
            text = full_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        line_count = len(text.splitlines()) or (1 if text else 0)
        entries.append({"path": path, "add": line_count, "del": 0})
    return entries


def _manifest_entries_for_context(ctx: BranchDiffContext) -> list[dict[str, Any]]:
    numstat = _git(ctx.git_cwd, "diff", "--numstat", *ctx.stat_args)
    entries = _parse_numstat(numstat.stdout or "") if numstat.returncode == 0 else []
    if ctx.mode in {"unstaged", "uncommitted"}:
        tracked_paths = {item["path"] for item in entries}
        for item in _untracked_manifest_entries(ctx.git_cwd):
            if item["path"] not in tracked_paths:
                entries.append(item)
    return entries


def branch_diff_manifest(
    workspace: Workspace,
    branch: str,
    *,
    base: str | None = None,
    mode: str = "base",
) -> dict[str, Any] | None:
    ctx = _branch_diff_context(workspace, branch, base=base, mode=mode)
    if not ctx:
        return None
    entries = _manifest_entries_for_context(ctx)
    if not entries:
        return None
    return _manifest_from_entries(
        entries,
        branch=branch,
        base=ctx.base,
        range_label=ctx.range_label,
    )


def capture_branch_file_diff(
    workspace: Workspace,
    branch: str,
    file_path: str,
    *,
    base: str | None = None,
    mode: str = "base",
) -> dict[str, Any] | None:
    ctx = _branch_diff_context(workspace, branch, base=base, mode=mode)
    if not ctx:
        return None

    file_path = file_path.strip()
    if not file_path:
        return None

    entries = _manifest_entries_for_context(ctx)
    entry_map = {item["path"]: item for item in entries}
    if file_path not in entry_map:
        return None

    counts = entry_map[file_path]
    stat_override = {file_path: (int(counts.get("add") or 0), int(counts.get("del") or 0))}

    patch = _git(ctx.git_cwd, "diff", *ctx.patch_args, "--", file_path)
    sections: list[dict[str, Any]] = []
    if patch.returncode == 0 and (patch.stdout or "").strip():
        sections = _parse_unified_diff(patch.stdout or "", stat_overrides=stat_override)

    if not sections and ctx.mode in {"unstaged", "uncommitted"}:
        sections = _untracked_diff_sections(ctx.git_cwd, only_path=file_path)
        if sections:
            section = sections[0]
            section["add"] = stat_override[file_path][0]
            section["del"] = stat_override[file_path][1]

    if not sections:
        return None

    section = sections[0]
    return {
        "file": file_path,
        "add": f"+{section.get('add', 0)}",
        "del": f"−{section.get('del', 0)}",
        "files": file_path,
        "range": ctx.range_label,
        "branch": branch,
        "base": ctx.base,
        "sections": [section],
    }


def capture_branch_diff(
    workspace: Workspace,
    branch: str,
    *,
    base: str | None = None,
    mode: str = "base",
) -> dict[str, Any] | None:
    """Return full diff artifact (all files). Prefer branch_diff_manifest + capture_branch_file_diff."""
    manifest = branch_diff_manifest(workspace, branch, base=base, mode=mode)
    if not manifest:
        return None
    sections: list[dict[str, Any]] = []
    for entry in manifest.get("file_entries") or []:
        file_diff = capture_branch_file_diff(
            workspace,
            branch,
            entry["path"],
            base=base,
            mode=mode,
        )
        if file_diff and file_diff.get("sections"):
            sections.extend(file_diff["sections"])
    if not sections:
        return None
    result = dict(manifest)
    result["sections"] = sections
    return result


def _capture_worktree_diff(cwd: Path, *, branch: str, mode: str) -> dict[str, Any] | None:
    if mode == "unstaged":
        range_label = "unstaged changes"
        base = "working tree"
        stat_args: list[str] = []
        patch_args: list[str] = []
    else:
        range_label = "uncommitted changes"
        base = "HEAD"
        stat_args = ["HEAD"]
        patch_args = ["HEAD"]

    artifact = _artifact_from_git_diff(
        cwd,
        branch=branch,
        base=base,
        range_label=range_label,
        stat_args=stat_args,
        patch_args=patch_args,
    )
    untracked = _untracked_diff_sections(cwd)
    if artifact is None and not untracked:
        return None
    if artifact is None:
        return _artifact_from_sections(
            branch=branch,
            base=base,
            range_label=range_label,
            sections=untracked,
        )
    if untracked:
        sections = list(artifact.get("sections") or []) + untracked
        return _artifact_from_sections(
            branch=branch,
            base=base,
            range_label=range_label,
            sections=sections,
        )
    return artifact


def _untracked_diff_sections(cwd: Path, *, only_path: str | None = None) -> list[dict[str, Any]]:
    proc = _git(cwd, "ls-files", "--others", "--exclude-standard")
    sections: list[dict[str, Any]] = []
    total_lines = 0

    for raw_path in (proc.stdout or "").splitlines():
        path = raw_path.strip()
        if not path or len(sections) >= MAX_UNTRACKED_FILES:
            continue
        if only_path and path != only_path:
            continue
        full_path = cwd / path
        if not full_path.is_file():
            continue
        try:
            size = full_path.stat().st_size
        except OSError:
            continue
        if size > MAX_UNTRACKED_FILE_BYTES:
            sections.append(
                {
                    "path": path,
                    "add": 1,
                    "del": 0,
                    "lines": [
                        {
                            "type": "c",
                            "ln": "",
                            "text": f"… file omitted ({size:,} bytes exceeds review limit) …",
                        }
                    ],
                }
            )
            total_lines += 1
            continue

        try:
            text = full_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        lines: list[dict[str, str]] = []
        truncated = False
        for line_no, line in enumerate(text.splitlines(), start=1):
            if total_lines >= MAX_DIFF_LINES:
                truncated = True
                break
            lines.append(
                {
                    "type": "a",
                    "ln": str(line_no),
                    "text": line[:MAX_DIFF_LINE_CHARS],
                }
            )
            total_lines += 1

        if truncated:
            lines.append({"type": "c", "ln": "", "text": "… diff truncated …"})

        if not lines:
            continue

        sections.append(
            {
                "path": path,
                "add": len(lines),
                "del": 0,
                "lines": lines,
            }
        )

    return sections


def _artifact_from_sections(
    *,
    branch: str,
    base: str,
    range_label: str,
    sections: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not sections:
        return None
    add = sum(int(section.get("add") or 0) for section in sections)
    delete = sum(int(section.get("del") or 0) for section in sections)
    primary_file = sections[0]["path"]
    file_count = len(sections)
    summary = f"{file_count} file{'s' if file_count != 1 else ''} changed, {add} insertions(+), {delete} deletions(-)"
    return {
        "file": primary_file,
        "add": f"+{add}",
        "del": f"−{delete}",
        "files": summary,
        "range": range_label,
        "branch": branch,
        "base": base,
        "sections": sections,
    }


def _artifact_from_git_diff(
    cwd: Path,
    *,
    branch: str,
    base: str,
    range_label: str,
    stat_args: list[str],
    patch_args: list[str],
) -> dict[str, Any] | None:
    stat = _git(cwd, "diff", "--stat", *stat_args)
    if stat.returncode != 0 or not stat.stdout.strip():
        return None

    numstat = _git(cwd, "diff", "--numstat", *stat_args)
    stat_map = {
        item["path"]: (int(item.get("add") or 0), int(item.get("del") or 0))
        for item in _parse_numstat(numstat.stdout or "")
    }

    stat_lines = [line for line in stat.stdout.splitlines() if line.strip()]
    summary = stat_lines[-1] if stat_lines else ""
    primary_file = stat_lines[0].split("|", 1)[0].strip() if stat_lines else "changes"

    add_match = re.search(r"(\d+)\s+insertion", summary)
    del_match = re.search(r"(\d+)\s+deletion", summary)
    add = f"+{add_match.group(1)}" if add_match else "+0"
    delete = f"−{del_match.group(1)}" if del_match else "−0"

    patch = _git(cwd, "diff", *patch_args)
    sections = _parse_unified_diff(patch.stdout or "", stat_overrides=stat_map)

    return {
        "file": primary_file,
        "add": add,
        "del": delete,
        "files": summary,
        "range": range_label,
        "branch": branch,
        "base": base,
        "sections": sections,
    }


def capture_git_diff(workspace: Workspace) -> dict[str, Any] | None:
    """Return diff artifact payload from the workspace git checkout."""
    cwd = resolve_workspace_root(workspace)
    if not (cwd / ".git").exists():
        return None

    base = _git_base_ref(cwd)
    diff_ref = base if base else "HEAD"
    stat = _git(cwd, "diff", "--stat", diff_ref)
    if stat.returncode != 0 or not stat.stdout.strip():
        stat = _git(cwd, "diff", "--stat")
        diff_ref = "working tree"
    if stat.returncode != 0 or not stat.stdout.strip():
        return None

    stat_lines = [line for line in stat.stdout.splitlines() if line.strip()]
    summary = stat_lines[-1] if stat_lines else ""
    primary_file = stat_lines[0].split("|", 1)[0].strip() if stat_lines else "changes"

    add_match = re.search(r"(\d+)\s+insertion", summary)
    del_match = re.search(r"(\d+)\s+deletion", summary)
    add = f"+{add_match.group(1)}" if add_match else "+0"
    delete = f"−{del_match.group(1)}" if del_match else "−0"

    patch = _git(cwd, "diff", diff_ref) if diff_ref != "working tree" else _git(cwd, "diff")
    sections = _parse_unified_diff(patch.stdout or "")

    return {
        "file": primary_file,
        "add": add,
        "del": delete,
        "files": summary,
        "range": diff_ref,
        "sections": sections,
    }


def _path_from_diff_git(line: str) -> str:
    parts = line.split()
    if len(parts) >= 4 and parts[3].startswith("b/"):
        return parts[3][2:]
    if len(parts) >= 3 and parts[2].startswith("a/"):
        return parts[2][2:]
    return line.removeprefix("diff --git ").strip()


def _parse_unified_diff(
    text: str,
    *,
    stat_overrides: dict[str, tuple[int, int]] | None = None,
) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    total_lines = 0

    def finalize() -> None:
        nonlocal current
        if current and current.get("lines"):
            path = current["path"]
            add = int(current.get("add") or 0)
            delete = int(current.get("del") or 0)
            if stat_overrides and path in stat_overrides:
                add, delete = stat_overrides[path]
            sections.append(
                {
                    "path": path,
                    "add": add,
                    "del": delete,
                    "lines": current["lines"],
                    "truncated": bool(current.get("_truncated")),
                }
            )
        current = None

    def start_section(path: str) -> None:
        nonlocal current
        finalize()
        current = {"path": path, "add": 0, "del": 0, "lines": []}

    def push_line(row: dict[str, str]) -> None:
        nonlocal total_lines
        if current is None:
            return
        if total_lines >= MAX_DIFF_LINES:
            if not current.get("_truncated"):
                current["lines"].append({"type": "c", "ln": "", "text": "… diff truncated …"})
                current["_truncated"] = True
            return
        current["lines"].append(row)
        total_lines += 1
        if row["type"] == "a":
            current["add"] += 1
        elif row["type"] == "d":
            current["del"] += 1

    for raw in text.splitlines():
        if raw.startswith("diff --git "):
            start_section(_path_from_diff_git(raw))
            continue
        if raw.startswith("+++ b/"):
            path = raw[6:]
            if current is None or current["path"] != path:
                start_section(path)
            continue
        if raw.startswith("--- "):
            continue
        if current is None:
            continue
        if raw.startswith("@@"):
            push_line({"type": "h", "ln": "", "text": raw[:MAX_DIFF_LINE_CHARS]})
            continue
        if raw.startswith("+"):
            push_line({"type": "a", "ln": "", "text": raw[1:][:MAX_DIFF_LINE_CHARS]})
            continue
        if raw.startswith("-"):
            push_line({"type": "d", "ln": "", "text": raw[1:][:MAX_DIFF_LINE_CHARS]})
            continue
        if raw.startswith(" "):
            push_line({"type": "c", "ln": "", "text": raw[1:][:MAX_DIFF_LINE_CHARS]})

    finalize()
    return sections


_PYTEST_ROW = re.compile(
    r"^(?P<name>\S+(?:::\S+)?)\s+(?P<status>PASSED|FAILED|SKIPPED|ERROR|XFAIL|XPASS)",
    re.IGNORECASE,
)
_PYTEST_SUMMARY = re.compile(
    r"(?P<passed>\d+)\s+passed(?:,\s*(?P<failed>\d+)\s+failed)?(?:,\s*(?P<skipped>\d+)\s+skipped)?",
    re.IGNORECASE,
)
_VITEST_SUMMARY = re.compile(
    r"Tests?\s+(?P<passed>\d+)\s+passed(?:,\s*(?P<failed>\d+)\s+failed)?",
    re.IGNORECASE,
)
_VALID_TEST_NAME = re.compile(r"^[\w./:-]+(?:::[\w]+)?$")


def _looks_like_test_output(text: str) -> bool:
    lower = text.lower()
    return (
        "test session starts" in lower
        or "::test_" in lower
        or _PYTEST_SUMMARY.search(text) is not None
        or _VITEST_SUMMARY.search(text) is not None
    )


def extract_pytest_sections_from_stream_json(text: str) -> list[tuple[str, str]]:
    """Pull pytest/npm output from Claude stream-json Bash tool_result blocks."""
    sections: list[tuple[str, str]] = []
    pending_commands: dict[str, str] = {}

    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue

        if payload.get("type") == "assistant":
            message = payload.get("message") or {}
            for block in message.get("content") or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use" and block.get("name") == "Bash":
                    tool_input = block.get("input") or {}
                    command = str(tool_input.get("command") or "").strip()
                    tool_id = block.get("id")
                    if tool_id and command:
                        pending_commands[tool_id] = command

        if payload.get("type") != "user":
            continue
        message = payload.get("message") or {}
        for block in message.get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            content = block.get("content")
            if isinstance(content, list):
                content = "\n".join(str(part) for part in content)
            content = str(content or "")
            if not _looks_like_test_output(content):
                continue
            tool_id = block.get("tool_use_id") or ""
            command = pending_commands.get(tool_id, "")
            sections.append((command or "pytest", content))

    return sections


def _pick_best_pytest_section(sections: list[tuple[str, str]]) -> tuple[str, str] | None:
    if not sections:
        return None
    best: tuple[str, str] | None = None
    best_score = -1
    for command, content in sections:
        match = _PYTEST_SUMMARY.search(content) or _VITEST_SUMMARY.search(content)
        score = int(match.group("passed")) if match else 0
        if score >= best_score:
            best_score = score
            best = (command, content)
    return best


def extract_test_source_from_run(run: AgentRun, *, log_text: str = "") -> tuple[str, str]:
    """Return (output text, command) for test artifact parsing."""
    combined = "\n".join(part for part in (run.stdout, run.stderr, log_text) if part)
    if combined.strip().startswith("{") or '"type":"user"' in combined[:500]:
        sections = extract_pytest_sections_from_stream_json(combined)
        picked = _pick_best_pytest_section(sections)
        if picked:
            return picked[1], picked[0]

    if log_text.strip():
        return log_text, "pytest"

    return combined, run.command or "pytest"


def _test_artifact_is_valid(content: dict[str, Any]) -> bool:
    cmd = str(content.get("cmd") or "")
    if "claude" in cmd and "--output-format" in cmd:
        return False
    for row in content.get("rows") or []:
        name = str(row.get("name") or "")
        if name.startswith("{") or len(name) > 180:
            return False
        if name and not _VALID_TEST_NAME.match(name):
            return False
    return bool(content.get("summary"))


def _log_text_for_run(session: Session, run_id: str) -> str:
    artifact = session.exec(
        select(Artifact).where(Artifact.run_id == run_id, Artifact.kind == "log")
    ).first()
    if not artifact:
        return ""
    body = json.loads(artifact.content_json or "{}")
    lines = body.get("lines") or []
    return "\n".join(str(line.get("text") or "") for line in lines if line.get("tag") == "OUT")


def parse_test_output(text: str, *, cmd: str = "") -> dict[str, Any] | None:
    """Parse pytest/vitest-style output into a test artifact payload."""
    if not text or not text.strip():
        return None

    rows: list[dict[str, str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            continue
        match = _PYTEST_ROW.search(stripped)
        if not match:
            continue
        name = match.group("name")
        if not _VALID_TEST_NAME.match(name):
            continue
        status = match.group("status").lower()
        if status in {"passed", "xpass"}:
            norm = "pass"
        elif status in {"skipped", "xfail"}:
            norm = "skip"
        else:
            norm = "fail"
        msg = ""
        if norm == "fail" and " - " in line:
            msg = line.split(" - ", 1)[1].strip()[:500]
        rows.append(
            {
                "name": match.group("name"),
                "status": norm,
                "dur": "",
                "msg": msg,
            }
        )

    summary = ""
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        py_sum = _PYTEST_SUMMARY.search(stripped)
        vit_sum = _VITEST_SUMMARY.search(stripped)
        if py_sum:
            passed = py_sum.group("passed")
            failed = py_sum.group("failed") or "0"
            skipped = py_sum.group("skipped") or "0"
            summary = f"{passed} passed · {failed} failed · {skipped} skipped"
            break
        if vit_sum:
            passed = vit_sum.group("passed")
            failed = vit_sum.group("failed") or "0"
            summary = f"{passed} passed · {failed} failed"
            break

    if not rows and not summary:
        return None

    display_cmd = cmd or "pytest"
    if "claude" in display_cmd and "--output-format" in display_cmd:
        display_cmd = "pytest"

    if not summary:
        passed = sum(1 for row in rows if row["status"] == "pass")
        failed = sum(1 for row in rows if row["status"] == "fail")
        skipped = sum(1 for row in rows if row["status"] == "skip")
        summary = f"{passed} passed · {failed} failed · {skipped} skipped"

    return {
        "summary": summary,
        "cmd": display_cmd[:200],
        "rows": rows[:200],
    }


def _build_test_artifact(session: Session, run: AgentRun) -> dict[str, Any] | None:
    log_text = _log_text_for_run(session, run.id)
    source_text, source_cmd = extract_test_source_from_run(run, log_text=log_text)
    return parse_test_output(source_text, cmd=source_cmd[:200])


def _upsert_artifact(
    session: Session,
    *,
    ticket_id: str,
    run_id: str | None,
    kind: str,
    title: str,
    content: dict[str, Any],
) -> Artifact:
    with _artifact_upsert_lock:
        existing = session.exec(
            select(Artifact).where(Artifact.ticket_id == ticket_id, Artifact.kind == kind)
        ).first()
        payload = json.dumps(content)
        if existing:
            existing.run_id = run_id or existing.run_id
            existing.title = title
            existing.content_json = payload
            existing.created_at = datetime.now(timezone.utc)
            session.add(existing)
            session.commit()
            persisted = session.get(Artifact, existing.id)
            return persisted or existing

        artifact = Artifact(
            ticket_id=ticket_id,
            run_id=run_id,
            kind=kind,
            title=title,
            content_json=payload,
        )
        session.add(artifact)
        session.commit()
        persisted = session.get(Artifact, artifact.id)
        return persisted or artifact


BLOCKING_ISSUE_INLINE_LIMIT = 200


def record_blocking_issue(
    session: Session,
    ticket: Ticket,
    *,
    run_id: str | None,
    stage_key: str,
    message: str,
) -> str:
    """Cap what lands in ticket.blocking_issues, which the workflow pane
    renders verbatim — raw agent/gate output over the inline limit is filed
    as an error artifact for the Errors tab instead, leaving a short pointer
    here so the pane stays readable.
    """
    message = message or ""
    if len(message) <= BLOCKING_ISSUE_INLINE_LIMIT:
        return message
    _upsert_artifact(
        session,
        ticket_id=ticket.id,
        run_id=run_id,
        kind="error",
        title=f"Stage blocked — {stage_key}" if stage_key else "Stage blocked",
        content={
            "message": message,
            "run_code": "",
            "agent_id": "",
            "stage_key": stage_key,
            "command": "",
        },
    )
    pointer = f"Stage '{stage_key}'" if stage_key else "This stage"
    return f"{pointer} hit a blocking issue — see the Errors tab for details."


def refresh_execution_artifacts(
    session: Session,
    *,
    ticket: Ticket,
    run: AgentRun,
    workspace: Workspace,
) -> None:
    """Update diff/test artifacts after an agent run completes."""
    diff = capture_git_diff(workspace)
    if diff and diff.get("sections"):
        _upsert_artifact(
            session,
            ticket_id=ticket.id,
            run_id=run.id,
            kind="diff",
            title=str(diff.get("file") or "git diff"),
            content=diff,
        )

    test_stages = {"testing", "test_break", "test_design"}
    if run.stage_key in test_stages or run.agent_id in {
        "static_qa",
        "test_breaker",
        "test_designer",
    }:
        tests = _build_test_artifact(session, run)
        if tests:
            _upsert_artifact(
                session,
                ticket_id=ticket.id,
                run_id=run.id,
                kind="test",
                title=tests.get("summary", "test results"),
                content=tests,
            )


def _diff_artifact_is_valid(content: dict[str, Any]) -> bool:
    sections = content.get("sections")
    return isinstance(sections, list) and len(sections) > 0


def ensure_diff_artifact(
    session: Session,
    *,
    ticket: Ticket,
    workspace: Workspace,
) -> dict[str, Any] | None:
    """Return stored diff or capture from git on demand."""
    existing = session.exec(
        select(Artifact).where(Artifact.ticket_id == ticket.id, Artifact.kind == "diff")
    ).first()
    if existing:
        stored = json.loads(existing.content_json or "{}")
        if _diff_artifact_is_valid(stored):
            return stored

    diff = capture_git_diff(workspace)
    if diff and diff.get("sections"):
        _upsert_artifact(
            session,
            ticket_id=ticket.id,
            run_id=None,
            kind="diff",
            title=str(diff.get("file") or "git diff"),
            content=diff,
        )
    return diff


def ensure_test_artifact(
    session: Session,
    *,
    ticket: Ticket,
) -> dict[str, Any] | None:
    """Return stored test artifact or derive from the latest QA run output."""
    existing = session.exec(
        select(Artifact).where(Artifact.ticket_id == ticket.id, Artifact.kind == "test")
    ).first()
    if existing:
        stored = json.loads(existing.content_json or "{}")
        if _test_artifact_is_valid(stored):
            return stored

    runs = session.exec(
        select(AgentRun).where(AgentRun.ticket_id == ticket.id).order_by(AgentRun.created_at.desc())
    ).all()
    for run in runs:
        if run.stage_key not in {"testing", "test_break", "test_design"} and run.agent_id not in {
            "static_qa",
            "test_breaker",
            "test_designer",
        }:
            continue
        tests = _build_test_artifact(session, run)
        if tests:
            _upsert_artifact(
                session,
                ticket_id=ticket.id,
                run_id=run.id,
                kind="test",
                title=tests.get("summary", "test results"),
                content=tests,
            )
            return tests
    return None
