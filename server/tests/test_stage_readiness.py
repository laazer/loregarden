"""A stage is not spawned when a check that costs nothing already knows its answer.

lg-milestone-that-2468. Three answers, each tested where it acts:

- a stage's declared inputs are read before its agent is spawned, through git
  in a real checkout and the ticket row, and a missing one blocks it (the
  drivers) or refuses it (`start_run`), naming what is missing;
- a review lens that passed on a tree nothing has changed since is carried
  forward, and the history says so with the commit range;
- both are counted per stage with the tokens they spared.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from loregarden.db.versions.stage_required_inputs import m_stage_required_inputs
from loregarden.models.domain import (
    AgentRun,
    DomainEvent,
    EventType,
    OrchestrationRun,
    RunStatus,
    StageInput,
    StageStatus,
    Ticket,
    TicketState,
    WorkflowStageDef,
    WorkflowTemplate,
    Workspace,
)
from loregarden.services.acceptance_criteria import serialize_criteria
from loregarden.services.criterion_gate import before_next_stage
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.review_relens import decide_lenses, record_relens_decisions
from loregarden.services.seed import seed_database
from loregarden.services.stage_readiness import (
    StageInputsMissing,
    missing_inputs,
    readiness_counts,
)
from loregarden.services.ticket_worktree import TicketCheckout
from loregarden.services.workflow_service import resolve_ticket_stages, workflow_instance_for
from loregarden.services.workflow_state import set_stage_status
from sqlalchemy import text
from sqlmodel import Session, select
from tests.factories import make_orchestration_run, make_workspace_ticket

CRITERIA = ["The width is read on every arm"]
REVIEWERS = ["architecture_reviewer", "static_qa"]

#: The live V3 spine, with the inputs the migration declares.
SHAPE = [
    {"key": "implement", "name": "Implement", "agent_id": "backend_implementer", "order": 1},
    {
        "key": "verify",
        "name": "Verify",
        "agent_id": "verifier",
        "order": 2,
        "stage_type": "verify",
        "required_inputs": ["diff", "acceptance_criteria"],
    },
    {
        "key": "review",
        "name": "Review",
        "order": 3,
        "stage_type": "parallel",
        "parallel_agents": [{"agent_id": agent} for agent in REVIEWERS],
        "required_inputs": ["diff"],
    },
    {"key": "done", "name": "Done", "order": 4, "terminal": True},
]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "HOME": str(repo)},
    ).stdout.strip()


@pytest.fixture(name="repo")
def repo_fixture(tmp_path: Path) -> Path:
    """A checkout on a ticket branch cut from `main`, with no change of its own yet.

    In its own directory: the test database lives in ``tmp_path`` and would
    read as an untracked change.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")
    _git(repo, "checkout", "-q", "-b", "ticket")
    return repo


@pytest.fixture(name="checkout")
def checkout_fixture(repo: Path):
    """Point readiness at the throwaway checkout, cut from `main`."""
    with patch(
        "loregarden.services.stage_readiness.resolve_ticket_checkout",
        return_value=TicketCheckout(repo, "main"),
    ) as patched:
        yield patched


def _commit(repo: Path, name: str = "b.py", body: str = "y = 2\n") -> str:
    (repo / name).write_text(body)
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", name)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture(name="ticket")
def ticket_fixture(db_session: Session) -> Ticket:
    """A ticket with criteria, implement done: verify is next."""
    seed_database(db_session)
    workspace = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()
    template = WorkflowTemplate(slug="readiness", name="Readiness", stages_json=json.dumps(SHAPE))
    db_session.add(template)
    db_session.flush()
    workspace.workflow_template_id = template.id
    db_session.add(workspace)
    db_session.commit()
    ticket = make_workspace_ticket(db_session, "rd-ticket")
    ticket.acceptance_criteria_json = serialize_criteria(CRITERIA)
    db_session.add(ticket)
    db_session.commit()
    OrchestrationService(db_session).ensure_workflow_instance(ticket, commit=True)
    _set(db_session, ticket, "implement", StageStatus.DONE)
    return ticket


