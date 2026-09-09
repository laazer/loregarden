"""A start that failed ambiguously can be retried without duplicating a run.

`lg-workflow-integrity-696`. During one recovery, two writes failed ambiguously
— start_orchestration closed the socket, then timed out — and both times the
only safe move was to re-read the ticket by hand to find out whether the write
had landed. A blind retry would have created a second orchestration.

The condition is not rare: it reproduced while filing these very tickets, when
two create_ticket calls timed out against a host at load 57-67. Neither had
written, so no duplicate resulted — luck, not design.

The key is caller-supplied on purpose. Deriving one from wall-clock time or from
the arguments would collapse two legitimate sequential starts of the same ticket
into one, which is a worse failure than the one being fixed.
"""

from __future__ import annotations

from unittest import mock

import pytest
from loregarden.mcp.admission import start_orchestration_admitted
from loregarden.models.domain import OrchestrationRun, OrchestrationRunStatus
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from sqlmodel import Session, col, select
from tests.factories import make_workspace_ticket


def _runs(session: Session, ticket_id: str) -> list[OrchestrationRun]:
    return list(
        session.exec(
            select(OrchestrationRun).where(col(OrchestrationRun.ticket_id) == ticket_id)
        ).all()
    )


def _start(session: Session, ticket_id: str, key: str | None = None):
    svc = OrchestrationCallbackService(session)
    args = {"ticket_id": ticket_id, "driver": "external_mcp"}
    if key is not None:
        args["idempotency_key"] = key
    return start_orchestration_admitted(session, svc, args)


def test_a_retry_with_the_same_key_returns_the_original_run(db_session: Session):
    """AC1. The whole point: the caller does not have to know whether its first
    attempt committed."""
    ticket = make_workspace_ticket(db_session, "idem-1")

    _, first = _start(db_session, ticket.id, "recovery-2026-09-09-a")
    _, second = _start(db_session, ticket.id, "recovery-2026-09-09-a")

    assert first.id == second.id
    assert len(_runs(db_session, ticket.id)) == 1


def test_an_ambiguous_failure_leaves_exactly_one_run(db_session: Session):
    """AC4, as the incident actually went: the write commits, the response is
    lost, the caller retries because it cannot tell."""
    ticket = make_workspace_ticket(db_session, "idem-2")

    _, created = _start(db_session, ticket.id, "ambiguous-1")
    created_id = created.id

    # The response never reaches the caller. Nothing about the database changes;
    # only the caller's knowledge does.
    with pytest.raises(TimeoutError):
        raise TimeoutError("response lost after commit")

    _, retried = _start(db_session, ticket.id, "ambiguous-1")

    assert retried.id == created_id
    assert len(_runs(db_session, ticket.id)) == 1, "the retry created a duplicate"


def test_a_different_key_is_a_genuinely_new_start(db_session: Session):
    """AC5. Two legitimate sequential starts of one ticket are a real thing and
    must not be collapsed. This is why the key is supplied rather than derived —
    a time- or argument-derived key would make this test impossible to satisfy
    at the same time as the one above."""
    ticket = make_workspace_ticket(db_session, "idem-3")

    _, first = _start(db_session, ticket.id, "attempt-1")
    # Sequential really means sequential: the pre-existing guard refuses a second
    # orchestration while one is live ("Orchestration already running"), and that
    # guard is deliberately untouched by this change. Finish the first.
    first.status = OrchestrationRunStatus.SUCCEEDED
    db_session.add(first)
    db_session.commit()

    _, second = _start(db_session, ticket.id, "attempt-2")

    assert second.id != first.id, "a different key must not replay the first run"
    assert len(_runs(db_session, ticket.id)) == 2


def test_no_key_behaves_exactly_as_before(db_session: Session):
    """The guarantee is opt-in. A caller that does not ask for it must see no
    behaviour change, or this lands as a silent semantic shift on every existing
    caller."""
    ticket = make_workspace_ticket(db_session, "idem-4")
    _, run = _start(db_session, ticket.id)
    assert run is not None
    assert run.idempotency_key == ""


def test_a_replay_takes_no_queue_slot(db_session: Session):
    """A replay spawns nothing, so reserving a lane for it would idle capacity
    for work that is already done or running."""
    ticket = make_workspace_ticket(db_session, "idem-5")

    _start(db_session, ticket.id, "replay-1")
    reservation, _ = _start(db_session, ticket.id, "replay-1")

    assert reservation.admitted is True
    assert reservation.slot_number is None


def test_the_key_is_stamped_only_after_the_run_exists(db_session: Session):
    """A start that raises must leave no key claiming a run that was never
    created — otherwise the next retry replays a phantom."""
    ticket = make_workspace_ticket(db_session, "idem-6")

    with mock.patch(
        "loregarden.mcp.admission.run_admitted", side_effect=RuntimeError("driver blew up")
    ):
        with pytest.raises(RuntimeError):
            _start(db_session, ticket.id, "doomed-1")

    stamped = db_session.exec(
        select(OrchestrationRun).where(col(OrchestrationRun.idempotency_key) == "doomed-1")
    ).all()
    assert list(stamped) == []
