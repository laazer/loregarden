"""Database storage for workflow handoff artifacts, and the export the gates read.

Handoffs used to live only as ``project_board/checkpoints/<ticket>/handoff-latest.yaml``
committed into the target repo. The rationale was CI hermeticity — the gate reads a
file with stdlib and pyyaml alone, so it could run with loregarden dead. Nothing ever
ran it that way: the only callers of a workspace's handoff gate are this control plane's
own ``gate_runner`` and ``handoff_writer``, both of which run with loregarden alive. The
files were therefore committed history that nothing read, and in loregarden's own repo
(which has no ``ci/`` tree at all) nothing *could* read them.

The artifact row is now the record of truth: ``kind='handoff'``, ``content_json`` holding
the canonical document. That gains what the files never had — a join to the run that
produced the handoff and the commit it attests to.

The gate still wants a file, and keeping it that way keeps the gate a pure function of a
directory rather than a client of this database. So the file becomes an *export*: written
to a gitignored scratch tree just before the gate runs, never into the repo's tracked
checkpoints. ``todos-latest.json`` is a separate artifact with no write path of its own and
still lives in the tracked checkpoints dir, so the scratch tree mirrors the ticket's real
checkpoint directory and overlays the database handoff on top — the todo gate reads the
same ``checkpoints_dir`` and must keep finding its file.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from loregarden.models.domain import Artifact, GitBoundary, Ticket, Workspace
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session, select

HANDOFF_ARTIFACT_KIND = "handoff"
HANDOFF_FILENAME = "handoff-latest.yaml"
CHECKPOINTS_SUBDIR = "project_board/checkpoints"
# Under the repo so the gate's own `_checkpoints_dir_allowed` accepts it (it requires a
# path beneath the repo root or cwd), and inside the already-gitignored `.loregarden/`
# runtime tree so an export is never committable.
HANDOFF_SCRATCH_SUBDIR = ".loregarden/handoffs"

#: Deliberately still 1.0 after the `boundary` block was added. The validator is
#: each workspace's *own* gate (see `handoff_writer`), and it accepts a closed set
#: of versions — blobert's, the only one on disk, allows {"1.0", "1"} and nothing
#: else. Announcing 1.1 from here would fail every handoff in every workspace whose
#: gate had not been updated first, and `write_handoff` rolls back on FAIL, so the
#: write path would simply stop working. Adding a key those gates ignore is
#: backward compatible; renaming the version is not. The bump belongs with a change
#: that updates the gates, not with the field.
SCHEMA_VERSION = "1.0"

#: The document key holding the boundary. Absent on every handoff written before
#: this existed, which reads back as an unrecorded boundary — unknown, not changed.
BOUNDARY_KEY = "boundary"


def build_handoff_doc(
    *,
    external_id: str,
    from_agent: str,
    to_agent: str,
    checklist: list[dict[str, Any]],
    required_items_met: int,
    total_required_items: int,
    boundary: GitBoundary,
) -> dict[str, Any]:
    """The canonical handoff document. Both the stored row and the YAML export
    project from this, so the two can never describe different handoffs."""
    return {
        "handoff": {
            "schema_version": SCHEMA_VERSION,
            "ticket_id": external_id,
            "from_agent": from_agent,
            "to_agent": to_agent,
            "validated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "required_items_met": required_items_met,
            "total_required_items": total_required_items,
            "checklist": checklist,
            # The tree the author attested against, so a receiving stage can ask
            # whether the world it inherited is still the one being described.
            BOUNDARY_KEY: boundary.model_dump(),
        }
    }


def boundary_from_doc(doc: dict[str, Any]) -> GitBoundary:
    """The boundary a stored handoff attested against.

    Documents written before the block existed have none, and answer with an
    unrecorded boundary rather than an error — the whole point of `is_recorded`
    is that "we never looked" is a third state beside match and mismatch.
    """
    raw = doc.get("handoff", {}).get(BOUNDARY_KEY)
    return GitBoundary.model_validate(raw) if raw else GitBoundary()


def render_handoff_yaml(doc: dict[str, Any]) -> str:
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100)


def store_handoff(
    session: Session,
    *,
    ticket: Ticket,
    doc: dict[str, Any],
    run_id: str | None = None,
) -> Artifact:
    """Append a handoff artifact. Rows are append-only: the history of what each
    agent attested to at each transition is the point, so a re-write adds a row
    rather than replacing one.

    The row's `commit_sha` is read off the document's own boundary rather than
    taken as an argument. It was a parameter no caller ever passed, so every
    handoff artifact carried an empty sha and none of them were reachable from
    the commit-scoped evidence queries in `services.evidence`. Deriving it here
    also makes the row and the document structurally unable to disagree.
    """
    handoff = doc["handoff"]
    artifact = Artifact(
        ticket_id=ticket.id,
        run_id=run_id,
        kind=HANDOFF_ARTIFACT_KIND,
        title=f"handoff {handoff['from_agent']} → {handoff['to_agent']}",
        content_json=json.dumps(doc),
        commit_sha=boundary_from_doc(doc).head_sha,
    )
    session.add(artifact)
    session.flush()
    return artifact


def latest_handoff_doc(session: Session, ticket_id: str) -> dict[str, Any] | None:
    """The most recent stored handoff document for a ticket, or None."""
    row = session.exec(
        select(Artifact)
        .where(Artifact.ticket_id == ticket_id, Artifact.kind == HANDOFF_ARTIFACT_KIND)
        .order_by(Artifact.created_at.desc(), Artifact.id.desc())
    ).first()
    if row is None:
        return None
    loaded = json.loads(row.content_json or "{}")
    return loaded if isinstance(loaded, dict) and "handoff" in loaded else None


def scratch_root(repo_root: Path) -> Path:
    return repo_root / HANDOFF_SCRATCH_SUBDIR


def export_for_gate(session: Session, workspace: Workspace, ticket: Ticket) -> Path:
    """Build the checkpoints tree the workspace gates should read, and return its root.

    The ticket's scratch directory is rebuilt from scratch each call so a handoff from an
    earlier transition can never be read as this one's. Absence is meaningful and is
    preserved: when no handoff has been stored, none is exported, and the gate fails the
    transition exactly as it did when the file was missing from the repo.
    """
    repo_root = resolve_workspace_root(workspace)
    root = scratch_root(repo_root)
    ticket_scratch = root / ticket.external_id
    if ticket_scratch.exists():
        shutil.rmtree(ticket_scratch)
    ticket_scratch.mkdir(parents=True, exist_ok=True)

    # Mirror the tracked checkpoint dir so co-located artifacts the other gates read
    # (todos-latest.json above all) are still found under the redirected root.
    source = repo_root / CHECKPOINTS_SUBDIR / ticket.external_id
    if source.is_dir():
        for entry in source.iterdir():
            if entry.is_file() and entry.name != HANDOFF_FILENAME:
                shutil.copy2(entry, ticket_scratch / entry.name)

    doc = latest_handoff_doc(session, ticket.id)
    if doc is not None:
        (ticket_scratch / HANDOFF_FILENAME).write_text(render_handoff_yaml(doc), encoding="utf-8")
    return root