def _set(session: Session, ticket: Ticket, key: str, status: StageStatus) -> None:
    instance = workflow_instance_for(session, ticket.id)
    _, stages = resolve_ticket_stages(session, ticket)
    set_stage_status(ticket, instance, stages, key, status)
    session.add_all([ticket, instance])
    session.commit()


def _stage(session: Session, ticket: Ticket, key: str) -> WorkflowStageDef:
    _, stages = resolve_ticket_stages(session, ticket)
    return next(stage for stage in stages if stage.key == key)


def _decisions(session: Session, ticket: Ticket, kind: str) -> list[dict]:
    rows = session.exec(
        select(DomainEvent).where(
            DomainEvent.ticket_id == ticket.id, DomainEvent.type == EventType.ORCHESTRATOR_DECISION
        )
    ).all()
    payloads = [json.loads(row.payload_json) for row in rows]
    return [payload for payload in payloads if payload["decision"] == kind]


def _orch_run(session: Session, ticket: Ticket) -> OrchestrationRun:
    return make_orchestration_run(
        session, workspace_id=ticket.workspace_id, ticket_id=ticket.id, run_code="o-rd"
    )


def _runs_at(session: Session, ticket: Ticket, stage_key: str) -> list[AgentRun]:
    return list(
        session.exec(
            select(AgentRun).where(AgentRun.ticket_id == ticket.id, AgentRun.stage_key == stage_key)
        ).all()
    )


# --- the inputs -----------------------------------------------------------


def test_a_branch_with_no_change_is_missing_its_diff(db_session, ticket, checkout):
    [missing] = missing_inputs(db_session, ticket, _stage(db_session, ticket, "verify"))
    assert missing.input is StageInput.DIFF
    assert "no change against main" in missing.why


def test_a_committed_change_is_a_diff(db_session, ticket, checkout, repo):
    _commit(repo)
    assert missing_inputs(db_session, ticket, _stage(db_session, ticket, "verify")) == []


def test_an_uncommitted_or_untracked_change_is_a_diff(db_session, ticket, checkout, repo):
    (repo / "new.py").write_text("z = 3\n")
    assert missing_inputs(db_session, ticket, _stage(db_session, ticket, "verify")) == []
    (repo / "new.py").unlink()
    (repo / "a.py").write_text("x = 2\n")
    assert missing_inputs(db_session, ticket, _stage(db_session, ticket, "verify")) == []


def test_a_diff_that_cannot_be_read_does_not_block(db_session, ticket, checkout, repo):
    """No parent, or a parent git cannot name: unknown, so the stage runs as before."""
    checkout.return_value = TicketCheckout(repo, "")
    assert missing_inputs(db_session, ticket, _stage(db_session, ticket, "verify")) == []
    checkout.return_value = TicketCheckout(repo, "no-such-branch")
    assert missing_inputs(db_session, ticket, _stage(db_session, ticket, "verify")) == []


def test_a_ticket_with_no_criteria_is_missing_them(db_session, ticket, checkout, repo):
    _commit(repo)
    ticket.acceptance_criteria_json = "[]"
    [missing] = missing_inputs(db_session, ticket, _stage(db_session, ticket, "verify"))
    assert missing.input is StageInput.ACCEPTANCE_CRITERIA
    # A review lens declares only the diff, so the same ticket is ready for it.
    assert missing_inputs(db_session, ticket, _stage(db_session, ticket, "review")) == []


def test_a_stage_that_declares_nothing_is_never_checked(db_session, ticket, checkout):
    ticket.acceptance_criteria_json = "[]"
    assert missing_inputs(db_session, ticket, _stage(db_session, ticket, "implement")) == []


# --- AC1: not spawned -----------------------------------------------------


