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

_VALID_STATUSES = {"pass", "fail", "needs_rework"}


@dataclass(frozen=True)
class StageReport:
    status: str
    confidence: float
    reroute_to_stage: str | None
    reroute_context: str


def parse_stage_report(stdout: str) -> StageReport | None:
    """Extract the last sentinel-delimited stage report from agent stdout.

    Returns None if absent or malformed. Never raises — callers must fall back
    to exit-code-only behavior when this returns None.
    """
    if not stdout:
        return None
    matches = _SENTINEL_RE.findall(stdout)
    if not matches:
        return None
    try:
        data = json.loads(matches[-1])
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
