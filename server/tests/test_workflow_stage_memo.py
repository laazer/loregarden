"""`stage_resolution_memo` holds the rows a stage resolution is keyed on.

The session memo in `workflow_service` holds what a template resolves to. This
block holds the two rows the resolution reads to get there — the ticket's
`WorkflowInstance` and its `Workspace` — which a session memo must not, because
an instance is created and advanced mid-request and a writer would be served its
own stale "no instance".

So these tests pin both halves: inside the block the rows are read once, and the
block does not outlive itself.
"""

from __future__ import annotations

from loregarden.models.domain import Ticket, WorkflowInstance, Workspace
from loregarden.services.workflow_service import (
    prime_workflow_instances,
    resolve_ticket_stages,
    stage_resolution_memo,
    workflow_instance_for,
)
from sqlalchemy import event
from sqlmodel import Session, select


class _StatementCounter:
    """Counts statements matching a substring, for the duration of a `with` block."""

    def __init__(self, engine, needle: str) -> None:
        self.engine = engine
        self.needle = needle
        self.count = 0

    def __enter__(self) -> _StatementCounter:
        event.listen(self.engine, "before_cursor_execute", self._on_execute)
        return self

    def __exit__(self, *_exc) -> None:
        event.remove(self.engine, "before_cursor_execute", self._on_execute)

    def _on_execute(self, _conn, _cursor, statement, *_args, **_kwargs) -> None:
        if self.needle in statement.lower():
            self.count += 1


def _tickets_with_instances(session: Session, count: int) -> list[Ticket]:
    instanced = {instance.ticket_id for instance in session.exec(select(WorkflowInstance)).all()}
    tickets = [t for t in session.exec(select(Ticket)).all() if t.id in instanced]
    assert len(tickets) >= count, "seed data no longer has enough workflow instances"
    return tickets[:count]


def test_the_memo_reads_each_ticket_instance_once(client, isolated_db):
    with Session(isolated_db) as session:
        tickets = _tickets_with_instances(session, 2)

        with (
            _StatementCounter(isolated_db, "workflow_instances") as counter,
            stage_resolution_memo(),
        ):
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

        with (
            _StatementCounter(isolated_db, "workflow_instances") as counter,
            stage_resolution_memo(),
        ):
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


def test_an_instance_written_after_the_block_is_visible(client, isolated_db):
    """Why this is a block and not a session memo: a ticket with no instance is
    remembered as having none, and something creates one a moment later."""
    with Session(isolated_db) as session:
        ticket = _tickets_with_instances(session, 1)[0]
        instance = session.exec(
            select(WorkflowInstance).where(WorkflowInstance.ticket_id == ticket.id)
        ).one()
        stages_json = instance.stages_json
        template_id = instance.template_id
        template_version = instance.template_version
        session.delete(instance)
        session.commit()

        with stage_resolution_memo():
            assert workflow_instance_for(session, ticket.id) is None

        session.add(
            WorkflowInstance(
                ticket_id=ticket.id,
                template_id=template_id,
                template_version=template_version,
                current_stage_key=ticket.workflow_stage_key,
                stages_json=stages_json,
            )
        )
        session.commit()

        with stage_resolution_memo():
            assert workflow_instance_for(session, ticket.id) is not None


def test_a_workspace_row_is_read_once_per_block(client, isolated_db):
    """SQLAlchemy's identity map is weak, so a serializer that reads `ws.slug`
    and drops the row re-queries it per ticket."""
    with Session(isolated_db) as session:
        tickets = _tickets_with_instances(session, 3)
        assert len({t.workspace_id for t in tickets}) == 1

        with _StatementCounter(isolated_db, "from workspaces") as counter, stage_resolution_memo():
            for ticket in tickets:
                resolve_ticket_stages(session, ticket)

        assert counter.count <= 1, f"{counter.count} workspace reads for one workspace"


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
