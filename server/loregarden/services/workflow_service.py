import json
from pathlib import Path

import yaml
from loregarden.config import settings
from loregarden.core.workflow_loader import get_template_stages, sync_workflow_templates
from loregarden.models.domain import (
    WORKFLOW_WORK_ITEM_TYPES,
    StageStatus,
    Ticket,
    WorkflowInstance,
    WorkflowStageDef,
    WorkflowTemplate,
    Workspace,
)
from loregarden.services.workflow_state import initial_stages_json, reconcile_workflow_state
from sqlmodel import Session, select


def _overrides_dir() -> Path:
    return settings.workflow_templates_dir / "overrides"


def load_workspace_override(workspace_slug: str) -> dict:
    path = _overrides_dir() / f"{workspace_slug}.yaml"
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def apply_stage_overrides(stages: list[WorkflowStageDef], override: dict) -> list[WorkflowStageDef]:
    if not override:
        return stages
    skip = set(override.get("skip_stages", []))
    rename = override.get("rename", {})
    result: list[WorkflowStageDef] = []
    for stage in sorted(stages, key=lambda s: s.order):
        if stage.key in skip:
            continue
        if stage.key in rename:
            stage = stage.model_copy(update={"name": rename[stage.key]})
        result.append(stage)
    return result


def resolve_workspace_stages(
    session: Session, workspace: Workspace
) -> tuple[WorkflowTemplate | None, list[WorkflowStageDef]]:
    if not workspace.workflow_template_id:
        return None, []
    template = session.get(WorkflowTemplate, workspace.workflow_template_id)
    if not template:
        return None, []
    stages = get_template_stages(template)
    override = load_workspace_override(workspace.slug)
    if workspace.workflow_override_json and workspace.workflow_override_json != "{}":
        override = {**override, **json.loads(workspace.workflow_override_json)}
    return template, apply_stage_overrides(stages, override)


def resolve_ticket_stages(
    session: Session, ticket: Ticket
) -> tuple[WorkflowTemplate | None, list[WorkflowStageDef]]:
    """Resolve workflow template + stages for a ticket (per-ticket override or workspace default)."""
    if ticket.workflow_disabled:
        return None, []

    ws = session.get(Workspace, ticket.workspace_id)
    if not ws:
        return None, []

    instance = session.exec(
        select(WorkflowInstance).where(WorkflowInstance.ticket_id == ticket.id)
    ).first()
    template: WorkflowTemplate | None = None
    if instance and instance.template_id:
        template = session.get(WorkflowTemplate, instance.template_id)
    if not template and ws.workflow_template_id:
        template = session.get(WorkflowTemplate, ws.workflow_template_id)
    if not template:
        return None, []

    stages = get_template_stages(template)
    override = load_workspace_override(ws.slug)
    if ws.workflow_override_json and ws.workflow_override_json != "{}":
        override = {**override, **json.loads(ws.workflow_override_json)}
    return template, apply_stage_overrides(stages, override)


