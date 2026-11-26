"""What may be put to a person at a human gate, and that nothing signs it in their place."""

from __future__ import annotations

import json

import pytest
from loregarden.config import settings
from loregarden.core.workflow_loader import MAX_INTENT_CHARS, expand_gate_checklist
from loregarden.db.migration_utils import table_columns, table_exists
from loregarden.db.versions.person_judged_criteria import (
    ROLES,
    SKILL_SECTION,
    SKILL_SLUG,
    m_person_judged_criteria,
)
from loregarden.mcp.tools import execute_tool, normalize_tool_arguments
from loregarden.models.domain import (
    AgentRun,
    ArtifactKind,
    CriterionDisposition,
    OrchestrationRun,
    PersonJudgment,
    RunStatus,
    Skill,
    StageReportStatus,
    StageStatus,
    Ticket,
)
from loregarden.services.autopilot_sign_off import signer_for_gate
from loregarden.services.criterion_check import check_criteria
from loregarden.services.criterion_ledger import settle_person, settlements_of, unsettled
from loregarden.services.criterion_review import gate_criteria_lines
from loregarden.services.gate_approvals import create_workflow_gate_approval
from loregarden.services.gate_prep import missing_review_kit
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.person_gate import is_a_persons_gate, ticket_person_gate
from loregarden.services.person_judgment import fact_problem, reason_problem
from loregarden.services.review_kit import ingest_review_kit
from loregarden.services.stage_verdict import collect_signals
from loregarden.services.workflow_service import resolve_ticket_stages, workflow_instance_for
from loregarden.services.workflow_state import set_stage_status
from loregarden.testing.factories import make_artifact
from sqlalchemy import create_engine
from sqlmodel import Session, select
from tests.role_refresh_helpers import strip_role_marker
from tests.test_criterion_gate import CRITERIA, _met, _orch_run, _ticket_on

SIGN_OFF = {
    "exit_actions_enabled": True,
    "exit_actions": [
        {
            "key": "legacy-stage-sign-off",
            "label": "Approve",
            "requirement": {"kind": "operator_judgment", "decision_prompt": "Approve?"},
        }
    ],
}

#: Blobert TDD's spine: implement prepares the playtest, the AC gate settles.
SHAPE = [
    {"key": "spec", "name": "Spec", "agent_id": "spec", "order": 1},
    {"key": "implement", "name": "Implement", "agent_id": "core_simulation", "order": 2},
    {"key": "ac_gate", "name": "AC Gate", "agent_id": "ac_gatekeeper", "order": 3,
     "stage_type": "gate"},
    {"key": "playtest", "name": "Playtest", "agent_id": "", "optional": True, "order": 4,
     "checklist": ["{{ticket_intent}}"], **SIGN_OFF},
    {"key": "done", "name": "Done", "order": 5, "terminal": True},
]  # fmt: skip

#: Loregarden V3's spine: verify settles, and the gate itself asks for the sign-off.
VERIFY_SHAPE = [
    {"key": "implement", "name": "Implement", "agent_id": "backend_implementer", "order": 1},
    {"key": "verify", "name": "Verify", "agent_id": "verifier", "order": 2},
    {"key": "gate", "name": "Gate", "agent_id": "gatekeeper", "order": 3, "stage_type": "gate",
     **SIGN_OFF},
    {"key": "done", "name": "Done", "order": 4, "terminal": True},
]  # fmt: skip

ASK = "Does letting go feel fair?"
WHY = "The timing is proven by test_thrash; only a player can call it fair"


@pytest.fixture(name="ticket")
def ticket_fixture(db_session: Session) -> Ticket:
    return _ticket_on(db_session, SHAPE, through_gate=False)


