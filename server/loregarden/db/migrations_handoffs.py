"""Backfill committed handoff YAML files into the artifacts table.

Handoffs became database-authoritative (see ``services/handoff_store``). The files
already committed across the workspaces are the only copy of what each agent attested
to at each historical transition, so they are imported rather than dropped.

Idempotent by ``(ticket, validated_at)``: re-running imports
nothing twice, and a workspace whose repo is absent from this machine is skipped rather
than failing the migration — the file is a snapshot of history, and history that is not
mounted is not an error.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import yaml
from loregarden.config import settings
from loregarden.db.migration_utils import table_exists
from sqlalchemy import text
from sqlalchemy.engine import Connection

logger = logging.getLogger(__name__)

CHECKPOINTS_SUBDIR = "project_board/checkpoints"
HANDOFF_FILENAME = "handoff-latest.yaml"
HANDOFF_ARTIFACT_KIND = "handoff"


def _existing_signatures(conn: Connection) -> set[tuple[str, str]]:
    """(ticket_id, validated_at) for handoffs already stored, so a re-run is a no-op."""
    rows = conn.execute(
        text("SELECT ticket_id, content_json FROM artifacts WHERE kind = :kind"),
        {"kind": HANDOFF_ARTIFACT_KIND},
    ).fetchall()
    seen: set[tuple[str, str]] = set()
    for ticket_id, content_json in rows:
        try:
            doc = json.loads(content_json or "{}")
        except json.JSONDecodeError:
            continue
        handoff = doc.get("handoff") if isinstance(doc, dict) else None
        if isinstance(handoff, dict):
            seen.add((ticket_id, str(handoff.get("validated_at", ""))))
    return seen


def _load_handoff(path: Path) -> dict | None:
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        logger.warning("handoff backfill skipping unreadable %s: %s", path, exc)
        return None
    if not isinstance(loaded, dict) or not isinstance(loaded.get("handoff"), dict):
        logger.warning("handoff backfill skipping %s: no `handoff` mapping", path)
        return None
    return loaded


def _checkpoints_root(repo_path: str) -> Path:
    """Same resolution as services.workspace_paths.resolve_workspace_root: a relative
    repo_path (loregarden's own is ".") is anchored to the repo root, never to whatever
    cwd the migration happens to run under."""
    root = Path((repo_path or ".").strip()).expanduser()
    if not root.is_absolute():
        root = settings.repo_root / root
    return (root / CHECKPOINTS_SUBDIR).resolve()


def _insert_handoff(conn: Connection, ticket_id: str, doc: dict) -> None:
    handoff = doc["handoff"]
    conn.execute(
        text(
            "INSERT INTO artifacts "
            "(id, ticket_id, run_id, kind, title, content_json, evidence_kind, "
            "commit_sha, created_at) "
            "VALUES (:id, :ticket_id, NULL, :kind, :title, :content, '', '', :now)"
        ),
        {
            "id": str(uuid4()),
            "ticket_id": ticket_id,
            "kind": HANDOFF_ARTIFACT_KIND,
            "title": (f"handoff {handoff.get('from_agent', '?')} → {handoff.get('to_agent', '?')}"),
            "content": json.dumps(doc),
            "now": datetime.now(timezone.utc),
        },
    )


def _import_checkpoints(
    conn: Connection,
    *,
    checkpoints: Path,
    tickets: dict[str, str],
    seen: set[tuple[str, str]],
) -> int:
    """Import one workspace's committed handoffs. Returns how many were new."""
    imported = 0
    for ticket_dir in sorted(checkpoints.iterdir(), key=lambda p: p.name):
        path = ticket_dir / HANDOFF_FILENAME
        if not path.is_file():
            continue
        ticket_id = tickets.get(ticket_dir.name)
        if ticket_id is None:
            # A checkpoint dir whose ticket this database never knew (renamed, deleted,
            # or from the workspace's own pre-loregarden scheme). Nothing to attach the
            # artifact to; the file stays in git history.
            continue
        doc = _load_handoff(path)
        if doc is None:
            continue
        signature = (ticket_id, str(doc["handoff"].get("validated_at", "")))
        if signature in seen:
            continue
        _insert_handoff(conn, ticket_id, doc)
        seen.add(signature)
        imported += 1
    return imported


def m_backfill_handoff_artifacts(conn: Connection) -> None:
    if not (table_exists(conn, "artifacts") and table_exists(conn, "tickets")):
        return

    seen = _existing_signatures(conn)
    imported = 0
    for workspace_id, repo_path in conn.execute(
        text("SELECT id, repo_path FROM workspaces")
    ).fetchall():
        checkpoints = _checkpoints_root(repo_path)
        if not checkpoints.is_dir():
            continue
        tickets = {
            external_id: ticket_id
            for ticket_id, external_id in conn.execute(
                text("SELECT id, external_id FROM tickets WHERE workspace_id = :ws"),
                {"ws": workspace_id},
            ).fetchall()
        }
        imported += _import_checkpoints(conn, checkpoints=checkpoints, tickets=tickets, seen=seen)

    if imported:
        logger.info("handoff backfill imported %d handoff artifact(s)", imported)
