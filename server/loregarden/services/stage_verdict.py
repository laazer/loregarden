"""One pessimistic StageVerdict from live judgeable signals.

Last-written does not win. A claimed pass cannot override a more pessimistic
judgeable signal. Unjudgeable inputs are not signals. No Session in
``derive_verdict``; ``collect_signals`` is the only composer.
"""

from __future__ import annotations

import importlib.util
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

from loregarden.agents.registry import SPEC_AGENT_ID
from loregarden.models.domain import (
    StageReportStatus,
    StageType,
    Ticket,
    WorkflowStageDef,
)
from loregarden.services.evidence import has_evidence, resolve_head_sha
from loregarden.services.fix_coverage import missing_fix_coverage
from loregarden.services.handoff_store import latest_handoff_doc
from loregarden.services.operation_criteria import pending_operation_criteria
from loregarden.services.stage_report import StageReport
from loregarden.services.studio_routing import is_agentless_stage, took_light_route
from loregarden.services.ticket_workspace_binding import workspace_for_ticket
from loregarden.services.ux_answers import missing_ux_answers
from loregarden.services.workflow_service import resolve_ticket_stages
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlmodel import Session

logger = logging.getLogger(__name__)

#: Rank among judgeable signals. Index is the rank; max wins.
_RANK: tuple[StageReportStatus, ...] = (
    StageReportStatus.PASS,
    StageReportStatus.NEEDS_REWORK,
    StageReportStatus.FAIL,
    StageReportStatus.BLOCKED,
)

_ROUTING_PASS = "pass"
_ROUTING_REJECT = "reject"

_TOOL_REJECT_NAME = "tool:reject"
_UNJUDGEABLE_EVIDENCE_NAME = "evidence:unjudgeable"
_UX_ANSWERS_NAME = "ux_answers missing"
_FIX_COVERAGE_NAME = "fix_coverage missing"
_OPERATION_CRITERIA_NAME = "operation_criteria pending"
# Same path handoff_writer uses. Copied rather than imported: handoff_writer
# imports orchestration_callbacks, which imports this module.
_GATE_MODULE_RELPATH = "ci/scripts/gates/handoff_validation_check.py"


class _HandoffItemStatus(StrEnum):
    """Stored handoff checklist status. Incomplete means anything else."""

    COMPLETE = "complete"


class _HandoffItem(BaseModel):
    """One checklist row from a stored handoff document."""

    model_config = ConfigDict(extra="ignore")

    item_key: str = ""
    status: str = ""
    required: bool = True


class _HandoffEnvelope(BaseModel):
    """The ``handoff`` object inside a stored handoff document."""

    model_config = ConfigDict(extra="ignore")

    from_agent: str = ""
    to_agent: str = ""
    checklist: list[_HandoffItem] = []


class _CatalogItem(BaseModel):
    """One pair-catalog entry from the workspace gate module."""

    model_config = ConfigDict(extra="ignore")

    item_key: str = ""
    required: bool = True


@dataclass(frozen=True)
class VerdictSignal:
    """One judgeable (or unjudgeable-required) input to ``derive_verdict``.

    ``outcome=None`` is unjudgeable-required: omitted from the max among
    judgeable, named on unresolved, and a claimed pass cannot win.
    Producer identity is stamped at collect; it does not affect rank.

    ``item_key`` is the stable name the ledger keys on; ``detail`` is what the
    agent is told to do about it, and only a signal that can say something
    actionable carries one. The two are separate because the ledger needs a name
    that never moves and the agent needs a sentence that names a tool.

    ``is_claim`` marks the run's own reported status, reflected back as a signal
    so it can be out-ranked. It is still ledger-visible, but it is never sent to
    the agent as something to resolve: the agent wrote it, and appending it to
    the rework feedback appended a line under the finding that said nothing.
    """

    source: str
    item_key: str
    outcome: StageReportStatus | None
    producer_agent_id: str = ""
    producer_stage_key: str = ""
    detail: str = ""
    is_claim: bool = False


@dataclass(frozen=True)
class UnresolvedItem:
    """A stable name for a live pessimistic input, and how to resolve it."""

    name: str
    producer_agent_id: str = ""
    producer_stage_key: str = ""
    detail: str = ""
    is_claim: bool = False

    def as_told_to_the_agent(self) -> str:
        """What the sent-back agent reads for this item, or "" when nothing.

        Its own claim says nothing it did not write. Everything else is named,
        and a signal that knows how to be resolved says so instead.
        """
        if self.is_claim:
            return ""
        return self.detail.strip() or self.name