def test_a_driver_blocks_the_stage_without_spawning_naming_the_input(db_session, ticket, checkout):
    ticket.acceptance_criteria_json = "[]"
    db_session.add(ticket)
    db_session.commit()

    assert before_next_stage(db_session, _orch_run(db_session, ticket), ticket)

    db_session.refresh(ticket)
    assert ticket.state == TicketState.BLOCKED
    assert "missing diff, acceptance_criteria" in ticket.blocking_issues
    assert _runs_at(db_session, ticket, "verify") == []
    [recorded] = _decisions(db_session, ticket, "stage_inputs_missing")
    assert recorded["stage_key"] == "verify"
    assert recorded["missing_inputs"] == ["diff", "acceptance_criteria"]
    assert "Re-run the stage that writes the code" in recorded["reason"]


def test_a_driver_lets_a_ready_stage_through(db_session, ticket, checkout, repo):
    _commit(repo)
    assert not before_next_stage(db_session, _orch_run(db_session, ticket), ticket)
    assert _decisions(db_session, ticket, "stage_inputs_missing") == []


def test_a_start_by_hand_is_refused_before_any_write(db_session, ticket, checkout):
    with pytest.raises(StageInputsMissing, match="'verify' on rd-ticket was not started"):
        OrchestrationService(db_session).start_run(ticket, stage_key="verify")

    db_session.refresh(ticket)
    assert _runs_at(db_session, ticket, "verify") == []
    assert ticket.workflow_stage_status != StageStatus.RUNNING
    assert _decisions(db_session, ticket, "stage_inputs_missing")


def test_a_start_by_hand_runs_once_the_input_exists(db_session, ticket, checkout, repo):
    _commit(repo)
    run = OrchestrationService(db_session).start_run(ticket, stage_key="verify")
    assert run.stage_key == "verify"


# --- AC2: carried forward -------------------------------------------------


def _lens_pass(session: Session, ticket: Ticket, agent_id: str, head: str, *, dirty=()) -> AgentRun:
    run = AgentRun(
        run_code=f"run-{agent_id}",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id=agent_id,
        stage_key="review",
        status=RunStatus.SUCCEEDED,
        stdout='<<<LOREGARDEN_STAGE_REPORT>>>\n{"status": "pass"}\n<<<END_STAGE_REPORT>>>\n',
        start_head_sha=head,
        start_dirty_paths_json=json.dumps(list(dirty)),
        input_tokens=1000,
        output_tokens=50,
    )
    session.add(run)
    session.commit()
    return run


def _decide(session: Session, ticket: Ticket):
    stage = _stage(session, ticket, "review")
    return stage, {d.spec.agent_id: d for d in decide_lenses(session, ticket, stage, "review")}


def test_a_lens_that_passed_on_an_unchanged_tree_is_carried_forward(
    db_session, ticket, checkout, repo
):
    """Even with no record of what it read: nothing changed for it to read."""
    head = _commit(repo)
    for agent in REVIEWERS:
        _lens_pass(db_session, ticket, agent, head)

    stage, decisions = _decide(db_session, ticket)
    assert not any(d.rerun for d in decisions.values())
    assert decisions["static_qa"].commit_range == f"{head[:9]}..{head[:9]}"

    record_relens_decisions(db_session, ticket, stage, "review", list(decisions.values()))
    carried = _decisions(db_session, ticket, "carried_forward_pass")
    assert {c["lens"] for c in carried} == set(REVIEWERS)
    assert carried[0]["commit_range"] == f"{head[:9]}..{head[:9]}"
    assert carried[0]["tokens_saved"] == 1050


def test_an_empty_commit_since_the_pass_still_carries(db_session, ticket, checkout, repo):
    head = _commit(repo)
    _lens_pass(db_session, ticket, "static_qa", head)
    _git(repo, "commit", "-q", "--allow-empty", "-m", "nothing")
    now = _git(repo, "rev-parse", "HEAD")

    _, decisions = _decide(db_session, ticket)
    assert decisions["static_qa"].rerun is False
    assert decisions["static_qa"].commit_range == f"{head[:9]}..{now[:9]}"


def test_a_change_since_the_pass_reruns_the_lens(db_session, ticket, checkout, repo):
    head = _commit(repo)
    _lens_pass(db_session, ticket, "static_qa", head)
    _commit(repo, "c.py")

    _, decisions = _decide(db_session, ticket)
    # No record of its reads either, so nothing else can spare it.
    assert decisions["static_qa"].rerun is True


