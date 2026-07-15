"""Parse the structured stage-report JSON block agents emit at the end of a run.

See agent_context/agents/common_assets/workflow_enforcement_v1.md — "STAGE REPORT CONTRACT".
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

_SENTINEL_RE = re.compile(
    r"<<<LOREGARDEN_STAGE_REPORT>>>\s*(\{.*?\})\s*<<<END_STAGE_REPORT>>>",
    re.DOTALL,
)

_VALID_STATUSES = {"pass", "fail", "needs_rework", "blocked"}

_SENTINEL = "<<<LOREGARDEN_STAGE_REPORT>>>"


@dataclass(frozen=True)
class StageReport:
    status: str
    confidence: float
    reroute_to_stage: str | None
    reroute_context: str


def _embedded_strings(node: object):
    """Yield every string inside a decoded stream-json line that carries a report."""
    if isinstance(node, str):
        if _SENTINEL in node:
            yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _embedded_strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _embedded_strings(value)


def _candidate_texts(stdout: str):
    """Yield every text the report block could live in, oldest-to-newest.

    The CLI adapters store stdout as raw `--output-format stream-json` lines, so
    the agent's report reaches us JSON-*escaped* (`>>>\\n{\\n  \\"status\\"...`) and
    the sentinel regex can never match the raw buffer. Decoding each line and
    searching the strings inside recovers it. Plain-text stdout (the local and
    LM Studio runners) still matches directly off the raw buffer.
    """
    yield stdout
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{") or _SENTINEL not in line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        yield from _embedded_strings(data)


def parse_stage_report(stdout: str) -> StageReport | None:
    """Extract the last sentinel-delimited stage report from agent stdout.

    Returns None if absent or malformed. Never raises — callers must fall back
    to exit-code-only behavior when this returns None.
    """
    if not stdout or _SENTINEL not in stdout:
        return None
    matches: list[str] = []
    for text in _candidate_texts(stdout):
        matches.extend(_SENTINEL_RE.findall(text))
    for candidate in reversed(matches):
        report = _build_report(candidate)
        if report:
            return report
    return None


def _build_report(payload: str) -> StageReport | None:
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None

    status = data.get("status")
    if status not in _VALID_STATUSES:
        return None

    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    reroute_to_stage = data.get("reroute_to_stage") or None
    if reroute_to_stage is not None and not isinstance(reroute_to_stage, str):
        reroute_to_stage = None

    reroute_context = data.get("reroute_context") or ""
    if not isinstance(reroute_context, str):
        reroute_context = ""

    return StageReport(
        status=status,
        confidence=confidence,
        reroute_to_stage=reroute_to_stage,
        reroute_context=reroute_context,
    )


def stage_report_artifact_content(stage_key: str, report: StageReport) -> dict:
    """Build the `context`-artifact `content` payload for a parsed stage report.

    Shape keeps both the generic {title, rows} the client Context tab already
    renders, plus flat fields the per-stage list matches on `stage_key`.
    """
    return {
        "stage_key": stage_key,
        "status": report.status,
        "confidence": report.confidence,
        "reroute_to_stage": report.reroute_to_stage,
        "reroute_context": report.reroute_context,
        "rows": [
            {"k": "status", "v": report.status},
            {"k": "confidence", "v": f"{report.confidence:.2f}"},
            {"k": "reroute_to_stage", "v": report.reroute_to_stage or "—"},
            {"k": "reroute_context", "v": report.reroute_context or "—"},
        ],
    }
