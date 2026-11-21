"""`loregarden_attach_evidence`. `loregarden_attach_artifact` is the `artifact_attach` operation."""

from __future__ import annotations

import json
from typing import Any

from sqlmodel import Session

from loregarden.services.artifact_content import parse_content_object
from loregarden.services.evidence import ARTIFACT_KIND as EVIDENCE_ARTIFACT_KIND
from loregarden.services.evidence import EVIDENCE_KINDS, resolve_head_sha
from loregarden.services.review_kit import ingest_review_kit


def attach_evidence(
    session: Session, svc, ticket, arguments: dict[str, Any], *, run_id: str | None
) -> str:
    """Record proof of behaviour, stamped with the commit it proves."""
    evidence_kind = arguments["evidence_kind"]
    if evidence_kind not in EVIDENCE_KINDS:
        raise ValueError(
            f"Unknown evidence_kind '{evidence_kind}'. Valid kinds: {', '.join(EVIDENCE_KINDS)}"
        )
    # A `review` block's screenshots are copied in here, before the artifact
    # exists, so a refused kit leaves no row pointing at missing captures.
    content = ingest_review_kit(ticket.id, parse_content_object(arguments.get("content_json")))
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
