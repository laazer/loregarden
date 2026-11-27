"""Build diff and test artifacts for the IDE truth-layer tabs."""

from __future__ import annotations

import json
import logging
import re
import subprocess
import threading
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loregarden.models.domain import (
    AgentRun,
    Artifact,
    ArtifactKind,
    Ticket,
    TicketState,
    Workspace,
)
from loregarden.services.artifact_records import is_system_record
from loregarden.services.block_classification import BLOCKING_POINTER_PHRASE
from loregarden.services.block_settlement import settle_block
from loregarden.services.git_subprocess import run_git
from loregarden.services.log_storage import read_log_lines
from loregarden.services.parent_ref import live_diff_base
from loregarden.services.run_concurrency import find_active_run
from loregarden.services.ticket_state_service import choose
from loregarden.services.ticket_worktree import resolve_ticket_checkout
from loregarden.services.unified_diff import MAX_DIFF_LINE_CHARS, MAX_DIFF_LINES, parse_unified_diff
from loregarden.services.workspace_paths import resolve_workspace_root
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

MAX_UNTRACKED_FILE_BYTES = 512_000
MAX_UNTRACKED_FILES = 20

_artifact_upsert_lock = threading.Lock()


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    # quotepath off: a path with a non-ASCII character is printed as itself,
    # not as a quoted octal escape no file on disk or in a manifest is named.
    return run_git(
        ["-c", "core.quotepath=off", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


#: The first base `git_base_ref` tries, and the one nearly every repository has.
_PREFERRED_BASE = "main"


def git_base_ref(cwd: Path) -> str | None:
    for ref in (_PREFERRED_BASE, "master", "origin/main", "origin/master"):
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
        base_ref = git_base_ref(repo_root)
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


def _untracked_paths(cwd: Path) -> list[str]:
    """Untracked, not ignored paths, exactly as named on disk.

    `-z` because a name may start with a space or hold a newline, and only a
    NUL cannot be part of one.
    """
    proc = _git(cwd, "ls-files", "-z", "--others", "--exclude-standard")
    if proc.returncode != 0:
        logger.warning(
            "untracked files omitted from the diff artifact; git ls-files failed in %s: %s",
            cwd,
            (proc.stderr or "").strip(),
        )
        return []
    return [path for path in (proc.stdout or "").split("\0") if path]


def _untracked_manifest_entries(cwd: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in _untracked_paths(cwd):
        if len(entries) >= MAX_UNTRACKED_FILES:
            break
        full_path = cwd / path
        if not full_path.is_file():
            continue
        try:
            size = full_path.stat().st_size
        except OSError as exc:
            logger.warning(
                "untracked file %s omitted from the diff artifact; stat failed: %s",
                full_path,
                exc,
                exc_info=True,
            )
            continue
        if size > MAX_UNTRACKED_FILE_BYTES:
            entries.append({"path": path, "add": 1, "del": 0})
            continue
        try:
            text = full_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning(
                "untracked file %s omitted from the diff artifact; read failed: %s",
                full_path,
                exc,
                exc_info=True,
            )
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

    # Not stripped: a file may be named with a leading or trailing space.
    if not file_path.strip():
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
        sections = parse_unified_diff(patch.stdout or "", stat_overrides=stat_override)

    if not sections and ctx.mode in {"unstaged", "uncommitted"}:
        untracked = _untracked_file_section(ctx.git_cwd, file_path)
        sections = [untracked] if untracked else []

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


def _untracked_file_section(cwd: Path, path: str) -> dict[str, Any] | None:
    """The diff section of one untracked file: every line added.

    Only a path git lists as untracked is read, so a request cannot name a
    file outside the checkout or one it ignores.
    """
    if path not in _untracked_paths(cwd):
        return None
    full_path = cwd / path
    if not full_path.is_file():
        return None
    try:
        size = full_path.stat().st_size
    except OSError as exc:
        logger.warning(
            "untracked file %s omitted from the diff artifact; stat failed: %s",
            full_path,
            exc,
            exc_info=True,
        )
        return None
    if size > MAX_UNTRACKED_FILE_BYTES:
        omitted = f"… file omitted ({size:,} bytes exceeds review limit) …"
        return {
            "path": path,
            "add": 1,
            "del": 0,
            "lines": [{"type": "c", "ln": "", "text": omitted}],
        }

    try:
        text = full_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning(
            "untracked file %s omitted from the diff artifact; read failed: %s",
            full_path,
            exc,
            exc_info=True,
        )
        return None

    rows = text.splitlines()
    lines = [
        {"type": "a", "ln": str(line_no), "text": line[:MAX_DIFF_LINE_CHARS]}
        for line_no, line in enumerate(rows[:MAX_DIFF_LINES], start=1)
    ]
    if len(rows) > MAX_DIFF_LINES:
        lines.append({"type": "c", "ln": "", "text": "… diff truncated …"})
    if not lines:
        return None
    return {"path": path, "add": len(rows), "del": 0, "lines": lines}


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
    sections = parse_unified_diff(patch.stdout or "", stat_overrides=stat_map)

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


def _stat_and_patch(cwd: Path, ref: str | None) -> tuple[str, str] | None:
    """`git diff --stat` and `git diff` against `ref` (the index when None), in one call.

    `--stat --patch` prints the stat block, a blank line, then the same patch
    the separate call would — and every patch starts on a `diff --git` line,
    which no stat line can (each begins with a space) (lg-build-verification-847).

    None when git could not diff — `ref` names no revision, most often — which
    the caller must tell apart from an empty diff: only the first sends it
    looking for another base.
    """
    proc = _git(cwd, "diff", "--stat", "--patch", *([ref, "--"] if ref else []))
    if proc.returncode != 0:
        return None
    text = proc.stdout or ""
    marker = text.find("\ndiff --git ")
    if marker < 0:
        return text, ""
    return text[: marker + 1], text[marker + 1 :]


def capture_git_diff(
    workspace: Workspace, repo_root: Path | None = None, *, against: str | None = None
) -> dict[str, Any] | None:
    """Return the diff artifact payload from `repo_root`, or the shared checkout.

    Ticket-scoped callers pass the ticket's worktree: its changes are not in the
    shared checkout at all, so the Diff tab would show whatever else happened to
    be dirty there — or nothing.

    `against` is a SHA the caller already resolved. When set, that string is the
    only base: git failure or an empty stat returns None, with no fallback.
    """
    cwd = repo_root or resolve_workspace_root(workspace)
    if not (cwd / ".git").exists():
        return None

    if against:
        diffed = _stat_and_patch(cwd, against)
        if diffed is None or not diffed[0].strip():
            return None
        stat_text, patch_text = diffed
        diff_ref = against
    else:
        # The usual base first, without resolving it separately: a diff against a
        # `main` that exists is the answer, and only when it is not does the
        # candidate search run, exactly as it always did.
        diff_ref = _PREFERRED_BASE
        diffed = _stat_and_patch(cwd, diff_ref)
        if diffed is None:
            base = git_base_ref(cwd)
            diff_ref = base if base else "HEAD"
            # A `main` that resolves but would not diff fails the same way twice.
            if diff_ref != _PREFERRED_BASE:
                diffed = _stat_and_patch(cwd, diff_ref)
        stat_text, patch_text = diffed or ("", "")
        if not stat_text.strip():
            diff_ref = "working tree"
            stat_text, patch_text = _stat_and_patch(cwd, None) or ("", "")
        if not stat_text.strip():
            return None

    stat_lines = [line for line in stat_text.splitlines() if line.strip()]
    summary = stat_lines[-1] if stat_lines else ""
    primary_file = stat_lines[0].split("|", 1)[0].strip() if stat_lines else "changes"

    add_match = re.search(r"(\d+)\s+insertion", summary)
    del_match = re.search(r"(\d+)\s+deletion", summary)
    add = f"+{add_match.group(1)}" if add_match else "+0"
    delete = f"−{del_match.group(1)}" if del_match else "−0"

    sections = parse_unified_diff(patch_text)

    return {
        "file": primary_file,
        "add": add,
        "del": delete,
        "files": summary,
        "range": diff_ref,
        "sections": sections,
    }


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


def looks_like_test_output(text: str) -> bool:
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
            # silent-ok: a run log interleaves pretty-printed JSON from tool
            # output, whose lines are not standalone objects
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
            if not looks_like_test_output(content):
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


def load_run_log(session: Session, run_id: str) -> dict[str, Any] | None:
    """The rendered `{lines, live}` log body for a run, or None if it has none.

    This is the capped artifact the log streamer maintains — not `AgentRun.stdout`,
    which holds the raw multi-megabyte stream-json transcript.
    """
    artifact = session.exec(
        select(Artifact).where(Artifact.run_id == run_id, Artifact.kind == "log")
    ).first()
    if not artifact:
        return None
    body = json.loads(artifact.content_json or "{}")
    if not isinstance(body, dict):
        return None
    # Lines live in `run_log_lines` for runs written after
    # lg-workflow-integrity-687, and inline for everything before it.
    body["lines"] = read_log_lines(session, run_id, body)
    return body


def _log_text_for_run(session: Session, run_id: str) -> str:
    body = load_run_log(session, run_id)
    if not body:
        return ""
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


def block_ticket_for_unresolved_blocker(
    session: Session,
    ticket: Ticket,
    *,
    entry: str,
    stage_key: str = "",
) -> str:
    """Block a ticket because an agent recorded a blocker it could not resolve.

    Ticket-level only: the state and the blocking issue, not the orchestration.
    Two reasons. `append_checkpoint`'s `run_id` is a filename slug — agents pass
    arbitrary strings, and this session's own checkpoints used
    `manual-2026-09-02-terminal-harness` — so there is often no orchestration to
    resolve, and guessing one to finish would end the wrong run. And ending a
    run is the orchestrator's decision; recording that the work cannot proceed
    is not the same act.

    Exists because documenting a blocker and *being* blocked were the same
    action (430). An agent that hit an unresolvable problem appended a
    checkpoint, which satisfied its "checkpoint logged" checklist item, and
    completed the stage 4/4 — so the more honestly it recorded the blocker, the
    more completely it discharged its obligations.
    """
    ticket.blocking_issues = record_blocking_issue(
        session,
        ticket,
        run_id=None,
        stage_key=stage_key,
        message=entry,
    )
    # `choose`, not a derived transition: an agent said this cannot proceed.
    choose(session, ticket, TicketState.BLOCKED, actor="agent", emit=True)
    session.add(ticket)
    session.commit()
    # Ticket-level, so there is no run to spend a repair turn on — the same
    # reason this function does not end one. While the agent's run is still
    # going, its own completion settles this block with its orchestration in
    # hand, and may offer a repair; settling here first recorded "outside an
    # orchestration run" for a block that had one. With no run in flight it is
    # settled now, so the block leaves with a kind and a recorded reason (802).
    if find_active_run(session, ticket.id) is None:
        settle_block(session, ticket, stage_key=stage_key, message=entry)
    return ticket.blocking_issues


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
    as an error artifact for the Timeline tab instead, leaving its opening
    words and a pointer here so the pane stays readable.

    The opening words stay because the pointer alone names no cause: "Stage
    'spec' hit a blocking issue — see the Timeline tab" was all the board said
    about lg-milestone-that-1424 and -1431, and the operator had to open each.

    ``run_id`` is an ``agent_runs`` id or None. An orchestration run is not an
    agent run: passing one here files an artifact no run-scoped query can find,
    and leaves a reference to a row that does not exist.
    """
    message = message or ""
    if len(message) <= BLOCKING_ISSUE_INLINE_LIMIT:
        return message
    _upsert_artifact(
        session,
        ticket_id=ticket.id,
        run_id=run_id,
        kind=ArtifactKind.ERROR,
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
    tail = f" … ({pointer} {BLOCKING_POINTER_PHRASE} for the rest.)"
    return _opening(message, BLOCKING_ISSUE_INLINE_LIMIT - len(tail)) + tail


def _opening(message: str, budget: int) -> str:
    """The message's first ``budget`` characters, whitespace collapsed, cut at a word."""
    flat = " ".join(message.split())
    budget = max(0, budget)
    if len(flat) <= budget:
        return flat
    cut = flat[:budget]
    space = cut.rfind(" ")
    return cut[:space] if space > budget // 2 else cut


@dataclass(frozen=True)
class _TicketDiffPlan:
    """How to capture this ticket's diff: SHA, omit, or skip.

    skip is not against=None. None means no parent (vs-main is correct); skip
    means a named parent has no live base (vs-main is forbidden).
    """

    path: Path
    against: str | None
    skip: bool


def _ticket_diff_plan(
    session: Session,
    *,
    ticket: Ticket,
    workspace: Workspace,
) -> _TicketDiffPlan:
    """Resolve path + against + skip. The only live_diff_base caller."""
    checkout = resolve_ticket_checkout(session, ticket, workspace)
    if checkout.parent_branch:
        sha = live_diff_base(checkout.path, checkout.parent_branch)
        if sha is None:
            return _TicketDiffPlan(path=checkout.path, against=None, skip=True)
        return _TicketDiffPlan(path=checkout.path, against=sha, skip=False)
    return _TicketDiffPlan(path=checkout.path, against=None, skip=False)


def _capture_from_plan(workspace: Workspace, plan: _TicketDiffPlan) -> dict[str, Any] | None:
    """The only capture_git_diff caller: skip, against=plan.against, or omit."""
    if plan.skip:
        return None
    if plan.against:
        return capture_git_diff(workspace, plan.path, against=plan.against)
    return capture_git_diff(workspace, plan.path)


def _capture_ticket_diff(
    session: Session,
    *,
    ticket: Ticket,
    workspace: Workspace,
) -> dict[str, Any] | None:
    """Thin wrapper for refresh: plan, then capture or skip."""
    return _capture_from_plan(
        workspace, _ticket_diff_plan(session, ticket=ticket, workspace=workspace)
    )


def refresh_execution_artifacts(
    session: Session,
    *,
    ticket: Ticket,
    run: AgentRun,
    workspace: Workspace,
) -> None:
    """Update diff/test artifacts after an agent run completes."""
    diff = _capture_ticket_diff(session, ticket=ticket, workspace=workspace)
    if diff and diff.get("sections"):
        _upsert_artifact(
            session,
            ticket_id=ticket.id,
            run_id=run.id,
            kind=ArtifactKind.DIFF,
            title=str(diff.get("file") or "git diff"),
            content=diff,
        )

    test_stages = {"testing", "test-break", "test-design"}
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
                kind=ArtifactKind.TEST,
                title=tests.get("summary", "test results"),
                content=tests,
            )


def _diff_artifact_is_valid(content: dict[str, Any]) -> bool:
    sections = content.get("sections")
    return isinstance(sections, list) and len(sections) > 0


#: Diffs a page read captured because no stored one still described the work,
#: keyed as the stored row is: the ticket and the base it was taken against. A
#: read stores no row (the run that changes the work does,
#: `refresh_execution_artifacts`), and without this each load of such a ticket
#: ran git again: on live, 2,815 of 3,371 tickets had no reusable row, and a
#: capture takes 0.1-0.6s.
_read_captures: OrderedDict[tuple[str, str | None], dict[str, Any]] = OrderedDict()
_READ_CAPTURE_LIMIT = 256
_read_captures_lock = threading.Lock()


def _stored_diff(session: Session, ticket: Ticket, plan: _TicketDiffPlan) -> dict[str, Any] | None:
    """The stored diff, while it still describes the work.

    Skip keeps a valid stored row; a SHA reuses only when stored.range equals
    that SHA; no parent keeps today's sections.
    """
    existing = session.exec(
        select(Artifact).where(Artifact.ticket_id == ticket.id, Artifact.kind == ArtifactKind.DIFF)
    ).first()
    if existing is None:
        return None
    stored = json.loads(existing.content_json or "{}")
    if _diff_artifact_is_valid(stored) and (
        plan.skip or plan.against is None or stored.get("range") == plan.against
    ):
        return stored
    return None


def diff_for_read(
    session: Session,
    *,
    ticket: Ticket,
    workspace: Workspace,
) -> dict[str, Any] | None:
    """The ticket's diff, for a read: writes nothing.

    The stored diff while it describes the work, else one captured from git
    now and remembered in this process under the stored row's key, so the
    next read reuses it as it would have reused the row.
    """
    plan = _ticket_diff_plan(session, ticket=ticket, workspace=workspace)
    stored = _stored_diff(session, ticket, plan)
    if stored is not None or plan.skip:
        return stored
    key = (ticket.id, plan.against)
    with _read_captures_lock:
        if key in _read_captures:
            _read_captures.move_to_end(key)
            return _read_captures[key]
    diff = _capture_from_plan(workspace, plan)
    if diff and diff.get("sections"):
        with _read_captures_lock:
            _read_captures[key] = diff
            while len(_read_captures) > _READ_CAPTURE_LIMIT:
                _read_captures.popitem(last=False)
    return diff


def ensure_diff_artifact(
    session: Session,
    *,
    ticket: Ticket,
    workspace: Workspace,
) -> dict[str, Any] | None:
    """Return the stored diff, or capture one from git and store it."""
    plan = _ticket_diff_plan(session, ticket=ticket, workspace=workspace)
    stored = _stored_diff(session, ticket, plan)
    if stored is not None or plan.skip:
        return stored

    diff = _capture_from_plan(workspace, plan)
    if diff and diff.get("sections"):
        _upsert_artifact(
            session,
            ticket_id=ticket.id,
            run_id=None,
            kind=ArtifactKind.DIFF,
            title=str(diff.get("file") or "git diff"),
            content=diff,
        )
    return diff


def _stored_tests(session: Session, ticket: Ticket) -> dict[str, Any] | None:
    existing = session.exec(
        select(Artifact).where(Artifact.ticket_id == ticket.id, Artifact.kind == ArtifactKind.TEST)
    ).first()
    if existing is None:
        return None
    stored = json.loads(existing.content_json or "{}")
    return stored if _test_artifact_is_valid(stored) else None


def _tests_from_runs(session: Session, ticket: Ticket) -> tuple[AgentRun, dict[str, Any]] | None:
    """The latest test-stage run's results, derived from its output."""
    runs = session.exec(
        select(AgentRun).where(AgentRun.ticket_id == ticket.id).order_by(AgentRun.created_at.desc())
    ).all()
    for run in runs:
        if run.stage_key not in {"testing", "test-break", "test-design"} and run.agent_id not in {
            "static_qa",
            "test_breaker",
            "test_designer",
        }:
            continue
        tests = _build_test_artifact(session, run)
        if tests:
            return run, tests
    return None


def tests_for_read(session: Session, *, ticket: Ticket) -> dict[str, Any] | None:
    """The ticket's test results, for a read: stored, or derived from the latest
    test-stage run without storing them."""
    stored = _stored_tests(session, ticket)
    if stored is not None:
        return stored
    derived = _tests_from_runs(session, ticket)
    return derived[1] if derived else None


def ensure_test_artifact(
    session: Session,
    *,
    ticket: Ticket,
) -> dict[str, Any] | None:
    """Return stored test artifact or derive from the latest QA run output."""
    stored = _stored_tests(session, ticket)
    if stored is not None:
        return stored
    derived = _tests_from_runs(session, ticket)
    if derived is None:
        return None
    run, tests = derived
    _upsert_artifact(
        session,
        ticket_id=ticket.id,
        run_id=run.id,
        kind=ArtifactKind.TEST,
        title=tests.get("summary", "test results"),
        content=tests,
    )
    return tests


def list_ticket_artifacts(
    session: Session,
    ticket_id: str,
    *,
    limit: int = 200,
) -> dict[str, Any]:
    """Chronological artifact feed for the Artifacts tab (newest first).

    The grouped ticket.artifacts payload only surfaces known kinds (diff/log/test/
    context/error/pr). Agents — especially local models — attach arbitrary kinds
    (`test_spec`, `source_analysis`, …) that never appear there. This list is the
    raw row feed so operators can watch attachments land during a run.

    Each row says which stage it belongs to when that is known — from its run,
    else from a `stage_key` its content names — and whether it is the platform's
    own bookkeeping (`system`), so the reader can put outputs on the timeline and
    hide machinery without guessing from titles.
    """
    # Only the two run columns the feed reads. Joining the whole AgentRun pulled
    # every run's stdout/stderr once per artifact row: 274 MB for one 48-run
    # ticket's 200 rows, and 4-16 s per request on a feed polled every 2 s.
    rows = session.exec(
        select(Artifact, AgentRun.run_code, AgentRun.stage_key)
        .join(AgentRun, Artifact.run_id == AgentRun.id, isouter=True)
        .where(Artifact.ticket_id == ticket_id)
        .order_by(Artifact.created_at.desc())
        .limit(limit)
    ).all()
    items: list[dict[str, Any]] = []
    for art, run_code, run_stage_key in rows:
        raw = art.content_json or "{}"
        try:
            content = json.loads(raw)
        except json.JSONDecodeError:
            # silent-ok: unparseable content is handed to the caller verbatim under _raw
            content = {"_raw": raw}
        items.append(
            {
                "id": art.id,
                "kind": art.kind,
                "title": art.title,
                "run_id": art.run_id,
                "stage_key": run_stage_key or _content_stage_key(content),
                "system": is_system_record(kind=art.kind, title=art.title, run_code=run_code),
                "evidence_kind": art.evidence_kind,
                "commit_sha": art.commit_sha,
                "created_at": art.created_at.isoformat() if art.created_at else None,
                "content_bytes": len(raw.encode("utf-8")),
                "content": content,
            }
        )
    return {"items": items, "total": len(items)}


class _StageTagged(BaseModel):
    """The one field of an artifact's free-form content the feed reads."""

    model_config = ConfigDict(extra="ignore")

    stage_key: str | None = None


def _content_stage_key(content: Any) -> str | None:
    try:
        return _StageTagged.model_validate(content).stage_key or None
    except ValidationError:
        # silent-ok: content is agent-shaped JSON; a non-object, or a stage_key that is not a string, names no stage and the row is placed by time instead
        return None
