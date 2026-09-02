"""Handing a person a prepared action rather than a description of one.

The shape this exists to stop, from blobert milestone 14 on 2026-08-15: a block
saying a human must capture GPU profiler timings for two scenes and attach five
measured numbers. Accurate, and about as much work as it could possibly be. The
agent had already built and run a CPU-side probe over those exact scenes and
handed over none of it (lg-workflow-integrity-460).
"""

from __future__ import annotations

import os
import stat

import pytest
from loregarden.models.domain import HumanActionTier
from loregarden.services.prepared_action import (
    PreparedAction,
    assess_handover,
    resolve_script,
    run_prepared_action,
)

TICKET_22_MESSAGE = (
    "a human/operator must capture real display-backed Godot editor GPU profiler "
    "timings and target-mobile GPU frame timings for baseline and spike scenes, then:\n"
    "- attach measured baseline GPU ms/frame\n"
    "- attach spike GPU ms/frame\n"
    "- attach the shader delta\n"
    "- compare against budget\n"
    "- record the final go/no-go decision\n"
)


def test_the_ticket_22_handover_is_reported_as_unprepared():
    """AC6. The original block, judged. It must not pass silently."""
    assessment = assess_handover(message=TICKET_22_MESSAGE, action=None)

    assert assessment.ok is False
    joined = " ".join(assessment.findings)
    assert "no prepared action" in joined
    assert "5 steps" in joined


def test_a_block_that_says_what_it_tried_and_committed_a_script_passes():
    action = PreparedAction(
        tier=HumanActionTier.ONE_CLICK,
        attempted="Ran the CPU-side probe headless; it reports draw calls but no GPU ms.",
        prepared="Committed a capture harness driving both scenes.",
        command="scripts/capture_gpu.sh baseline spike",
        script_path="scripts/capture_gpu.sh",
        captures=["baseline GPU ms/frame", "spike GPU ms/frame"],
    )
    assert assess_handover(message="Needs a display-backed run.", action=action).ok is True


def test_claiming_one_click_without_committing_the_script_is_refused():
    action = PreparedAction(
        tier=HumanActionTier.ONE_CLICK,
        attempted="Tried headless; no GPU counters.",
        command="scripts/capture_gpu.sh",
    )
    findings = assess_handover(message="Run it.", action=action).findings
    assert any("no `script_path`" in f for f in findings)


def test_manual_must_be_earned_not_defaulted_to():
    """The rung that costs a person the most is the one an agent reaches for
    first. A multi-step MANUAL handover with no script is exactly ticket 22."""
    action = PreparedAction(
        tier=HumanActionTier.MANUAL,
        attempted="Nothing — needs a display.",
    )
    findings = assess_handover(message=TICKET_22_MESSAGE, action=action).findings
    assert any("MANUAL is for the part that needs a person present" in f for f in findings)


def test_a_block_that_never_says_what_it_tried_is_incomplete():
    """AC1. `attempted` is the field that turns a shrug into a report."""
    action = PreparedAction(tier=HumanActionTier.MANUAL, prepared="A fixture scene.")
    findings = assess_handover(message="Plug in the device.", action=action).findings
    assert any("`attempted` is empty" in f for f in findings)


def test_a_single_step_manual_handover_is_fine():
    """The ladder is not a demand that everything be automated. Some steps
    genuinely need a person, and one of those is not a procedure."""
    action = PreparedAction(
        tier=HumanActionTier.MANUAL,
        attempted="Ran the emulator suite; it cannot expose the hardware sensor.",
        prepared="Committed the fixture app and the reading script.",
        command="Open the app on the device and tap Measure.",
    )
    assert assess_handover(message="Needs the physical device.", action=action).ok is True


# --- the runnable rung -------------------------------------------------------


def _script(tmp_path, body: str, name: str = "capture.sh"):
    path = tmp_path / name
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def test_running_a_prepared_action_captures_its_output(tmp_path):
    """AC3. The result is captured, not read off a screen and retyped."""
    _script(tmp_path, "echo 'baseline 8.2ms spike 11.9ms'")
    action = PreparedAction(
        tier=HumanActionTier.ONE_CLICK,
        attempted="Headless probe has no GPU counters.",
        script_path="capture.sh",
    )
    result = run_prepared_action(repo_path=str(tmp_path), action=action)

    assert result.ok is True
    assert "baseline 8.2ms spike 11.9ms" in result.output


