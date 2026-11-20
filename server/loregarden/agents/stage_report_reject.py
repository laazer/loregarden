"""The stage-report `Stop` hook's reject check: a reject stands on a test that fails.

`complete_stage` refuses a judging agent's reject whose named tests all pass
(`finding_bar.refuse_unshown_reject`), while the agent can still fix its test.
A builtin-driver run reports through the sentinel instead, so before this its
reject was only checked once the run had ended, and quietly settled as a pass.
The hook runs the same check while the turn is open.

It reads the database read-only (`stage_report_criteria.ledger`), so it can
refuse a stop but records nothing: completion's admission runs the tests again
and records them. They are run under the agent's own lease
(`named_tests.run_named_tests_in_turn`), as the agent's own shell ran them.

A recheck round is left to completion: its reject stands on the prior
findings it answers, and its new findings never block (`recheck_round`).
"""

from __future__ import annotations

from pathlib import Path

from loregarden.agents.stage_report_criteria import THEN, ledger
from loregarden.models.domain import StageReportStatus
from loregarden.services.finding_bar import is_judging, reject_not_shown_message, unadmitted_reason
from loregarden.services.recheck_round import prior_findings
from loregarden.services.reject_tests import check_reject_tests, standing
from loregarden.services.stage_report import StageReport
from loregarden.services.workflow_service import resolve_ticket_stages


def unshown_reject_refusal(db_path: Path, run_id: str, report: StageReport) -> str | None:
    """Why run ``run_id``'s reject cannot end the turn, or None when it can.

    Only a reject whose findings are complete is checked: an incomplete one is
    settled as a pass at completion, as it always was (`finding_bar`).
    """
    if report.status == StageReportStatus.PASS or unadmitted_reason(report.findings):
        return None
    with ledger(db_path, run_id) as (session, run, ticket):
        _, stages = resolve_ticket_stages(session, ticket)
        stage = next((s for s in stages if s.key == run.stage_key), None)
        if not is_judging(run.agent_id, stage) or prior_findings(session, ticket, run):
            return None
        check = check_reject_tests(session, ticket, report.findings, in_turn=True)
        if unadmitted_reason(standing(report.findings, check)):
            return reject_not_shown_message(check, then=THEN)
        return None
