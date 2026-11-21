"""Where "run entry" actually is, for the symbol map's refresh.

Ticket lg-code-knowledge-284, requirement R7 / acceptance criterion 1. Its own
module, because every test here drives a REAL dispatch through
`CliAgentExecutor.execute` and so costs a worktree cut and a seeded database;
the fast `tmp_path`-only tests live in `test_symbol_map_refresh.py`.

**The deviation these tests exist to pin.** The criterion says "at
orchestration run entry". `OrchestrationCallbacks.start_orchestration_run`
(`services/orchestration_callbacks.py:309`) runs BEFORE the worktree is cut
(`services/parallel_run_service.py:161`), and partitions are keyed on
`realpath(repo_root)`. A refresh there refreshes the PRIMARY checkout's
partition while every stage of the run reads a worktree that has no row:
`NO_PARTITION` on every read, forever, with all twelve acceptance criteria
passing and the map never read by anybody.

That is the failure mode the first test below falsifies, and it is why the
assertion is not "a partition exists" but "the partition's `checkout_root` is
the worktree, and the primary checkout has none".
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path
from unittest import mock

import pytest
from loregarden.agents.executors import cli as cli_module
from loregarden.agents.executors import stage_turn
from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.models.domain import (
    AgentRun,
    Freshness,
    RunStatus,
    SymbolMapPartition,
    Ticket,
    Workspace,
)
from loregarden.models.domain.enums import utcnow
from loregarden.services.symbol_map import definitions_for
from loregarden.services.symbol_map import refresh as refresh_module
from loregarden.services.symbol_map import sources as sources_module
from loregarden.services.symbol_map import store as store_module
from loregarden.services.symbol_map.checkout_identity import identify
from loregarden.services.ticket_worktree import resolve_ticket_root
from loregarden.testing.factories import make_workspace_ticket
from sqlmodel import Session, select
from tests.worktree_helpers import git

CLI_SOURCE = Path(cli_module.__file__)


def _make_run(session: Session, ticket: Ticket, code: str) -> AgentRun:
    run = AgentRun(
        run_code=code,
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="backend_implementer",
        stage_key="implement",
        status=RunStatus.RUNNING,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _silent_agent(*, repo_root: Path, **_kwargs):  # noqa: ARG001 - dispatch signature
    """An agent that does nothing, so the dispatch is the only thing measured."""
    return "", "", RunStatus.SUCCEEDED


@pytest.fixture(name="dispatch_case")
def dispatch_case_fixture(db_session: Session) -> tuple[Ticket, Workspace, Path]:
    """A ticket in the seeded workspace, whose repo is a throwaway git tree.

    `tests/conftest.py` repoints the seeded "loregarden" workspace at a
    scratch repository, so `resolve_execution_root` cuts a real worktree there
    rather than switching branches in this checkout.
    """
    ticket = make_workspace_ticket(db_session, "symbol-map-run-entry")
    workspace = db_session.get(Workspace, ticket.workspace_id)
    return ticket, workspace, Path(workspace.repo_path).resolve()


def _partitions(session: Session, workspace_id: str) -> dict[str, SymbolMapPartition]:
    rows = session.exec(
        select(SymbolMapPartition).where(SymbolMapPartition.workspace_id == workspace_id)
    ).all()
    return {row.checkout_identity: row for row in rows}


def test_a_dispatch_that_got_a_worktree_maps_that_worktree_not_the_primary(
    db_session: Session, dispatch_case
):
    """AC1 / R7.1 — the highest-risk criterion in the ticket, and the one the
    literal reading of its own wording would silently fail.

    Write this test first. A refresh at `start_orchestration_run` passes every
    other criterion in this suite and ships a map no stage of the run reads.
    """
    ticket, workspace, primary = dispatch_case
    run = _make_run(db_session, ticket, "run-entry-worktree")

    with mock.patch.object(stage_turn, "run_print_mode", side_effect=_silent_agent):
        CliAgentExecutor(db_session).execute(run, ticket)

    worktree = resolve_ticket_root(db_session, ticket, workspace).resolve()
    assert worktree != primary, "the fixture must actually cut a worktree"
    partitions = _partitions(db_session, workspace.id)
    assert identify(worktree) in partitions
    assert Path(partitions[identify(worktree)].checkout_root) == worktree
    assert identify(primary) not in partitions, (
        "the primary checkout's partition is the one no stage of this run reads"
    )


def test_the_dispatched_run_can_read_its_own_tree_back_as_fresh(db_session: Session, dispatch_case):
    """AC1's consequence, which is the only reason the criterion exists: after
    a dispatch, a read against the tree the stage is standing in answers
    MATCHES with that tree's line numbers."""
    ticket, workspace, _primary = dispatch_case
    run = _make_run(db_session, ticket, "run-entry-readback")

    with mock.patch.object(stage_turn, "run_print_mode", side_effect=_silent_agent):
        CliAgentExecutor(db_session).execute(run, ticket)

    worktree = resolve_ticket_root(db_session, ticket, workspace)
    read = definitions_for(db_session, workspace.id, worktree, "nothing_is_named_this")
    assert read.status.freshness is Freshness.MATCHES
    assert list(read.rows) == []


