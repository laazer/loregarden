import json
from pathlib import Path

import yaml
from sqlmodel import Session, select

from loregarden.config import settings
from loregarden.models.domain import WorkflowStageDef, WorkflowTemplate


def load_workflow_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def sync_workflow_templates(session: Session) -> list[WorkflowTemplate]:
    templates_dir = settings.workflow_templates_dir
    if not templates_dir.exists():
        return []

    synced: list[WorkflowTemplate] = []
    for path in sorted(templates_dir.glob("*.yaml")):
        data = load_workflow_yaml(path)
        slug = data["slug"]
        existing = session.exec(
            select(WorkflowTemplate).where(WorkflowTemplate.slug == slug)
        ).first()
        stages = data.get("stages", [])
        transitions = data.get("transitions", [])
        if existing:
            existing.name = data.get("name", slug)
            existing.description = data.get("description", "")
            existing.stages_json = json.dumps(stages)
            existing.transitions_json = json.dumps(transitions)
            existing.source_path = str(path.relative_to(settings.repo_root))
            session.add(existing)
            synced.append(existing)
        else:
            tpl = WorkflowTemplate(
                slug=slug,
                name=data.get("name", slug),
                description=data.get("description", ""),
                stages_json=json.dumps(stages),
                transitions_json=json.dumps(transitions),
                source_path=str(path.relative_to(settings.repo_root)),
            )
            session.add(tpl)
            synced.append(tpl)
    session.commit()
    for tpl in synced:
        session.refresh(tpl)
    return synced


def get_template_stages(template: WorkflowTemplate) -> list[WorkflowStageDef]:
    raw = json.loads(template.stages_json or "[]")
    return [WorkflowStageDef.model_validate(item) for item in raw]


def stage_display_name(template: WorkflowTemplate, stage_key: str) -> str:
    for stage in get_template_stages(template):
        if stage.key == stage_key:
            return stage.name
    return stage_key.replace("_", " ").title()
