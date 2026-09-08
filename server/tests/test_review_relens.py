"""A rework round should re-run the lenses that have something to re-derive.

lg-workflow-integrity-499. The saving is real — several tickets show 12 runs
across 3 lenses — but the ticket is explicit that "skip the lenses that passed"
is the wrong rule, because a lens can pass and still depend on code the rework
later changed. Its own example is security on ticket 181, whose clearance rested
on a shared reader the rework then modified.

So the tests that matter most here are the ones asserting a lens DOES re-run.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from loregarden.models.domain import (
    AgentRun,
    ParallelAgentSpec,
    RunStatus,
    WorkflowStageDef,
)
from loregarden.services.review_relens import decide_lenses, relens_note, skipped_members
from sqlmodel import Session
from tests.factories import make_workspace_ticket

STAGE = "review"
ARCH = "architecture_reviewer"
SECURITY = "security_reviewer"


def _passing_report() -> str:
    return (
        "<<<LOREGARDEN_STAGE_REPORT>>>\n"
        '{"status": "pass", "confidence": 0.9}\n'
        "<<<END_STAGE_REPORT>>>\n"
    )


def _rejecting_report() -> str:
    return (
        "<<<LOREGARDEN_STAGE_REPORT>>>\n"
        '{"status": "reject", "confidence": 0.9}\n'
        "<<<END_STAGE_REPORT>>>\n"
    )


@pytest.fixture(name="stage_def")
def stage_def_fixture() -> WorkflowStageDef:
    return WorkflowStageDef(
        key=STAGE,
        name="Review",
        order=1,
        stage_type="parallel",
        parallel_agents=[
            ParallelAgentSpec(agent_id=ARCH),
            ParallelAgentSpec(agent_id=SECURITY),
        ],
    )


def _lens_run(
    session: Session,
    ticket,
    agent_id: str,
    *,
    passed: bool,
    read: list[str] | None,
    when: datetime,
) -> AgentRun:
    """A settled review run. `read=None` means nobody recorded what it read."""
    run = AgentRun(
        run_code=f"{agent_id}-{when.timestamp()}",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id=agent_id,
        skill_name="",
        stage_key=STAGE,
        status=RunStatus.SUCCEEDED,
        stdout=_passing_report() if passed else _rejecting_report(),
        created_at=when,
    )
    if read is not None:
        run.read_paths_json = json.dumps(read)
        run.read_paths_recorded_at = when
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _rework_run(session: Session, ticket, *, changed: list[str] | None, when: datetime) -> AgentRun:
    """An implement run after the review. `changed=None` means unrecorded."""
    run = AgentRun(
        run_code=f"impl-{when.timestamp()}",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="backend_implementer",
        skill_name="",
        stage_key="implement",
        status=RunStatus.SUCCEEDED,
        created_at=when,
    )
    if changed is not None:
        run.changed_paths_json = json.dumps(changed)
        run.changed_paths_recorded_at = when
    session.add(run)
    session.commit()
    return run


def _decide(session, ticket, stage_def):
    return {d.spec.agent_id: d for d in decide_lenses(session, ticket, stage_def, STAGE)}


def test_a_first_run_runs_every_lens(db_session: Session, stage_def):
    ticket = make_workspace_ticket(db_session, "relens-first")
    decisions = _decide(db_session, ticket, stage_def)
    assert all(d.rerun for d in decisions.values())
    assert "has not run" in decisions[ARCH].reason


def test_a_passing_lens_untouched_by_the_rework_is_not_rerun(db_session: Session, stage_def):
    """AC1, and the only case in this file where a lens is skipped."""
    ticket = make_workspace_ticket(db_session, "relens-skip")
    base = datetime.now(timezone.utc) - timedelta(hours=2)
    _lens_run(db_session, ticket, ARCH, passed=False, read=["a.py"], when=base)
    _lens_run(db_session, ticket, SECURITY, passed=True, read=["security/auth.py"], when=base)
    _rework_run(db_session, ticket, changed=["a.py"], when=base + timedelta(minutes=5))

    decisions = _decide(db_session, ticket, stage_def)
    assert decisions[SECURITY].rerun is False
    assert "touched none" in decisions[SECURITY].reason


def test_a_passing_lens_whose_reads_the_rework_touched_is_rerun(db_session: Session, stage_def):
    """AC2, and the ticket's own warning: security passed on 181, then the rework
    changed a shared reader its clearance rested on. Skipping it there would have
    traded tokens for the defect class this pipeline exists to catch."""
    ticket = make_workspace_ticket(db_session, "relens-shared-reader")
    base = datetime.now(timezone.utc) - timedelta(hours=2)
    _lens_run(
        db_session,
        ticket,
        SECURITY,
        passed=True,
        read=["memory/obsidian_store.py", "security/auth.py"],
        when=base,
    )
    _rework_run(
        db_session, ticket, changed=["memory/obsidian_store.py"], when=base + timedelta(minutes=5)
    )

    decisions = _decide(db_session, ticket, stage_def)
    assert decisions[SECURITY].rerun is True
    assert "memory/obsidian_store.py" in decisions[SECURITY].reason


def test_the_rejecting_lens_always_reruns(db_session: Session, stage_def):
    """AC4. It holds the open finding, and the rework was aimed at its subject."""
    ticket = make_workspace_ticket(db_session, "relens-rejector")
    base = datetime.now(timezone.utc) - timedelta(hours=2)
    _lens_run(db_session, ticket, ARCH, passed=False, read=["untouched.py"], when=base)
    _rework_run(db_session, ticket, changed=["something/else.py"], when=base + timedelta(minutes=5))

    decisions = _decide(db_session, ticket, stage_def)
    assert decisions[ARCH].rerun is True
    assert "did not pass" in decisions[ARCH].reason


def test_a_lens_with_no_read_record_reruns(db_session: Session, stage_def):
    """Fail closed. This is the state of every run before 681 landed, so the
    change is a no-op until the data exists rather than a silent skip."""
    ticket = make_workspace_ticket(db_session, "relens-noreads")
    base = datetime.now(timezone.utc) - timedelta(hours=2)
    _lens_run(db_session, ticket, SECURITY, passed=True, read=None, when=base)
    _rework_run(db_session, ticket, changed=["a.py"], when=base + timedelta(minutes=5))

    decisions = _decide(db_session, ticket, stage_def)
    assert decisions[SECURITY].rerun is True
    assert "no record of what it read" in decisions[SECURITY].reason


def test_an_unrecorded_rework_diff_reruns_everything(db_session: Session, stage_def):
    """Fail closed the other way. One later run with no changed-path record makes
    the diff unknown, and an unknown intersection is not an empty one — the files
    it touched could be exactly the ones the lens depended on."""
    ticket = make_workspace_ticket(db_session, "relens-unknown-diff")
    base = datetime.now(timezone.utc) - timedelta(hours=2)
    _lens_run(db_session, ticket, SECURITY, passed=True, read=["security/auth.py"], when=base)
    _rework_run(db_session, ticket, changed=None, when=base + timedelta(minutes=5))

    decisions = _decide(db_session, ticket, stage_def)
    assert decisions[SECURITY].rerun is True
    assert "not fully recorded" in decisions[SECURITY].reason


def test_a_lens_that_read_nothing_reruns(db_session: Session, stage_def):
    """`[]` with a stamp is a real answer — the run read nothing in the repo —
    but it is not a basis for skipping: a lens that examined nothing has no
    footprint to intersect, so its clearance cannot be shown to still hold."""
    ticket = make_workspace_ticket(db_session, "relens-readnothing")
    base = datetime.now(timezone.utc) - timedelta(hours=2)
    _lens_run(db_session, ticket, SECURITY, passed=True, read=[], when=base)
    _rework_run(db_session, ticket, changed=["a.py"], when=base + timedelta(minutes=5))

    decisions = _decide(db_session, ticket, stage_def)
    assert decisions[SECURITY].rerun is True


def test_the_decision_and_its_reason_are_reportable(db_session: Session, stage_def):
    """AC3. A skip that is not written down is indistinguishable from a lens that
    ran and found nothing."""
    ticket = make_workspace_ticket(db_session, "relens-note")
    base = datetime.now(timezone.utc) - timedelta(hours=2)
    _lens_run(db_session, ticket, ARCH, passed=False, read=["a.py"], when=base)
    _lens_run(db_session, ticket, SECURITY, passed=True, read=["security/auth.py"], when=base)
    _rework_run(db_session, ticket, changed=["a.py"], when=base + timedelta(minutes=5))

    note = relens_note(stage_def, decide_lenses(db_session, ticket, stage_def, STAGE))
    assert "re-ran" in note and "skipped" in note
    assert ARCH in note and SECURITY in note


def test_skipped_members_are_keyed_by_agent_and_skill(db_session: Session, stage_def):
    """Lanes may share an agent and differ only by skill; keying on agent alone
    would let one lane's decision answer for its siblings."""
    ticket = make_workspace_ticket(db_session, "relens-key")
    base = datetime.now(timezone.utc) - timedelta(hours=2)
    _lens_run(db_session, ticket, SECURITY, passed=True, read=["security/auth.py"], when=base)
    _rework_run(db_session, ticket, changed=["elsewhere.py"], when=base + timedelta(minutes=5))

    skipped = skipped_members(stage_def, decide_lenses(db_session, ticket, stage_def, STAGE))
    assert (SECURITY, "") in skipped
    assert (ARCH, "") not in skipped
