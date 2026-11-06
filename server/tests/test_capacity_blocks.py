"""A block whose run never got capacity is re-filed from work to harness.

lg-milestone-that-1431 was blocked on 'test-design' as WORK when its run's host
lease never came. The live row carries the message the fixed-budget wait wrote,
so the detector must read that as well as today's prefix.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from loregarden.models.domain import (
    BlockKind,
    OrchestrationRun,
    OrchestrationRunStatus,
    TicketState,
)
from loregarden.models.domain.enums import utcnow
from loregarden.services.block_classification import classify_block_message
from loregarden.services.capacity_blocks import (
    refile_capacity_blocks,
    work_blocks_from_capacity_waits,
)
from loregarden.services.run_capacity import HostCapacityUnavailable
from loregarden.services.stage_docker_capacity import DockerCapacityUnavailable
from sqlmodel import Session
from tests.factories import make_ticket, make_workspace

#: The message lg-milestone-that-1431's orchestration ended with, as stored.
LEGACY_MESSAGE = (
    "Run 1533c2a4 (agent run test_designer · stage test-design) did not get 0.25 cpus / "
    "768 MB of host capacity: still queued — Host capacity is full. Waiting is not a failure."
)


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


def _blocked(session, workspace, message: str, kind: BlockKind = BlockKind.WORK):
    ticket = make_ticket(
        session, workspace_id=workspace.id, external_id=f"cap-{uuid4().hex[:6]}",
        state=TicketState.BLOCKED,
    )  # fmt: skip
    ticket.block_kind = kind
    ticket.workflow_stage_key = "test-design"
    session.add(ticket)
    session.add(
        OrchestrationRun(
            run_code=f"orch_{uuid4().hex[:6]}",
            ticket_id=ticket.id,
            workspace_id=workspace.id,
            status=OrchestrationRunStatus.BLOCKED,
            current_stage_key="test-design",
            error_message=message,
            finished_at=utcnow(),
        )
    )
    session.commit()
    return ticket


def test_a_capacity_block_filed_as_work_is_found_and_refiled_as_harness(session) -> None:
    workspace = make_workspace(session, slug="capacity-blocks")
    legacy = _blocked(session, workspace, LEGACY_MESSAGE)
    current = _blocked(session, workspace, str(HostCapacityUnavailable("the line stopped")))
    work = _blocked(session, workspace, "AssertionError: the tests the agent wrote fail")
    harness = _blocked(session, workspace, LEGACY_MESSAGE, kind=BlockKind.HARNESS)

    found = {block.ticket.id for block in work_blocks_from_capacity_waits(session)}
    assert found == {legacy.id, current.id}

    assert refile_capacity_blocks(session, sorted(found)) == 2
    for ticket in (legacy, current, work, harness):
        session.refresh(ticket)
    assert (legacy.block_kind, current.block_kind) == (BlockKind.HARNESS, BlockKind.HARNESS)
    assert work.block_kind is BlockKind.WORK, "a block about the work stays the work's"
    assert work_blocks_from_capacity_waits(session) == []


def test_a_capacity_failure_reads_as_harness_without_the_declared_kind() -> None:
    """The run boundary declares HARNESS; the message alone must also say it, for
    any path that blocks with the text and no declared kind."""
    for exc in (HostCapacityUnavailable("still queued"), DockerCapacityUnavailable("stalled")):
        assert classify_block_message(f"Stage 'implement' failed: {exc}") is BlockKind.HARNESS