def test_a_failing_action_reports_rather_than_claiming_success(tmp_path):
    _script(tmp_path, "echo 'no display available' >&2\nexit 3")
    action = PreparedAction(tier=HumanActionTier.ONE_CLICK, attempted="x", script_path="capture.sh")
    result = run_prepared_action(repo_path=str(tmp_path), action=action)

    assert result.ok is False
    assert result.exit_code == 3
    assert "no display available" in result.error


def test_only_the_committed_script_runs_never_the_command_string(tmp_path):
    """The command was written by an agent. Executing it would make the control
    plane run whatever an agent typed, so it is shown and not run."""
    _script(tmp_path, "echo ran")
    action = PreparedAction(
        tier=HumanActionTier.ONE_CLICK,
        attempted="x",
        command="rm -rf / # what an agent could have written",
        script_path="capture.sh",
    )
    result = run_prepared_action(repo_path=str(tmp_path), action=action)

    # The extra tokens are arguments to the resolved file, not a command line.
    assert result.ok is True
    assert "ran" in result.output


def test_a_script_path_escaping_the_repository_is_refused(tmp_path):
    outside = tmp_path.parent / "outside.sh"
    outside.write_text("#!/bin/sh\necho pwned\n")
    outside.chmod(outside.stat().st_mode | stat.S_IEXEC)
    repo = tmp_path / "repo"
    repo.mkdir()

    assert resolve_script(str(repo), "../outside.sh") is None

    action = PreparedAction(
        tier=HumanActionTier.ONE_CLICK, attempted="x", script_path="../outside.sh"
    )
    result = run_prepared_action(repo_path=str(repo), action=action)
    assert result.ok is False
    assert "No committed script" in result.error


def test_a_missing_script_is_a_reported_failure_not_a_crash(tmp_path):
    action = PreparedAction(tier=HumanActionTier.ONE_CLICK, attempted="x", script_path="nope.sh")
    result = run_prepared_action(repo_path=str(tmp_path), action=action)
    assert result.ok is False
    assert "No committed script" in result.error


def test_a_script_without_its_execute_bit_reports_rather_than_raising(tmp_path):
    path = tmp_path / "plain.sh"
    path.write_text("#!/bin/sh\necho hi\n")
    path.chmod(path.stat().st_mode & ~stat.S_IEXEC & ~stat.S_IXGRP & ~stat.S_IXOTH)
    action = PreparedAction(tier=HumanActionTier.ONE_CLICK, attempted="x", script_path="plain.sh")
    result = run_prepared_action(repo_path=str(tmp_path), action=action)
    assert result.ok is False
    assert result.error


def test_a_slow_action_times_out_instead_of_holding_the_request(tmp_path):
    _script(tmp_path, "sleep 30")
    action = PreparedAction(tier=HumanActionTier.ONE_CLICK, attempted="x", script_path="capture.sh")
    result = run_prepared_action(repo_path=str(tmp_path), action=action, timeout_seconds=1)
    assert result.ok is False
    assert "timed out" in result.error


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell scripts")
def test_arguments_from_the_command_reach_the_script(tmp_path):
    """A capture script usually takes the scene or target to measure."""
    _script(tmp_path, 'echo "measuring $1 and $2"')
    action = PreparedAction(
        tier=HumanActionTier.ONE_CLICK,
        attempted="x",
        command="capture.sh baseline spike",
        script_path="capture.sh",
    )
    result = run_prepared_action(repo_path=str(tmp_path), action=action)
    assert result.ok is True
    assert "measuring baseline and spike" in result.output


# --- the whole path ----------------------------------------------------------


def _blocked_with(db_session, tmp_path, action: PreparedAction | None, message: str):
    """Block a ticket the way an agent does, and return (ticket, approval)."""
    from loregarden.models.domain import (
        Approval,
        ApprovalKind,
        OrchestrationRun,
        Ticket,
        TicketState,
        WorkItemType,
        Workspace,
    )
    from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
    from sqlmodel import select

    workspace = Workspace(slug="pa", name="PA", repo_path=str(tmp_path))
    db_session.add(workspace)
    db_session.commit()
    db_session.refresh(workspace)

    ticket = Ticket(
        external_id="pa-1",
        workspace_id=workspace.id,
        title="GPU timings",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key="implement",
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)

    orch_run = OrchestrationRun(ticket_id=ticket.id, workspace_id=workspace.id, run_code="pa_run")
    db_session.add(orch_run)
    db_session.commit()
    db_session.refresh(orch_run)

    OrchestrationCallbackService(db_session).block_ticket(
        orch_run, ticket, stage_key="implement", message=message, prepared_action=action
    )
    db_session.refresh(ticket)
    approval = db_session.exec(
        select(Approval).where(
            Approval.ticket_id == ticket.id, Approval.kind == ApprovalKind.HUMAN_ACTION
        )
    ).first()
    return ticket, approval