def test_an_uncommitted_change_reruns_the_lens(db_session, ticket, checkout, repo):
    head = _commit(repo)
    _lens_pass(db_session, ticket, "static_qa", head)
    (repo / "a.py").write_text("x = 9\n")

    _, decisions = _decide(db_session, ticket)
    assert decisions["static_qa"].rerun is True


def test_a_pass_that_started_on_a_dirty_tree_reruns(db_session, ticket, checkout, repo):
    """What it read was not the commit it names, so the commit proves nothing."""
    head = _commit(repo)
    _lens_pass(db_session, ticket, "static_qa", head, dirty=["a.py"])

    _, decisions = _decide(db_session, ticket)
    assert decisions["static_qa"].rerun is True


def test_a_lens_that_did_not_pass_is_never_carried(db_session, ticket, checkout, repo):
    head = _commit(repo)
    run = _lens_pass(db_session, ticket, "static_qa", head)
    run.stdout = '<<<LOREGARDEN_STAGE_REPORT>>>\n{"status": "reject"}\n<<<END_STAGE_REPORT>>>\n'
    db_session.add(run)
    db_session.commit()

    _, decisions = _decide(db_session, ticket)
    assert decisions["static_qa"].rerun is True


# --- AC3: counted ---------------------------------------------------------


def test_blocks_and_carries_are_counted_per_stage_with_the_tokens_spared(
    db_session, ticket, checkout, repo
):
    ticket.acceptance_criteria_json = "[]"
    db_session.add(ticket)
    db_session.commit()
    before_next_stage(db_session, _orch_run(db_session, ticket), ticket)

    head = _commit(repo)
    for agent in REVIEWERS:
        _lens_pass(db_session, ticket, agent, head)
    stage, decisions = _decide(db_session, ticket)
    record_relens_decisions(db_session, ticket, stage, "review", list(decisions.values()))

    counts = {count.stage_key: count for count in readiness_counts(db_session)}
    assert counts["verify"].inputs_missing == 1
    # No run has ever succeeded at verify here, so its estimate is unmeasured.
    assert counts["verify"].unmeasured == 1
    assert counts["review"].carried_forward == 2
    assert counts["review"].tokens_saved == 2100


# --- the migration --------------------------------------------------------


def test_the_migration_declares_inputs_by_role_and_leaves_edits_alone(db_session: Session):
    stages = [
        {"key": "plan", "stage_type": "parallel", "parallel_agents": [{"agent_id": "planner"}]},
        {"key": "verify", "stage_type": "verify", "agent_id": "verifier"},
        {"key": "review", "stage_type": "parallel", "parallel_agents": [{"agent_id": "static_qa"}]},
        {"key": "gate", "stage_type": "gate", "agent_id": "gatekeeper"},
        {"key": "signoff", "agent_id": "ac_gatekeeper"},
        {"key": "edited", "stage_type": "gate", "required_inputs": ["acceptance_criteria"]},
    ]
    template = WorkflowTemplate(slug="mig", name="Mig", stages_json=json.dumps(stages), version=3)
    db_session.add(template)
    db_session.commit()

    m_stage_required_inputs(db_session.connection())
    m_stage_required_inputs(db_session.connection())  # a second run changes nothing

    row = (
        db_session.connection()
        .execute(text("SELECT stages_json, version FROM workflow_templates WHERE slug='mig'"))
        .one()
    )
    declared = {s["key"]: s.get("required_inputs") for s in json.loads(row.stages_json)}
    assert declared == {
        "plan": None,
        "verify": ["diff", "acceptance_criteria"],
        "review": ["diff"],
        "gate": ["diff", "acceptance_criteria"],
        "signoff": ["diff", "acceptance_criteria"],
        "edited": ["acceptance_criteria"],
    }
    assert row.version == 4
    assert [StageInput(v) for v in declared["verify"]] == [
        StageInput.DIFF,
        StageInput.ACCEPTANCE_CRITERIA,
    ]
