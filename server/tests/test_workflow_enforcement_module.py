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

import pytest
from loregarden.agents.mcp_context import load_stage_report_contract_doc
from loregarden.models.domain import ContractAudience
from tests.repo_paths import REPO_ROOT

MODULE = REPO_ROOT / ("agent_context/agents/common_assets/workflow_enforcement_v1.md")


@pytest.fixture(scope="module")
def text() -> str:
    return MODULE.read_text(encoding="utf-8")


def test_the_stage_report_contract_is_still_extractable(text):
    """AC1. `stage_report.py` cites this section as its spec and
    `mcp_context.load_stage_report_contract_doc` injects only this section, by
    splitting on the dividers and matching the title. Removing the fossil must
    not disturb either."""
    for audience in ContractAudience:
        doc = load_stage_report_contract_doc(MODULE.parents[2], audience=audience)

        assert "LOREGARDEN_STAGE_REPORT" in doc
        assert "reroute_to_stage" in doc
        assert "needs_rework" in doc
    # The extractor keys on this exact title; renaming the section silently
    # empties every agent's stage report contract.
    assert "STAGE REPORT CONTRACT" in text
