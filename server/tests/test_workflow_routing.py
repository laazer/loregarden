import json

import pytest
from fastapi.testclient import TestClient
from loregarden.core.state_machine import StateMachine
from loregarden.core.workflow_loader import sync_workflow_templates
from loregarden.models.domain import (
    StageStatus,
    Ticket,
    TicketState,
    WorkflowInstance,
    WorkflowTemplate,
    WorkItemType,
    Workspace,
)
from loregarden.services.workflow_routing import (
    apply_stage_route,
    previous_stage_key,
    routes_forward,
    valid_route_targets,
)
from loregarden.services.workflow_state import initial_stages_json, parse_stage_map
from sqlmodel import Session, select


def _blobert_transitions() -> list[dict[str, str]]:
    return [
        {"from": "script_review", "to": "ac_gate", "when": "pass"},
        {"from": "script_review", "to": "implementation", "when": "reject"},
        {"from": "ac_gate", "to": "playtest", "when": "pass"},
        {"from": "ac_gate", "to": "implementation", "when": "reject"},
        {"from": "test_design", "to": "specification", "when": "reject"},
    ]


def test_resolve_transition_target_reject_route():
    transitions = _blobert_transitions()
    routed = StateMachine.resolve_transition_target(transitions, "ac_gate", "reject")
    assert routed == ("implementation", "")


def test_resolve_transition_target_pass_route():
    transitions = _blobert_transitions()
    routed = StateMachine.resolve_transition_target(transitions, "ac_gate", "pass")
    assert routed == ("playtest", "")


def test_resolve_transition_target_legacy_linear():
    transitions = [{"from": "planning", "to": "specification"}]
    routed = StateMachine.resolve_transition_target(transitions, "planning", "pass")
    assert routed == ("specification", "")


def test_reset_upstream_stages_reopens_rework_window():
    from loregarden.models.domain import WorkflowStageDef

    stages = [
        WorkflowStageDef(key="implementation", name="Implementation", order=6),
        WorkflowStageDef(key="script_review", name="Script Review", order=7),
        WorkflowStageDef(key="ac_gate", name="AC Gate", order=8),
    ]
    stage_map = {
        "implementation": StageStatus.DONE,
        "script_review": StageStatus.DONE,
        "ac_gate": StageStatus.RUNNING,
    }
    reset = StateMachine.reset_upstream_stages(
        stage_map,
        stages,
        from_key="ac_gate",
        to_key="implementation",
    )
    assert reset["implementation"] == StageStatus.PENDING
    assert reset["script_review"] == StageStatus.PENDING
    assert reset["ac_gate"] == StageStatus.PENDING


def test_complete_stage_routes_upstream_via_api(client: TestClient, db_session: Session):
    sync_workflow_templates(db_session)
    template = db_session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == "blobert-tdd")
    ).first()
    assert template is not None

    ws = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    assert ws is not None

    ticket = Ticket(
        external_id="upstream-route-test",
        workspace_id=ws.id,
        title="Upstream route test",
        description="Verify reject routing",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key="ac_gate",
        workflow_stage_status=StageStatus.RUNNING,
        next_agent="ac_gatekeeper",
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)

    stages = json.loads(template.stages_json)
    from loregarden.core.workflow_loader import get_template_stages

    stage_defs = get_template_stages(template)
    stage_map = {item["key"]: StageStatus.DONE for item in stages if item["key"] != "ac_gate"}
    stage_map["ac_gate"] = StageStatus.RUNNING
    instance = WorkflowInstance(
        ticket_id=ticket.id,
        template_id=template.id,
        current_stage_key="ac_gate",
        stages_json=json.dumps(
            [{"key": key, "status": status.value} for key, status in stage_map.items()]
        ),
    )
    db_session.add(instance)
    db_session.commit()

    started = client.post(
        f"/api/orchestration/tickets/{ticket.id}/start",
        json={"driver": "external_mcp"},
    )
    assert started.status_code == 200
    run_id = started.json()["id"]

    complete = client.post(
        f"/api/orchestration/runs/{run_id}/complete_stage",
        json={
            "stage_key": "ac_gate",
            "outcome": "reject",
            "next_stage_key": "implementation",
            "next_agent": "core_simulation",
            "blocking_issues": "AC-2 missing test evidence",
        },
    )
    assert complete.status_code == 200
    body = complete.json()
    assert body["workflow_stage_key"] == "implementation"

    db_session.refresh(ticket)
    db_session.refresh(instance)
    refreshed_map = parse_stage_map(instance, stage_defs)
    assert refreshed_map["implementation"] == StageStatus.PENDING
    assert refreshed_map["script_review"] == StageStatus.PENDING
    assert refreshed_map["ac_gate"] == StageStatus.PENDING
    assert ticket.next_agent == "core_simulation"
    assert "AC-2" in ticket.blocking_issues


