"""`stage_resolution_memo` answers once per block, and never past it.

Resolving a ticket's stages queries its workflow instance, reads the workspace
override YAML off disk and re-parses the template's `stages_json` — and a ticket
list asks four times per row. The memo collapses that to one answer per distinct
workflow for the block it is opened over.

The risk it carries is staleness, so these tests pin both halves: inside the
block the answer is reused, and outside it a template edit is visible again.
"""

from __future__ import annotations

import json

from loregarden.models.domain import Ticket, WorkflowInstance, Workspace
from loregarden.services.workflow_service import (
    prime_workflow_instances,
    resolve_ticket_stages,
    stage_resolution_memo,
    workflow_instance_for,
)
from sqlalchemy import event
from sqlmodel import Session, select


class _InstanceQueryCounter:
    """Counts `workflow_instances` reads on an engine for a `with` block."""

    def __init__(self, engine) -> None:
        self.engine = engine
        self.count = 0

    def __enter__(self) -> _InstanceQueryCounter:
        event.listen(self.engine, "before_cursor_execute", self._on_execute)
        return self

    def __exit__(self, *_exc) -> None:
        event.remove(self.engine, "before_cursor_execute", self._on_execute)

    def _on_execute(self, _conn, _cursor, statement, *_args, **_kwargs) -> None:
        if "workflow_instances" in statement.lower():
            self.count += 1


def _tickets_with_instances(session: Session, count: int) -> list[Ticket]:
    instanced = {instance.ticket_id for instance in session.exec(select(WorkflowInstance)).all()}
    tickets = [t for t in session.exec(select(Ticket)).all() if t.id in instanced]
    assert len(tickets) >= count, "seed data no longer has enough workflow instances"
    return tickets[:count]


def test_the_memo_resolves_each_ticket_once(client, isolated_db):
    with Session(isolated_db) as session:
        tickets = _tickets_with_instances(session, 2)

        with _InstanceQueryCounter(isolated_db) as counter, stage_resolution_memo():
            for _ in range(4):
                for ticket in tickets:
                    resolve_ticket_stages(session, ticket)

        assert counter.count == len(tickets), (
            f"{counter.count} workflow_instance queries for {len(tickets)} tickets "
            "resolved four times each"
        )


def test_priming_loads_a_page_of_instances_in_one_query(client, isolated_db):
    with Session(isolated_db) as session:
        tickets = _tickets_with_instances(session, 3)

        with _InstanceQueryCounter(isolated_db) as counter, stage_resolution_memo():
            prime_workflow_instances(session, [t.id for t in tickets])
            for ticket in tickets:
                assert workflow_instance_for(session, ticket.id) is not None

        assert counter.count == 1


def test_priming_outside_a_memo_scope_changes_nothing(client, isolated_db):
    """No memo means no cache to fill — the reader still gets its own answer,
    rather than a `None` left behind by a prime that had nowhere to write."""
    with Session(isolated_db) as session:
        ticket = _tickets_with_instances(session, 1)[0]
        prime_workflow_instances(session, [ticket.id])
        assert workflow_instance_for(session, ticket.id) is not None


def test_the_memo_does_not_outlive_its_block(client, isolated_db):
    """A template edited after the block is visible to the next read."""
    with Session(isolated_db) as session:
        ticket = _tickets_with_instances(session, 1)[0]

        with stage_resolution_memo():
            template, before = resolve_ticket_stages(session, ticket)
        assert template is not None and before

        stages = json.loads(template.stages_json)
        renamed = stages[0]["key"]
        stages[0]["key"] = f"{renamed}_renamed"
        template.stages_json = json.dumps(stages)
        session.add(template)
        session.commit()

        with stage_resolution_memo():
            _, after = resolve_ticket_stages(session, ticket)
        assert [s.key for s in after] != [s.key for s in before]


def test_a_caller_cannot_mutate_the_memoized_stages(client, isolated_db):
    """The memo hands out its own list, so a caller that trims or reorders its
    stages does not rewrite the answer every later ticket in the block gets."""
    with Session(isolated_db) as session:
        ticket = _tickets_with_instances(session, 1)[0]

        with stage_resolution_memo():
            _, first = resolve_ticket_stages(session, ticket)
            assert len(first) > 1
            first.clear()
            _, second = resolve_ticket_stages(session, ticket)

        assert len(second) > 1


def test_a_workspace_row_is_read_once_per_block(client, isolated_db):
    """SQLAlchemy's identity map is weak, so a serializer that reads `ws.slug`
    and drops the row re-queries it per ticket."""
    with Session(isolated_db) as session:
        tickets = _tickets_with_instances(session, 3)
        assert len({t.workspace_id for t in tickets}) == 1

        counted = {"n": 0}

        def _on_execute(_conn, _cursor, statement, *_args, **_kwargs) -> None:
            if "from workspaces" in statement.lower():
                counted["n"] += 1

        event.listen(isolated_db, "before_cursor_execute", _on_execute)
        try:
            with stage_resolution_memo():
                for ticket in tickets:
                    resolve_ticket_stages(session, ticket)
        finally:
            event.remove(isolated_db, "before_cursor_execute", _on_execute)

        assert counted["n"] <= 1, f"{counted['n']} workspace reads for one workspace"


def test_resolution_without_a_memo_is_unchanged(client, isolated_db):
    """Every writer and single-ticket reader runs outside a scope; they must get
    the same answer the memo serves."""
    with Session(isolated_db) as session:
        ticket = _tickets_with_instances(session, 1)[0]
        plain_template, plain_stages = resolve_ticket_stages(session, ticket)
        with stage_resolution_memo():
            memo_template, memo_stages = resolve_ticket_stages(session, ticket)

        assert memo_template is plain_template
        assert [s.model_dump() for s in memo_stages] == [s.model_dump() for s in plain_stages]


def test_a_workspaceless_ticket_still_resolves_to_nothing(client, isolated_db):
    """The memo must not turn "no workspace" into a cached wrong answer."""
    with Session(isolated_db) as session:
        ticket = _tickets_with_instances(session, 1)[0]
        orphan = Ticket(
            external_id="lg-memo-orphan",
            workspace_id="does-not-exist",
            title="Orphan",
        )
        with stage_resolution_memo():
            assert resolve_ticket_stages(session, orphan) == (None, [])
            template, stages = resolve_ticket_stages(session, ticket)
        assert template is not None and stages

        assert (
            session.exec(select(Workspace).where(Workspace.id == "does-not-exist")).first() is None
        )