@dataclass(frozen=True)
class StageVerdict:
    """The derived status and the live unresolved names that produced it."""

    status: StageReportStatus
    unresolved: tuple[UnresolvedItem, ...]


def claim_from_tool_outcome(outcome: str) -> StageReportStatus:
    """Map MCP ``pass|reject`` to a claim. Reject is needs_rework, not fail."""
    if outcome == StageReportStatus.PASS:
        return StageReportStatus.PASS
    return StageReportStatus.NEEDS_REWORK


def routing_outcome(verdict: StageVerdict) -> str:
    """The only vocab bridge into ``resolve_next_stage_key`` / ``apply_stage_route``.

    ``pass`` iff the derived status is PASS; everything else is ``reject``.
    Derived fail/needs_rework/blocked must never be threaded as a routing when.
    """
    if verdict.status is StageReportStatus.PASS:
        return _ROUTING_PASS
    return _ROUTING_REJECT


def handoff_expected_for(stage_def: WorkflowStageDef) -> bool:
    """Whether this stage is a finishing agent (not parallel, not agentless).

    Documents the parallel case for tests. A missing handoff row is still not a
    veto; a row that exists is always judged.
    """
    return stage_def.stage_type != StageType.PARALLEL and not is_agentless_stage(stage_def)


def derive_verdict(signals: Sequence[VerdictSignal] | Iterable[VerdictSignal]) -> StageVerdict:
    """Most pessimistic judgeable signal wins. Last-written does not.

    Unjudgeable-required (``outcome=None``) is omitted from the max. A claimed
    pass cannot win against it. Absence of every judgeable signal is not a pass.
    """
    collected = tuple(signals)
    judgeable = tuple(signal for signal in collected if signal.outcome is not None)
    unjudgeable = tuple(signal for signal in collected if signal.outcome is None)
    if not judgeable:
        status = StageReportStatus.NEEDS_REWORK
    else:
        status = max(judgeable, key=lambda signal: _RANK.index(signal.outcome)).outcome
        if status is StageReportStatus.PASS and unjudgeable:
            status = StageReportStatus.NEEDS_REWORK
    producers: dict[str, VerdictSignal] = {}
    for signal in collected:
        if signal.item_key and signal.outcome is not StageReportStatus.PASS:
            producers.setdefault(signal.item_key, signal)
    names = sorted(producers)
    unresolved = (
        ()
        if status is StageReportStatus.PASS
        else tuple(
            UnresolvedItem(
                name=name,
                producer_agent_id=producers[name].producer_agent_id,
                producer_stage_key=producers[name].producer_stage_key,
                detail=producers[name].detail,
                is_claim=producers[name].is_claim,
            )
            for name in names
        )
    )
    return StageVerdict(status=status, unresolved=unresolved)


def collect_signals(
    session: Session,
    ticket: Ticket,
    *,
    report: StageReport | None,
    tool_outcome: StageReportStatus | None,
    stage_def: WorkflowStageDef,
    handoff_expected: bool,
    agent_id: str,
) -> list[VerdictSignal]:
    """Assemble live judgeable signals. The only composer.

    ``handoff_expected`` documents the parallel case; a missing row is never a
    veto, even for a finishing agent. A row that exists is always judged.
    """
    _ = handoff_expected
    signals: list[VerdictSignal] = []
    signals.extend(
        _claimed_signals(report, tool_outcome, agent_id=agent_id, stage_key=stage_def.key)
    )
    signals.extend(_handoff_signals(session, ticket))
    signals.extend(_evidence_signals(session, ticket, stage_def, agent_id=agent_id))
    if _claimed_pass_only(report, tool_outcome):
        signals.extend(
            _folded_pass_held_signals(session, ticket, agent_id, stage_key=stage_def.key)
        )
    return signals


