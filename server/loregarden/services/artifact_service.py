"""Build diff and test artifacts for the IDE truth-layer tabs."""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import threading

from sqlmodel import Session, select

from loregarden.models.domain import AgentRun, Artifact, Ticket, Workspace
from loregarden.services.workspace_paths import resolve_workspace_root

MAX_DIFF_LINES = 400
MAX_DIFF_LINE_CHARS = 500

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


def _parse_unified_diff(text: str) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    total_lines = 0

    def finalize() -> None:
        nonlocal current
        if current and current.get("lines"):
            sections.append(
                {
                    "path": current["path"],
                    "add": current["add"],
                    "del": current["del"],
                    "lines": current["lines"],
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
    if run.stage_key in test_stages or run.agent_id in {"static_qa", "test_breaker", "test_designer"}:
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
        select(AgentRun)
        .where(AgentRun.ticket_id == ticket.id)
        .order_by(AgentRun.created_at.desc())
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
