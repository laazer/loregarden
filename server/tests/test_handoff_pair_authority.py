"""The orchestrator, not a frozen list in a workspace script, decides a transition.

`lg-workflow-integrity-730`. On blob-procedural-sdf-31 the ui-design stage handed
off to spec and the workspace gate refused:

    Pair (ui-design-decision, spec) is not in the frozen pair table

blobert's gate hardcodes VALID_PAIRS for a workflow that no longer exists —
`ui-design-decision` was inserted by migration 0122 and the table never learned
it. Loregarden, at that moment, KNEW the pair was valid: it had dispatched
ui-design-decision and was dispatching spec, from the template it owns. It is the
authority on transitions. Yet it returned the gate's verdict verbatim, so the
party with less information overruled the one executing the order.

The override is narrow on purpose, and most of these tests are about the edges
of that narrowness.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

from loregarden.models.domain import (
    Ticket,
    WorkflowInstance,
    WorkflowStageDef,
    WorkflowTemplate,
    Workspace,
)
from loregarden.services.handoff_writer import write_handoff
from loregarden.services.workflow_state import initial_stages_json
from sqlmodel import Session
from tests.worktree_helpers import make_repo

# blobert's refusal, reproduced: a frozen pair set, and an early return on an
# unknown pair that never reaches the checklist — exactly the shape at
# handoff_validation_check.py:990-999.
_FROZEN_PAIR_GATE = textwrap.dedent(
    """
    VALID_PAIRS = {("planner", "spec"), ("spec", "test_designer")}

    def run(inputs):
        pair = (inputs["from_agent"], inputs["to_agent"])
        if pair not in VALID_PAIRS:
            return {
                "status": "FAIL",
                "message": f"Unknown handoff pair: {pair[0]} -> {pair[1]}",
                "violations": [{"rule": "handoff_pair_unknown",
                                "message": "Pair is not in the frozen pair table"}],
            }
        return {"status": "PASS", "message": "ok"}
    """
)

# A gate whose FAIL carries a pair violation AND a real catalog violation. The
# override must leave this alone: the pair part is stale, the catalog part is
# not, and discarding both would throw away what the gate was right about.
_MIXED_FAIL_GATE = textwrap.dedent(
    """
    def run(inputs):
        return {
            "status": "FAIL",
            "message": "two problems",
            "violations": [
                {"rule": "handoff_pair_unknown", "message": "unknown pair"},
                {"rule": "handoff_required_item_missing", "message": "no evidence"},
            ],
        }
    """
)


def _repo_with_gate(tmp_path: Path, source: str) -> Path:
    repo = make_repo(tmp_path, name="repo")
    gates = repo / "ci" / "scripts" / "gates"
    gates.mkdir(parents=True)
    (gates / "__init__.py").write_text("", encoding="utf-8")
    (gates / "handoff_validation_check.py").write_text(source, encoding="utf-8")
    (repo / "project_board" / "checkpoints").mkdir(parents=True)
    return repo


def _stage(key: str, order: int, agent_id: str = "") -> WorkflowStageDef:
    return WorkflowStageDef(key=key, name=key, order=order, agent_id=agent_id)


def _ticket_on_template(session: Session, repo: Path, stages: list[WorkflowStageDef]) -> Ticket:
    """A ticket whose workflow INSTANCE pins a template with these stages."""
    ws = Workspace(slug="wsx", name="WSX", repo_path=str(repo))
    session.add(ws)
    session.commit()
    session.refresh(ws)

    template = WorkflowTemplate(
        slug="pair-authority-tdd",
        name="pair authority",
        version=1,
        stages_json=json.dumps([s.model_dump() for s in stages]),
        transitions_json="[]",
    )
    session.add(template)
    session.commit()
    session.refresh(template)

    ticket = Ticket(external_id="pair-1", workspace_id=ws.id, title="pair authority")
    session.add(ticket)
    session.commit()
    session.refresh(ticket)

    session.add(
        WorkflowInstance(
            ticket_id=ticket.id,
            template_id=template.id,
            template_version=1,
            current_stage_key=stages[0].key,
            stages_json=initial_stages_json(stages),
        )
    )
    session.commit()
    return ticket


def _blobert_like_stages() -> list[WorkflowStageDef]:
    """The live blobert-tdd order, with its agentless stages, so the pair the
    real gate refused is the pair under test."""
    return [
        _stage("plan", 1, "planner"),
        _stage("domain_consultation", 2, "retriever"),
        _stage("ui-design", 3, "ui-design-decision"),
        _stage("spec", 4, "spec"),
        _stage("test-design", 5, "test_designer"),
        _stage("implement", 6, "core_simulation"),
        _stage("script_review", 7, ""),
        _stage("ac_gate", 8, "ac_gatekeeper"),
    ]


def _write(session: Session, ticket: Ticket, from_agent: str, to_agent: str) -> dict:
    return write_handoff(
        session,
        ticket_id=ticket.external_id,
        workspace_slug="wsx",
        from_agent=from_agent,
        to_agent=to_agent,
        checklist=[{"item_key": "anything", "item": "x", "status": "complete", "evidence": "y"}],
    )


def test_the_recorded_refusal_is_now_stored(isolated_db, tmp_path: Path):
    """AC1. The exact pair blob-procedural-sdf-31 was refused on."""
    repo = _repo_with_gate(tmp_path, _FROZEN_PAIR_GATE)
    with Session(isolated_db) as s:
        ticket = _ticket_on_template(s, repo, _blobert_like_stages())
        result = _write(s, ticket, "ui-design-decision", "spec")

    assert result["status"] == "PASS"
    assert result["artifact_id"], "the handoff must actually be stored, not just reported PASS"


def test_the_override_says_the_gate_is_stale(isolated_db, tmp_path: Path):
    """AC2. Absorbing the drift silently would be worse than the refusal: the
    gate would stay wrong forever with nobody told."""
    repo = _repo_with_gate(tmp_path, _FROZEN_PAIR_GATE)
    with Session(isolated_db) as s:
        ticket = _ticket_on_template(s, repo, _blobert_like_stages())
        result = _write(s, ticket, "ui-design-decision", "spec")

    assert result.get("warnings"), "the stale gate must be named, not absorbed"
    assert "ui-design-decision" in result["warnings"][0]
    assert "spec" in result["warnings"][0]


def test_a_pair_the_template_does_not_confirm_still_fails(isolated_db, tmp_path: Path):
    """AC3, and the edge that keeps this from being 'ignore the gate'.

    `planner -> ac_gatekeeper` is not consecutive in this template. The gate
    said unknown; the template agrees. The FAIL stands.
    """
    repo = _repo_with_gate(tmp_path, _FROZEN_PAIR_GATE)
    with Session(isolated_db) as s:
        ticket = _ticket_on_template(s, repo, _blobert_like_stages())
        result = _write(s, ticket, "planner", "ac_gatekeeper")

    assert result["status"] == "FAIL"
    assert result["rolled_back"] is True


def test_a_fail_carrying_any_other_violation_is_untouched(isolated_db, tmp_path: Path):
    """AC4. Only handoff_pair_unknown is overrulable, and only on its own. A FAIL
    that also carries a catalog violation is a real failure that happens to
    mention the pair — overruling it discards what the gate was right about."""
    repo = _repo_with_gate(tmp_path, _MIXED_FAIL_GATE)
    with Session(isolated_db) as s:
        ticket = _ticket_on_template(s, repo, _blobert_like_stages())
        result = _write(s, ticket, "ui-design-decision", "spec")

    assert result["status"] == "FAIL"
    assert len(result["violations"]) == 2


def test_agentless_stages_do_not_break_the_chain(isolated_db, tmp_path: Path):
    """blobert-tdd runs implement -> script_review -> ac_gate, and script_review
    names no agent. The real handoff is core_simulation -> ac_gatekeeper, and a
    check on consecutive STAGES rather than consecutive AGENTS would miss it."""
    repo = _repo_with_gate(tmp_path, _FROZEN_PAIR_GATE)
    with Session(isolated_db) as s:
        ticket = _ticket_on_template(s, repo, _blobert_like_stages())
        result = _write(s, ticket, "core_simulation", "ac_gatekeeper")

    assert result["status"] == "PASS"


def test_a_pair_the_gate_already_knows_is_not_touched(isolated_db, tmp_path: Path):
    """The override must not run at all when the gate is happy. A known pair
    passes through the gate's own verdict, warnings and all."""
    repo = _repo_with_gate(tmp_path, _FROZEN_PAIR_GATE)
    with Session(isolated_db) as s:
        ticket = _ticket_on_template(s, repo, _blobert_like_stages())
        result = _write(s, ticket, "planner", "spec")

    assert result["status"] == "PASS"
    assert not result.get("warnings")