def test_apply_stage_route_uses_template_reject_transition(db_session: Session):
    sync_workflow_templates(db_session)
    template = db_session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == "blobert-tdd")
    ).first()
    assert template is not None
    from loregarden.core.workflow_loader import get_template_stages

    stages = get_template_stages(template)
    transitions = StateMachine.parse_transitions(template.transitions_json)

    ws = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    ticket = Ticket(
        external_id="template-reject-route",
        workspace_id=ws.id,
        title="Template reject route",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key="script_review",
        workflow_stage_status=StageStatus.RUNNING,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)

    instance = WorkflowInstance(
        ticket_id=ticket.id,
        template_id=template.id,
        current_stage_key="script_review",
        stages_json=initial_stages_json(stages),
    )
    db_session.add(instance)
    db_session.commit()

    plan = apply_stage_route(
        ticket,
        instance,
        stages,
        transitions,
        from_key="script_review",
        outcome="reject",
    )
    assert plan.to_key == "implementation"
    assert plan.upstream is True
    assert ticket.workflow_stage_key == "implementation"

    reject = StateMachine.resolve_transition_target(transitions, "script_review", "reject")
    assert reject is not None
    assert reject[0] == "implementation"


def test_apply_stage_route_ignores_next_agent_hint_on_pass():
    """A completing agent's next_agent hint must not hijack a forward (pass)
    transition — only reject/rework routing should honor it. Regression for
    ticket 33's review stage landing on backend_implementer instead of the
    template's architecture_reviewer."""
    from loregarden.models.domain import WorkflowStageDef

    stages = [
        WorkflowStageDef(
            key="implement", name="Implement", agent_id="frontend_implementer", order=1
        ),
        WorkflowStageDef(key="review", name="Review", agent_id="architecture_reviewer", order=2),
    ]
    transitions = [{"from": "implement", "to": "review", "when": "pass"}]

    ticket = Ticket(
        external_id="pass-hint-test",
        workspace_id="ws",
        title="Pass hint test",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key="implement",
        workflow_stage_status=StageStatus.RUNNING,
    )
    instance = WorkflowInstance(
        ticket_id="t1",
        template_id="tpl1",
        current_stage_key="implement",
        stages_json=initial_stages_json(stages),
    )

    plan = apply_stage_route(
        ticket,
        instance,
        stages,
        transitions,
        from_key="implement",
        outcome="pass",
        # A rogue/mistaken hint from the completing agent — must be ignored
        # on a normal pass; the template's own agent assignment must win.
        next_agent="backend_implementer",
    )
    assert plan.to_key == "review"
    assert ticket.next_agent == "architecture_reviewer"


def test_apply_stage_route_honors_next_agent_hint_on_reject():
    from loregarden.models.domain import WorkflowStageDef

    stages = [
        WorkflowStageDef(
            key="implement", name="Implement", agent_id="frontend_implementer", order=1
        ),
        WorkflowStageDef(key="review", name="Review", agent_id="architecture_reviewer", order=2),
    ]
    transitions = [{"from": "review", "to": "implement", "when": "reject"}]

    ticket = Ticket(
        external_id="reject-hint-test",
        workspace_id="ws",
        title="Reject hint test",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key="review",
        workflow_stage_status=StageStatus.RUNNING,
    )
    instance = WorkflowInstance(
        ticket_id="t1",
        template_id="tpl1",
        current_stage_key="review",
        stages_json=initial_stages_json(stages),
    )

    plan = apply_stage_route(
        ticket,
        instance,
        stages,
        transitions,
        from_key="review",
        outcome="reject",
        next_agent="core_simulation",
    )
    assert plan.to_key == "implement"
    assert ticket.next_agent == "core_simulation"


