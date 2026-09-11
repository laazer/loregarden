"""The tables the git automation pipeline writes: CI, autofix, worktrees, conflicts.

Split out of ``tables`` when the stage-park waiver columns pushed that module
past its 1500-line cap — the same reason and the same shape as
``queue_tables`` and ``docker_tables``.

This is the group that came out because it is cohesive rather than merely
adjacent: a ticket's branch is built in a `Worktree`, its push is judged by a
`CIRunResult`, a red result is retried through an `AutoFixAttempt`, and a merge
that cannot proceed lands in a `ConflictReport`. They are one pipeline's
bookkeeping, written by `services/git_*` and read by nothing else in the model
layer.

Re-exported from ``models.domain``, so every existing import site is unchanged.
Note the ordering caveat ``queue_tables`` documents: moving classes changes
mapper configuration order, and these are joined to `tickets` by bare foreign
key columns with no `Relationship`, so a test adding a child and its parent in
one flush must commit the parent first.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any
from uuid import uuid4

from loregarden.models.domain.enums import (
    AutoFixStatus,
    CIStatus,
    WorktreeState,
    str_enum_column,
    utcnow,
)
from pydantic import model_validator
from sqlmodel import Field, SQLModel

logger = logging.getLogger(__name__)


class CIRunResult(SQLModel, table=True):
    __tablename__ = "ci_run_results"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    workspace_id: str = Field(foreign_key="workspaces.id", index=True)
    ticket_id: str = Field(foreign_key="tickets.id", index=True)
    status: CIStatus = Field(
        default=CIStatus.PENDING,
        sa_column=str_enum_column(CIStatus, CIStatus.PENDING, index=True),
    )
    provider: str = ""
    external_run_id: str | None = None
    logs_url: str | None = None
    failure_summary: str | None = None
    full_logs: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class AutoFixAttempt(SQLModel, table=True):
    __tablename__ = "auto_fix_attempts"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    ci_run_result_id: str = Field(foreign_key="ci_run_results.id", index=True)
    attempt_number: int = 1
    run_id: str | None = Field(default=None, foreign_key="agent_runs.id")
    status: AutoFixStatus = Field(
        default=AutoFixStatus.PENDING,
        sa_column=str_enum_column(AutoFixStatus, AutoFixStatus.PENDING, index=True),
    )
    result_summary: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    completed_at: datetime | None = None


class Worktree(SQLModel, table=True):
    __tablename__ = "worktrees"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    workspace_id: str = Field(foreign_key="workspaces.id", index=True)
    agent_run_id: str = Field(foreign_key="agent_runs.id", index=True)
    #: Set when the worktree belongs to a ticket rather than to one run. A
    #: ticket's stages share one tree, so reuse is a lookup on this column;
    #: `agent_run_id` stays as provenance for whichever run cut it. Null for
    #: the fan-out and parallel-queue paths, where a run really does want its
    #: own tree.
    ticket_id: str | None = Field(default=None, foreign_key="tickets.id", index=True)
    parent_branch: str = "main"
    worktree_path: str = ""
    state: WorktreeState = Field(
        default=WorktreeState.ACTIVE,
        sa_column=str_enum_column(WorktreeState, WorktreeState.ACTIVE, index=True),
    )
    #: The branch checked out in this worktree, as opposed to `parent_branch`
    #: (what it was cut from) or the directory name. Merging needs this: the
    #: directory is named after the run, which is not a ref.
    branch: str = ""
    merge_base: str | None = None
    has_conflicts: bool = False
    conflict_files_json: str = "[]"
    conflict_summary: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    merged_at: datetime | None = None
    cleaned_at: datetime | None = None

    @property
    def conflict_files(self) -> list[str]:
        try:
            return json.loads(self.conflict_files_json or "[]")
        except json.JSONDecodeError:
            # An unreadable blob reads as "no conflicts", which is the one answer
            # a merge gate must never be given by accident.
            logger.warning(
                "Unreadable conflict_files_json on worktree %s; reporting no files",
                self.id,
                exc_info=True,
            )
            return []

    @conflict_files.setter
    def conflict_files(self, value: list[str]) -> None:
        self.conflict_files_json = json.dumps(value)

    @model_validator(mode="before")
    @classmethod
    def _coerce_conflict_files(cls, data: Any) -> Any:
        # A mode="before" validator is handed the raw pre-validation payload,
        # which Pydantic types as Any: a kwargs dict, or an already-built
        # instance. There is no model to parse it into, because this runs before
        # the model exists. Moved here unchanged from `tables` — the gate sees it
        # because the file is new, not because the code is.
        if isinstance(data, dict) and "conflict_files" in data:  # py-org: allow-isinstance
            data = dict(data)
            files = data.pop("conflict_files")
            data["conflict_files_json"] = json.dumps(files)
        return data


class ConflictReport(SQLModel, table=True):
    __tablename__ = "conflict_reports"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    worktree_id: str = Field(foreign_key="worktrees.id", index=True)
    ticket_id: str = Field(foreign_key="tickets.id", index=True)
    merge_attempt_number: int = 1
    conflict_type: str = "merge_conflict"
    conflicting_files_json: str = "[]"
    conflict_details: str = ""
    resolution_attempted: bool = False
    resolution_successful: bool = False
    created_at: datetime = Field(default_factory=utcnow)

    @property
    def conflicting_files(self) -> list[str]:
        try:
            return json.loads(self.conflicting_files_json or "[]")
        except json.JSONDecodeError:
            # Same trap as Worktree.conflict_files: empty must mean empty, not
            # "the column could not be read".
            logger.warning(
                "Unreadable conflicting_files_json on conflict report %s; reporting no files",
                self.id,
                exc_info=True,
            )
            return []

    @conflicting_files.setter
    def conflicting_files(self, value: list[str]) -> None:
        self.conflicting_files_json = json.dumps(value)

    @model_validator(mode="before")
    @classmethod
    def _coerce_conflicting_files(cls, data: Any) -> Any:
        # A mode="before" validator is handed the raw pre-validation payload,
        # which Pydantic types as Any: a kwargs dict, or an already-built
        # instance. There is no model to parse it into, because this runs before
        # the model exists. Moved here unchanged from `tables` — the gate sees it
        # because the file is new, not because the code is.
        if isinstance(data, dict) and "conflicting_files" in data:  # py-org: allow-isinstance
            data = dict(data)
            files = data.pop("conflicting_files")
            data["conflicting_files_json"] = json.dumps(files)
        return data
