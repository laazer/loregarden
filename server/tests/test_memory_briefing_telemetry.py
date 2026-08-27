"""Briefing telemetry: the classifier, the write, and the aggregate.

S5 and S7 of the ticket spec. The module under test is the sole writer, sole
classifier and sole aggregate reader of `memory_briefings`.

Two properties carry most of the ticket's value and are pinned hardest here:

* the classifier reads `store_states` and nothing else, so a store that was
  never there can never be reported as a store that read nothing; and
* the aggregate denominates over `agent_runs`, so a seam that quietly stopped
  writing shows up as a hole rather than as the last healthy numbers forever.
"""

import json
from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch

import pytest
from loregarden.agents.inherited_wisdom import InheritedWisdom
from loregarden.db.migration_ids import SHIPPED_MIGRATION_IDS
from loregarden.db.migrations import MIGRATIONS, apply_migrations
from loregarden.models.domain import (
    AgentRun,
    MemoryBriefing,
    MemoryBriefingAssembly,
    MemoryBriefingOutcome,
    MemoryStoreKind,
    MemoryStoreState,
    Ticket,
    WorkItemType,
    Workspace,
    utcnow,
)
from loregarden.services.memory_briefing_telemetry import (
    briefing_stats,
    classify,
    record_briefing,
)
from sqlalchemy import text
from sqlmodel import Session, create_engine, select

_MIGRATION_ID = "0097_memory_briefings_table"


def _result(**overrides) -> InheritedWisdom:
    """An InheritedWisdom built by difference from the canonical zero value, so
    these tests do not depend on the dataclass's field order."""
    return replace(InheritedWisdom.not_attempted(), **overrides)


def _all(state: MemoryStoreState) -> dict[MemoryStoreKind, MemoryStoreState]:
    return {
        MemoryStoreKind.CHECKPOINTS: state,
        MemoryStoreKind.VAULT: state,
        MemoryStoreKind.GRAPH: state,
    }


# ---------------------------------------------------------------------------
# S5 — classify. Precedence SKIPPED > STORE_ERROR > BUILT > NO_STORE > EMPTY.
# ---------------------------------------------------------------------------


def test_a_skipped_assembly_outranks_everything_else():
    result = _result(
        chars_injected=412,
        store_states={
            **_all(MemoryStoreState.READ),
            MemoryStoreKind.GRAPH: MemoryStoreState.ERRORED,
        },
    )

    assert classify(result, skipped=True) == MemoryBriefingOutcome.SKIPPED


def test_a_store_error_outranks_a_briefing_that_still_had_content():
    """AC2 — an error is the headline even when something was assembled anyway.
    A run whose graph is unopenable has a retrieval problem regardless of what
    the surviving half managed to return."""
    result = _result(
        chars_injected=412,
        store_states={
            MemoryStoreKind.CHECKPOINTS: MemoryStoreState.READ,
            MemoryStoreKind.VAULT: MemoryStoreState.READ,
            MemoryStoreKind.GRAPH: MemoryStoreState.ERRORED,
        },
    )

    assert classify(result, skipped=False) == MemoryBriefingOutcome.STORE_ERROR


def test_a_half_configured_service_that_produced_content_is_built():
    """Rule 3 fires before rule 4: content is content, and the unconfigured half
    is still recorded in store_states for whoever looks."""
    result = _result(
        chars_injected=412,
        store_states={
            MemoryStoreKind.CHECKPOINTS: MemoryStoreState.READ,
            MemoryStoreKind.VAULT: MemoryStoreState.READ,
            MemoryStoreKind.GRAPH: MemoryStoreState.UNCONFIGURED,
        },
    )

    assert classify(result, skipped=False) == MemoryBriefingOutcome.BUILT


def test_no_store_when_nothing_was_read():
    """AC2 / S8 case 4 — the outcome that must not collapse into EMPTY."""
    result = _result(store_states=_all(MemoryStoreState.UNCONFIGURED))

    assert classify(result, skipped=False) == MemoryBriefingOutcome.NO_STORE


