import json
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
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
from sqlmodel import Session, col, select

#: SQLite's default parameter limit is 999; a page of tickets is bigger than that.
_ID_CHUNK = 400


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


#: Memo buckets for the block a `stage_resolution_memo()` scope is open over;
#: `None` outside one, which is what every writer and single-ticket reader sees.
_STAGE_RESOLUTION_MEMO: ContextVar[dict[str, dict] | None] = ContextVar(
    "_STAGE_RESOLUTION_MEMO", default=None
)


@contextmanager
def stage_resolution_memo() -> Iterator[None]:
    """Resolve each distinct workflow, and each ticket's instance, once per block.

    A ticket list asks `resolve_ticket_stages` four times per row — template, stage
    cursor, stage views, stage agent — and every one of those re-queries the
    ticket's `WorkflowInstance`, re-reads the workspace override YAML off disk, and
    re-parses the template's `stages_json` through `WorkflowStageDef`. For a page of
    837 tickets sharing one template that is the same answer 3348 times.

    The scope is opened deliberately, around a read that serializes many tickets,
    rather than hung off the session: nothing inside may edit a template, an
    instance or a workspace override and then expect to read its own write. The
    stage key carries the pinned template version, so a ticket held at an older
    snapshot is still resolved separately from one on the live template.
    """
    token = _STAGE_RESOLUTION_MEMO.set({"instances": {}, "stages": {}, "workspaces": {}})
    try:
        yield
    finally:
        _STAGE_RESOLUTION_MEMO.reset(token)


def _memo_bucket(name: str) -> dict | None:
    memo = _STAGE_RESOLUTION_MEMO.get()
    return None if memo is None else memo[name]


def workflow_instance_for(session: Session, ticket_id: str) -> WorkflowInstance | None:
    """This ticket's workflow instance — the memo's copy inside a read scope.

    Every reader of an instance goes through here, `OrchestrationService`
    included, so a page that asks four times per ticket queries once.
    """
    bucket = _memo_bucket("instances")
    if bucket is not None and ticket_id in bucket:
        return bucket[ticket_id]
    instance = session.exec(
        select(WorkflowInstance).where(WorkflowInstance.ticket_id == ticket_id)
    ).first()
    if bucket is not None:
        bucket[ticket_id] = instance
    return instance


def prime_workflow_instances(session: Session, ticket_ids: list[str]) -> None:
    """Load a page's workflow instances in one query instead of one per ticket.

    Outside a `stage_resolution_memo()` scope there is nothing to prime and this
    does nothing — the caller is a single-ticket reader, which wants one query
    either way.
    """
    bucket = _memo_bucket("instances")
    if bucket is None or not ticket_ids:
        return
    for start in range(0, len(ticket_ids), _ID_CHUNK):
        chunk = ticket_ids[start : start + _ID_CHUNK]
        for instance in session.exec(
            select(WorkflowInstance).where(col(WorkflowInstance.ticket_id).in_(chunk))
        ).all():
            bucket.setdefault(instance.ticket_id, instance)
    for ticket_id in ticket_ids:
        # An explicit "this ticket has none", so the miss is not re-queried per row.
        bucket.setdefault(ticket_id, None)


def workspace_for(session: Session, workspace_id: str) -> Workspace | None:
    """A workspace row, held for the length of a read scope.

    SQLAlchemy's identity map holds weak references, so a serializer that reads
    `ws.slug` and drops the row re-queries the same four workspaces once per
    ticket. The memo keeps them alive for the block.
    """
    bucket = _memo_bucket("workspaces")
    if bucket is not None and workspace_id in bucket:
        return bucket[workspace_id]
    ws = session.get(Workspace, workspace_id)
    if bucket is not None:
        bucket[workspace_id] = ws
    return ws


def _resolve_stages(
    session: Session, ws: Workspace, instance: WorkflowInstance | None
) -> tuple[WorkflowTemplate | None, list[WorkflowStageDef]]:
    template: WorkflowTemplate | None = None
    pinned_version: int | None = None
    if instance and instance.template_id:
        template = session.get(WorkflowTemplate, instance.template_id)
        pinned_version = instance.template_version
    if not template and ws.workflow_template_id:
        template = session.get(WorkflowTemplate, ws.workflow_template_id)
    if not template:
        return None, []

    stages = get_template_stages_at_version(session, template, pinned_version)
    override = load_workspace_override(ws.slug)
    if ws.workflow_override_json and ws.workflow_override_json != "{}":
        override = {**override, **json.loads(ws.workflow_override_json)}
    return template, apply_stage_overrides(stages, override)


def resolve_ticket_stages(
    session: Session, ticket: Ticket
) -> tuple[WorkflowTemplate | None, list[WorkflowStageDef]]:
    """Resolve workflow template + stages for a ticket (per-ticket override or workspace default)."""
    if ticket.workflow_disabled:
        return None, []

    ws = workspace_for(session, ticket.workspace_id)
    if not ws:
        return None, []

    instance = workflow_instance_for(session, ticket.id)
    bucket = _memo_bucket("stages")
    if bucket is None:
        template, stages = _resolve_stages(session, ws, instance)
        return template, stages

    # Everything `_resolve_stages` reads: the workspace row, and the template the
    # instance pins it to. Two tickets agreeing on this key cannot disagree on the
    # stages — the same key `ticket_tree_estimate` already caches on.
    key = (
        ws.id,
        instance.template_id if instance else "",
        instance.template_version if instance else None,
    )
    if key not in bucket:
        bucket[key] = _resolve_stages(session, ws, instance)
    template, stages = bucket[key]
    # A copy, so a caller that sorts or trims its stages in place cannot rewrite
    # the answer every later ticket in this block gets.
    return template, list(stages)


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