def _settle(session: Session, ticket: Ticket, **arguments) -> dict:
    run = AgentRun(
        run_code="gk",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="ac_gatekeeper",
        stage_key="ac_gate",
        status=RunStatus.RUNNING,
    )
    session.add(run)
    session.commit()
    name = "loregarden_settle_criterion"
    values = {
        "ticket_id": ticket.id,
        "criterion": CRITERIA[0],
        "disposition": "person",
        "judges": "feel",
        "ask": ASK,
        "reason": WHY,
        **arguments,
    }
    return json.loads(
        execute_tool(
            session, name, normalize_tool_arguments(name, values), orchestrated=True, run_id=run.id
        )
    )


def _persons(session: Session, ticket: Ticket) -> None:
    settle_person(
        session,
        ticket,
        CRITERIA[0],
        judges=PersonJudgment.FEEL,
        ask=ASK,
        commit_sha="",
        settled_by="run:g",
        reason=WHY,
    )


def _stage(session: Session, ticket: Ticket, key: str):
    _, stages = resolve_ticket_stages(session, ticket)
    return next(s for s in stages if s.key == key)


def _set(session: Session, ticket: Ticket, key: str, status: StageStatus) -> None:
    instance = workflow_instance_for(session, ticket.id)
    _, stages = resolve_ticket_stages(session, ticket)
    set_stage_status(ticket, instance, stages, key, status)
    session.add_all([ticket, instance])
    session.commit()


# The words. The refused half is from the kits live on 2026-11-25.


@pytest.mark.parametrize(
    "fact",
    [
        'A short low thud. The yellow text says "attach=failed no_target"',
        "It steps 0.50, then 1.50, then 3.00",
        "You let go and bank= keeps its place",
        'The same throw, labelled "thrash"',
        "After about 2.5 seconds the enemy shakes",
        "The enemy is absorbed about half a second later",
        "The ring pulses about twice a second",
        "HP falls about 1 to 2 points a second",
        "You let go and HP is unchanged",
        "HP at top left drops to about 97 while you hold",
        "The count under the slots goes from 4 to 5",
        "Runs with no console errors",
        "All the tests pass",
        "The state is `GLIDING`",
        "attachment_failed is returned",
        "Open scenes/latch_3d.tscn and it loads",
        "The list endpoint answers HTTP 200",
    ],
)
def test_a_fact_is_not_put_to_a_person(fact):
    assert fact_problem(fact)


@pytest.mark.parametrize(
    "judgment",
    [
        "The blue flash is easy to see and does not look like fire",
        "It feels like a payoff: you notice it without reading the text",
        "You can react to the shake in time, and it does not feel unfair",
        'You can tell the ring means "this is costing you HP"',
        "You can tell amber from grey at a glance",
        "The dots help you pick a target",
        "You understood what to do without help",
        "Getting underneath reads as the trick",
        "You can read when to press from the wings alone",
        "The thud sounds like a rejection, not a hit",
        "Is this the wording you want on the empty page?",
    ],
)
def test_a_judgment_is(judgment):
    assert fact_problem(judgment) == ""


@pytest.mark.parametrize(
    "excuse",
    [
        "There is no test for this",
        "This is untested in the sandbox",
        "I could not launch the editor here",
        "Unable to capture the running scene",
        "n/a",
        "",
    ],
)
def test_missing_agent_work_is_not_a_reason(excuse):
    assert reason_problem(excuse)


def test_a_reason_that_names_the_proof_and_the_judgment_stands():
    assert reason_problem(WHY) == ""


# A criterion settled as a person's.


def test_the_settling_stage_hands_a_judgment_to_the_person(db_session, ticket):
    assert _settle(db_session, ticket)["disposition"] == "person"
    row = settlements_of(db_session, ticket)[CRITERIA[0]]
    assert (row.judges, row.person_ask) == (PersonJudgment.FEEL, ASK)
    assert unsettled(db_session, ticket) == [CRITERIA[1]]


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        ({"ask": "Does HP fall 2 points a second?"}, "measurement"),
        ({"ask": ""}, "ask"),
        ({"judges": ""}, "judges"),
        ({"reason": "There is no test for it yet"}, "still owes"),
    ],
)
def test_a_fact_or_an_excuse_is_refused(db_session, ticket, bad, message):
    with pytest.raises(ValueError, match=message):
        _settle(db_session, ticket, **bad)
    assert settlements_of(db_session, ticket) == {}