def test_empty_when_a_store_really_read_and_had_nothing():
    result = _result(
        store_states={
            MemoryStoreKind.CHECKPOINTS: MemoryStoreState.READ,
            MemoryStoreKind.VAULT: MemoryStoreState.NOT_QUERIED,
            MemoryStoreKind.GRAPH: MemoryStoreState.NOT_QUERIED,
        }
    )

    assert classify(result, skipped=False) == MemoryBriefingOutcome.EMPTY


def test_not_queried_alone_does_not_count_as_read():
    """A store that was skipped because the query had no terms was not read.
    Counting it as read would let an all-stopword title on an unconfigured box
    report EMPTY — 'we looked and there was nothing' — when nothing was looked
    at and nothing was configured."""
    result = _result(
        store_states={
            MemoryStoreKind.CHECKPOINTS: MemoryStoreState.UNCONFIGURED,
            MemoryStoreKind.VAULT: MemoryStoreState.NOT_QUERIED,
            MemoryStoreKind.GRAPH: MemoryStoreState.NOT_QUERIED,
        }
    )

    assert classify(result, skipped=False) == MemoryBriefingOutcome.NO_STORE


def test_the_classifier_reads_store_states_and_not_the_row_counts():
    """AC2, stated as the property. Three records with identical counters — all
    zero — must classify three different ways, and the only thing that differs
    is what the stores were.

    Deriving error-from-empty out of `checkpoints_injected` / `learnings_injected`
    is the defect this ticket exists to remove, and it passes every other test in
    this file.
    """
    errored = _result(store_states=_all(MemoryStoreState.ERRORED))
    absent = _result(store_states=_all(MemoryStoreState.UNCONFIGURED))
    read = _result(store_states=_all(MemoryStoreState.READ))

    assert classify(errored, skipped=False) == MemoryBriefingOutcome.STORE_ERROR
    assert classify(absent, skipped=False) == MemoryBriefingOutcome.NO_STORE
    assert classify(read, skipped=False) == MemoryBriefingOutcome.EMPTY


def test_row_counts_cannot_rescue_a_service_with_no_store():
    """The mirror image: non-zero counters on stores that were never configured
    is an incoherent record, and the store states are the half that is true."""
    result = _result(
        checkpoints_injected=3,
        learnings_injected=3,
        store_states=_all(MemoryStoreState.UNCONFIGURED),
    )

    assert classify(result, skipped=False) == MemoryBriefingOutcome.NO_STORE


# ---------------------------------------------------------------------------
# S5 — record_briefing.
# ---------------------------------------------------------------------------