def report_with_verdict(
    report: StageReport,
    verdict: StageVerdict,
    *,
    current_stage: str,
) -> StageReport:
    """Stamp the derived status, and what to do about it, onto a report for persist.

    ``reroute_context`` is what the next turn of the agent reads, so each item
    renders itself for that reader (``as_told_to_the_agent``): its stable name,
    or the sentence naming the tool that resolves it, and nothing at all for the
    agent's own claim reflected back.
    """
    told = "\n".join(
        line for line in (item.as_told_to_the_agent() for item in verdict.unresolved) if line
    )
    existing = (report.reroute_context or "").strip()
    if told and existing:
        context = f"{existing}\n\n{told}"
    elif told:
        context = told
    else:
        context = existing
    reroute_to = report.reroute_to_stage
    claimed_pass = report.status == StageReportStatus.PASS
    if claimed_pass and verdict.status is not StageReportStatus.PASS:
        reroute_to = current_stage
    return replace(
        report,
        status=verdict.status.value,
        reroute_to_stage=reroute_to,
        reroute_context=context,
    )


def unresolved_section_lines(verdict: StageVerdict) -> list[str]:
    """Prompt lines for a live unresolved list, or empty when there is none."""
    if not verdict.unresolved:
        return []
    return [
        "",
        "## Unresolved items",
        *(f"- `{item.name}`" for item in verdict.unresolved),
    ]


def _claimed_signals(
    report: StageReport | None,
    tool_outcome: StageReportStatus | None,
    *,
    agent_id: str,
    stage_key: str,
) -> list[VerdictSignal]:
    signals: list[VerdictSignal] = []
    if report is not None:
        status = _report_status(report)
        item_key = "report:pass" if status is StageReportStatus.PASS else f"report:{status.value}"
        signals.append(
            VerdictSignal(
                source="claimed",
                item_key=item_key,
                outcome=status,
                producer_agent_id=agent_id,
                producer_stage_key=stage_key,
                is_claim=True,
            )
        )
    if tool_outcome is StageReportStatus.PASS:
        signals.append(
            VerdictSignal(
                source="tool",
                item_key="tool:pass",
                outcome=StageReportStatus.PASS,
                producer_agent_id=agent_id,
                producer_stage_key=stage_key,
            )
        )
    elif tool_outcome is not None:
        signals.append(
            VerdictSignal(
                source="tool",
                item_key=_TOOL_REJECT_NAME,
                outcome=StageReportStatus.NEEDS_REWORK,
                producer_agent_id=agent_id,
                producer_stage_key=stage_key,
                is_claim=True,
            )
        )
    return signals


def _report_status(report: StageReport) -> StageReportStatus:
    try:
        return StageReportStatus(report.status)
    except ValueError:
        return StageReportStatus.NEEDS_REWORK


def _claimed_pass_only(report: StageReport | None, tool_outcome: StageReportStatus | None) -> bool:
    """Folded _pass_held predicates emit only when a claimed pass exists.

    A claimed fail/needs_rework/blocked is not upgraded to blocked by them.
    """
    has_pass = (report is not None and report.status == StageReportStatus.PASS) or (
        tool_outcome is StageReportStatus.PASS
    )
    if not has_pass:
        return False
    if report is not None and _report_status(report) is not StageReportStatus.PASS:
        return False
    if tool_outcome is not None and tool_outcome is not StageReportStatus.PASS:
        return False
    return True


def _handoff_signals(session: Session, ticket: Ticket) -> list[VerdictSignal]:
    payload = latest_handoff_doc(session, ticket.id)
    if payload is None:
        return []
    try:
        envelope = _HandoffEnvelope.model_validate(payload.get("handoff"))
    except ValidationError:
        logger.warning("Ignoring malformed handoff document on ticket %s", ticket.external_id)
        return []
    signals: list[VerdictSignal] = []
    present: set[str] = set()
    for item in envelope.checklist:
        if not item.item_key:
            continue
        present.add(item.item_key)
        if not item.required:
            continue
        if item.status == _HandoffItemStatus.COMPLETE:
            continue
        signals.append(
            VerdictSignal(
                source="handoff",
                item_key=f"handoff:{item.item_key} incomplete",
                outcome=StageReportStatus.NEEDS_REWORK,
                producer_agent_id=envelope.from_agent,
                producer_stage_key="",
            )
        )
    catalog_keys = _required_catalog_keys(session, ticket, envelope.from_agent, envelope.to_agent)
    if catalog_keys is None:
        return signals
    for key in catalog_keys:
        if key in present:
            continue
        signals.append(
            VerdictSignal(
                source="handoff",
                item_key=f"handoff:{key} missing",
                outcome=StageReportStatus.NEEDS_REWORK,
                producer_agent_id=envelope.from_agent,
                producer_stage_key="",
            )
        )
    return signals