def test_it_needs_a_human_gate_still_ahead(db_session, ticket):
    _set(db_session, ticket, "playtest", StageStatus.WONT_DO)
    with pytest.raises(ValueError, match="no human gate ahead"):
        _settle(db_session, ticket)


def test_settling_it_met_later_clears_the_question(db_session, ticket):
    _persons(db_session, ticket)
    _met(db_session, ticket, CRITERIA[0], "server/tests/test_arms.py::test_reads_width")
    row = settlements_of(db_session, ticket)[CRITERIA[0]]
    assert row.disposition is CriterionDisposition.MET
    assert (row.judges, row.person_ask) == (None, "")


def test_the_criteria_check_counts_it_answered_for(db_session, ticket):
    _persons(db_session, ticket)
    assert [gap.criterion for gap in check_criteria(db_session, ticket).gaps] == [CRITERIA[1]]


def test_the_gate_may_not_be_skipped_while_one_waits(db_session, ticket):
    _persons(db_session, ticket)
    with pytest.raises(ValueError, match="where a person answers"):
        OrchestrationCallbackService(db_session).skip_stage(
            _orch_run(db_session, ticket), ticket, stage_key="playtest", reason="not needed"
        )


@pytest.mark.parametrize(("shape", "gate"), [(SHAPE, "playtest"), (VERIFY_SHAPE, "gate")])
def test_automation_never_signs_the_gate_one_waits_at(db_session, shape, gate):
    ticket = _ticket_on(db_session, shape, through_gate=False)
    _set(db_session, ticket, gate, StageStatus.AWAITING)
    assert ticket_person_gate(db_session, ticket).key == gate
    stage = _stage(db_session, ticket, gate)
    approval = create_workflow_gate_approval(db_session, ticket, gate, gate, stage_def=stage)
    run: OrchestrationRun = _orch_run(db_session, ticket)
    run.auto_approve = True
    assert signer_for_gate(db_session, ticket, run, approval) is not None

    _persons(db_session, ticket)

    assert is_a_persons_gate(db_session, ticket, gate)
    assert signer_for_gate(db_session, ticket, run, approval) is None


def test_an_agents_own_gate_before_it_is_still_signed(db_session, ticket):
    _persons(db_session, ticket)
    assert not is_a_persons_gate(db_session, ticket, "ac_gate")


def test_the_gate_brief_lists_only_what_is_a_persons_or_unproven(db_session, ticket):
    assert gate_criteria_lines(db_session, ticket) == [
        "Acceptance criteria:",
        *(f"- {c}" for c in CRITERIA),
    ]
    _persons(db_session, ticket)
    assert gate_criteria_lines(db_session, ticket)[1:] == [
        f"- For you to judge: {ASK}",
        f"- No proof recorded: {CRITERIA[1]}",
    ]
    _met(db_session, ticket, CRITERIA[1], "server/tests/test_arms.py::test_names_the_arm")
    assert gate_criteria_lines(db_session, ticket)[1:] == [
        f"- For you to judge: {ASK}",
        "- 1 of 2 answered for by agents",
    ]


# The kit the gate is prepared with.

STEP = {
    "action": "Press C",
    "expect": "You can tell you were rejected",
    "judges": "clarity",
    "why_person": "Only a player can say it reads",
}
NOTHING = "Every claim is a state change; the attach tests drive the scene and pass."


def _kit(session: Session, ticket: Ticket, review: dict, *, ingest: bool = True) -> None:
    content = {"review": review}
    if ingest:
        content = ingest_review_kit(ticket.id, content)
    make_artifact(
        session, ticket_id=ticket.id, kind=ArtifactKind.EVIDENCE, content=content, title="kit"
    )


