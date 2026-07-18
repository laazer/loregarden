"""Prior decisions reach a stage without it having to go looking (#5)."""

import json

from loregarden.agents.inherited_wisdom import build_inherited_wisdom
from loregarden.models.domain import Ticket, WorkItemType
from loregarden.services.memory_store import AgentMemoryService, ObsidianMemoryStore


def _ticket() -> Ticket:
    return Ticket(
        id="ticket-uuid-1",
        external_id="42-add-rate-limiting",
        workspace_id="ws",
        title="Add rate limiting to the public API",
        work_item_type=WorkItemType.TASK,
        acceptance_criteria_json=json.dumps([]),
    )


def _memory(tmp_path) -> AgentMemoryService:
    return AgentMemoryService(obsidian=ObsidianMemoryStore(tmp_path), graph_sqlite_base=None)


def test_checkpoints_from_earlier_stages_are_surfaced(tmp_path):
    memory = _memory(tmp_path)
    ticket = _ticket()
    memory.append_checkpoint(
        ticket_id=ticket.external_id,
        workspace_slug="lg",
        run_id="run_1",
        entry="Chose a token bucket over a sliding window; simpler to reason about.",
    )

    text = build_inherited_wisdom(ticket, "lg", memory=memory)
    assert "token bucket" in text
    assert "do not re-derive" in text


def test_checkpoints_are_found_under_either_identifier(tmp_path):
    """append_checkpoint slugs whatever id the caller passed, and the MCP tool
    accepts a UUID or an external id, so both spellings must resolve."""
    memory = _memory(tmp_path)
    ticket = _ticket()
    memory.append_checkpoint(
        ticket_id=ticket.id,
        workspace_slug="lg",
        run_id="run_1",
        entry="Recorded against the UUID form.",
    )
    assert "UUID form" in build_inherited_wisdom(ticket, "lg", memory=memory)


def test_returns_empty_when_the_ticket_has_no_history(tmp_path):
    """An empty block drops out of the prompt entirely."""
    assert build_inherited_wisdom(_ticket(), "lg", memory=_memory(tmp_path)) == ""


def test_output_is_capped(tmp_path):
    memory = _memory(tmp_path)
    ticket = _ticket()
    for i in range(12):
        memory.append_checkpoint(
            ticket_id=ticket.external_id,
            workspace_slug="lg",
            run_id=f"run_{i}",
            entry="x" * 900,
        )
    assert len(build_inherited_wisdom(ticket, "lg", memory=memory, max_chars=500)) <= 500


def test_unreadable_memory_never_breaks_the_prompt():
    """The vault is optional and lives on synced network storage — a failure
    there must cost the section, not the run."""

    class Exploding:
        obsidian = None

        def search(self, *args, **kwargs):
            raise OSError("vault unavailable")

    assert build_inherited_wisdom(_ticket(), "lg", memory=Exploding()) == ""
