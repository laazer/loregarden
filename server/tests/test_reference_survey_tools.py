"""One pass end to end through the MCP tools, from the queue to the finish.

Shaped like ThinkTerm's first pass in the legacy record (GPL-3.0-only, so
patterns only; `docs/agent-status.md` and the `agent_status` module read; the
finding appended to a ticket that answers it), driven only by the tool calls an
agent makes, with the feeder in between. Clone and lane are faked; nothing else is.
"""

from __future__ import annotations

import json
from unittest import mock

import pytest
from loregarden.mcp.tools import execute_tool
from loregarden.models.domain import Ticket
from loregarden.models.domain.survey_enums import LicenceUse, SurveyPassStatus, SurveySourceStatus
from loregarden.models.domain.survey_tables import SurveyPass, SurveySource
from loregarden.services.acceptance_criteria import load_criteria
from loregarden.services.orchestration import ApprovalService
from loregarden.services.reference_survey import feeder
from loregarden.services.reference_survey.errors import SurveyError
from sqlmodel import Session, select
from tests.survey_support import build_world, write_tree

GPL3 = "GNU GENERAL PUBLIC LICENSE\nVersion 3, 29 June 2007\n" + "terms " * 40


def _call(session, tool, **arguments):
    raw = execute_tool(session, tool, arguments, orchestrated=True, run_id="")
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise AssertionError(f"{tool} returned {raw[:400]}") from exc


def test_a_pass_from_the_queue_to_its_finish(isolated_db, tmp_path):
    tree = write_tree(
        tmp_path / "clone",
        {
            "COPYING": GPL3,
            "README.md": "ThinkTerm",
            "docs/agent-status.md": "status",
            "docs/thinkterm/ui-colors.md": "colors",
            "mux/src/agent_status/hysteresis.rs": "h",
            "mux/src/agent_status/contract.rs": "c",
            "thinkterm-web/ui/src/AgentsPanel.svelte": "p",
        },
    )
    with Session(isolated_db) as session:
        world = build_world(session)
        queued = _call(
            session, "loregarden_survey_queue", action="enqueue", workspace_slug="loregarden", kind="repo",
            url="https://github.com/RoversX/ThinkTerm", question="How does the mux decide an agent's status?",
            queued_reason="closest product analogue", priority=10,
        )  # fmt: skip
        ApprovalService(session).resolve(queued["approval_id"], approved=True)
        with (
            mock.patch.object(
                feeder, "clone_or_refresh", return_value=(tree, "main", "ccf410c" + "0" * 33)
            ),
            mock.patch.object(feeder, "QueueLaneService"),
            mock.patch.object(feeder, "least_busy_lane", return_value=1),
        ):
            fed = feeder.feed_once(session)
        assert fed.promoted == 1 and fed.dispatched
        pass_id = fed.dispatched

        brief = _call(session, "loregarden_survey_pass", action="brief", pass_id=pass_id)
        assert brief["source"]["licence"]["use"] == LicenceUse.PATTERNS_ONLY.value
        assert brief["coverage"]["tree_files"] == 7
        source_id = brief["source"]["source_id"]

        reads = _call(
            session, "loregarden_survey_pass", action="reads", pass_id=pass_id,
            reads=json.dumps([
                {"path": "docs/agent-status.md", "state": "read", "files_read": 1, "file_count": 1},
                {"path": "mux/src", "state": "partial", "files_read": 2},
                {"path": "thinkterm-web/ui", "state": "skipped", "note": "UI pass is a later question"},
            ]),
        )  # fmt: skip
        assert reads["recorded"] == 3
        evidence = _call(
            session, "loregarden_survey_record", action="evidence", source_id=source_id, pass_id=pass_id,
            epistemic="observed", operation="read mux/src/agent_status/hysteresis.rs",
            result="status changes only after two consecutive matching probes",
            authority="the file at @ccf410c", corpus=["mux/src/agent_status/hysteresis.rs"],
        )  # fmt: skip
        with pytest.raises(SurveyError, match="does not establish absence"):
            _call(
                session, "loregarden_survey_record", action="finding", source_id=source_id, pass_id=pass_id,
                claim="absence", epistemic="observed", statement="No flicker tests",
                evidence_ids=[evidence["evidence_id"]],
            )  # fmt: skip
        finding = _call(
            session, "loregarden_survey_record", action="finding", source_id=source_id, pass_id=pass_id,
            claim="assertion", epistemic="observed", statement="Agent status is debounced by hysteresis.",
            evidence_ids=[evidence["evidence_id"]],
        )  # fmt: skip
        landed = _call(
            session, "loregarden_survey_write", pass_id=pass_id, finding_ids=[finding["finding_id"]],
            ticket=world.feature_ext, criteria=["A status change needs two consecutive matching probes."],
            block="ThinkTerm debounces agent status rather than trusting one probe.",
        )  # fmt: skip
        assert (landed["action"], landed["verified"], landed["criteria_total"]) == (
            "appended",
            True,
            1,
        )
        target = session.get(Ticket, world.feature_id)
        assert load_criteria(target.acceptance_criteria_json) == [
            "A status change needs two consecutive matching probes."
        ]

        finished = _call(
            session, "loregarden_survey_pass", action="finish", pass_id=pass_id, outcome="partial",
            outcome_note="hysteresis read; the rule engine is not", stopped_at="after hysteresis.rs",
            unread_priority=["mux/src"],
        )  # fmt: skip
        assert finished["status"] == SurveyPassStatus.FINISHED.value
        status = _call(session, "loregarden_survey_queue", action="status", source_id=source_id)
        assert [t["ticket"] for t in status["tickets"]] == [world.feature_ext]
        # 7 files: docs/. (1, read in full), mux/src (2, partial), thinkterm-web/ui (1,
        # skipped), and README, COPYING and docs/thinkterm (3) never opened.
        coverage = status["coverage"]
        assert (coverage["files_in_read_leaves"], coverage["files_in_partial_leaves"]) == (1, 2)
        assert (coverage["files_in_skipped_leaves"], coverage["files_in_unopened_leaves"]) == (1, 3)
        follow = session.exec(
            select(SurveyPass).where(
                SurveyPass.source_id == source_id, SurveyPass.status == SurveyPassStatus.QUEUED
            )
        ).one()
        assert follow.question == "How does the mux decide an agent's status?"
        assert session.get(SurveySource, source_id).status is SurveySourceStatus.ACTIVE
