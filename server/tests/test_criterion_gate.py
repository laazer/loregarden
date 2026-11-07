"""A ticket enters its gate only with every acceptance criterion answered for.

The check reruns the tests each criterion names, at HEAD, in a real checkout:
a throwaway git repo with real test files, run by a real command. A test that
was not rerun is never reported as one that was.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from loregarden.models.domain import (
    BlockKind,
    CriterionDisposition,
    DomainEvent,
    EventType,
    OrchestrationDriver,
    OrchestrationRun,
    OrchestrationRunStatus,
    StageStatus,
    Ticket,
    TicketState,
    WorkflowTemplate,
    WorkItemType,
    Workspace,
    utcnow,
)
from loregarden.services import criterion_split_creator  # noqa: F401 — installs the creator
from loregarden.services.acceptance_criteria import load_criteria, serialize_criteria
from loregarden.services.auto_decisions import auto_decisions_under
from loregarden.services.criterion_gate import check_criteria_if_due, check_due, gate_of
from loregarden.services.criterion_ledger import (
    record_delegation,
    settle_met,
    settlements_of,
)
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_profile import (
    CriterionTestCommand,
    GatesConfig,
    OrchestrationProfile,
)
from loregarden.services.seed import seed_database
from loregarden.services.unsettled_criteria_runs import done_tickets_with_unsettled_criteria
from loregarden.services.workflow_service import resolve_ticket_stages, workflow_instance_for
from loregarden.services.workflow_state import (
    next_executable_stage,
    parse_stage_map,
    set_stage_status,
)
from sqlmodel import Session, select
from tests.factories import make_workspace_ticket

CRITERIA = ["Width is read on every arm", "Errors name the arm"]

#: The live V3 template's spine: a verify stage, then review, then the gate.
V3_SHAPE = [
    {"key": "spec", "name": "Spec", "agent_id": "spec", "order": 1},
    {"key": "implement", "name": "Implement", "agent_id": "backend_implementer", "order": 2},
    {"key": "verify", "name": "Verify", "agent_id": "verifier", "order": 3},
    {"key": "review", "name": "Review", "agent_id": "architecture_reviewer", "order": 4},
    {"key": "gate", "name": "Gate", "agent_id": "gatekeeper", "order": 5, "stage_type": "gate"},
    {"key": "done", "name": "Done", "order": 6, "terminal": True},
]

TESTS_PY = """
def test_reads_width():
    assert True


def test_names_the_arm():
    assert 1 == 2