def _required_catalog_keys(
    session: Session, ticket: Ticket, from_agent: str, to_agent: str
) -> tuple[str, ...] | None:
    """Required catalog keys for this pair, or None if the catalog cannot load."""
    if not from_agent or not to_agent:
        return None
    workspace = workspace_for_ticket(session, ticket)
    if workspace is None or not workspace.repo_path:
        return None
    path = Path(workspace.repo_path) / _GATE_MODULE_RELPATH
    catalogs = _load_pair_catalogs(path)
    if catalogs is None:
        return None
    try:
        entries = catalogs[(from_agent, to_agent)]
    except (KeyError, TypeError):
        return None
    keys: list[str] = []
    for entry in entries:
        try:
            item = _CatalogItem.model_validate(entry)
        except ValidationError:
            continue
        if item.item_key and item.required:
            keys.append(item.item_key)
    return tuple(keys)


def _load_pair_catalogs(path: Path) -> Mapping[Any, Any] | None:
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("_loregarden_handoff_catalog", path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:  # noqa: BLE001
        # py-silent: allow - workspace catalog is optional; unloadable means
        # skip absent-key detection (R3), not a crash of collect_signals
        logger.exception("Could not load handoff pair catalog from %s", path)
        return None
    try:
        catalogs = module.PAIR_CATALOGS
    except AttributeError:
        logger.warning("Handoff catalog module %s has no PAIR_CATALOGS", path)
        return None
    return catalogs


def _evidence_signals(
    session: Session, ticket: Ticket, stage_def: WorkflowStageDef, *, agent_id: str
) -> list[VerdictSignal]:
    required = [kind for kind in (stage_def.required_evidence or []) if kind]
    if not required:
        return []
    _, stages = resolve_ticket_stages(session, ticket)
    if stages and took_light_route(ticket, stages):
        return []
    head = resolve_head_sha(session, ticket)
    if not head:
        return [
            VerdictSignal(
                source="evidence",
                item_key=_UNJUDGEABLE_EVIDENCE_NAME,
                outcome=None,
                producer_agent_id=agent_id,
                producer_stage_key=stage_def.key,
            )
        ]
    missing = [
        kind
        for kind in required
        if not has_evidence(session, ticket, commit_sha=head, evidence_kind=kind)
    ]
    return [
        VerdictSignal(
            source="evidence",
            item_key=f"evidence:{kind} missing",
            outcome=StageReportStatus.NEEDS_REWORK,
            producer_agent_id=agent_id,
            producer_stage_key=stage_def.key,
        )
        for kind in missing
    ]


def _folded_pass_held_signals(
    session: Session, ticket: Ticket, agent_id: str, *, stage_key: str
) -> list[VerdictSignal]:
    signals: list[VerdictSignal] = []
    # Both helpers return the reason as prose naming the tool that resolves it;
    # it travels as `detail` so the sent-back agent is told what to call.
    ux_missing = missing_ux_answers(session, ticket, agent_id=agent_id)
    if ux_missing:
        signals.append(
            VerdictSignal(
                source="ux_answers",
                item_key=_UX_ANSWERS_NAME,
                outcome=StageReportStatus.NEEDS_REWORK,
                producer_agent_id=agent_id,
                producer_stage_key=stage_key,
                detail=ux_missing,
            )
        )
    coverage_missing = missing_fix_coverage(session, ticket, agent_id=agent_id)
    if coverage_missing:
        signals.append(
            VerdictSignal(
                source="fix_coverage",
                item_key=_FIX_COVERAGE_NAME,
                outcome=StageReportStatus.NEEDS_REWORK,
                producer_agent_id=agent_id,
                producer_stage_key=stage_key,
                detail=coverage_missing,
            )
        )
    if agent_id == SPEC_AGENT_ID and pending_operation_criteria(session, ticket):
        signals.append(
            VerdictSignal(
                source="operation_criteria",
                item_key=_OPERATION_CRITERIA_NAME,
                outcome=StageReportStatus.BLOCKED,
                producer_agent_id=agent_id,
                producer_stage_key=stage_key,
            )
        )
    return signals
