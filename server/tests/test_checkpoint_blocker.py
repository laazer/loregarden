"""Recording an unresolved blocker blocks the ticket (430).

Observed on blobert ticket 22 (2026-08-14). An implementation agent hit a
read-only git index, could not commit, appended a checkpoint documenting the
blocker — which *satisfied* its required `impl_checkpoint_logged` item — and
completed the stage 4/4.

Documenting a blocker and being blocked were the same action, so the failure
mode is self-laundering: the more honestly an agent records a blocker, the more
completely it discharges its checklist. Nothing punishes the honest agent, and
nothing stops the ticket.

**Scope.** This is loregarden's half. The catalog that decides whether an item
is satisfied lives in each workspace's own gate module — blobert's
`ci/scripts/gates/handoff_validation_check.py` holds `impl_checkpoint_logged` —
and its half is filed separately. It depends on this one: the gate can only
tell a blocker checkpoint from an informational one if loregarden records the
distinction, which is what `blocker` does.

The escalation is ticket-level and deliberately does not touch orchestration.
`run_id` here is a filename slug — agents pass arbitrary strings — so there is
often no orchestration to resolve, and ending a run is the orchestrator's call
rather than a side effect of writing a note.
"""

import json

import pytest
from loregarden.mcp.tools import execute_tool, normalize_tool_arguments
from loregarden.models.domain import Ticket, TicketState, Workspace
from sqlmodel import Session

_BLOCKER = (
    "### [t1] implement — could not commit\n"
    "- **Blocker:** the git index is read-only; six produced files cannot be committed.\n"
    "- **Unresolved.**\n"
)
_ASSUMPTION = (
    "### [t1] implement — field name\n"
    "- **Would have asked:** what to call the column.\n"
    "- **Assumption made:** `head_sha`, matching the sibling table.\n"
    "- **Confidence:** high.\n"
)


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="vault")
def vault_fixture(tmp_path, monkeypatch):
    """`resolved_obsidian_vault` returns None for a path that is not a directory,
    so the vault has to exist before the service will write to it."""
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setattr("loregarden.config.settings.obsidian_vault_dir", str(vault))
    return vault


@pytest.fixture(name="ticket")
def ticket_fixture(session, tmp_path):
    ws = Workspace(slug="wsx", name="WSX", repo_path=str(tmp_path))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    ticket = Ticket(external_id="t1", workspace_id=ws.id, title="demo")
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def _append(session, *, entry: str, blocker: bool | None):
    args = {
        "ticket_id": "t1",
        "workspace_slug": "wsx",
        "run_id": "run-1",
        "entry": entry,
    }
    if blocker is not None:
        args["blocker"] = blocker
    return json.loads(execute_tool(session, "loregarden_append_checkpoint", args))


def test_a_blocker_checkpoint_blocks_the_ticket(session, ticket, vault):
    """AC2. The entry reaches the operator through `blocking_issues`, not just the vault."""
    result = _append(session, entry=_BLOCKER, blocker=True)

    assert result["blocked"] is True, result
    assert "read-only" in result["blocking_issues"], result
    session.refresh(ticket)
    assert ticket.state == TicketState.BLOCKED
    assert "read-only" in ticket.blocking_issues


def test_an_ordinary_checkpoint_leaves_the_ticket_alone(session, ticket, vault):
    """AC3, and the discriminator.

    A checkpoint is normally an assumption an agent made and moved past — the
    common case by far. A fix that blocked on every checkpoint would satisfy the
    test above and stop every ticket in the fleet, which is worse than the hole
    it closes.
    """
    before = ticket.state

    result = _append(session, entry=_ASSUMPTION, blocker=False)

    assert "blocked" not in result, result
    session.refresh(ticket)
    assert ticket.state == before


def test_omitting_the_flag_does_not_block(session, ticket, vault):
    """Every existing caller omits it, including this session's own checkpoints.

    A default that blocked would have blocked each of them.
    """
    before = ticket.state

    _append(session, entry=_ASSUMPTION, blocker=None)

    session.refresh(ticket)
    assert ticket.state == before


def test_the_entry_is_written_before_the_ticket_is_blocked(session, ticket, vault):
    """The record of *why* must outlive a failure to resolve the ticket.

    An operator opening a blocked ticket needs the checkpoint to already exist;
    blocking first and writing second would leave a ticket blocked for a reason
    nobody can read.
    """
    result = _append(session, entry=_BLOCKER, blocker=True)

    written = list(vault.rglob("run-1.md"))
    assert written, f"no checkpoint file was written: {result}"
    assert "read-only" in written[0].read_text(encoding="utf-8")


def test_the_normalizer_preserves_the_flag():
    """A declared field the normalizer drops is a silent no-op — the tool would
    accept `blocker: true` and quietly not block."""
    args = normalize_tool_arguments(
        "loregarden_append_checkpoint",
        {"ticket_id": "t1", "workspace_slug": "wsx", "run_id": "r", "entry": "e", "blocker": True},
    )
    assert args["blocker"] is True

    bare = normalize_tool_arguments(
        "loregarden_append_checkpoint",
        {"ticket_id": "t1", "workspace_slug": "wsx", "run_id": "r", "entry": "e"},
    )
    assert bare["blocker"] is False
