"""The lifespan's boot modes: the full production boot, and the test suite's prepared database."""

import asyncio
from contextlib import ExitStack
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from loregarden import main as main_module
from loregarden.main import BootMode, app, lifespan
from loregarden.models.domain import Ticket, TicketState
from sqlmodel import Session, select

#: Every step `_recover_previous_boot` takes, in the order it must take them.
RECOVERY_STEPS = (
    "reattach_surviving_runs",
    "fail_interrupted_runs",
    "fail_interrupted_orchestration_runs",
    "fail_interrupted_triage_turns",
    "fail_interrupted_branch_triage_turns",
    "fail_interrupted_baxter_chat_turns",
    "fail_interrupted_studio_turns",
    "fail_interrupted_planner_turns",
    "fail_interrupted_asides",
    "resume_chat_waits",
    "clear_orphaned_chat_turn_thinking",
    "settle_stranded_stages",
    "reconcile_worktrees",
    "enable_ref_logs_in_workspaces",
    "sweep_all_chat_branches",
    "reconcile_once",
    "resume_interrupted_orchestrations",
)
#: What the full boot does around recovery, in order.
BOOT_STEPS = (
    "init_db",
    "drop_retired_tables",
    "seed_database",
    *RECOVERY_STEPS,
    "remember_running_build",
)


def _record_boot_steps(stack: ExitStack) -> list[str]:
    order: list[str] = []
    for name in BOOT_STEPS:
        stack.enter_context(
            patch.object(main_module, name, side_effect=lambda *_a, _n=name, **_k: order.append(_n))
        )
    return order


def test_full_boot_runs_every_recovery_step_in_order(isolated_db) -> None:
    with ExitStack() as stack:
        order = _record_boot_steps(stack)
        asyncio.run(_run(lifespan(app)))
    assert order == list(BOOT_STEPS)


def test_prepared_test_database_skips_migration_seed_recovery_and_build_discovery(
    isolated_db,
) -> None:
    probe = FastAPI()
    probe.state.boot_mode = BootMode.PREPARED_TEST_DATABASE
    with ExitStack() as stack:
        order = _record_boot_steps(stack)
        asyncio.run(_run(lifespan(probe)))
    assert order == []


def test_prepared_test_database_is_refused_outside_pytest(isolated_db, monkeypatch) -> None:
    """No server can start in the mode that leaves dead runs RUNNING."""
    monkeypatch.delenv(main_module._PYTEST_PHASE_ENV)
    probe = FastAPI()
    probe.state.boot_mode = BootMode.PREPARED_TEST_DATABASE
    with ExitStack() as stack:
        order = _record_boot_steps(stack)
        with pytest.raises(RuntimeError, match="test suite only"):
            asyncio.run(_run(lifespan(probe)))
    assert order == []


def test_app_boots_full_unless_a_fixture_says_otherwise(isolated_db) -> None:
    """Fixtures hand the module-global app back on the production default."""
    assert app.state.boot_mode is BootMode.FULL


def test_client_fixture_boots_prepared(client: TestClient) -> None:
    assert app.state.boot_mode is BootMode.PREPARED_TEST_DATABASE


def test_booted_client_fixture_boots_full(booted_client: TestClient) -> None:
    assert app.state.boot_mode is BootMode.FULL
    assert booted_client.get("/health").status_code == 200


def _tickets(engine) -> dict[str, tuple[TicketState, str]]:
    with Session(engine) as session:
        return {
            t.external_id: (t.state, t.workflow_stage_key) for t in session.exec(select(Ticket))
        }


def test_recovery_changes_no_ticket_on_the_prepared_database(client, isolated_db) -> None:
    """`client` skips recovery, so its template must already hold recovery's result.

    The seed alone writes the bootstrap milestone and features DONE over leaf
    tasks still IN_PROGRESS, and a full boot's parent reconcile reopens them. A
    template built without that pass handed every `client` test a state no
    server serves, and a test that took the first ticket got a closed one.
    """
    before = _tickets(isolated_db)
    assert before, "the seeded database has tickets"
    with Session(isolated_db) as session:
        main_module._recover_previous_boot(session)
        session.commit()
    assert _tickets(isolated_db) == before


async def _run(context) -> None:
    async with context:
        pass
