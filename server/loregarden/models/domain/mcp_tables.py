"""MCP servers this control plane knows about, and the tool-permission decisions it records.

Split out of `tables.py`, which had reached its size limit.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from loregarden.models.domain.enums import utcnow
from sqlmodel import Field, SQLModel


class McpServer(SQLModel, table=True):
    """A third-party MCP server this control plane knows about.

    Agents reached exactly one server — loregarden's own, hardcoded into the
    `--mcp-config` payload — so anything else an agent might need had to be
    configured outside the control plane, where nothing could see or audit it.

    Two departures from a naive "store the config" table:

    `auth_env_var` holds the *name* of an environment variable, never a token.
    This database is copied around freely — into scratch dirs for migration
    dry-runs, into worktrees — and a secret at rest in it would travel with
    every copy. The value is read from the environment when the server is used.

    The health columns record what an actual `initialize` handshake found, and
    were added only once something performed one. There is still no rate-limit
    column: nothing enforces a limit, and a column no code reads is a claim the
    UI would happily render as fact.
    """

    __tablename__ = "mcp_servers"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    #: Key under `mcpServers` in the CLI config, so it must be unique.
    name: str = Field(index=True, unique=True)
    description: str = ""
    #: "http" (url) or "stdio" (command + args).
    transport: str = "http"
    url: str = ""
    command: str = ""
    args_json: str = "[]"
    #: Name of the env var holding the credential — not the credential.
    auth_env_var: str = ""
    #: Disabled servers stay registered but are withheld from agents, so a
    #: broken server can be parked without losing how it was configured.
    enabled: bool = True
    #: "prompt" (every call asks the operator) or "auto" (the server is trusted
    #: and its tools run unattended). Server-level rather than per-tool: it
    #: matches how an operator actually reasons about a third party, and a
    #: per-tool allowlist would have to be maintained for every server added.
    tool_policy: str = "prompt"
    #: Calls per minute this server will accept before further calls are
    #: refused. 0 means no ceiling, which is the default — a limit nobody set
    #: should not start refusing work.
    rate_limit_per_min: int = 0
    #: Result of the last health check. `last_checked_at` empty means never
    #: checked, which is a different thing from checked-and-failing and reads
    #: differently in the UI.
    last_checked_at: str = ""
    last_health_ok: bool = False
    last_health_latency_ms: int = 0
    #: Why the last check failed, in terms an operator can act on. Empty when
    #: the check passed.
    last_health_error: str = ""
    #: Tool names the server reported to `tools/list` during the last check.
    #: Cached rather than fetched per request: listing means dialling the
    #: server, which is not something a page render should do.
    tools_json: str = "[]"
    #: When that catalogue was collected. Empty means the tools were never
    #: listed — a server can answer `initialize` and still refuse `tools/list`,
    #: and "no tools" and "we never asked" are not the same answer.
    tools_listed_at: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class McpToolCall(SQLModel, table=True):
    """One tool-permission decision, recorded as it is made.

    The observation point is the permission bridge, which is what makes the
    columns what they are: it sees the *request* and issues a *decision*, and
    never sees the result. Tool execution latency and success are therefore not
    recorded — the CLI runs the tool itself and reports nothing back. Storing
    them as nullable columns would invite a UI to average over nulls.

    `decision_ms` is how long the decision took, which for a prompted call is
    how long the operator took to answer.
    """

    __tablename__ = "mcp_tool_calls"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    run_id: str = Field(index=True)
    ticket_id: str = Field(index=True)
    agent_id: str = ""
    #: Full name as the CLI reported it, e.g. `mcp__github__create_issue`.
    tool_name: str = ""
    #: Parsed server, or "" for a non-MCP tool such as Bash.
    server_name: str = Field(default="", index=True)
    #: How the call was resolved — see services.tool_telemetry.DECISIONS.
    decision: str = ""
    decision_ms: int = 0
    created_at: datetime = Field(default_factory=utcnow, index=True)
