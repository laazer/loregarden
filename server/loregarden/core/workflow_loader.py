import json
from pathlib import Path
from uuid import uuid4

import yaml
from loregarden.config import settings
from loregarden.models.domain import (
    Ticket,
    WorkflowStageDef,
    WorkflowTemplate,
    WorkflowTemplateVersion,
)
from sqlmodel import Session, select

# Fields captured verbatim in each WorkflowTemplateVersion snapshot. Must match the
# migration backfill (0022) so restore round-trips cleanly.
_TEMPLATE_SNAPSHOT_FIELDS = (
    "slug",
    "name",
    "description",
    "stages_json",
    "transitions_json",
    "source_path",
    "built_in",
)


def template_snapshot(tpl: WorkflowTemplate) -> dict:
    return tpl.model_dump(include=set(_TEMPLATE_SNAPSHOT_FIELDS))


def write_template_version(
    session: Session, tpl: WorkflowTemplate, *, created_by: str, change_note: str = ""
) -> None:
    session.add(
        WorkflowTemplateVersion(
            id=str(uuid4()),
            template_id=tpl.id,
            version=tpl.version,
            snapshot_json=json.dumps(template_snapshot(tpl)),
            created_by=created_by,
            change_note=change_note or "",
        )
    )


AC_CHECKLIST_PLACEHOLDER = "{{acceptance_criteria}}"


def expand_gate_checklist(ticket: Ticket, checklist: list[str]) -> list[str]:
    """Expand a stage's static checklist into ticket-specific items.

    An ``{{acceptance_criteria}}`` entry is replaced by one concrete play-test
    item per acceptance criterion, so each gate lists what actually needs
    exercising for this change instead of the same generic bullet every time.
    Every other entry passes through unchanged, and a ticket with no acceptance
    criteria simply drops the placeholder.

    Idempotent: an already-expanded checklist contains no placeholder and is
    returned as-is. Callers apply this on both the write and read path so a raw
    token can never reach the UI, even if a gate was recorded while the workflow
    yaml and this code were out of sync.
    """
    try:
        criteria = json.loads(ticket.acceptance_criteria_json or "[]")
    except json.JSONDecodeError:
        criteria = []
    expanded: list[str] = []
    for item in checklist:
        if item.strip() == AC_CHECKLIST_PLACEHOLDER:
            expanded.extend(
                f"Play-test by hand — {str(c).strip()}" for c in criteria if str(c).strip()
            )
        else:
            expanded.append(item)
    return expanded


def load_workflow_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def sync_workflow_templates(session: Session) -> list[WorkflowTemplate]:
    """Seed built-in workflow templates from YAML **when missing**.

    The DB is the source of truth for workflow templates. This is a one-time seed:
    an existing slug is left untouched (never overwritten from YAML), so DB edits
    and version history survive. Returns the templates newly seeded this call.
    """
    templates_dir = settings.workflow_templates_dir
    if not templates_dir.exists():
        return []

    seeded: list[WorkflowTemplate] = []
    for path in sorted(templates_dir.glob("*.yaml")):
        data = load_workflow_yaml(path)
        slug = data["slug"]
        existing = session.exec(
            select(WorkflowTemplate).where(WorkflowTemplate.slug == slug)
        ).first()
        if existing:
            continue
        tpl = WorkflowTemplate(
            slug=slug,
            name=data.get("name", slug),
            description=data.get("description", ""),
            stages_json=json.dumps(data.get("stages", [])),
            transitions_json=json.dumps(data.get("transitions", [])),
            source_path=str(path.relative_to(settings.repo_root)),
            version=1,
            built_in=True,
        )
        session.add(tpl)
        session.flush()
        write_template_version(session, tpl, created_by="seed")
        seeded.append(tpl)
    if seeded:
        session.commit()
    for tpl in seeded:
        session.refresh(tpl)
    return seeded


def get_template_stages(template: WorkflowTemplate) -> list[WorkflowStageDef]:
    raw = json.loads(template.stages_json or "[]")
    return [WorkflowStageDef.model_validate(item) for item in raw]


def get_template_stages_at_version(
    session: Session, template: WorkflowTemplate, version: int | None
) -> list[WorkflowStageDef]:
    """Resolve stage definitions from a pinned template version snapshot, so an
    in-flight ticket runs against the definition it started under even if the
    template is later edited. Falls back to the live template when unpinned
    (pre-versioning rows) or when the snapshot is missing."""
    if version is None or version == template.version:
        return get_template_stages(template)
    row = session.exec(
        select(WorkflowTemplateVersion).where(
            WorkflowTemplateVersion.template_id == template.id,
            WorkflowTemplateVersion.version == version,
        )
    ).first()
    if not row:
        return get_template_stages(template)
    snap = json.loads(row.snapshot_json or "{}")
    raw = json.loads(snap.get("stages_json") or "[]")
    return [WorkflowStageDef.model_validate(item) for item in raw]


def stage_display_name(template: WorkflowTemplate, stage_key: str) -> str:
    for stage in get_template_stages(template):
        if stage.key == stage_key:
            return stage.name
    return stage_key.replace("_", " ").title()