"""


@pytest.fixture(name="checkout")
def checkout_fixture(tmp_path: Path) -> Path:
    """A real git checkout holding one passing and one failing test."""
    (tmp_path / "server" / "tests").mkdir(parents=True)
    (tmp_path / "server" / "tests" / "test_arms.py").write_text(TESTS_PY)
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    for args in (["init", "-q"], ["add", "."], ["commit", "-q", "-m", "init"]):
        subprocess.run(
            ["git", *args], cwd=tmp_path, check=True, env={**env, "PATH": "/usr/bin:/bin"}
        )
    return tmp_path


def _profile(commands: list[CriterionTestCommand]) -> OrchestrationProfile:
    return OrchestrationProfile(
        slug="t", name="t", gates=GatesConfig(criterion_test_commands=commands)
    )


PYTEST = CriterionTestCommand(
    prefix="server/",
    command=f"{sys.executable} -m pytest -q -p no:cacheprovider {{tests}}",
    strip_prefix=False,
)


@pytest.fixture(name="env")
def env_fixture(checkout: Path):
    """Point the check at the throwaway checkout and a pytest runner."""
    with (
        patch("loregarden.services.criterion_check.resolve_ticket_root", return_value=checkout),
        patch(
            "loregarden.services.criterion_check.resolve_orchestration_profile",
            return_value=_profile([PYTEST]),
        ) as profile,
    ):
        yield profile


@pytest.fixture(name="ticket")
def ticket_fixture(db_session: Session) -> Ticket:
    """A feature under a milestone, every stage before its gate done: about to enter it."""
    seed_database(db_session)
    workspace = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()
    template = WorkflowTemplate(
        slug="criteria-v3-shape", name="V3 shape", stages_json=json.dumps(V3_SHAPE)
    )
    db_session.add(template)
    db_session.flush()
    workspace.workflow_template_id = template.id
    db_session.add(workspace)
    db_session.commit()
    milestone = make_workspace_ticket(db_session, "cg-milestone")
    milestone.work_item_type = WorkItemType.MILESTONE
    ticket = make_workspace_ticket(db_session, "cg-ticket")
    ticket.work_item_type = WorkItemType.FEATURE
    ticket.parent_ticket_id = milestone.id
    ticket.acceptance_criteria_json = serialize_criteria(CRITERIA)
    db_session.add_all([milestone, ticket])
    db_session.commit()
    OrchestrationService(db_session).ensure_workflow_instance(ticket, commit=True)
    instance = workflow_instance_for(db_session, ticket.id)
    _, stages = resolve_ticket_stages(db_session, ticket)
    gate = gate_of(stages)
    for stage in stages:
        if stage.order < gate.order:
            set_stage_status(ticket, instance, stages, stage.key, StageStatus.DONE)
    db_session.add_all([ticket, instance])
    db_session.commit()
    return ticket


def _next(session: Session, ticket: Ticket) -> str:
    instance = workflow_instance_for(session, ticket.id)
    _, stages = resolve_ticket_stages(session, ticket)
    return next_executable_stage(stages, parse_stage_map(instance, stages))


def _met(session: Session, ticket: Ticket, criterion: str, test: str) -> None:
    settle_met(
        session,
        ticket,
        criterion,
        tests=[test],
        evidence_artifact_id=None,
        commit_sha="",
        settled_by="run:v",
        reason="r",
    )


def _decisions(session: Session, ticket: Ticket) -> list[str]:
    rows = session.exec(
        select(DomainEvent).where(
            DomainEvent.ticket_id == ticket.id, DomainEvent.type == EventType.ORCHESTRATOR_DECISION
        )
    ).all()
    return [row.payload_json for row in rows]


def _orch_run(session: Session, ticket: Ticket) -> OrchestrationRun:
    run = OrchestrationRun(
        run_code=f"o-{ticket.external_id}",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        profile_slug="loregarden",
        driver=OrchestrationDriver.BUILTIN_AUTOPILOT,
        status=OrchestrationRunStatus.RUNNING,
        started_at=utcnow(),
    )
    session.add(run)
    session.commit()
    return run


def test_the_check_is_due_only_on_entering_the_gate_after_verify(db_session, ticket):
    _, stages = resolve_ticket_stages(db_session, ticket)
    gate = gate_of(stages)
    assert gate.key == "gate"
    assert check_due(stages, gate)
    assert not check_due(stages, next(s for s in stages if s.key == "review"))


def test_the_gate_is_not_entered_with_a_criterion_unanswered(db_session, ticket, env):
    assert _next(db_session, ticket) == "gate"
    blocked = check_criteria_if_due(db_session, _orch_run(db_session, ticket), ticket)
    assert not blocked
    assert _next(db_session, ticket) == "verify"
    assert any("criteria_sent_to_verify" in p for p in _decisions(db_session, ticket))


def test_criteria_met_by_tests_that_pass_at_head_let_the_gate_run(db_session, ticket, env):
    _met(db_session, ticket, CRITERIA[0], "server/tests/test_arms.py::test_reads_width")
    carrier = make_workspace_ticket(db_session, "cg-carrier")
    carrier.acceptance_criteria_json = serialize_criteria([CRITERIA[1]])
    db_session.add(carrier)
    db_session.commit()
    record_delegation(
        db_session, ticket, CRITERIA[1], target=carrier, settled_by="operator", reason="r"
    )
    db_session.commit()

    assert not check_criteria_if_due(db_session, _orch_run(db_session, ticket), ticket)
    assert _next(db_session, ticket) == "gate"
    checked = [p for p in _decisions(db_session, ticket) if "criteria_checked" in p]
    assert checked and "test_reads_width" in checked[0]


def test_a_named_test_that_fails_at_head_is_a_gap(db_session, ticket, env):
    _met(db_session, ticket, CRITERIA[0], "server/tests/test_arms.py::test_reads_width")
    _met(db_session, ticket, CRITERIA[1], "server/tests/test_arms.py::test_names_the_arm")
    check_criteria_if_due(db_session, _orch_run(db_session, ticket), ticket)
    assert _next(db_session, ticket) == "verify"
    sent = next(p for p in _decisions(db_session, ticket) if "criteria_sent_to_verify" in p)
    assert "1 acceptance criteria" in sent


def test_a_named_test_that_does_not_exist_is_a_gap(db_session, ticket, env):
    _met(db_session, ticket, CRITERIA[0], "server/tests/test_arms.py::test_reads_width")
    _met(db_session, ticket, CRITERIA[1], "server/tests/test_arms.py::test_never_written")
    check_criteria_if_due(db_session, _orch_run(db_session, ticket), ticket)
    assert _next(db_session, ticket) == "verify"


def test_tests_no_command_runs_are_reported_as_not_rerun(db_session, ticket, env):
    env.return_value = _profile([])
    _met(db_session, ticket, CRITERIA[0], "server/tests/test_arms.py::test_reads_width")
    # Fails if run, but nothing runs it: it is only checked to exist, and said so.
    _met(db_session, ticket, CRITERIA[1], "server/tests/test_arms.py::test_names_the_arm")
    assert not check_criteria_if_due(db_session, _orch_run(db_session, ticket), ticket)
    checked = next(p for p in _decisions(db_session, ticket) if "criteria_checked" in p)
    assert "only checked to exist" in checked


def test_gaps_verify_could_not_answer_split_off_to_a_sibling(db_session, ticket, env):
    _met(db_session, ticket, CRITERIA[0], "server/tests/test_arms.py::test_reads_width")
    run = _orch_run(db_session, ticket)
    check_criteria_if_due(db_session, run, ticket)  # sent to verify
    instance = workflow_instance_for(db_session, ticket.id)
    _, stages = resolve_ticket_stages(db_session, ticket)
    set_stage_status(
        ticket, instance, stages, "verify", StageStatus.DONE
    )  # verify answered nothing
    db_session.add_all([ticket, instance])
    db_session.commit()

    assert not check_criteria_if_due(db_session, run, ticket)  # the same gap: split

    row = settlements_of(db_session, ticket)[CRITERIA[1]]
    assert row.disposition is CriterionDisposition.DELEGATED and row.by_split
    split = db_session.get(Ticket, row.delegated_to_ticket_id)
    assert split.parent_ticket_id == ticket.parent_ticket_id
    assert load_criteria(split.acceptance_criteria_json) == [CRITERIA[1]]
    assert not check_criteria_if_due(db_session, run, ticket)
    assert _next(db_session, ticket) == "gate"


def test_a_criterion_that_fails_again_after_a_split_goes_to_a_person(db_session, ticket, env):
    origin = make_workspace_ticket(db_session, "cg-origin")
    origin.acceptance_criteria_json = serialize_criteria([CRITERIA[1]])
    db_session.add(origin)
    db_session.commit()
    record_delegation(
        db_session,
        origin,
        CRITERIA[1],
        target=ticket,
        settled_by="control_plane",
        reason="r",
        by_split=True,
    )
    _met(db_session, ticket, CRITERIA[0], "server/tests/test_arms.py::test_reads_width")
    run = _orch_run(db_session, ticket)
    check_criteria_if_due(db_session, run, ticket)
    instance = workflow_instance_for(db_session, ticket.id)
    _, stages = resolve_ticket_stages(db_session, ticket)
    set_stage_status(ticket, instance, stages, "verify", StageStatus.DONE)
    db_session.add_all([ticket, instance])
    db_session.commit()

    assert check_criteria_if_due(db_session, run, ticket)
    db_session.refresh(ticket)
    assert ticket.state is TicketState.BLOCKED
    assert ticket.block_kind is BlockKind.DECISION


def test_tests_that_cannot_run_block_as_the_environment_not_as_rework(db_session, ticket, env):
    env.return_value = _profile(
        [CriterionTestCommand(prefix="server/", command="definitely-not-a-command-xyz {tests}")]
    )
    _met(db_session, ticket, CRITERIA[0], "server/tests/test_arms.py::test_reads_width")
    _met(db_session, ticket, CRITERIA[1], "server/tests/test_arms.py::test_reads_width")
    assert check_criteria_if_due(db_session, _orch_run(db_session, ticket), ticket)
    db_session.refresh(ticket)
    assert ticket.block_kind is BlockKind.HARNESS


def test_the_detector_reports_a_ticket_done_with_a_criterion_unanswered(db_session, ticket):
    _orch_run(db_session, ticket)
    _met(db_session, ticket, CRITERIA[0], "server/tests/test_arms.py::test_reads_width")
    ticket.state = TicketState.DONE
    db_session.add(ticket)
    db_session.commit()

    found = done_tickets_with_unsettled_criteria(db_session)
    assert [v.ticket_id for v in found] == [ticket.id]
    assert "1 criteria" in found[0].detail

    _met(db_session, ticket, CRITERIA[1], "server/tests/test_arms.py::test_names_the_arm")
    assert done_tickets_with_unsettled_criteria(db_session) == []


def test_a_split_shows_in_the_digest_of_the_tree_above_it(db_session, ticket, env):
    _met(db_session, ticket, CRITERIA[0], "server/tests/test_arms.py::test_reads_width")
    run = _orch_run(db_session, ticket)
    check_criteria_if_due(db_session, run, ticket)
    instance = workflow_instance_for(db_session, ticket.id)
    _, stages = resolve_ticket_stages(db_session, ticket)
    set_stage_status(ticket, instance, stages, "verify", StageStatus.DONE)
    db_session.add_all([ticket, instance])
    db_session.commit()
    check_criteria_if_due(db_session, run, ticket)

    milestone = db_session.get(Ticket, ticket.parent_ticket_id)
    digest = auto_decisions_under(db_session, milestone)
    split = db_session.get(
        Ticket, settlements_of(db_session, ticket)[CRITERIA[1]].delegated_to_ticket_id
    )
    assert [(d.external_id, d.decision.value, d.related_external_id) for d in digest] == [
        (ticket.external_id, "split_criteria", split.external_id)
    ]
