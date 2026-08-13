"""Validated write path for workflow handoff artifacts.

Finishing agents historically hand-wrote ``project_board/checkpoints/<ticket>/
handoff-latest.yaml`` as free-form YAML, with no schema and no catalog at write
time — so they invented item keys the gate rejects, and only found out when the
orchestrator ran the gate much later. This service builds the canonical document from
structured input, stores it (see `handoff_store` for why the database rather than a
committed file), exports the YAML the gate reads to a gitignored scratch tree, then runs
the workspace's *own* handoff gate as the validator and returns its violations so the
agent can self-correct in the same turn.

The frozen catalog stays single-sourced in the workspace gate
(``ci/scripts/gates/handoff_validation_check.py``); loregarden never duplicates it —
it only invokes that gate's ``run()`` in a subprocess for structured validation,
deliberately bypassing ``gate_runner.py`` so no audit-log / gate-results files are
written on a mere authoring attempt.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from loregarden.models.domain import Workspace
from loregarden.services.git_boundary import read_boundary
from loregarden.services.handoff_store import (
    HANDOFF_SCRATCH_SUBDIR,
    build_handoff_doc,
    export_for_gate,
    store_handoff,
)
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.ticket_worktree import resolve_ticket_root
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session

GATE_MODULE_RELPATH = "ci/scripts/gates/handoff_validation_check.py"
GATE_PACKAGE_ROOT = "ci/scripts"
VALIDATION_TIMEOUT_SECONDS = 60
VALID_STATUSES = frozenset({"complete", "incomplete", "deferred", "blocked"})

# Imports only the workspace gate module (stdlib + pyyaml) and prints its structured
# result — no gate_runner, so no audit-log / gate-results side effects in the repo.
_VALIDATOR_SRC = (
    "import sys, json\n"
    "sys.path.insert(0, sys.argv[2])\n"
    "from gates.handoff_validation_check import run\n"
    "print(json.dumps(run(json.loads(sys.argv[1]))))\n"
)


class HandoffWriteError(ValueError):
    """Raised for caller-fixable input problems (bad checklist, missing repo)."""


def _normalize_checklist(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise HandoffWriteError(f"checklist is not valid JSON: {exc}") from exc
    if not isinstance(raw, list) or not raw:
        raise HandoffWriteError("checklist must be a non-empty list of items")

    items: list[dict[str, Any]] = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise HandoffWriteError(f"checklist[{i}] must be an object")
        item_key = str(entry.get("item_key", "")).strip()
        label = str(entry.get("item", "")).strip()
        status = str(entry.get("status", "")).strip()
        if not item_key:
            raise HandoffWriteError(f"checklist[{i}].item_key is required")
        if not label:
            raise HandoffWriteError(
                f"checklist[{i}].item (label) is required and must match the catalog text for "
                f"{item_key!r}"
            )
        if status not in VALID_STATUSES:
            raise HandoffWriteError(
                f"checklist[{i}].status must be one of {sorted(VALID_STATUSES)}, got {status!r}"
            )

        evidence_raw = entry.get("evidence", "")
        evidence = evidence_raw if isinstance(evidence_raw, str) else str(evidence_raw)
        required = entry.get("required", True)
        required = (
            bool(required)
            if not isinstance(required, str)
            else required.strip().lower()
            in {
                "1",
                "true",
                "yes",
                "on",
            }
        )

        item: dict[str, Any] = {
            "item_key": item_key,
            "item": label,
            "required": required,
            "status": status,
            "evidence": evidence,
        }
        evidence_type = str(entry.get("evidence_type", "")).strip()
        if evidence_type:
            item["evidence_type"] = evidence_type
        items.append(item)
    return items


def _counters(checklist: list[dict[str, Any]]) -> tuple[int, int]:
    """Derive (required_items_met, total_required_items) from the checklist so the
    agent never hand-counts. The gate compares these against its catalog; they match
    when the supplied checklist covers exactly the pair's required catalog items
    (which the frozen-catalog docs instruct agents to do)."""
    total = sum(1 for it in checklist if it["required"])
    met = sum(
        1
        for it in checklist
        if it["required"] and it["status"] == "complete" and it["evidence"].strip()
    )
    return met, total


def _validate_via_workspace_gate(
    repo_root: Path,
    *,
    external_id: str,
    from_agent: str,
    to_agent: str,
    checkpoints_dir: str,
) -> dict[str, Any]:
    """Run the workspace's own handoff gate module against the just-written file.

    Returns a dict with ``ran`` (bool). When ``ran`` is True it also carries
    ``status`` / ``violations`` / ``remediation_hints`` / ``gaps`` from the gate.
    When False it carries ``reason`` explaining why validation was skipped.
    """
    if not (repo_root / GATE_MODULE_RELPATH).is_file():
        return {"ran": False, "reason": f"No handoff gate at {GATE_MODULE_RELPATH}"}

    payload = json.dumps(
        {
            "ticket_id": external_id,
            "from_agent": from_agent,
            "to_agent": to_agent,
            "checkpoints_dir": checkpoints_dir,
        }
    )
    try:
        completed = subprocess.run(
            [sys.executable, "-c", _VALIDATOR_SRC, payload, str(repo_root / GATE_PACKAGE_ROOT)],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=VALIDATION_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "ran": False,
            "reason": f"Gate validation timed out after {VALIDATION_TIMEOUT_SECONDS}s",
        }

    stdout = (completed.stdout or "").strip()
    if completed.returncode == 0 and stdout:
        try:
            result = json.loads(stdout.splitlines()[-1])
        except json.JSONDecodeError:
            return {"ran": False, "reason": f"Gate produced unparseable output: {stdout[:400]}"}
        return {
            "ran": True,
            "status": result.get("status", "FAIL"),
            "message": result.get("message", ""),
            "violations": result.get("violations", []),
            "remediation_hints": result.get("remediation_hints", []),
            "gaps": result.get("gaps", []),
        }

    stderr = (completed.stderr or "").strip()
    return {
        "ran": False,
        "reason": f"Gate did not run (exit {completed.returncode}): {stderr[:400]}",
    }


def write_handoff(
    session: Session,
    *,
    ticket_id: str,
    workspace_slug: str,
    from_agent: str,
    to_agent: str,
    checklist: Any,
) -> dict[str, Any]:
    """Store a ticket's handoff and gate-validate it.

    The handoff is persisted as an artifact row; the YAML the gate reads is exported to
    the gitignored scratch tree, never into the repo's tracked checkpoints. On validation
    FAIL nothing is committed to the database, so a broken authoring attempt never becomes
    the ticket's latest handoff.
    """
    from_agent = str(from_agent).strip()
    to_agent = str(to_agent).strip()
    if not from_agent or not to_agent:
        raise HandoffWriteError("from_agent and to_agent are required")

    normalized = _normalize_checklist(checklist)

    svc = OrchestrationCallbackService(session)
    ticket = svc.resolve_ticket(ticket_id=ticket_id, workspace_slug=workspace_slug)
    workspace = session.get(Workspace, ticket.workspace_id)
    if not workspace:
        raise HandoffWriteError("Workspace not found for ticket")

    repo_root = resolve_workspace_root(workspace)
    if not repo_root.is_dir():
        raise HandoffWriteError(f"Workspace repo path does not exist: {repo_root}")

    external_id = ticket.external_id
    met, total = _counters(normalized)
    # Read here rather than accepted from the caller: an agent reporting the tree
    # it worked in is the claim, not the evidence for it. The ticket's worktree,
    # not `repo_root` above — that is the shared checkout the gate export is
    # written under, while the agent's edits are in the tree the stages ran in.
    boundary = read_boundary(resolve_ticket_root(session, ticket, workspace))
    doc = build_handoff_doc(
        external_id=external_id,
        from_agent=from_agent,
        to_agent=to_agent,
        checklist=normalized,
        required_items_met=met,
        total_required_items=total,
        boundary=boundary,
    )

    # Store first, then export: the gate validates what was actually persisted, and a
    # rollback below un-stores it. Flushing without committing keeps the row visible to
    # `export_for_gate` in this session while leaving the transaction abortable.
    artifact = store_handoff(session, ticket=ticket, doc=doc)
    export_for_gate(session, workspace, ticket)

    validation = _validate_via_workspace_gate(
        repo_root,
        external_id=external_id,
        from_agent=from_agent,
        to_agent=to_agent,
        checkpoints_dir=HANDOFF_SCRATCH_SUBDIR,
    )
    base: dict[str, Any] = {
        "artifact_id": artifact.id,
        "from_agent": from_agent,
        "to_agent": to_agent,
        "required_items_met": met,
        "total_required_items": total,
    }

    if not validation["ran"]:
        # No gate for this workspace, or the gate could not run. There is no catalog to
        # have violated, so the handoff stands — but say plainly that nothing checked it,
        # rather than letting "unvalidated" read as "passed" (ticket 88).
        session.commit()
        return {
            **base,
            "status": "stored_unvalidated",
            "message": f"Handoff stored but not gate-validated: {validation['reason']}",
        }

    if validation["status"] == "PASS":
        session.commit()
        return {
            **base,
            "status": "PASS",
            "message": validation.get("message") or "Handoff stored and gate-validated.",
        }

    # Validation failed — discard the row so the ticket's latest handoff stays whatever
    # last passed, and re-export so the scratch tree matches the database again.
    session.rollback()
    export_for_gate(session, workspace, ticket)

    return {
        **base,
        "artifact_id": "",
        "status": "FAIL",
        "message": validation.get("message") or "Handoff failed gate validation; not stored.",
        "violations": validation.get("violations", []),
        "remediation_hints": validation.get("remediation_hints", []),
        "gaps": validation.get("gaps", []),
        "rolled_back": True,
    }
