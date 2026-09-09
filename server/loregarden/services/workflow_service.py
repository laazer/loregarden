import json
from pathlib import Path

import yaml
from loregarden.config import settings
from loregarden.core.workflow_loader import (
    get_template_stages,
    get_template_stages_at_version,
    sync_workflow_templates,
)
from loregarden.models.domain import (
    WORKFLOW_WORK_ITEM_TYPES,
    StageStatus,
    Ticket,
    WorkflowInstance,
    WorkflowStageDef,
    WorkflowTemplate,
    Workspace,
)
from loregarden.services.ticket_rollup import has_children
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


#: Keys of the session-scoped memos, held in ``Session.info``.
_STAGE_MEMO = "_resolved_stage_memo"
_OVERRIDE_MEMO = "_workspace_override_memo"


def _merged_override(session: Session, workspace: Workspace) -> dict:
    """A workspace's stage override: the YAML on disk, then its stored JSON.

    Memoised per session. The disk read depends on nothing but the workspace,
    so it must not repeat per ticket — or per pinned template version, which
    is why this is keyed more coarsely than the stage memo.
    """
    memo: dict[tuple[str, str], dict] = session.info.setdefault(_OVERRIDE_MEMO, {})
    key = (workspace.slug, workspace.workflow_override_json or "")
    override = memo.get(key)
    if override is None:
        override = load_workspace_override(workspace.slug)
        if workspace.workflow_override_json and workspace.workflow_override_json != "{}":
            override = {**override, **json.loads(workspace.workflow_override_json)}
        memo[key] = override
    return override


def resolve_workspace_stages(
    session: Session, workspace: Workspace
) -> tuple[WorkflowTemplate | None, list[WorkflowStageDef]]:
    if not workspace.workflow_template_id:
        return None, []
    template = session.get(WorkflowTemplate, workspace.workflow_template_id)
    if not template:
        return None, []
    stages = get_template_stages(template)
    return template, apply_stage_overrides(stages, _merged_override(session, workspace))


def resolve_ticket_stages(
    session: Session, ticket: Ticket
) -> tuple[WorkflowTemplate | None, list[WorkflowStageDef]]:
    """Resolve workflow template + stages for a ticket (per-ticket override or workspace default).

    Memoised per session, because the tail of this function is expensive and
    identical for every ticket sharing a template: it parses the template's
    ``stages_json``, validates every stage, and reads the workspace override
    **off disk**. A page of tickets used to pay all three per row — 836 tickets
    meant 836 YAML reads of the same file.

    The key names everything the result depends on, `template.version`
    included, so a template edited mid-session (studio publish bumps it) is not
    served from the memo. The memo lives on the `Session`, which the API
    creates per request, so nothing survives the response.
    """
    if ticket.workflow_disabled:
        return None, []

    ws = session.get(Workspace, ticket.workspace_id)
    if not ws:
        return None, []

    instance = session.exec(
        select(WorkflowInstance).where(WorkflowInstance.ticket_id == ticket.id)
    ).first()
    template: WorkflowTemplate | None = None
    pinned_version: int | None = None
    if instance and instance.template_id:
        template = session.get(WorkflowTemplate, instance.template_id)
        pinned_version = instance.template_version
    if not template and ws.workflow_template_id:
        template = session.get(WorkflowTemplate, ws.workflow_template_id)
    if not template:
        return None, []

    memo: dict[tuple, list[WorkflowStageDef]] = session.info.setdefault(_STAGE_MEMO, {})
    key = (
        ws.id,
        ws.slug,
        ws.workflow_override_json,
        template.id,
        template.version,
        pinned_version,
    )
    cached = memo.get(key)
    if cached is None:
        stages = get_template_stages_at_version(session, template, pinned_version)
        cached = apply_stage_overrides(stages, _merged_override(session, ws))
        memo[key] = cached
    # A copy per caller: the memo hands out the same list to every ticket that
    # shares a template, and a caller that sorted or trimmed it in place would
    # corrupt every later read in the request.
    return template, list(cached)


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
                template_version=template.version,
                current_stage_key=first_stage.key,
                stages_json=initial_stages_json(stages),
            )
        else:
            instance.template_id = template.id
            instance.template_version = template.version
            instance.current_stage_key = first_stage.key
            instance.stages_json = initial_stages_json(stages)

        ticket.workflow_stage_key = first_stage.key
        ticket.workflow_stage_status = StageStatus.PENDING
        reconcile_workflow_state(
            ticket, instance, stages, owns_state=not has_children(self.session, ticket.id)
        )
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
                    template_version=template.version,
                    current_stage_key=ticket.workflow_stage_key,
                    stages_json=initial_stages_json(stages),
                )
            else:
                instance.template_id = template.id
                instance.template_version = template.version
                instance.stages_json = initial_stages_json(stages)
            reconcile_workflow_state(
                ticket, instance, stages, owns_state=not has_children(self.session, ticket.id)
            )
            self.session.add(instance)
            self.session.add(ticket)
        self.session.commit()