def test_a_parked_run_pays_for_no_refresh(db_session: Session, dispatch_case):
    """AC1 / R7.2. The call goes after the `if parked is not None: return
    parked` guard, so a stage that is not going to run does not pay a build.
    """
    ticket, _workspace, _primary = dispatch_case
    run = _make_run(db_session, ticket, "run-entry-parked")
    executor = CliAgentExecutor(db_session)

    with (
        mock.patch.object(CliAgentExecutor, "_record_and_check_boundary", return_value=run),
        mock.patch.object(
            cli_module, "refresh_at_run_entry", wraps=refresh_module.refresh_at_run_entry
        ) as refresh,
        mock.patch.object(stage_turn, "run_print_mode", side_effect=_silent_agent),
    ):
        executor.execute(run, ticket)

    assert refresh.call_args_list == []


def test_a_refresh_failure_does_not_fail_or_delay_the_dispatch(
    db_session: Session, dispatch_case, caplog
):
    """AC9 / R7.4. A refresh must never fail a run: a stale or missing map
    degrades consumers to live grep, which is the designed fallback and
    strictly better than blocking a stage.

    The first dispatch builds the map; a commit then moves the tree past it;
    the second dispatch refreshes with the delta oracle raising. The run must
    still complete, the warning must be logged once, and the subsequent read
    must say SHA_MISMATCH — which is the grep signal, not a silent MATCHES.
    """
    ticket, workspace, _primary = dispatch_case
    with mock.patch.object(stage_turn, "run_print_mode", side_effect=_silent_agent):
        CliAgentExecutor(db_session).execute(
            _make_run(db_session, ticket, "run-entry-first"), ticket
        )
    worktree = resolve_ticket_root(db_session, ticket, workspace)
    (worktree / "added_after_the_map.py").write_text("def added():\n    return 1\n")
    git(worktree, "add", "-A")
    git(worktree, "commit", "-q", "-m", "feat: move past the map")
    stored = _partitions(db_session, workspace.id)[identify(worktree)].head_sha
    second = _make_run(db_session, ticket, "run-entry-degraded")

    with (
        caplog.at_level(logging.DEBUG, logger=refresh_module.__name__),
        mock.patch.object(
            sources_module, "changed_paths", side_effect=OSError("git is unavailable")
        ),
        mock.patch.object(stage_turn, "run_print_mode", side_effect=_silent_agent),
    ):
        completed = CliAgentExecutor(db_session).execute(second, ticket)

    assert completed.status is RunStatus.SUCCEEDED
    records = [r for r in caplog.records if r.name == refresh_module.__name__]
    assert [r.levelno for r in records] == [logging.WARNING]
    db_session.expire_all()
    assert _partitions(db_session, workspace.id)[identify(worktree)].head_sha == stored
    read = definitions_for(db_session, workspace.id, worktree, "added")
    assert read.status.freshness is Freshness.SHA_MISMATCH


def test_a_cold_build_another_run_won_does_not_fail_the_dispatch(
    db_session: Session, dispatch_case
):
    """Two members of a parallel stage share one checkout, and both find no map.

    The second insert of that checkout's partition breaks the unique key. The
    refresh degrades, but the failed flush used to leave the dispatch's own
    Session pending a rollback, so its next write raised and the run failed:
    test_parallel_stage_runs_all_agents blocked on CI three times this way.
    The other run's row is committed from its own Session at the moment this
    build has looked and found nothing, which is the race itself.
    """
    ticket, workspace, _primary = dispatch_case
    real_partition_for = store_module.partition_for

    def _the_other_run_wins(session: Session, workspace_id: str, repo_root: Path):
        found = real_partition_for(session, workspace_id, repo_root)
        with Session(db_session.get_bind()) as other:
            other.add(
                SymbolMapPartition(
                    workspace_id=workspace_id,
                    checkout_identity=identify(repo_root),
                    checkout_root=str(repo_root),
                    head_sha="0" * 40,
                    built_at=utcnow(),
                )
            )
            other.commit()
        return found

    with (
        mock.patch.object(store_module, "partition_for", side_effect=_the_other_run_wins),
        mock.patch.object(stage_turn, "run_print_mode", side_effect=_silent_agent),
    ):
        completed = CliAgentExecutor(db_session).execute(
            _make_run(db_session, ticket, "run-entry-lost-race"), ticket
        )

    assert completed.status is RunStatus.SUCCEEDED, completed.stderr


