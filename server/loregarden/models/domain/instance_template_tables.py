"""Instance templates a person defined in the UI, per workspace.

A workspace's templates come from three places: loregarden's own code (its
sandboxed server and client), the workspace's committed
`.loregarden/instances.yaml`, and rows here. The spec is stored as the JSON
lore-eden's `TemplateSpec` validates, so a row and a file entry are the same
shape and pass the same checks (see `services/workspace_instance_templates.py`).

Its own module because ``tables`` and ``enums`` both sit at their size caps.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from loregarden.models.domain.enums import utcnow
from sqlmodel import Field, SQLModel, UniqueConstraint


class InstanceTemplateRecord(SQLModel, table=True):
    __tablename__ = "instance_templates"
    __table_args__ = (UniqueConstraint("workspace_id", "name"),)

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    workspace_id: str = Field(foreign_key="workspaces.id", index=True)
    #: The spec's own name, unqualified; unique within the workspace.
    name: str
    #: lore-eden `TemplateSpec`, as JSON.
    spec_json: str
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


__all__ = ["InstanceTemplateRecord"]