def test_the_stage_before_the_gate_does_not_pass_without_a_kit(db_session, ticket):
    assert "judges" in missing_review_kit(db_session, ticket, stage_key="implement")
    assert missing_review_kit(db_session, ticket, stage_key="spec") == ""


def test_its_pass_is_sent_back_naming_the_tool(db_session, ticket):
    signals = collect_signals(
        db_session,
        ticket,
        report=None,
        tool_outcome=StageReportStatus.PASS,
        stage_def=_stage(db_session, ticket, "implement"),
        handoff_expected=False,
        agent_id="core_simulation",
    )
    (held,) = [s for s in signals if s.source == "review_kit"]
    assert held.outcome is StageReportStatus.NEEDS_REWORK
    assert "loregarden_attach_evidence" in held.detail


@pytest.mark.parametrize("review", [{"steps": [STEP]}, {"nothing_to_judge": NOTHING}])
def test_judgments_or_nothing_to_judge_lets_it_pass(db_session, ticket, review):
    _kit(db_session, ticket, review)
    assert missing_review_kit(db_session, ticket, stage_key="implement") == ""


@pytest.mark.parametrize(
    "review",
    [
        {"links": [{"url": "", "path": "/x", "label": ""}]},
        {"steps": [{"action": "Press C", "expect": "bank= steps 0.50"}]},
    ],
)
def test_a_kit_without_judgments_does_not(db_session, ticket, review):
    _kit(db_session, ticket, review, ingest=False)
    assert missing_review_kit(db_session, ticket, stage_key="implement")


def test_a_gate_ruled_out_needs_no_kit(db_session, ticket):
    _set(db_session, ticket, "playtest", StageStatus.WONT_DO)
    assert missing_review_kit(db_session, ticket, stage_key="implement") == ""


def test_the_checklist_quotes_the_point_of_the_ticket_not_its_whole_spec(db_session, ticket):
    ticket.description = (
        "**Source:** backlog/14.md\n\n## Description\n\nA creature you time your jump to.\n\n"
        + "Constants. " * 500
    )
    (item,) = expand_gate_checklist(ticket, ["{{ticket_intent}}"])
    assert item.endswith("— A creature you time your jump to.")
    ticket.description = "word " * 500
    (item,) = expand_gate_checklist(ticket, ["{{ticket_intent}}"])
    assert len(item) < MAX_INTENT_CHARS + 100 and item.endswith("…")


# The migration.


def test_the_seeds_carry_what_the_migration_looks_for():
    for role_file, marker in ROLES.values():
        assert marker in (settings.agent_context_dir / role_file).read_text(encoding="utf-8")
    skill = settings.agent_context_dir / "skills" / SKILL_SLUG / "SKILL.md"
    assert SKILL_SECTION.strip() in skill.read_text(encoding="utf-8")


def test_the_roles_and_skill_are_refreshed_once(isolated_db):
    with Session(isolated_db) as session:
        for slug, (_, marker) in ROLES.items():
            strip_role_marker(session, slug, marker)
        session.add(Skill(slug=SKILL_SLUG, name="n", body="Old body.", built_in=True))
        session.commit()
    with isolated_db.begin() as conn:
        m_person_judged_criteria(conn)
        m_person_judged_criteria(conn)
        assert {"judges", "person_ask"} <= table_columns(conn, "criterion_settlements")
    with Session(isolated_db) as session:
        skill = session.exec(select(Skill).where(Skill.slug == SKILL_SLUG)).one()
        assert skill.body == f"Old body.\n\n{SKILL_SECTION}" and skill.version == 2


def test_an_existing_settlement_gains_the_columns_unset():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE criterion_settlements (id VARCHAR PRIMARY KEY)")
        conn.exec_driver_sql("INSERT INTO criterion_settlements (id) VALUES ('a')")
        m_person_judged_criteria(conn)
        row = conn.exec_driver_sql("SELECT judges, person_ask FROM criterion_settlements").one()
        assert tuple(row) == (None, "")
        assert not table_exists(conn, "skills")
