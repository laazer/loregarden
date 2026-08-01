"""Migrations for the MCP registry's own schema.

Split out of `migrations.py`, which had grown past the organization gate's
limit again. The division follows `migrations_templates.py`: a cluster that
belongs together moves as a cluster. These six own the `mcp_servers` and
`mcp_tool_calls` tables from the registry's first table to its tool catalogue,
and nothing else touches them.

Migration identity is the id string in the MIGRATIONS list, which is unchanged,
so nothing about applied history moves with this.
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing, table_exists
from sqlalchemy import text
from sqlalchemy.engine import Connection


def m_mcp_servers_table(conn: Connection) -> None:
    """Registry of third-party MCP servers agents may reach.

    No token column: `auth_env_var` names an environment variable instead. This
    database is copied for dry-runs and worktrees, and a secret stored here
    would travel with every copy.
    """
    if table_exists(conn, "mcp_servers"):
        return
    conn.execute(
        text(
            """
            CREATE TABLE mcp_servers (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                transport TEXT NOT NULL DEFAULT 'http',
                url TEXT NOT NULL DEFAULT '',
                command TEXT NOT NULL DEFAULT '',
                args_json TEXT NOT NULL DEFAULT '[]',
                auth_env_var TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
    )
    # The name is the key under `mcpServers`, so a duplicate would silently
    # shadow rather than conflict.
    conn.execute(text("CREATE UNIQUE INDEX ix_mcp_servers_name ON mcp_servers (name)"))


def m_mcp_server_tool_policy(conn: Connection) -> None:
    """Whether a registered server's tools run without asking the operator.

    U1a made third-party servers reachable, but the auto-approve check only
    recognised loregarden's own prefix, so every call to a registered server
    stopped for a human — which stalls an unattended run on its first use.
    Defaults to "prompt": trusting a third party is a decision the operator
    makes, not one inherited from being registered.
    """
    add_columns_if_missing(
        conn,
        "mcp_servers",
        {
            "tool_policy": (
                "ALTER TABLE mcp_servers ADD COLUMN tool_policy TEXT NOT NULL DEFAULT 'prompt'"
            )
        },
    )


def m_mcp_tool_calls_table(conn: Connection) -> None:
    """Per-decision record for MCP and CLI tool permissions."""
    if table_exists(conn, "mcp_tool_calls"):
        return
    conn.execute(
        text(
            """
            CREATE TABLE mcp_tool_calls (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                ticket_id TEXT NOT NULL,
                agent_id TEXT NOT NULL DEFAULT '',
                tool_name TEXT NOT NULL DEFAULT '',
                server_name TEXT NOT NULL DEFAULT '',
                decision TEXT NOT NULL DEFAULT '',
                decision_ms INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
    )
    conn.execute(text("CREATE INDEX ix_mcp_tool_calls_created_at ON mcp_tool_calls (created_at)"))
    conn.execute(text("CREATE INDEX ix_mcp_tool_calls_server ON mcp_tool_calls (server_name)"))
    conn.execute(text("CREATE INDEX ix_mcp_tool_calls_run ON mcp_tool_calls (run_id)"))


def m_mcp_server_health(conn: Connection) -> None:
    """Record what the last health check found.

    Added with the check itself rather than ahead of it: until something
    performed a real handshake these columns would have rendered as fact in the
    UI while holding nothing.
    """
    add_columns_if_missing(
        conn,
        "mcp_servers",
        {
            "last_checked_at": (
                "ALTER TABLE mcp_servers ADD COLUMN last_checked_at TEXT NOT NULL DEFAULT ''"
            ),
            "last_health_ok": (
                "ALTER TABLE mcp_servers ADD COLUMN last_health_ok INTEGER NOT NULL DEFAULT 0"
            ),
            "last_health_latency_ms": (
                "ALTER TABLE mcp_servers ADD COLUMN last_health_latency_ms "
                "INTEGER NOT NULL DEFAULT 0"
            ),
            "last_health_error": (
                "ALTER TABLE mcp_servers ADD COLUMN last_health_error TEXT NOT NULL DEFAULT ''"
            ),
        },
    )


def m_mcp_server_rate_limit(conn: Connection) -> None:
    """A per-server ceiling on calls per minute.

    Default 0 (no ceiling): a limit nobody set should not start refusing work.
    """
    add_columns_if_missing(
        conn,
        "mcp_servers",
        {
            "rate_limit_per_min": (
                "ALTER TABLE mcp_servers ADD COLUMN rate_limit_per_min INTEGER NOT NULL DEFAULT 0"
            ),
        },
    )


def m_mcp_server_tool_catalog(conn: Connection) -> None:
    """Cache the tools a server reported to `tools/list`.

    Added with the listing itself: the gateway shows how many tools a server
    exposes, and dialling every registered server on each page render would put
    a network round trip behind a read. `tools_listed_at` empty means never
    listed, which is not the same as a server that listed nothing.
    """
    add_columns_if_missing(
        conn,
        "mcp_servers",
        {
            "tools_json": (
                "ALTER TABLE mcp_servers ADD COLUMN tools_json TEXT NOT NULL DEFAULT '[]'"
            ),
            "tools_listed_at": (
                "ALTER TABLE mcp_servers ADD COLUMN tools_listed_at TEXT NOT NULL DEFAULT ''"
            ),
        },
    )