def test_the_call_site_wraps_the_refresh_in_no_try_except():
    """AC9's structural half. `refresh_at_run_entry` never raises, so a
    `try`/`except` around it at the call site is dead code that reads as a
    guarantee — and the next reader relaxes the function's contract because
    the caller "already handles it".
    """
    tree = ast.parse(CLI_SOURCE.read_text(encoding="utf-8"))
    guarded: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):  # py-org: allow-isinstance - ast node dispatch
            continue
        for inner in ast.walk(node):
            called = getattr(inner, "func", None)
            if getattr(called, "id", "") == "refresh_at_run_entry" or (
                getattr(called, "attr", "") == "refresh_at_run_entry"
            ):
                guarded.append(inner.lineno)

    assert guarded == [], f"cli.py wraps the refresh at line(s) {guarded}"


def test_the_refresh_is_called_after_the_parked_guard_and_before_the_prompt():
    """AC1, read off the source rather than off a mock.

    The ordering is the criterion: before the guard it pays for parked stages;
    after `_build_prompt` the prompt is assembled from a map the refresh was
    about to update, which is the staleness this ticket exists to remove.
    """
    tree = ast.parse(CLI_SOURCE.read_text(encoding="utf-8"))
    lines: dict[str, int] = {}
    for node in ast.walk(tree):
        called = getattr(node, "func", None)
        name = getattr(called, "id", "") or getattr(called, "attr", "")
        if name in {"refresh_at_run_entry", "_record_and_check_boundary", "_build_prompt"}:
            lines.setdefault(name, node.lineno)

    assert set(lines) == {"refresh_at_run_entry", "_record_and_check_boundary", "_build_prompt"}
    assert lines["_record_and_check_boundary"] < lines["refresh_at_run_entry"]
    assert lines["refresh_at_run_entry"] < lines["_build_prompt"]


def test_the_report_reaches_the_warning_surface(db_session: Session, dispatch_case):
    """284 produces the `RefreshReport` and carries it to `warn_before_run`;
    `lg-code-knowledge-2731` owns the wording, the states and the budget.
    Without this hand-off 2731 has nothing to print, and the operator watches
    a cold build with nothing on screen."""
    ticket, _workspace, _primary = dispatch_case
    run = _make_run(db_session, ticket, "run-entry-warning")
    seen: list[object] = []

    def capturing(**kwargs):
        seen.extend(kwargs.values())

    with (
        mock.patch.object(cli_module, "warn_before_run", side_effect=capturing),
        mock.patch.object(stage_turn, "run_print_mode", side_effect=_silent_agent),
    ):
        CliAgentExecutor(db_session).execute(run, ticket)

    assert any(type(value).__name__ == "RefreshReport" for value in seen), (
        "warn_before_run never received the refresh report"
    )


def test_a_second_dispatch_on_an_unchanged_worktree_re_parses_nothing(
    db_session: Session, dispatch_case
):
    """AC1's actual payoff, measured where it is paid rather than in a unit
    test over a `tmp_path` repository.

    Worktrees are cut per ticket and then dispatched into repeatedly — six run
    starts in one minute at the measured peak — and most of those dispatches
    stand on a tree the previous one already mapped. If the warm path at THIS
    call site is not warm, the ticket's 7.9 s cold build is paid per dispatch
    and every other criterion still passes.
    """
    ticket, _workspace, _primary = dispatch_case
    with mock.patch.object(stage_turn, "run_print_mode", side_effect=_silent_agent):
        CliAgentExecutor(db_session).execute(
            _make_run(db_session, ticket, "run-entry-warm-first"), ticket
        )
    parsed: list[str] = []
    real = store_module.extraction_for

    def recording(repo_root, path):
        parsed.append(path)
        return real(repo_root, path)

    with (
        mock.patch.object(store_module, "extraction_for", side_effect=recording),
        mock.patch.object(stage_turn, "run_print_mode", side_effect=_silent_agent),
    ):
        completed = CliAgentExecutor(db_session).execute(
            _make_run(db_session, ticket, "run-entry-warm-second"), ticket
        )

    assert completed.status is RunStatus.SUCCEEDED
    assert parsed == [], f"a warm dispatch re-parsed {len(parsed)} files"
