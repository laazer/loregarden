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
PLAYTEST_SCENES_PLACEHOLDER = "{{playtest_scenes}}"

#: What the operator is told when the scenes this change touches could not be
#: read off the branch — a missing repo, an unknown branch, a git failure. The
#: step still has to happen, so it is stated generically rather than dropped.
UNRESOLVED_SCENES_ITEM = (
    "Open the scene(s) this change touches in the editor and run them — "
    "the branch diff could not be read, so identify them from the ticket's changes"
)


def expand_gate_checklist(
    ticket: Ticket, checklist: list[str], *, scenes: list[str] | None = None
) -> list[str]:
    """Expand a stage's static checklist into ticket-specific items.

    An ``{{acceptance_criteria}}`` entry is replaced by one concrete play-test
    item per acceptance criterion, so each gate lists what actually needs
    exercising for this change instead of the same generic bullet every time.
    A ``{{playtest_scenes}}`` entry is replaced by one item per scene file the
    ticket's branch touches, so "run the affected scenes" names the files to
    open instead of leaving the operator to work them out.

    ``scenes`` distinguishes *resolved and empty* (``[]`` — the branch changes no
    scene, so there is nothing to open and the placeholder drops) from
    *unresolved* (``None`` — the diff could not be read, so the step is kept in
    generic form rather than silently disappearing).

    Every other entry passes through unchanged.

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
        token = item.strip()
        if token == AC_CHECKLIST_PLACEHOLDER:
            expanded.extend(
                f"Play-test by hand — {str(c).strip()}" for c in criteria if str(c).strip()
            )
        elif token == PLAYTEST_SCENES_PLACEHOLDER:
            if scenes is None:
                expanded.append(UNRESOLVED_SCENES_ITEM)
            else:
                expanded.extend(
                    f"Open `{scene}` in the editor, run it, and play through this change — "
                    "it must reach a playable state with no errors"
                    for scene in scenes
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
