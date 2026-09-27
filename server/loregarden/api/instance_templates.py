"""Every workspace's launch templates, and the ones a person stores from the UI.

Reading answers where each template comes from — code, the workspace's
committed `.loregarden/instances.yaml`, or a stored row — and what is wrong
with any of them: a file that does not parse, a row shadowed by a file entry
of the same name. Writing only ever touches stored rows; the file is edited in
the repo, like any other code.

No MCP tool reaches these endpoints. Launching still takes a template name,
never a command, and defining one stays with the operator.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import APIRouter, Depends, HTTPException, status
from lore_eden.instances import InstanceKind, TemplateSpec
from loregarden.db.session import get_session
from loregarden.services.instance_template_store import (
    TemplateNameTakenError,
    TemplateNotFoundError,
    TemplateRenameError,
    TemplateStoreError,
    WorkspaceNotFoundError,
    create_template,
    delete_template,
    replace_template,
)
from loregarden.services.local_instances import get_template_source
from loregarden.services.workspace_instance_templates import (
    TemplateEntry,
    TemplateOrigin,
    WorkspaceTemplates,
)
from pydantic import BaseModel
from sqlmodel import Session

router = APIRouter(prefix="/instance-templates", tags=["instances"])


class TemplateEntryView(BaseModel):
    name: str
    qualified_name: str
    origin: TemplateOrigin
    kind: InstanceKind | None
    description: str
    spec: TemplateSpec | None
    shadowed_by: TemplateOrigin | None
    error: str
    launchable: bool


class WorkspaceTemplatesView(BaseModel):
    slug: str
    name: str
    repo_root: str
    template_file: str
    file_error: str
    conflicts: list[str]
    entries: list[TemplateEntryView]


def _entry_view(entry: TemplateEntry) -> TemplateEntryView:
    info = entry.template.describe() if entry.template is not None else None
    kind = info.kind if info else (entry.spec.kind if entry.spec else None)
    description = info.description if info else (entry.spec.description if entry.spec else "")
    return TemplateEntryView(
        name=entry.name,
        qualified_name=entry.qualified_name,
        origin=entry.origin,
        kind=kind,
        description=description,
        spec=entry.spec,
        shadowed_by=entry.shadowed_by,
        error=entry.error,
        launchable=entry.template is not None,
    )


def _view(found: WorkspaceTemplates) -> WorkspaceTemplatesView:
    return WorkspaceTemplatesView(
        slug=found.slug,
        name=found.name,
        repo_root=str(found.repo_root),
        template_file=str(found.template_file),
        file_error=found.file_error,
        conflicts=found.conflicts,
        entries=[_entry_view(entry) for entry in found.entries],
    )


_STATUS: dict[type[TemplateStoreError], int] = {
    WorkspaceNotFoundError: status.HTTP_404_NOT_FOUND,
    TemplateNotFoundError: status.HTTP_404_NOT_FOUND,
    TemplateNameTakenError: status.HTTP_409_CONFLICT,
    TemplateRenameError: 422,
}


@contextmanager
def _as_http() -> Iterator[None]:
    try:
        yield
    except TemplateStoreError as exc:
        raise HTTPException(_STATUS.get(type(exc), 400), str(exc)) from exc


def _workspace_view(session: Session, slug: str) -> WorkspaceTemplates:
    found = next((w for w in get_template_source().collect(session) if w.slug == slug), None)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no workspace {slug!r}")
    return found


@router.get("", response_model=list[WorkspaceTemplatesView])
def list_workspace_templates(
    session: Session = Depends(get_session),
) -> list[WorkspaceTemplatesView]:
    return [_view(found) for found in get_template_source().collect(session)]


@router.post("/{slug}", response_model=WorkspaceTemplatesView, status_code=status.HTTP_201_CREATED)
def create_workspace_template(
    slug: str, spec: TemplateSpec, session: Session = Depends(get_session)
) -> WorkspaceTemplatesView:
    with _as_http():
        create_template(session, slug, spec, _workspace_view(session, slug))
    return _view(_workspace_view(session, slug))


@router.put("/{slug}/{name}", response_model=WorkspaceTemplatesView)
def replace_workspace_template(
    slug: str, name: str, spec: TemplateSpec, session: Session = Depends(get_session)
) -> WorkspaceTemplatesView:
    with _as_http():
        replace_template(session, slug, name, spec)
    return _view(_workspace_view(session, slug))


@router.delete("/{slug}/{name}", status_code=status.HTTP_204_NO_CONTENT)
def delete_workspace_template(
    slug: str, name: str, session: Session = Depends(get_session)
) -> None:
    with _as_http():
        delete_template(session, slug, name)
