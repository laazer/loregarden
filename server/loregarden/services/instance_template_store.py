"""Create, replace and delete the instance templates a person defines in the UI.

The command a stored template runs is typed into a form. That is a deliberate
widening of who may define what runs — see `workspace_instance_templates` —
and it stays operator-only: these functions sit behind REST endpoints the API
token guards, and no MCP tool reaches them, so an agent cannot write a
command and then launch it.

A name is refused when the workspace already has a template by it, from any
source. A stored row may still end up shadowed later, when a file or code
template takes its name; that is reported on the page, not prevented here.
"""

from __future__ import annotations

from lore_eden.instances import TemplateSpec
from loregarden.models.domain import InstanceTemplateRecord, Workspace
from loregarden.models.domain.enums import utcnow
from loregarden.services.workspace_instance_templates import WorkspaceTemplates
from sqlmodel import Session, select


class TemplateStoreError(ValueError):
    """Base for refusals the page shows as they are."""


class WorkspaceNotFoundError(TemplateStoreError):
    pass


class TemplateNotFoundError(TemplateStoreError):
    pass


class TemplateNameTakenError(TemplateStoreError):
    pass


class TemplateRenameError(TemplateStoreError):
    pass


def _workspace(session: Session, slug: str) -> Workspace:
    workspace = session.exec(select(Workspace).where(Workspace.slug == slug)).first()
    if workspace is None:
        raise WorkspaceNotFoundError(f"no workspace {slug!r}")
    return workspace


def _row(session: Session, workspace: Workspace, name: str) -> InstanceTemplateRecord:
    row = session.exec(
        select(InstanceTemplateRecord).where(
            InstanceTemplateRecord.workspace_id == workspace.id, InstanceTemplateRecord.name == name
        )
    ).first()
    if row is None:
        raise TemplateNotFoundError(f"{workspace.slug} has no stored template {name!r}")
    return row


def create_template(
    session: Session, slug: str, spec: TemplateSpec, current: WorkspaceTemplates
) -> InstanceTemplateRecord:
    """Store ``spec`` for workspace ``slug``. ``current`` is its templates now."""
    workspace = _workspace(session, slug)
    clash = next((entry for entry in current.entries if entry.name == spec.name), None)
    if clash is not None:
        raise TemplateNameTakenError(f"{slug} already has a {clash.origin} template named {spec.name!r}")
    row = InstanceTemplateRecord(workspace_id=workspace.id, name=spec.name, spec_json=spec.model_dump_json())
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def replace_template(session: Session, slug: str, name: str, spec: TemplateSpec) -> InstanceTemplateRecord:
    """Replace a stored template's spec. Renaming is not an edit: delete and create."""
    if spec.name != name:
        raise TemplateRenameError(f"cannot rename {name!r} to {spec.name!r}; create a new template instead")
    row = _row(session, _workspace(session, slug), name)
    row.spec_json = spec.model_dump_json()
    row.updated_at = utcnow()
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def delete_template(session: Session, slug: str, name: str) -> None:
    row = _row(session, _workspace(session, slug), name)
    session.delete(row)
    session.commit()
