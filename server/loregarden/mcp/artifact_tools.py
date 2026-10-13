"""The artifact-writing MCP tools: `loregarden_attach_artifact` and `loregarden_attach_evidence`."""

from __future__ import annotations

import json
from typing import Any

from pydantic import TypeAdapter, ValidationError
from sqlmodel import Session

from loregarden.services.evidence import ARTIFACT_KIND as EVIDENCE_ARTIFACT_KIND
from loregarden.services.evidence import EVIDENCE_KINDS, resolve_head_sha

_CONTENT_OBJECT = TypeAdapter(dict[str, Any])


def _content_object(raw: str | None) -> dict[str, Any]:
    """`content_json` parsed, refusing anything but a JSON object.

    Every reader of `Artifact.content_json` treats it as an object. A JSON string
    holding an object (an agent that encoded twice) was once accepted and made
    `GET /api/tickets/{id}` fail on every load for that ticket.
    """
    if not raw:
        return {}
    try:
        return _CONTENT_OBJECT.validate_json(raw)
    except ValidationError as exc:
        raise ValueError(
            'content_json must be a JSON object, e.g. \'{"summary": "…"}\'. '
            "If you passed an object as a string, pass it once, not encoded twice."
        ) from exc


def attach_artifact(svc, ticket, arguments: dict[str, Any], *, run_id: str | None) -> str:
    content = _content_object(arguments.get("content_json"))
    artifact = svc.attach_artifact(
        ticket,
        kind=arguments.get("kind", "log"),
        title=arguments.get("title", ""),
        content=content,
        run_id=run_id,
    )
    return json.dumps({"ok": True, "artifact_id": artifact.id}, indent=2)


def attach_evidence(
    session: Session, svc, ticket, arguments: dict[str, Any], *, run_id: str | None
) -> str:
    """Record proof of behaviour, stamped with the commit it proves."""
    evidence_kind = arguments["evidence_kind"]
    if evidence_kind not in EVIDENCE_KINDS:
        raise ValueError(
            f"Unknown evidence_kind '{evidence_kind}'. Valid kinds: {', '.join(EVIDENCE_KINDS)}"
        )
    content = _content_object(arguments.get("content_json"))
    # Stamped here, not taken from the agent: an agent choosing its own sha can
    # claim proof against a commit its work predates.
    artifact = svc.attach_artifact(
        ticket,
        kind=EVIDENCE_ARTIFACT_KIND,
        title=arguments.get("title", ""),
        content=content,
        evidence_kind=evidence_kind,
        commit_sha=resolve_head_sha(session, ticket),
        run_id=run_id,
    )
    return json.dumps(
        {"ok": True, "artifact_id": artifact.id, "commit_sha": artifact.commit_sha}, indent=2
    )
