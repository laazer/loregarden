"""Parse the structured stage-report JSON block agents emit at the end of a run.

See agent_context/agents/common_assets/workflow_enforcement_v1.md — "STAGE REPORT CONTRACT".
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from loregarden.services.usage_limits import detect_usage_limit
from pydantic import BaseModel, ConfigDict, Field, ValidationError

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
    #: The acceptance criteria this stage says are not met. The contract has
    #: told agents to supply these since the reject-only-for-an-unmet-criterion
    #: rule landed, and nothing read them — an agent that named its evidence had
    #: it dropped on the floor, and the stage it rerouted to received the
    #: complaint without the grounds (lg-workflow-integrity-205).
    unmet_criteria: list[str] = field(default_factory=list)


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


class _ReportPayload(BaseModel):
    """The sentinel block as an agent actually writes it.

    Modelled rather than shape-checked by hand: agents compose this JSON
    themselves, so it is a foreign payload, and the `isinstance` chain this
    replaces is the pattern the organization gate exists to stop. Pydantic's own
    coercion does the work — no hand-written type tests.

    `extra="ignore"` absorbs fields we do not read, which is what lets the
    criteria below be parsed separately without this model rejecting them.
    """

    model_config = ConfigDict(extra="ignore")

    status: str = ""
    confidence: float = 0.0
    reroute_to_stage: str | None = None
    reroute_context: str = ""


class _CriteriaPayload(BaseModel):
    """`unmet_criteria` alone, parsed apart from the verdict on purpose.

    A criterion list an agent got wrong must not cost the report its status —
    that verdict is the one thing this parser exists to recover, and failing the
    whole block over a malformed side field would turn a usable rejection into
    no report at all, which fails the stage closed
    (lg-workflow-integrity-205).
    """

    model_config = ConfigDict(extra="ignore")

    unmet_criteria: list[str] = Field(default_factory=list)


def _criteria_of(payload: str) -> list[str]:
    """The criteria an agent named, or none when the field is unusable."""
    try:
        parsed = _CriteriaPayload.model_validate_json(payload)
    except ValidationError:
        return []
    return [item.strip() for item in parsed.unmet_criteria if item.strip()]


def _build_report(payload: str) -> StageReport | None:
    try:
        parsed = _ReportPayload.model_validate_json(payload)
    except ValidationError:
        return None
    if parsed.status not in _VALID_STATUSES:
        return None
    return StageReport(
        status=parsed.status,
        confidence=max(0.0, min(1.0, parsed.confidence)),
        reroute_to_stage=parsed.reroute_to_stage or None,
        reroute_context=parsed.reroute_context or "",
        unmet_criteria=_criteria_of(payload),
    )


# Signatures of an agent run that died from infrastructure — an API/usage-limit,
# overload, or CLI credential error — rather than reporting bad work. The Claude
# CLI ends its stream with a JSON result carrying `terminal_reason`; `api_error`
# is its label for a non-recoverable API failure (usage limit included). The text
# signatures catch the same across adapters and log lines. Kept deliberately
# narrow: a false "transient" only pauses a real rejection (recoverable on
# resume), whereas too broad would mask genuine failures as retryable.
_TRANSIENT_TERMINAL_REASONS = ("api_error",)
_TRANSIENT_SIGNATURES = (
    "usage limit",
    "rate limit",
    "rate_limit",
    "overloaded",
    "quota exceeded",
    "too many requests",
    "service unavailable",
)
# A CLI that cannot authenticate never reaches the model, so it has no opinion on
# the work. Parallel lanes make these common: concurrent `cursor-agent` launches
# contend for the same macOS keychain item and lose their saved login mid-read.
_AUTH_FAILURE_SIGNATURES = (
    "keychain",
    "errsecduplicateitem",
    "agent logout",
    "invalid api key",
    "not logged in",
)


def is_transient_failure(stdout: str, stderr: str) -> bool:
    """True when a *failed* agent run died from infrastructure (API/usage limit,
    overload, or a CLI that could not authenticate) rather than reporting bad work.

    Such a failure is not a rework signal: the stage should pause for a
    human/resume, not reroute upstream (which wastes a cycle and, with the rework
    loop cap, inches toward blocking for the wrong reason). Only meaningful for a
    run whose status is FAILED — a clean run that merely mentions these words in
    its output is not a transient failure, so callers must gate on status first.
    """
    blob = f"{stdout}\n{stderr}".lower()
    if "terminal_reason" in blob and any(r in blob for r in _TRANSIENT_TERMINAL_REASONS):
        return True
    if any(sig in blob for sig in _AUTH_FAILURE_SIGNATURES):
        return True
    if any(sig in blob for sig in _TRANSIENT_SIGNATURES):
        return True
    # The substring list above predates the Claude CLI's current wording, which
    # names the window instead of the word "limit" it was written for: "You've hit
    # your session limit · resets 3pm" matched nothing here, so a quota death was
    # rerouted for rework as though the agent had reported bad work.
    return detect_usage_limit(stdout, stderr) is not None


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