def test_previous_stage_key_returns_immediate_predecessor():
    from loregarden.models.domain import WorkflowStageDef

    stages = [
        WorkflowStageDef(key="spec", name="Spec", order=1),
        WorkflowStageDef(key="implement", name="Implement", order=2),
        WorkflowStageDef(key="review", name="Review", order=3),
    ]
    assert previous_stage_key(stages, "review") == "implement"
    assert previous_stage_key(stages, "implement") == "spec"


def test_previous_stage_key_returns_none_for_first_stage():
    from loregarden.models.domain import WorkflowStageDef

    stages = [
        WorkflowStageDef(key="spec", name="Spec", order=1),
        WorkflowStageDef(key="implement", name="Implement", order=2),
    ]
    assert previous_stage_key(stages, "spec") is None


def test_apply_stage_route_reject_falls_back_to_previous_stage_when_undefined():
    """No template `reject` transition and no explicit next_stage_key — should
    route to the immediately preceding stage rather than raise/stall."""
    from loregarden.models.domain import WorkflowStageDef

    stages = [
        WorkflowStageDef(key="spec", name="Spec", order=1),
        WorkflowStageDef(key="implement", name="Implement", order=2),
        WorkflowStageDef(key="review", name="Review", order=3),
    ]
    transitions = [{"from": "implement", "to": "review", "when": "pass"}]  # no reject route

    ticket = Ticket(
        external_id="fallback-route-test",
        workspace_id="ws",
        title="Fallback route test",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key="review",
        workflow_stage_status=StageStatus.RUNNING,
    )
    instance = WorkflowInstance(
        ticket_id="t1",
        template_id="tpl1",
        current_stage_key="review",
        stages_json=initial_stages_json(stages),
    )

    plan = apply_stage_route(
        ticket,
        instance,
        stages,
        transitions,
        from_key="review",
        outcome="reject",
        blocking_issues="reviewer found regressions",
    )
    assert plan.to_key == "implement"
    assert plan.upstream is True
    assert ticket.workflow_stage_key == "implement"
    assert ticket.blocking_issues == "reviewer found regressions"


def test_apply_stage_route_reject_raises_when_first_stage_has_no_fallback():
    """A failing first-in-order stage has nowhere to fall back to — must
    still raise so the caller can BLOCKED the ticket instead of silently
    stalling on a fabricated route."""
    from loregarden.models.domain import WorkflowStageDef

    stages = [
        WorkflowStageDef(key="spec", name="Spec", order=1),
        WorkflowStageDef(key="implement", name="Implement", order=2),
    ]
    transitions: list[dict[str, str]] = []

    ticket = Ticket(
        external_id="no-fallback-test",
        workspace_id="ws",
        title="No fallback test",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key="spec",
        workflow_stage_status=StageStatus.RUNNING,
    )
    instance = WorkflowInstance(
        ticket_id="t1",
        template_id="tpl1",
        current_stage_key="spec",
        stages_json=initial_stages_json(stages),
    )

    try:
        apply_stage_route(
            ticket,
            instance,
            stages,
            transitions,
            from_key="spec",
            outcome="reject",
        )
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_blobert_template_includes_reject_transitions(client: TestClient, db_session: Session):
    sync_workflow_templates(db_session)
    template = db_session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == "blobert-tdd")
    ).first()
    assert template is not None
    transitions = StateMachine.parse_transitions(template.transitions_json)
    reject_targets = {
        (item["from"], item["to"])
        for item in transitions
        if StateMachine._transition_when(item) == "reject"
    }
    assert ("script_review", "implementation") in reject_targets
    assert ("ac_gate", "implementation") in reject_targets
    assert ("test_design", "specification") in reject_targets


