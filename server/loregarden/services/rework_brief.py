"""What the agent re-running a rejected stage is told, from every report that rejected it.

The contract asks a rejecting stage to name the acceptance criteria its
finding makes false, and nothing read them until lg-workflow-integrity-205.
The parallel review path still dropped them: it passed on one lens's prose,
and only from a lens that also named a stage, so the other lenses' reasons and
every lens's criteria never reached the implementer. On the live database
(Oct 20 - Nov 18) every one of 41 reject reports named its criteria.

A blocking finding's test is the job itself (`reject_tests`): the defect is
fixed when it passes, everywhere the pattern occurs, not at the one site the
prose describes.
"""

from __future__ import annotations

from loregarden.services.stage_report import StageReport


def rework_context(reports: list[tuple[str, StageReport]], fallback: str) -> str:
    """Reason, criteria and tests from each ``(who, report)``; ``fallback`` when none says why.

    ``who`` labels a report's reason when several lenses rejected; "" for one.
    """
    reasons = [
        f"{who}: {text}" if who and len(reports) > 1 else text
        for who, report in reports
        if (text := (report.reroute_context or "").strip())
    ]
    context = "\n\n".join(reasons) or fallback or "Agent run failed"
    criteria: list[str] = []
    tests: list[str] = []
    for _who, report in reports:
        criteria.extend(c for c in report.unmet_criteria if c not in criteria)
        for finding in report.findings:
            line = (
                f"- {finding.test} ({finding.summary})" if finding.summary else f"- {finding.test}"
            )
            if finding.blocking and finding.test and line not in tests:
                tests.append(line)
    if criteria:
        listed = "\n".join(f"- {item}" for item in criteria)
        context = f"{context}\n\nAcceptance criteria reported unmet:\n{listed}"
    if tests:
        context = (
            f"{context}\n\nTests that show the defect. Each fails now; the rework is done when "
            "every one passes. Fix the cause wherever the same pattern occurs, not only the "
            "site a test names. Do not edit or delete these tests: if one asserts the wrong "
            "behaviour, say so in your report.\n" + "\n".join(tests)
        )
    return context
