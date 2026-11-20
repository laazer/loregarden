"""Later stages see where earlier stages on the ticket found the code, and what they noted."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.agents.stage_digest import (
    MAX_DIGEST_CHARS,
    MAX_FILES_PER_LIST,
    build_stage_digest,
)
from loregarden.models.domain import (
    AgentRun,
    MemoryBriefingAssembly,
    RunStatus,
    Ticket,
    WorkflowStageDef,
    Workspace,
)
from loregarden.models.domain.enums import utcnow
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.studio_routing import VERIFY_STAGE_TYPE
from loregarden.services.workspace_paths import resolve_agent_context_dir
from loregarden.testing.factories import make_orchestration_run
from sqlmodel import Session
from tests.factories import make_workspace_ticket

DIGEST_HEADING = "## What earlier stages on this ticket found"
NOTE = "parse_header lives in server/loregarden/io/headers.py:40; the null case is upstream."


@pytest.fixture
def ticket(db_session: Session) -> Ticket:
    return make_workspace_ticket(db_session, "stage-digest")


@pytest.fixture
def orch_id(db_session: Session, ticket: Ticket) -> str:
    return make_orchestration_run(
        db_session, workspace_id=ticket.workspace_id, ticket_id=ticket.id
    ).id


def _run(
    session: Session,
    ticket: Ticket,
    stage_key: str,
    *,
    orch_id: str | None,
    code: str,
    minutes_ago: int = 0,
    status: RunStatus = RunStatus.SUCCEEDED,
    changed: list[str] | None = None,
    read: list[str] | None = None,
    notes: str = "",
    agent_id: str = "planner",
) -> AgentRun:
    run = AgentRun(
        run_code=code,
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        orchestration_run_id=orch_id,
        agent_id=agent_id,
        stage_key=stage_key,
        status=status,
        changed_paths_json=json.dumps(changed or []),
        read_paths_json=json.dumps(read or []),
        next_stage_notes=notes,
        created_at=utcnow() - timedelta(minutes=minutes_ago),
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def test_each_earlier_stage_shows_its_notes_changes_and_reads(db_session, ticket, orch_id):
    _run(
        db_session, ticket, "plan", orch_id=orch_id, code="run_plan", minutes_ago=30,
        read=["server/a.py", "server/b.py"], notes=NOTE,
    )  # fmt: skip
    _run(
        db_session, ticket, "test-design", orch_id=orch_id, code="run_tests", minutes_ago=20,
        changed=["server/tests/test_a.py"], read=["server/tests/test_a.py", "server/a.py"],
        agent_id="test_designer",
    )  # fmt: skip
    current = _run(db_session, ticket, "implement", orch_id=orch_id, code="run_impl")

    digest = build_stage_digest(db_session, ticket, current, include_notes=True)

    assert digest == "\n".join(
        [
            "### plan (planner, run_plan)",
            NOTE,
            "- Read: `server/a.py`, `server/b.py`",
            "",
            "### test-design (test_designer, run_tests)",
            "- Changed: `server/tests/test_a.py`",
            "- Read: `server/a.py`",
        ]
    )


def test_only_the_latest_success_of_each_other_stage_counts(db_session, ticket, orch_id):
    _run(
        db_session, ticket, "spec", orch_id=orch_id, code="run_old", minutes_ago=40, read=["old.py"]
    )
    _run(
        db_session, ticket, "spec", orch_id=orch_id, code="run_new", minutes_ago=30, read=["new.py"]
    )
    _run(
        db_session, ticket, "spec", orch_id=orch_id, code="run_failed", minutes_ago=20,
        status=RunStatus.FAILED, read=["failed.py"],
    )  # fmt: skip
    _run(
        db_session,
        ticket,
        "implement",
        orch_id=orch_id,
        code="run_own",
        minutes_ago=10,
        read=["own.py"],
    )
    _run(db_session, ticket, "plan", orch_id=None, code="run_chat", minutes_ago=5, read=["chat.py"])
    current = _run(db_session, ticket, "implement", orch_id=orch_id, code="run_now")
    _run(
        db_session,
        ticket,
        "verify",
        orch_id=orch_id,
        code="run_later",
        minutes_ago=-5,
        read=["later.py"],
    )

    digest = build_stage_digest(db_session, ticket, current, include_notes=True)

    assert "new.py" in digest
    for absent in ("old.py", "failed.py", "own.py", "chat.py", "later.py"):
        assert absent not in digest


def test_a_judging_stage_gets_the_files_but_not_the_notes(db_session, ticket, orch_id):
    _run(
        db_session, ticket, "implement", orch_id=orch_id, code="run_impl", minutes_ago=10,
        changed=["server/a.py"], notes="I handled every edge case.",
    )  # fmt: skip
    review = _run(db_session, ticket, "review", orch_id=orch_id, code="run_rev")

    digest = build_stage_digest(db_session, ticket, review, include_notes=False)

    assert "`server/a.py`" in digest
    assert "every edge case" not in digest


def test_nothing_recorded_means_no_digest(db_session, ticket, orch_id):
    _run(db_session, ticket, "plan", orch_id=orch_id, code="run_plan", minutes_ago=10)
    current = _run(db_session, ticket, "spec", orch_id=orch_id, code="run_spec")

    assert build_stage_digest(db_session, ticket, current, include_notes=True) == ""


def test_long_lists_and_long_digests_are_capped(db_session, ticket, orch_id):
    many = [f"server/f{index:03}.py" for index in range(MAX_FILES_PER_LIST + 7)]
    for index in range(12):
        _run(
            db_session, ticket, f"stage-{index}", orch_id=orch_id, code=f"run_{index}",
            minutes_ago=60 - index, read=many, notes="n" * 1500,
        )  # fmt: skip
    current = _run(db_session, ticket, "implement", orch_id=orch_id, code="run_now")

    digest = build_stage_digest(db_session, ticket, current, include_notes=True)

    assert "(+7 more)" in digest and "server/f020.py" not in digest
    assert len(digest) <= MAX_DIGEST_CHARS + len("\n… (digest truncated)")
    assert digest.endswith("(digest truncated)")


def test_an_unreadable_path_list_is_omitted_with_a_warning(db_session, ticket, orch_id, caplog):
    prior = _run(
        db_session, ticket, "plan", orch_id=orch_id, code="run_plan", minutes_ago=10, notes=NOTE
    )
    prior.read_paths_json = "{not json"
    db_session.add(prior)
    db_session.commit()
    current = _run(db_session, ticket, "spec", orch_id=orch_id, code="run_spec")

    digest = build_stage_digest(db_session, ticket, current, include_notes=True)

    assert NOTE in digest
    assert "unreadable path list" in caplog.text


# ---- in the stage prompt -------------------------------------------------------------


def _prompt(session: Session, ticket: Ticket, run: AgentRun, stage_def: WorkflowStageDef) -> str:
    workspace = session.get(Workspace, ticket.workspace_id)
    return CliAgentExecutor(session)._build_prompt(
        ticket,
        run,
        {"role_body": "role"},
        resolve_agent_context_dir(workspace),
        workspace,
        stage_def,
        assembly_source=MemoryBriefingAssembly.DISPATCH,
    )


def test_the_stage_prompt_carries_the_digest(db_session, ticket, orch_id):
    _run(db_session, ticket, "plan", orch_id=orch_id, code="run_plan", minutes_ago=10, notes=NOTE)
    current = _run(db_session, ticket, "spec", orch_id=orch_id, code="run_spec", agent_id="spec")

    prompt = _prompt(
        db_session, ticket, current, WorkflowStageDef(key="spec", name="Spec", order=4)
    )

    assert DIGEST_HEADING in prompt and NOTE in prompt


def test_a_verify_prompt_carries_no_digest(db_session, ticket, orch_id):
    _run(
        db_session, ticket, "implement", orch_id=orch_id, code="run_impl", minutes_ago=10,
        changed=["server/a.py"], notes=NOTE,
    )  # fmt: skip
    current = _run(db_session, ticket, "verify", orch_id=orch_id, code="run_v", agent_id="verifier")

    prompt = _prompt(
        db_session,
        ticket,
        current,
        WorkflowStageDef(key="verify", name="Verify", order=9, stage_type=VERIFY_STAGE_TYPE),
    )

    assert DIGEST_HEADING not in prompt and NOTE not in prompt


# ---- the notes reach the run row -----------------------------------------------------


def test_a_completed_run_keeps_its_notes_for_later_stages(db_session, ticket, orch_id):
    run = _run(
        db_session, ticket, "plan", orch_id=orch_id, code="run_plan", status=RunStatus.RUNNING
    )
    report = json.dumps({"status": "pass", "confidence": 0.9, "notes_for_next_stage": NOTE})
    stdout = f"<<<LOREGARDEN_STAGE_REPORT>>>\n{report}\n<<<END_STAGE_REPORT>>>\n"

    OrchestrationService(db_session).complete_run(
        run, status=RunStatus.SUCCEEDED, stdout=stdout, advance_workflow=False
    )

    db_session.refresh(run)
    assert run.next_stage_notes == NOTE