def test_apply_stage_route_reject_ignores_unknown_explicit_stage_key():
    """An agent naming a stage this workflow doesn't have (the real case: a
    gatekeeper asking for `implementation` where the key is `implement`) must
    not park the cursor on a phantom stage — reset_upstream_stages no-ops on an
    unknown target and reconcile then snaps the cursor to the first PENDING
    stage, so the rework silently goes nowhere."""
    from loregarden.models.domain import WorkflowStageDef

    stages = [
        WorkflowStageDef(key="spec", name="Spec", order=1),
        WorkflowStageDef(key="implement", name="Implement", order=2),
        WorkflowStageDef(key="review", name="Review", order=3),
        WorkflowStageDef(key="gate", name="Quality Gate", order=4),
    ]
    transitions = [{"from": "review", "to": "gate", "when": "pass"}]  # no reject route

    ticket = Ticket(
        external_id="unknown-reroute-test",
        workspace_id="ws",
        title="Unknown reroute test",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key="gate",
        workflow_stage_status=StageStatus.RUNNING,
    )
    instance = WorkflowInstance(
        ticket_id="t1",
        template_id="tpl1",
        current_stage_key="gate",
        stages_json=initial_stages_json(stages),
    )

    plan = apply_stage_route(
        ticket,
        instance,
        stages,
        transitions,
        from_key="gate",
        outcome="reject",
        next_stage_key="implementation",  # not a stage key in this workflow
        blocking_issues="receptionist NPC is missing",
    )

    assert plan.to_key == "review"
    assert plan.upstream is True
    assert ticket.workflow_stage_key == "review"
    assert ticket.workflow_stage_status == StageStatus.PENDING
    assert "receptionist NPC is missing" in ticket.blocking_issues
    assert "implementation" in ticket.blocking_issues


def test_apply_stage_route_reject_honors_known_explicit_stage_key():
    """The valid-key path must keep working: a real upstream key routes there
    directly and reopens the intervening stages."""
    from loregarden.models.domain import WorkflowStageDef

    stages = [
        WorkflowStageDef(key="spec", name="Spec", order=1),
        WorkflowStageDef(key="implement", name="Implement", order=2),
        WorkflowStageDef(key="review", name="Review", order=3),
        WorkflowStageDef(key="gate", name="Quality Gate", order=4),
    ]

    ticket = Ticket(
        external_id="known-reroute-test",
        workspace_id="ws",
        title="Known reroute test",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key="gate",
        workflow_stage_status=StageStatus.RUNNING,
    )
    instance = WorkflowInstance(
        ticket_id="t1",
        template_id="tpl1",
        current_stage_key="gate",
        stages_json=initial_stages_json(stages),
    )

    plan = apply_stage_route(
        ticket,
        instance,
        stages,
        [],  # no transitions at all — the explicit key is the only route
        from_key="gate",
        outcome="reject",
        next_stage_key="implement",
        blocking_issues="receptionist NPC is missing",
    )
    assert plan.to_key == "implement"
    assert ticket.workflow_stage_key == "implement"
    stage_map = parse_stage_map(instance, stages)
    assert stage_map["implement"] == StageStatus.PENDING
    assert stage_map["review"] == StageStatus.PENDING
    assert ticket.blocking_issues == "receptionist NPC is missing"


# --- Constrained stage routing (U5a) ---------------------------------------
#
# `next_stage_key` was honored for any existing stage in any direction, so an
# agent could skip forward. `strict=True` marks the live agent path, where the
# ValueError comes back as a tool error it can act on.


def _routing_fixture(current_key: str = "implement"):
    from loregarden.models.domain import WorkflowStageDef

    stages = [
        WorkflowStageDef(key="plan", name="Plan", agent_id="planner", order=1),
        WorkflowStageDef(key="spec", name="Spec", agent_id="spec", order=2),
        WorkflowStageDef(key="implement", name="Implement", agent_id="backend", order=3),
        WorkflowStageDef(key="review", name="Review", agent_id="architecture_reviewer", order=4),
    ]
    transitions = [
        {"from": "implement", "to": "review", "when": "pass"},
        {"from": "implement", "to": "spec", "when": "reject"},
    ]
    ticket = Ticket(
        external_id="u5a-routing",
        workspace_id="ws",
        title="Constrained routing",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key=current_key,
        workflow_stage_status=StageStatus.RUNNING,
    )
    instance = WorkflowInstance(
        ticket_id="t1",
        template_id="tpl1",
        current_stage_key=current_key,
        stages_json=initial_stages_json(stages),
    )
    return stages, transitions, ticket, instance


