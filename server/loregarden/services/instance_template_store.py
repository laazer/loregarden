"""Create, replace and delete the instance templates a person defines in the UI.

The command a stored template runs is typed into a form. That is a deliberate
widening of who may define what runs — see `workspace_instance_templates` —
and it stays operator-only: these functions sit behind REST endpoints the API
token guards, and no MCP tool reaches them, so an agent cannot write a
command and then launch it.

A name is refused when the workspace already has a template by it, from any
source. A stored row may still end up shadowed later, when a file or code
template takes its name; that is reported on the page, not prevented here.

Stored templates are also how a workspace's `.loregarden/instances.yaml` gets
started: `move_to_file` writes them out as that file, for the operator to
review and commit, and deletes the rows — which the file would otherwise
shadow, leaving a second, dead copy of every template on the page.
"""

from __future__ import annotations

import yaml
from lore_eden.instances import TemplateFile, TemplateSpec, parse_template_file
from loregarden.models.domain import InstanceTemplateRecord, Workspace
from loregarden.models.domain.enums import utcnow
from loregarden.services.workspace_instance_templates import (
    TEMPLATE_FILE,
    TemplateOrigin,
    WorkspaceTemplates,
)
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


class TemplateFileExistsError(TemplateStoreError):
    pass


class NothingToMoveError(TemplateStoreError):
    pass


FILE_HEADER = (
    "# Launch templates for this workspace's local instances, read by loregarden.\n"
    "# Written from the templates saved on its Instances page; edit and commit it\n"
    "# like any other code. Format: lore_eden/instances/spec.py.\n"
)


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
        raise TemplateNameTakenError(
            f"{slug} already has a {clash.origin} template named {spec.name!r}"
        )
    row = InstanceTemplateRecord(
        workspace_id=workspace.id, name=spec.name, spec_json=spec.model_dump_json()
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def replace_template(
    session: Session, slug: str, name: str, spec: TemplateSpec
) -> InstanceTemplateRecord:
    """Replace a stored template's spec. Renaming is not an edit: delete and create."""
    if spec.name != name:
        raise TemplateRenameError(
            f"cannot rename {name!r} to {spec.name!r}; create a new template instead"
        )
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


def render_template_file(specs: list[TemplateSpec]) -> str:
    """``specs`` as a template file, defaults left out so it reads like a hand-written one."""
    document = TemplateFile(version=1, templates=specs).model_dump(
        mode="json", exclude_defaults=True
    )
    return FILE_HEADER + yaml.safe_dump(document, sort_keys=False, allow_unicode=True)


def move_to_file(session: Session, current: WorkspaceTemplates) -> list[str]:
    """Write the workspace's usable stored templates as its template file; return their names.

    Refused when the file already exists: merging into it would rewrite a
    reviewed file and drop its comments, which is the operator's edit to make.
    Stored rows that are shadowed or no longer validate are left where they are.
    """
    if current.file_exists:
        raise TemplateFileExistsError(
            f"{current.template_file} already exists; add templates to it in the repo"
        )
    moving = [
        entry
        for entry in current.entries
        if entry.origin is TemplateOrigin.STORED
        and entry.spec is not None
        and entry.shadowed_by is None
    ]
    if not moving:
        raise NothingToMoveError(
            f"{current.slug} has no saved templates to write to {TEMPLATE_FILE}"
        )
    specs = [entry.spec for entry in moving if entry.spec is not None]
    text = render_template_file(specs)
    # The file is read back by the same parser that will read it at launch; a
    # rendering that does not survive that is not written.
    if parse_template_file(text, source=str(current.template_file)) != specs:
        raise RuntimeError(
            f"the rendered {TEMPLATE_FILE} does not read back as the saved templates"
        )
    current.template_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        with current.template_file.open("x", encoding="utf-8") as handle:
            handle.write(text)
    except FileExistsError as exc:
        raise TemplateFileExistsError(f"{current.template_file} was created meanwhile") from exc
    for entry in moving:
        row = session.get(InstanceTemplateRecord, entry.record_id)
        if row is not None:
            session.delete(row)
    session.commit()
    return [entry.name for entry in moving]
