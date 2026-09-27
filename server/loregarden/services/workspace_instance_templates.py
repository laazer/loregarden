"""Every workspace's instance templates, from the three places they are defined.

- **code** — loregarden's own `server` and `client`, in `local_instances.py`,
  which snapshot the database and boot sandboxed. Only the `loregarden`
  workspace has these.
- **file** — the workspace's committed `.loregarden/instances.yaml`, read from
  its primary checkout.
- **stored** — rows a person created on the Instances page.

Templates are named `<workspace>/<name>` so two workspaces' `api` do not
collide. Within a workspace a name is taken once, in that order: a stored row
whose name a file or code template already uses is *shadowed* — kept, shown
with a warning, never launched — so a template committed to the repo cannot
be replaced by one typed into a form.

Read on every call rather than cached: a file edited in a worktree, or a row
saved a second ago, is what the next launch should see. A file that does not
parse is reported on its workspace, not skipped — a template set that silently
went empty is a launch button that silently disappeared.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from lore_eden.instances import (
    FileInstanceRegistry,
    InstanceTemplate,
    SpecTemplate,
    TemplateFileError,
    TemplateInfo,
    TemplateSpec,
    UnknownTemplateError,
    list_worktrees,
    load_template_file,
    validate_spec,
)
from loregarden.db import session as db
from loregarden.models.domain import InstanceTemplateRecord, Workspace
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

#: Where a workspace commits its templates, relative to its repo root.
TEMPLATE_FILE = Path(".loregarden") / "instances.yaml"
SEPARATOR = "/"


class TemplateOrigin(StrEnum):
    CODE = "code"
    FILE = "file"
    STORED = "stored"


def qualified(workspace_slug: str, name: str) -> str:
    return f"{workspace_slug}{SEPARATOR}{name}"


@dataclass
class TemplateEntry:
    name: str
    qualified_name: str
    origin: TemplateOrigin
    #: The launchable template; None when shadowed or invalid.
    template: InstanceTemplate | None
    #: The spec, for file and stored entries — what the page shows and edits.
    spec: TemplateSpec | None = None
    #: A stored row whose name a file or code template already took.
    shadowed_by: TemplateOrigin | None = None
    #: Why a stored row cannot be used, when it does not validate.
    error: str = ""
    record_id: str | None = None


@dataclass
class WorkspaceTemplates:
    slug: str
    name: str
    repo_root: Path
    template_file: Path
    #: Why the file could not be read or parsed; empty when fine or absent.
    file_error: str = ""
    #: File entries ignored because code already took the name.
    conflicts: list[str] = field(default_factory=list)
    entries: list[TemplateEntry] = field(default_factory=list)

    def launchable(self) -> list[TemplateEntry]:
        return [entry for entry in self.entries if entry.template is not None]


#: Code templates by workspace slug: loregarden's own, registered by
#: `local_instances` so this module does not import it (it imports this one).
CodeTemplates = Callable[[], dict[str, list[InstanceTemplate]]]


def _stored_entries(
    session: Session, workspace: Workspace, make: Callable[[TemplateSpec], InstanceTemplate]
) -> list[TemplateEntry]:
    rows = session.exec(
        select(InstanceTemplateRecord)
        .where(InstanceTemplateRecord.workspace_id == workspace.id)
        .order_by(InstanceTemplateRecord.name)
    ).all()
    entries = []
    for row in rows:
        entry = TemplateEntry(
            name=row.name,
            qualified_name=qualified(workspace.slug, row.name),
            origin=TemplateOrigin.STORED,
            template=None,
            record_id=row.id,
        )
        try:
            entry.spec = validate_spec(json.loads(row.spec_json))
        except (TemplateFileError, ValueError) as exc:
            # Stored before a rule tightened, or edited outside the API. Shown
            # with the reason on the page; the rest of the workspace still works.
            logger.warning("stored instance template %s is invalid: %s", entry.qualified_name, exc)
            entry.error = str(exc)
        else:
            entry.template = make(entry.spec)
        entries.append(entry)
    return entries


def _spec_maker(
    slug: str, repo: Path, registry: FileInstanceRegistry
) -> Callable[[TemplateSpec], InstanceTemplate]:
    def make(spec: TemplateSpec) -> InstanceTemplate:
        return SpecTemplate(
            spec,
            name=qualified(slug, spec.name),
            project=slug,
            worktrees=lambda: list_worktrees(repo),
            registry=registry,
        )

    return make


def _code_entries(templates: list[InstanceTemplate]) -> list[TemplateEntry]:
    entries = []
    for template in templates:
        name = template.describe().name
        entries.append(
            TemplateEntry(
                name=name.split(SEPARATOR, 1)[-1],
                qualified_name=name,
                origin=TemplateOrigin.CODE,
                template=template,
            )
        )
    return entries


def _add_file_entries(found: WorkspaceTemplates, make: Callable[[TemplateSpec], InstanceTemplate]) -> None:
    try:
        specs = load_template_file(found.template_file)
    except TemplateFileError as exc:
        logger.warning("instance template file for %s is unusable: %s", found.slug, exc)
        found.file_error = str(exc)
        return
    taken = {entry.name for entry in found.entries}
    for spec in specs:
        if spec.name in taken:
            found.conflicts.append(
                f"{TEMPLATE_FILE}: {spec.name!r} is already a code template; the file's entry is ignored"
            )
            continue
        found.entries.append(
            TemplateEntry(
                name=spec.name,
                qualified_name=qualified(found.slug, spec.name),
                origin=TemplateOrigin.FILE,
                template=make(spec),
                spec=spec,
            )
        )


def collect(
    session: Session, registry: FileInstanceRegistry, code_templates: CodeTemplates
) -> list[WorkspaceTemplates]:
    """Every workspace's templates, with what is wrong with any of them."""
    code = code_templates()
    result = []
    for workspace in session.exec(select(Workspace).order_by(Workspace.name)).all():
        root = resolve_workspace_root(workspace)
        found = WorkspaceTemplates(
            slug=workspace.slug, name=workspace.name, repo_root=root, template_file=root / TEMPLATE_FILE
        )
        make = _spec_maker(workspace.slug, root, registry)
        found.entries.extend(_code_entries(code.get(workspace.slug, [])))
        _add_file_entries(found, make)
        taken = {entry.name: entry.origin for entry in found.entries}
        for entry in _stored_entries(session, workspace, make):
            if entry.name in taken:
                entry.shadowed_by = taken[entry.name]
                entry.template = None
            found.entries.append(entry)
        result.append(found)
    return result


class WorkspaceTemplateSource:
    """A lore-eden `TemplateSource` over every workspace, read fresh per call."""

    def __init__(self, registry: FileInstanceRegistry, code_templates: CodeTemplates) -> None:
        self._registry = registry
        self._code_templates = code_templates

    def collect(self, session: Session | None = None) -> list[WorkspaceTemplates]:
        """Every workspace's templates; in ``session`` when a request has one."""
        if session is not None:
            return collect(session, self._registry, self._code_templates)
        with Session(db.engine) as own:
            return collect(own, self._registry, self._code_templates)

    def get(self, name: str) -> InstanceTemplate:
        for workspace in self.collect():
            for entry in workspace.entries:
                if entry.qualified_name == name and entry.template is not None:
                    return entry.template
        raise UnknownTemplateError(name)

    def describe_all(self) -> list[TemplateInfo]:
        return [
            entry.template.describe()
            for workspace in self.collect()
            for entry in workspace.launchable()
            if entry.template is not None
        ]