class WorkflowService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_templates(self) -> list[WorkflowTemplate]:
        sync_workflow_templates(self.session)
        return list(self.session.exec(select(WorkflowTemplate)).all())

    def get_template_by_slug(self, slug: str) -> WorkflowTemplate | None:
        sync_workflow_templates(self.session)
        return self.session.exec(
            select(WorkflowTemplate).where(WorkflowTemplate.slug == slug)
        ).first()

    def create_workspace(
        self,
        *,
        slug: str,
        name: str,
        workflow_template_slug: str = "loregarden-tdd",
        repo_path: str = ".",
        orchestration_profile_slug: str = "",
    ) -> Workspace:
        existing = self.session.exec(select(Workspace).where(Workspace.slug == slug)).first()
        if existing:
            raise ValueError(f"Workspace already exists: {slug}")
        template = self.get_template_by_slug(workflow_template_slug)
        if not template:
            raise ValueError(f"Unknown workflow template: {workflow_template_slug}")
        ws = Workspace(
            slug=slug,
            name=name,
            repo_path=repo_path,
            workflow_template_id=template.id,
            orchestration_profile_slug=orchestration_profile_slug.strip(),
        )
        self.session.add(ws)
        self.session.commit()
        self.session.refresh(ws)
        return ws

    def set_workspace_template(self, workspace_slug: str, template_slug: str) -> Workspace:
        ws = self.session.exec(select(Workspace).where(Workspace.slug == workspace_slug)).first()
        if not ws:
            raise ValueError(f"Workspace not found: {workspace_slug}")
        template = self.get_template_by_slug(template_slug)
        if not template:
            raise ValueError(f"Unknown workflow template: {template_slug}")
        previous_template_id = ws.workflow_template_id
        ws.workflow_template_id = template.id
        self.session.add(ws)
        self.session.commit()
        self._rebind_ticket_workflows(ws, template, previous_template_id=previous_template_id)
        self.session.refresh(ws)
        return ws

    def clear_ticket_workflow(self, ticket: Ticket) -> None:
        if ticket.work_item_type not in WORKFLOW_WORK_ITEM_TYPES:
            raise ValueError(f"Workflows are not supported for {ticket.work_item_type.value}")

        instance = self.session.exec(
            select(WorkflowInstance).where(WorkflowInstance.ticket_id == ticket.id)
        ).first()
        if instance:
            self.session.delete(instance)

        ticket.workflow_disabled = True
        ticket.workflow_stage_key = ""
        ticket.workflow_stage_status = StageStatus.PENDING
        ticket.next_agent = ""
        ticket.next_status = "Proceed"
        ticket.blocking_issues = ""
        self.session.add(ticket)
        self.session.commit()

    def set_ticket_workflow_template(self, ticket: Ticket, template_slug: str) -> WorkflowInstance:
        if ticket.work_item_type not in WORKFLOW_WORK_ITEM_TYPES:
            raise ValueError(f"Workflows are not supported for {ticket.work_item_type.value}")

        ticket.workflow_disabled = False

        template = self.get_template_by_slug(template_slug)
        if not template:
            raise ValueError(f"Unknown workflow template: {template_slug}")

        ws = self.session.get(Workspace, ticket.workspace_id)
        if not ws:
            raise ValueError("Workspace not found")

        stages = get_template_stages(template)
        override = load_workspace_override(ws.slug)
        if ws.workflow_override_json and ws.workflow_override_json != "{}":
            override = {**override, **json.loads(ws.workflow_override_json)}
        stages = apply_stage_overrides(stages, override)
        if not stages:
            raise ValueError(f"Workflow template has no stages: {template_slug}")

        first_stage = min(stages, key=lambda s: s.order)
        instance = self.session.exec(
            select(WorkflowInstance).where(WorkflowInstance.ticket_id == ticket.id)
        ).first()
        if not instance:
            instance = WorkflowInstance(
                ticket_id=ticket.id,
                template_id=template.id,
                current_stage_key=first_stage.key,
                stages_json=initial_stages_json(stages),
            )
        else:
            instance.template_id = template.id
            instance.current_stage_key = first_stage.key
            instance.stages_json = initial_stages_json(stages)

        ticket.workflow_stage_key = first_stage.key
        ticket.workflow_stage_status = StageStatus.PENDING
        ticket.next_agent = first_stage.agent_id
        reconcile_workflow_state(ticket, instance, stages)
        self.session.add(instance)
        self.session.add(ticket)
        self.session.commit()
        self.session.refresh(instance)
        return instance

    def _rebind_ticket_workflows(
        self,
        workspace: Workspace,
        template: WorkflowTemplate,
        *,
        previous_template_id: str | None = None,
    ) -> None:
        _, stages = resolve_workspace_stages(self.session, workspace)
        tickets = self.session.exec(select(Ticket).where(Ticket.workspace_id == workspace.id)).all()
        for ticket in tickets:
            if ticket.work_item_type not in WORKFLOW_WORK_ITEM_TYPES:
                continue
            if ticket.workflow_disabled:
                continue
            instance = self.session.exec(
                select(WorkflowInstance).where(WorkflowInstance.ticket_id == ticket.id)
            ).first()
            if instance and previous_template_id and instance.template_id != previous_template_id:
                continue
            if not instance:
                instance = WorkflowInstance(
                    ticket_id=ticket.id,
                    template_id=template.id,
                    current_stage_key=ticket.workflow_stage_key,
                    stages_json=initial_stages_json(stages),
                )
            else:
                instance.template_id = template.id
                instance.stages_json = initial_stages_json(stages)
            reconcile_workflow_state(ticket, instance, stages)
            self.session.add(instance)
            self.session.add(ticket)
        self.session.commit()
