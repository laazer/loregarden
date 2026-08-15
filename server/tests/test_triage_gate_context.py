"""Triage (Baxter) must know when a ticket is parked at a human verification
gate — a playtest or UX sign-off — and frame the conversation around running
that verification instead of generic ticket Q&A.
"""

from loregarden.core.workflow_loader import (
    GENERIC_INTENT_ITEM,
    UNRESOLVED_SCENES_ITEM,
    expand_gate_checklist,
    get_template_stages,
    sync_workflow_templates,
)
from loregarden.models.domain import (
    StageStatus,
    Ticket,
    TicketState,
    WorkflowInstance,
    WorkflowStageDef,
    WorkflowTemplate,
    WorkItemType,
    Workspace,
)
from loregarden.services.triage_service import (
    _gate_focus_guidance,
    build_triage_prompt,
    current_human_gate_stage,
)
from loregarden.services.workflow_state import initial_stages_json
from sqlmodel import Session, select


def _setup_ticket_at_stage(db_session: Session, stage_key: str, stage_status: StageStatus):
    sync_workflow_templates(db_session)
    template = db_session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == "blobert-tdd")
    ).first()
    assert template is not None
    ws = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    assert ws is not None

    stages = get_template_stages(template)
    ticket = Ticket(
        external_id=f"triage-gate-{stage_key}",
        workspace_id=ws.id,
        title="Triage gate context test",
        description="Verify triage prompt carries human-gate context",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key=stage_key,
        workflow_stage_status=stage_status,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)

    instance = WorkflowInstance(
        ticket_id=ticket.id,
        template_id=template.id,
        current_stage_key=stage_key,
        stages_json=initial_stages_json(stages),
    )
    db_session.add(instance)
    db_session.commit()
    return ticket


def test_triage_prompt_includes_playtest_gate_section(db_session: Session):
    ticket = _setup_ticket_at_stage(db_session, "playtest", StageStatus.AWAITING)

    prompt = build_triage_prompt(
        ticket, [], "how is the change looking?", session=db_session, interactive=True
    )

    assert "## Human verification gate: Playtest" in prompt
    assert "gameplay playtest" in prompt.lower()
    assert "Verification checklist for this gate:" in prompt
    # The workspace fixture has no branch for this ticket, so the scene lookup
    # cannot read a diff and the step is kept in its generic form.
    assert UNRESOLVED_SCENES_ITEM in prompt
    assert "route back to an earlier workflow stage" in prompt


def test_triage_prompt_skips_gate_section_for_agent_stage(db_session: Session):
    ticket = _setup_ticket_at_stage(db_session, "implementation", StageStatus.RUNNING)

    prompt = build_triage_prompt(ticket, [], "status?", session=db_session, interactive=True)

    assert "Human verification gate" not in prompt


def test_current_human_gate_stage_resolves_playtest(db_session: Session):
    ticket = _setup_ticket_at_stage(db_session, "playtest", StageStatus.AWAITING)
    stage = current_human_gate_stage(db_session, ticket)
    assert stage is not None
    assert stage.key == "playtest"


def test_current_human_gate_stage_ignores_done_and_agent_stages(db_session: Session):
    ticket = _setup_ticket_at_stage(db_session, "implementation", StageStatus.RUNNING)
    assert current_human_gate_stage(db_session, ticket) is None

    ticket.workflow_stage_key = "done"
    assert current_human_gate_stage(db_session, ticket) is None


def test_expand_gate_checklist_substitutes_acceptance_criteria():
    ticket = Ticket(
        external_id="expand-ck",
        workspace_id="ws",
        title="t",
        acceptance_criteria_json='["Dash moves the player", "Cooldown blocks re-triggering"]',
    )
    checklist = [
        "Open the affected scenes",
        "{{acceptance_criteria}}",
        "Confirm no console errors/warnings appear during play",
    ]

    assert expand_gate_checklist(ticket, checklist) == [
        "Open the affected scenes",
        "Play-test by hand — Dash moves the player",
        "Play-test by hand — Cooldown blocks re-triggering",
        "Confirm no console errors/warnings appear during play",
    ]


