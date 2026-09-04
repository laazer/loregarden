"""The rerouted agent gets the grounds, not just the complaint.

`lg-workflow-integrity-205` asks for "the reason for routing and evidence
injected to the initial prompt". The reason has flowed since the rework ledger
landed. The evidence had not: the contract asked rejecting agents for
`unmet_criteria` and nothing read the field, so the criteria a reviewer named
were dropped between the report and the stage that had to act on them.
"""

from __future__ import annotations

from loregarden.services.run_completion import _rework_context
from loregarden.services.stage_report import StageReport


def _report(context: str, criteria: list[str]) -> StageReport:
    return StageReport(
        status="needs_rework",
        confidence=0.9,
        reroute_to_stage="implement",
        reroute_context=context,
        unmet_criteria=criteria,
    )


def test_the_criteria_travel_with_the_reason():
    text = _rework_context(
        _report("the retry path is untested", ["AC2: a failed run is retried once"]), ""
    )

    assert "the retry path is untested" in text
    assert "AC2: a failed run is retried once" in text
    # The prose comes first: it is written for the agent that has to act, and
    # the criteria say what finishing would mean.
    assert text.index("retry path") < text.index("AC2")


def test_a_report_with_no_criteria_is_unchanged():
    """Every report emitted before this field existed, and every honest `pass`."""
    assert _rework_context(_report("just fix it", []), "") == "just fix it"


def test_stderr_still_backs_a_report_with_no_context():
    assert _rework_context(_report("", []), "boom") == "boom"


def test_criteria_survive_even_when_the_context_fell_back_to_stderr():
    """A crashed reviewer that still named a criterion has said something worth
    forwarding."""
    text = _rework_context(_report("", ["AC1: the gate refuses an unknown key"]), "boom")
    assert "boom" in text
    assert "AC1: the gate refuses an unknown key" in text
