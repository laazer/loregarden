import json
from pathlib import Path

import yaml
from sqlmodel import Session, select

from loregarden.config import settings
from loregarden.core.workflow_loader import get_template_stages, sync_workflow_templates
from loregarden.services.workflow_state import initial_stages_json, reconcile_workflow_state
from loregarden.models.domain import (
    Ticket,
    WorkflowInstance,
    WorkflowStageDef,
    WorkflowTemplate,
    Workspace,
)


def _overrides_dir() -> Path:
    return settings.workflow_templates_dir / "overrides"


def load_workspace_override(workspace_slug: str) -> dict:
    path = _overrides_dir() / f"{workspace_slug}.yaml"
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def apply_stage_overrides(
    stages: list[WorkflowStageDef], override: dict
) -> list[WorkflowStageDef]:
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
        )
        self.session.add(ws)
        self.session.commit()
        self.session.refresh(ws)
        return ws

    def set_workspace_template(self, workspace_slug: str, template_slug: str) -> Workspace:
        ws = self.session.exec(
            select(Workspace).where(Workspace.slug == workspace_slug)
        ).first()
        if not ws:
            raise ValueError(f"Workspace not found: {workspace_slug}")
        template = self.get_template_by_slug(template_slug)
        if not template:
            raise ValueError(f"Unknown workflow template: {template_slug}")
        ws.workflow_template_id = template.id
        self.session.add(ws)
        self.session.commit()
        self._rebind_ticket_workflows(ws, template)
        self.session.refresh(ws)
        return ws

    def _rebind_ticket_workflows(self, workspace: Workspace, template: WorkflowTemplate) -> None:
        _, stages = resolve_workspace_stages(self.session, workspace)
        tickets = self.session.exec(
            select(Ticket).where(Ticket.workspace_id == workspace.id)
        ).all()
        for ticket in tickets:
            instance = self.session.exec(
                select(WorkflowInstance).where(WorkflowInstance.ticket_id == ticket.id)
            ).first()
            if not instance:
                instance = WorkflowInstance(
                    ticket_id=ticket.id,
                    template_id=template.id,
                    current_stage_key=ticket.workflow_stage_key,
                    stages_json=initial_stages_json(stages),
                )
            else:
                instance.template_id = template.id
            reconcile_workflow_state(ticket, instance, stages)
            self.session.add(instance)
            self.session.add(ticket)
        self.session.commit()