def test_blocking_with_a_prepared_action_puts_it_in_the_inbox(db_session, tmp_path):
    """AC5's data half: the inbox item carries the command, not a paragraph."""
    action = PreparedAction(
        tier=HumanActionTier.ONE_CLICK,
        attempted="Headless probe reports no GPU counters.",
        prepared="Committed a capture harness.",
        command="capture.sh baseline spike",
        script_path="capture.sh",
        captures=["baseline GPU ms/frame"],
    )
    _, approval = _blocked_with(db_session, tmp_path, action, "Needs a display-backed run.")

    assert approval is not None
    assert approval.tool_name == "capture.sh baseline spike"
    assert PreparedAction.model_validate_json(approval.tool_input_json).is_runnable()


def test_an_unprepared_human_block_still_reaches_the_inbox_carrying_its_findings(
    db_session, tmp_path
):
    """The ladder is not enforced by making badly-described work invisible: a
    stranded block is worse than a badly filed one. It arrives with what it
    failed to prepare stated on it."""
    import json

    _, approval = _blocked_with(db_session, tmp_path, None, TICKET_22_MESSAGE)

    assert approval is not None
    findings = json.loads(approval.checklist_json)
    assert findings and any("no prepared action" in f for f in findings)


def test_an_ordinary_block_does_not_become_a_human_action(db_session, tmp_path):
    """Most blocks are faults, not handovers. They must keep behaving exactly as
    they did — this feature is additive or it is a regression."""
    _, approval = _blocked_with(db_session, tmp_path, None, "Gate failed: ruff errors remain.")
    assert approval is None


def test_running_it_from_the_inbox_captures_evidence_and_clears_the_block(
    client, db_session, tmp_path
):
    """AC3 and AC4 together, which is the point of the whole ticket: the person
    says go once, and nothing is transcribed or requeued by hand."""
    from loregarden.models.domain import Artifact, TicketState
    from sqlmodel import select

    _script(tmp_path, "echo 'baseline 8.2ms spike 11.9ms'")
    action = PreparedAction(
        tier=HumanActionTier.ONE_CLICK,
        attempted="Headless probe reports no GPU counters.",
        command="capture.sh baseline spike",
        script_path="capture.sh",
    )
    ticket, approval = _blocked_with(db_session, tmp_path, action, "Needs a display.")
    assert ticket.state is TicketState.BLOCKED

    res = client.post(f"/api/inbox/approvals/{approval.id}/run")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    assert "baseline 8.2ms" in body["output"]

    db_session.refresh(ticket)
    assert ticket.state is TicketState.IN_PROGRESS
    assert not (ticket.blocking_issues or "")

    artifacts = db_session.exec(select(Artifact).where(Artifact.ticket_id == ticket.id)).all()
    assert any("baseline 8.2ms" in (a.content_json or "") for a in artifacts)


def test_a_failed_run_leaves_the_block_in_place(client, db_session, tmp_path):
    """Clearing on failure would report success the run never had."""
    from loregarden.models.domain import TicketState

    _script(tmp_path, "echo 'no display' >&2\nexit 1")
    action = PreparedAction(tier=HumanActionTier.ONE_CLICK, attempted="x", script_path="capture.sh")
    ticket, approval = _blocked_with(db_session, tmp_path, action, "Needs a display.")

    res = client.post(f"/api/inbox/approvals/{approval.id}/run")
    assert res.status_code == 200, res.text
    assert res.json()["ok"] is False

    db_session.refresh(ticket)
    assert ticket.state is TicketState.BLOCKED


def test_a_manual_action_is_not_runnable_from_the_inbox(client, db_session, tmp_path):
    action = PreparedAction(
        tier=HumanActionTier.MANUAL, attempted="Emulator cannot expose the sensor."
    )
    _, approval = _blocked_with(db_session, tmp_path, action, "Needs the physical device.")

    res = client.post(f"/api/inbox/approvals/{approval.id}/run")
    assert res.status_code == 400
    assert "not runnable" in res.text