def test_strict_rejects_unknown_next_stage_key_and_lists_valid_targets():
    stages, transitions, ticket, instance = _routing_fixture()
    with pytest.raises(ValueError) as exc:
        apply_stage_route(
            ticket,
            instance,
            stages,
            transitions,
            from_key="implement",
            outcome="reject",
            next_stage_key="implementation",  # plausible-but-wrong name
            strict=True,
        )
    message = str(exc.value)
    assert "implementation" in message
    # The agent is told what it *may* say, so it can retry.
    assert "plan" in message and "spec" in message


def test_strict_rejects_next_stage_key_on_pass():
    """The field is rework-only; honoring it on a pass let an agent skip stages."""
    stages, transitions, ticket, instance = _routing_fixture()
    with pytest.raises(ValueError, match="only valid with outcome='reject'"):
        apply_stage_route(
            ticket,
            instance,
            stages,
            transitions,
            from_key="implement",
            outcome="pass",
            next_stage_key="review",
            strict=True,
        )


def test_strict_rejects_forward_rework_target():
    stages, transitions, ticket, instance = _routing_fixture()
    with pytest.raises(ValueError, match="must not come after"):
        apply_stage_route(
            ticket,
            instance,
            stages,
            transitions,
            from_key="implement",
            outcome="reject",
            next_stage_key="review",
            strict=True,
        )


def test_strict_allows_self_redo_rework():
    """Gate-autofix routes a stage to itself for a redo — that must stay legal."""
    stages, transitions, ticket, instance = _routing_fixture()
    plan = apply_stage_route(
        ticket,
        instance,
        stages,
        transitions,
        from_key="implement",
        outcome="reject",
        next_stage_key="implement",
        strict=True,
    )
    assert plan.to_key == "implement"
    assert ticket.workflow_stage_key == "implement"


def test_strict_rejects_unknown_from_key():
    """An unknown from-stage fell through to keys[0], rewinding to stage one."""
    stages, transitions, ticket, instance = _routing_fixture()
    with pytest.raises(ValueError, match="Unknown stage key: nope"):
        apply_stage_route(
            ticket,
            instance,
            stages,
            transitions,
            from_key="nope",
            outcome="reject",
            strict=True,
        )


def test_non_strict_discards_unknown_target_and_notes_it():
    """Post-run callers can't retry, so a bad hint falls back — but is recorded."""
    stages, transitions, ticket, instance = _routing_fixture()
    plan = apply_stage_route(
        ticket,
        instance,
        stages,
        transitions,
        from_key="implement",
        outcome="reject",
        next_stage_key="implementation",
        blocking_issues="tests fail",
    )
    assert plan.to_key == "spec"  # template's reject route
    assert "implementation" in ticket.blocking_issues
    assert "tests fail" in ticket.blocking_issues


def test_valid_route_targets_includes_self_and_is_empty_on_pass():
    stages, _, _, _ = _routing_fixture()
    assert valid_route_targets(stages, "implement", "reject") == ["plan", "spec", "implement"]
    assert valid_route_targets(stages, "implement", "pass") == []


def test_routes_forward_direction():
    stages, _, _, _ = _routing_fixture()
    assert routes_forward(stages, "implement", "review") is True
    assert routes_forward(stages, "implement", "spec") is False
    assert routes_forward(stages, "implement", "implement") is False


def test_next_stage_key_unknown_current_does_not_rewind():
    from loregarden.models.domain import WorkflowStageDef

    stages = [
        WorkflowStageDef(key="plan", name="Plan", order=1),
        WorkflowStageDef(key="implement", name="Implement", order=2),
    ]
    assert StateMachine.next_stage_key(stages, "ghost") is None
    # An empty current key still legitimately means "start at the beginning".
    assert StateMachine.next_stage_key(stages, "") == "plan"


def test_resolve_next_stage_key_rejects_unknown_explicit_target():
    from loregarden.models.domain import WorkflowStageDef

    stages = [
        WorkflowStageDef(key="plan", name="Plan", order=1),
        WorkflowStageDef(key="implement", name="Implement", order=2),
    ]
    with pytest.raises(ValueError, match="Unknown target stage 'ghost'"):
        StateMachine.resolve_next_stage_key(
            stages, [], "implement", outcome="reject", explicit_to="ghost"
        )
