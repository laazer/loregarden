"""The `loregarden_doctor` MCP tool: schema and handler.

Lives in its own module, like `organization_tool`, so `mcp/tools.py` and its
`execute_tool` if-chain stop growing — both are already past their caps.

Returns structured findings rather than a rendered report. The caller decides how
to show them: a terminal wants lines, the inbox wants a remediation string, and a
tool that pre-formats prose forces every consumer to parse it back out.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ConfigDict, ValidationError, field_validator
from sqlmodel import Field, Session, SQLModel

from loregarden.mcp.tool_ids import McpTool
from loregarden.models.domain import DoctorCheck, DoctorStatus
from loregarden.services.doctor import run_checks
from loregarden.services.organization_gate_service import workspace_for_slug
from loregarden.services.ticket_worktree import resolve_workspace_root

TOOL_DEFINITION: dict[str, Any] = {
    "name": McpTool.DOCTOR,
    "description": (
        "Check a workspace for the environment traps that break runs in ways that "
        "do not look like their cause: a worktree left with core.bare=true, GIT_DIR "
        "leaking into subprocesses and overriding cwd, the database resolved "
        "relative to a worktree so ticket queries answer a silent zero, backend "
        "sources newer than the reload sentinel, a missing agent-CLI credential, a "
        "branch diverged from its remote, a repository with no commit. Read-only: "
        "nothing is written, no remote is contacted, and no credential value is "
        "read. Run it when something behaves impossibly, or before a long "
        "unattended run."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "workspace_slug": {
                "type": "string",
                "description": "Workspace slug, e.g. loregarden.",
            },
            "checks": {
                "type": "array",
                "description": "Which checks to run. Omit to run all of them.",
                "items": {
                    "type": "string",
                    "enum": [check.value for check in DoctorCheck],
                },
            },
        },
        "required": ["workspace_slug"],
        "additionalProperties": False,
    },
}


class DoctorRequest(SQLModel):
    """The tool's arguments, parsed once at the boundary.

    A model rather than `.get()` plus hand-rolled type checks: an unknown check
    name and a `checks` that is not a list both become validation errors here,
    where the caller can be told what it sent wrong. A caller asking for a check
    that does not exist wants to know that, not a clean report about the checks
    it did not mean.
    """

    model_config = ConfigDict(extra="forbid")

    workspace_slug: str
    checks: list[DoctorCheck] = Field(default_factory=list)

    @field_validator("workspace_slug")
    @classmethod
    def _required(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("workspace_slug is required")
        return value.strip()


def doctor(session: Session, arguments: dict[str, Any]) -> str:
    try:
        request = DoctorRequest.model_validate(arguments)
    except ValidationError as exc:
        raise ValueError(f"invalid arguments for {McpTool.DOCTOR.value}: {exc}") from exc

    workspace = workspace_for_slug(session, request.workspace_slug)
    repo_root = resolve_workspace_root(workspace)

    findings = run_checks(session, workspace, repo_root, checks=tuple(request.checks) or None)
    return json.dumps(
        {
            "workspace_slug": request.workspace_slug,
            "repo_root": str(repo_root),
            # A caller scanning one field: ok is false only for a FAIL, since a
            # WARN is a thing to know rather than a thing to stop for.
            "ok": all(finding.ok for finding in findings),
            "fail_count": sum(1 for f in findings if f.status is DoctorStatus.FAIL),
            "warn_count": sum(1 for f in findings if f.status is DoctorStatus.WARN),
            "findings": [finding.model_dump(mode="json") for finding in findings],
        },
        indent=2,
    )