def _seed(session: Session, *, started_at=None, run_code: str = "run_1"):
    workspace = session.exec(select(Workspace).where(Workspace.slug == "lg")).first()
    if not workspace:
        workspace = Workspace(slug="lg", name="Loregarden")
        session.add(workspace)
        session.commit()
        session.refresh(workspace)
    ticket = Ticket(
        external_id="lg-briefing-1",
        workspace_id=workspace.id,
        title="Cap how fast a trusted MCP server can be called",
        work_item_type=WorkItemType.TASK,
    )
    session.add(ticket)
    session.commit()
    run = AgentRun(
        run_code=run_code,
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="test_designer",
        stage_key="test-design",
        started_at=started_at if started_at is not None else utcnow(),
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return workspace, ticket, run


def test_a_recorded_briefing_carries_every_figure_ac1_names(isolated_db):
    """AC1 — one queryable record per assembly, with the counts, the flags, the
    per-store labels and the elapsed time."""
    with Session(isolated_db) as session:
        workspace, ticket, run = _seed(session)
        result = _result(
            text="body",
            checkpoints_injected=2,
            learnings_injected=5,
            learnings_saturated=True,
            query_had_terms=True,
            chars_injected=3000,
            pre_truncation_chars=4200,
            truncated=True,
            store_states={
                MemoryStoreKind.CHECKPOINTS: MemoryStoreState.READ,
                MemoryStoreKind.VAULT: MemoryStoreState.READ,
                MemoryStoreKind.GRAPH: MemoryStoreState.UNCONFIGURED,
            },
            elapsed_ms=17,
        )

        row_id = record_briefing(
            session,
            run,
            ticket,
            result,
            skipped=False,
            assembly_source=MemoryBriefingAssembly.DISPATCH,
        )

    assert row_id
    with Session(isolated_db) as reader:
        row = reader.get(MemoryBriefing, row_id)
        assert row is not None
        assert row.run_id == run.id
        assert row.ticket_id == ticket.id
        assert row.workspace_id == workspace.id
        assert row.stage_key == "test-design"
        assert row.assembly_source == MemoryBriefingAssembly.DISPATCH
        assert row.outcome == MemoryBriefingOutcome.BUILT
        assert row.checkpoints_injected == 2
        assert row.learnings_injected == 5
        assert row.learnings_saturated is True
        assert row.checkpoints_saturated is False
        assert row.query_had_terms is True
        assert row.chars_injected == 3000
        assert row.pre_truncation_chars == 4200
        assert row.truncated is True
        assert row.elapsed_ms == 17
        assert json.loads(row.store_states_json) == {
            "checkpoints": "read",
            "vault": "read",
            "graph": "unconfigured",
        }
        assert row.store_errors == ""


def test_store_error_tokens_are_recorded_as_a_sorted_comma_joined_string(isolated_db):
    """AC2 — the operator-facing half of the record. Two failing stores must
    both survive into the row, and in a stable order."""
    with Session(isolated_db) as session:
        _workspace, ticket, run = _seed(session)
        result = _result(
            store_states={
                MemoryStoreKind.CHECKPOINTS: MemoryStoreState.ERRORED,
                MemoryStoreKind.VAULT: MemoryStoreState.ERRORED,
                MemoryStoreKind.GRAPH: MemoryStoreState.READ,
            },
            store_errors=("checkpoints:OSError", "vault:OSError"),
        )

        row_id = record_briefing(
            session,
            run,
            ticket,
            result,
            skipped=False,
            assembly_source=MemoryBriefingAssembly.DISPATCH,
        )

    with Session(isolated_db) as reader:
        row = reader.get(MemoryBriefing, row_id)
        assert row.store_errors == "checkpoints:OSError,vault:OSError"
        assert row.outcome == MemoryBriefingOutcome.STORE_ERROR


def test_a_skipped_row_records_no_store_states_at_all(isolated_db):
    """A verify assembly happened and deliberately carried no briefing. Its
    counters are placeholder zeros, and an empty store_states_json is what says
    so — three stores recorded as 'unconfigured' would be a measurement nobody
    took."""
    with Session(isolated_db) as session:
        _workspace, ticket, run = _seed(session)

        row_id = record_briefing(
            session,
            run,
            ticket,
            InheritedWisdom.not_attempted(),
            skipped=True,
            assembly_source=MemoryBriefingAssembly.DISPATCH,
        )

    with Session(isolated_db) as reader:
        row = reader.get(MemoryBriefing, row_id)
        assert row.outcome == MemoryBriefingOutcome.SKIPPED
        assert row.store_states_json == "{}"
        assert row.store_errors == ""
        assert row.chars_injected == 0
        assert row.elapsed_ms == 0


def test_the_returned_id_resolves_to_a_real_row(isolated_db):
    """AC5 — ticket 178 attaches its surfaced-learning rows by foreign-keying
    this id, so it has to be a row identifier and not a best-effort token."""
    with Session(isolated_db) as session:
        _workspace, ticket, run = _seed(session)
        row_id = record_briefing(
            session,
            run,
            ticket,
            _result(store_states=_all(MemoryStoreState.READ)),
            skipped=False,
            assembly_source=MemoryBriefingAssembly.RENDER,
        )

    with Session(isolated_db) as reader:
        assert reader.get(MemoryBriefing, row_id) is not None


def test_the_row_lands_in_the_database_the_caller_is_using(isolated_db):
    """The telemetry write must bind the caller's engine. A module-global engine
    binding that the test fixture does not redirect writes into the developer's
    real data/loregarden.db while the suite runs, and every other assertion here
    still passes."""
    with Session(isolated_db) as session:
        _workspace, ticket, run = _seed(session)
        record_briefing(
            session,
            run,
            ticket,
            _result(store_states=_all(MemoryStoreState.READ)),
            skipped=False,
            assembly_source=MemoryBriefingAssembly.DISPATCH,
        )

    with Session(isolated_db) as reader:
        assert len(reader.exec(select(MemoryBriefing)).all()) == 1


def test_a_failing_write_returns_nothing_and_leaves_the_caller_usable(isolated_db):
    """AC4 — the telemetry write must never cost the run.

    Three assertions, because the first two alone are a false green: a write
    that raised inside the caller's transaction leaves that session needing a
    rollback nobody asked for, and the damage only surfaces at the run
    lifecycle's next commit, far from here.
    """
    with Session(isolated_db) as session:
        _workspace, ticket, run = _seed(session)

        with patch(
            "loregarden.services.memory_briefing_telemetry.Session",
            side_effect=RuntimeError("telemetry backend down"),
        ):
            row_id = record_briefing(
                session,
                run,
                ticket,
                _result(store_states=_all(MemoryStoreState.READ)),
                skipped=False,
                assembly_source=MemoryBriefingAssembly.DISPATCH,
            )

        assert row_id == ""

        run.stage_key = "still-usable"
        session.add(run)
        session.commit()

    with Session(isolated_db) as reader:
        assert reader.exec(select(MemoryBriefing)).all() == []
        assert reader.get(AgentRun, run.id).stage_key == "still-usable"


def test_a_write_against_a_run_that_does_not_exist_is_swallowed(isolated_db):
    """Foreign keys are enforced on every engine in this repo, so a run that was
    never persisted raises inside the write. AC4 says that costs the row, not the
    run."""
    with Session(isolated_db) as session:
        _workspace, ticket, _run = _seed(session)
        phantom = AgentRun(
            run_code="run_phantom",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            agent_id="test_designer",
            stage_key="test-design",
        )

        row_id = record_briefing(
            session,
            phantom,
            ticket,
            _result(store_states=_all(MemoryStoreState.READ)),
            skipped=False,
            assembly_source=MemoryBriefingAssembly.DISPATCH,
        )

    assert row_id == ""


# ---------------------------------------------------------------------------
# S5 — briefing_stats. Denominated over agent_runs, on purpose.
# ---------------------------------------------------------------------------


def _record(session, ticket, run, outcome_states, *, skipped=False, chars=0):
    return record_briefing(
        session,
        run,
        ticket,
        _result(store_states=outcome_states, chars_injected=chars),
        skipped=skipped,
        assembly_source=MemoryBriefingAssembly.DISPATCH,
    )


def _extra_run(session, ticket, *, run_code, started_at):
    run = AgentRun(
        run_code=run_code,
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="test_designer",
        stage_key="test-design",
        started_at=started_at,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def test_the_buckets_sum_to_the_rows_they_summarise(isolated_db):
    """AC3 — the stated invariant. Buckets count rows, and two assemblies for one
    run is a live path, so they are not comparable to the run counts."""
    with Session(isolated_db) as session:
        _workspace, ticket, first = _seed(session)
        second = _extra_run(session, ticket, run_code="run_2", started_at=utcnow())
        third = _extra_run(session, ticket, run_code="run_3", started_at=utcnow())

        _record(session, ticket, first, _all(MemoryStoreState.READ), chars=100)
        _record(session, ticket, second, _all(MemoryStoreState.READ))
        _record(session, ticket, third, _all(MemoryStoreState.ERRORED))
        _record(session, ticket, third, InheritedWisdom.not_attempted().store_states, skipped=True)

        stats = briefing_stats(session, window_days=7)

    assert stats.rows_in_window == 4
    assert stats.built == 1
    assert stats.empty == 1
    assert stats.store_error == 1
    assert stats.skipped == 1
    assert stats.no_store == 0
    assert (
        stats.built + stats.empty + stats.store_error + stats.no_store + stats.skipped
        == stats.rows_in_window
    )


def test_a_skipped_assembly_is_neither_built_nor_empty(isolated_db):
    """A verify stage must not inflate the healthy buckets, and must not read as
    a hole either — it is its own thing."""
    with Session(isolated_db) as session:
        _workspace, ticket, run = _seed(session)
        _record(session, ticket, run, InheritedWisdom.not_attempted().store_states, skipped=True)

        stats = briefing_stats(session, window_days=7)

    assert stats.skipped == 1
    assert stats.built == 0
    assert stats.empty == 0
    assert stats.runs_with_no_briefing_row == 0


def test_a_run_that_wrote_no_row_is_counted_as_a_hole(isolated_db):
    """AC3 — the point of denominating over runs."""
    with Session(isolated_db) as session:
        _workspace, ticket, first = _seed(session)
        _extra_run(session, ticket, run_code="run_2", started_at=utcnow())
        _record(session, ticket, first, _all(MemoryStoreState.READ), chars=10)

        stats = briefing_stats(session, window_days=7)

    assert stats.runs_in_window == 2
    assert stats.runs_with_briefing_row == 1
    assert stats.runs_with_no_briefing_row == 1


def test_a_seam_that_stopped_writing_shows_as_holes_not_as_stale_health(isolated_db):
    """AC3 + AC4, together, and the reason the aggregate cannot count only rows.

    AC4 requires the write to fail silently. An aggregate denominated over its
    own rows therefore reports the last healthy numbers forever after the seam
    dies — this ticket's own defect, one level up. The signal that the seam
    stopped is that the newest run is newer than the newest row.
    """
    now = utcnow()
    with Session(isolated_db) as session:
        _workspace, ticket, old = _seed(session, started_at=now - timedelta(days=3))
        _record(session, ticket, old, _all(MemoryStoreState.READ), chars=100)
        _extra_run(session, ticket, run_code="run_new_1", started_at=now - timedelta(hours=2))
        _extra_run(session, ticket, run_code="run_new_2", started_at=now - timedelta(hours=1))

        stats = briefing_stats(session, window_days=7)

    assert stats.runs_in_window == 3
    assert stats.runs_with_no_briefing_row == 2
    assert stats.built == 1
    assert stats.last_row_at is not None
    assert stats.newest_run_at is not None
    assert stats.last_row_at < stats.newest_run_at


def test_two_assemblies_for_one_run_are_two_rows_and_one_run(isolated_db):
    with Session(isolated_db) as session:
        _workspace, ticket, run = _seed(session)
        _record(session, ticket, run, _all(MemoryStoreState.READ), chars=10)
        _record(session, ticket, run, _all(MemoryStoreState.READ), chars=10)

        stats = briefing_stats(session, window_days=7)

    assert stats.rows_in_window == 2
    assert stats.runs_with_briefing_row == 1
    assert stats.runs_in_window == 1


def test_runs_started_outside_the_window_are_excluded(isolated_db):
    with Session(isolated_db) as session:
        _workspace, ticket, old = _seed(session, started_at=utcnow() - timedelta(days=30))
        _record(session, ticket, old, _all(MemoryStoreState.READ), chars=10)

        stats = briefing_stats(session, window_days=7)

    assert stats.runs_in_window == 0
    assert stats.rows_in_window == 0
    assert stats.built == 0


def test_last_row_at_is_scoped_to_the_window(isolated_db):
    """A row belonging to an out-of-window run must not set `last_row_at`.
    A global maximum would report a timestamp for a window in which nothing was
    written, which is the comfortable silence the denominator exists to kill."""
    with Session(isolated_db) as session:
        _workspace, ticket, old = _seed(session, started_at=utcnow() - timedelta(days=30))
        _record(session, ticket, old, _all(MemoryStoreState.READ), chars=10)
        _extra_run(session, ticket, run_code="run_recent", started_at=utcnow())

        stats = briefing_stats(session, window_days=7)

    assert stats.runs_in_window == 1
    assert stats.runs_with_no_briefing_row == 1
    assert stats.last_row_at is None


def test_a_run_that_never_started_is_not_a_hole(isolated_db):
    """The denominator is runs that reached execution. A queued-then-cancelled
    run never assembled a prompt and must not be reported as missing telemetry."""
    with Session(isolated_db) as session:
        _workspace, ticket, run = _seed(session)
        _extra_run(session, ticket, run_code="run_queued", started_at=None)
        _record(session, ticket, run, _all(MemoryStoreState.READ), chars=10)

        stats = briefing_stats(session, window_days=7)

    assert stats.runs_in_window == 1
    assert stats.runs_with_no_briefing_row == 0


def test_an_empty_window_reports_zeros_and_no_timestamps(isolated_db):
    with Session(isolated_db) as session:
        stats = briefing_stats(session, window_days=7)

    assert stats.window_days == 7
    assert stats.runs_in_window == 0
    assert stats.runs_with_briefing_row == 0
    assert stats.runs_with_no_briefing_row == 0
    assert stats.rows_in_window == 0
    assert stats.newest_run_at is None
    assert stats.last_row_at is None
    assert stats.window_from < stats.window_to


# ---------------------------------------------------------------------------
# S7 — GET /api/memory/briefings.
# ---------------------------------------------------------------------------


def test_the_briefings_endpoint_reports_the_aggregate(client):
    """AC3 — the aggregate has to be reachable from outside the server log."""
    response = client.get("/api/memory/briefings")

    assert response.status_code == 200
    body = response.json()
    assert body["window_days"] == 7
    assert set(body) >= {
        "window_days",
        "window_from",
        "window_to",
        "runs_in_window",
        "runs_with_briefing_row",
        "runs_with_no_briefing_row",
        "rows_in_window",
        "newest_run_at",
        "last_row_at",
        "built",
        "empty",
        "store_error",
        "no_store",
        "skipped",
    }


def test_the_endpoint_honours_the_requested_window(client):
    assert client.get("/api/memory/briefings?window_days=30").json()["window_days"] == 30


@pytest.mark.parametrize("window_days", [0, 366])
def test_the_endpoint_rejects_a_window_outside_its_bounds(client, window_days):
    assert client.get(f"/api/memory/briefings?window_days={window_days}").status_code == 422


def test_an_empty_window_is_zeros_rather_than_a_404(client):
    body = client.get("/api/memory/briefings").json()

    assert body["rows_in_window"] == 0
    assert body["last_row_at"] is None
    assert body["built"] == 0


# ---------------------------------------------------------------------------
# S0 — the migration.
# ---------------------------------------------------------------------------


def test_the_briefings_migration_is_registered_in_both_ledgers():
    """`apply_migrations` keys on the id string, so a migration missing from the
    ledger stops being protected against a rename or a duplicate."""
    assert _MIGRATION_ID in [migration_id for migration_id, _ in MIGRATIONS]
    assert _MIGRATION_ID in SHIPPED_MIGRATION_IDS


def test_the_migration_creates_the_table_on_a_database_that_predates_it(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        conn.execute(
            text("CREATE TABLE tickets (id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '')")
        )
        assert (
            conn.execute(
                text(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='memory_briefings'"
                )
            ).fetchone()
            is None
        )

    apply_migrations(engine)

    with engine.connect() as conn:
        columns = {
            row[1] for row in conn.execute(text("PRAGMA table_info(memory_briefings)")).fetchall()
        }
    assert {
        "id",
        "run_id",
        "ticket_id",
        "workspace_id",
        "stage_key",
        "assembly_source",
        "outcome",
        "checkpoints_injected",
        "learnings_injected",
        "checkpoints_saturated",
        "learnings_saturated",
        "query_had_terms",
        "chars_injected",
        "pre_truncation_chars",
        "truncated",
        "store_states_json",
        "store_errors",
        "elapsed_ms",
        "created_at",
    } == columns
