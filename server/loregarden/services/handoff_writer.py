"""Validated write path for workflow handoff artifacts.

Finishing agents historically hand-wrote ``project_board/checkpoints/<ticket>/
handoff-latest.yaml`` as free-form YAML, with no schema and no catalog at write
time — so they invented item keys the CI gate rejects, and only found out when the
orchestrator ran the gate much later. This service renders the canonical YAML from
structured input, writes it into the workspace repo (where the hermetic CI gate
reads it), then runs the workspace's *own* handoff gate as the validator and returns
its violations so the agent can self-correct in the same turn.

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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from loregarden.models.domain import Workspace
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session

CHECKPOINTS_SUBDIR = "project_board/checkpoints"
HANDOFF_FILENAME = "handoff-latest.yaml"
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


def _render_yaml(
    *,
    external_id: str,
    from_agent: str,
    to_agent: str,
    checklist: list[dict[str, Any]],
) -> str:
    met, total = _counters(checklist)
    doc = {
        "handoff": {
            "schema_version": "1.0",
            "ticket_id": external_id,
            "from_agent": from_agent,
            "to_agent": to_agent,
            "validated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "required_items_met": met,
            "total_required_items": total,
            "checklist": checklist,
        }
    }
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100)


def _validate_via_workspace_gate(
    repo_root: Path,
    *,
    external_id: str,
    from_agent: str,
    to_agent: str,
) -> dict[str, Any]:
    """Run the workspace's own handoff gate module against the just-written file.

    Returns a dict with ``ran`` (bool). When ``ran`` is True it also carries
    ``status`` / ``violations`` / ``remediation_hints`` / ``gaps`` from the gate.
    When False it carries ``reason`` explaining why validation was skipped.
    """
    if not (repo_root / GATE_MODULE_RELPATH).is_file():
        return {"ran": False, "reason": f"No handoff gate at {GATE_MODULE_RELPATH}"}

    payload = json.dumps({"ticket_id": external_id, "from_agent": from_agent, "to_agent": to_agent})
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
    """Render, write, and gate-validate a ticket's ``handoff-latest.yaml``.

    On validation FAIL the just-written file is rolled back (to the prior artifact,
    or removed if none existed) unless a concurrent writer has since replaced it, so
    a broken authoring attempt never clobbers a previously valid handoff.
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
    ticket_dir = repo_root / CHECKPOINTS_SUBDIR / external_id
    target = ticket_dir / HANDOFF_FILENAME

    content = _render_yaml(
        external_id=external_id,
        from_agent=from_agent,
        to_agent=to_agent,
        checklist=normalized,
    )

    prior: bytes | None = target.read_bytes() if target.is_file() else None
    ticket_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")

    validation = _validate_via_workspace_gate(
        repo_root, external_id=external_id, from_agent=from_agent, to_agent=to_agent
    )
    met, total = _counters(normalized)
    base: dict[str, Any] = {
        "path": str(target),
        "from_agent": from_agent,
        "to_agent": to_agent,
        "required_items_met": met,
        "total_required_items": total,
    }

    if not validation["ran"]:
        # Could not validate (no gate / infra error). Keep the file — there's no
        # catalog to have violated — but tell the caller it is unverified.
        return {
            **base,
            "status": "written_unvalidated",
            "message": f"Handoff written but not gate-validated: {validation['reason']}",
        }

    if validation["status"] == "PASS":
        return {
            **base,
            "status": "PASS",
            "message": validation.get("message") or "Handoff written and gate-validated.",
        }

    # Validation failed — roll back so a broken artifact does not linger or clobber a
    # previously valid one, but never overwrite a concurrent writer's newer file.
    if target.read_bytes() == content.encode("utf-8"):
        if prior is None:
            target.unlink(missing_ok=True)
        else:
            target.write_bytes(prior)

    return {
        **base,
        "status": "FAIL",
        "message": validation.get("message") or "Handoff failed gate validation; not written.",
        "violations": validation.get("violations", []),
        "remediation_hints": validation.get("remediation_hints", []),
        "gaps": validation.get("gaps", []),
        "rolled_back": True,
    }