def test_expand_gate_checklist_drops_placeholder_when_no_criteria():
    ticket = Ticket(external_id="expand-empty", workspace_id="ws", title="t")
    checklist = ["Load the scene", "{{acceptance_criteria}}", "Check for regressions"]

    # No acceptance criteria → placeholder expands to nothing, rest passes through.
    assert expand_gate_checklist(ticket, checklist) == [
        "Load the scene",
        "Check for regressions",
    ]


def test_gate_focus_guidance_by_stage_kind():
    playtest = WorkflowStageDef(key="playtest", name="Playtest")
    ux = WorkflowStageDef(key="ux_verification", name="UX Verification")
    generic = WorkflowStageDef(key="approval", name="Approval")

    assert "gameplay playtest" in _gate_focus_guidance(playtest).lower()
    assert "user experience" in _gate_focus_guidance(ux).lower()
    assert "human verification step" in _gate_focus_guidance(generic).lower()


def test_expand_gate_checklist_names_each_changed_scene():
    ticket = Ticket(external_id="expand-scenes", workspace_id="ws", title="t")
    checklist = ["{{playtest_scenes}}", "Check for regressions"]

    assert expand_gate_checklist(
        ticket,
        checklist,
        scenes=["scenes/levels/sandbox/dash.tscn", "scenes/levels/sandbox/hub.tscn"],
    ) == [
        "Open `scenes/levels/sandbox/dash.tscn` in the editor, run it, and play through this "
        "change — it must reach a playable state with no errors",
        "Open `scenes/levels/sandbox/hub.tscn` in the editor, run it, and play through this "
        "change — it must reach a playable state with no errors",
        "Check for regressions",
    ]


def test_expand_gate_checklist_drops_scene_item_when_branch_changes_none():
    """Resolved-and-empty is not the same as unresolved: there is nothing to open."""
    ticket = Ticket(external_id="expand-no-scenes", workspace_id="ws", title="t")

    assert expand_gate_checklist(
        ticket, ["{{playtest_scenes}}", "Check for regressions"], scenes=[]
    ) == [
        "Check for regressions",
    ]


def test_expand_gate_checklist_keeps_scene_item_when_diff_unreadable():
    """An unreadable diff must not silently delete the run-the-scene step."""
    ticket = Ticket(external_id="expand-unresolved", workspace_id="ws", title="t")

    assert expand_gate_checklist(ticket, ["{{playtest_scenes}}"], scenes=None) == [
        UNRESOLVED_SCENES_ITEM,
    ]


def test_expand_gate_checklist_asks_the_operator_to_judge_the_ticket_intent():
    """The one thing no upstream agent can sign off: did it deliver what was asked."""
    ticket = Ticket(
        external_id="expand-intent",
        workspace_id="ws",
        title="Dash",
        description="Add a dash ability with a cooldown timer.",
    )

    assert expand_gate_checklist(ticket, ["{{ticket_intent}}"]) == [
        "Play it and judge whether it delivers what the ticket asked for — "
        "Add a dash ability with a cooldown timer."
    ]


def test_expand_gate_checklist_falls_back_when_the_ticket_has_no_description():
    ticket = Ticket(external_id="expand-no-desc", workspace_id="ws", title="Dash")

    assert expand_gate_checklist(ticket, ["{{ticket_intent}}"]) == [GENERIC_INTENT_ITEM]


def test_the_seeded_playtest_gate_asks_for_nothing_an_agent_could_sign_off(db_session: Session):
    """Regression guard on the template itself, not just the expansion code."""
    sync_workflow_templates(db_session)
    template = db_session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == "blobert-tdd")
    ).first()
    assert template is not None
    stages = {s.key: s for s in get_template_stages(template)}

    assert stages["playtest"].checklist == ["{{playtest_scenes}}", "{{ticket_intent}}"]
    # The retired duties are now owned by the stages that can actually do them.
    assert "console errors" in stages["implementation"].stage_brief
    assert "regressions" in stages["script_review"].stage_brief
