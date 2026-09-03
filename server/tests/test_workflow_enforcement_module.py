"""The workflow enforcement module must not tell agents things that are false.

Every role file says to read this module before acting, so an agent pulls the
whole of it in through the Read tool — after its run context, giving anything
stale in it a recency advantage over the truth. It was roughly half live
contract and half v1-era fossil, and the fossil contradicted the live half
directly: line 21 said tickets live in the database and not to look for a ticket
file, while a later section called the ticket file the single source of truth and
told agents where to find it (lg-workflow-integrity-101).

These tests pin what was removed. They are string assertions on a document, which
is normally worth avoiding — but this document is an instruction set an agent
obeys, and the specific strings below are the ones that caused real
misbehaviour: the STRICT stage enum's values match no real stage key and were a
direct cause of a hallucinated `implementation` reroute.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from loregarden.agents.mcp_context import load_stage_report_contract_doc

MODULE = Path(__file__).resolve().parents[2] / (
    "agent_context/agents/common_assets/workflow_enforcement_v1.md"
)


@pytest.fixture(scope="module")
def text() -> str:
    return MODULE.read_text(encoding="utf-8")


def test_the_stage_report_contract_is_still_extractable(text):
    """AC1. `stage_report.py` cites this section as its spec and
    `mcp_context.load_stage_report_contract_doc` injects only this section, by
    splitting on the dividers and matching the title. Removing the fossil must
    not disturb either."""
    doc = load_stage_report_contract_doc(MODULE.parents[2])

    assert "LOREGARDEN_STAGE_REPORT" in doc
    assert "reroute_to_stage" in doc
    assert "needs_rework" in doc
    # The extractor keys on this exact title; renaming the section silently
    # empties every agent's stage report contract.
    assert "STAGE REPORT CONTRACT" in text


def test_the_strict_stage_enum_is_gone(text):
    """AC2. Its values matched no real stage key — real ones are `plan`,
    `implement`, `backend-impl` — and "No other values allowed" pointed agents
    at names that would be discarded as a reroute target."""
    for fossil in (
        "IMPLEMENTATION_BACKEND",
        "IMPLEMENTATION_FRONTEND",
        "IMPLEMENTATION_GENERALIST",
        "No other values allowed",
    ):
        assert fossil not in text, f"fossil stage enum returned: {fossil}"


def test_the_ticket_file_authority_is_gone(text):
    """AC2 and AC3. There are no ticket files; tickets are database rows."""
    assert "The ticket file is the single source of truth" not in text
    assert "agent_context/projects/" not in text


def test_the_module_no_longer_contradicts_itself(text):
    """AC3. The one surviving mention of a ticket file must be the instruction
    *not* to look for one."""
    mentions = [line for line in text.splitlines() if "ticket file" in line.lower()]
    assert len(mentions) == 1, f"expected one mention, found: {mentions}"
    assert "Do not search for a ticket file" in mentions[0]


def test_the_folder_rule_is_gone(text):
    """AC2. `00_backlog/`, `01_active/` and `02_complete/` exist nowhere in this
    repository."""
    for fossil in ("00_backlog", "01_active", "02_complete"):
        assert fossil not in text, f"fossil folder rule returned: {fossil}"


def test_the_planner_branch_mandate_is_gone(text):
    """AC2. The orchestrator checks out the branch, commits the tree and opens
    the PR; an agent doing it by hand races that."""
    assert "Planner mandate" not in text
    assert "The orchestrator owns branching" in text


def test_the_live_sections_survived(text):
    """The point was to remove the fossil, not the contract."""
    for live in (
        "LOREGARDEN CONTROL PLANE (MCP)",
        "STAGE REPORT CONTRACT",
        "TESTING DISCIPLINE",
        "Never write a markdown file to report your work",
        "todo_validation_check",
    ):
        assert live in text, f"live content lost: {live}"
