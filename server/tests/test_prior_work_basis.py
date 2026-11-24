"""`search_prior_work` ships the basis for its own emptiness (AC2a, AC7a, AC8).

Three different facts return the same bare `[]` today: the query tokenised to
nothing, the workspace slug did not resolve, and nothing matched. A caller
cannot tell them apart, so every one of them reads as "there is no prior work",
and the most expensive of the three — a typo in a slug — reads as the cheapest.

The return type changes from `list` to `dict`; its one caller,
`mcp/tool_registry.py:_search_prior_work`, is migrated in the same change.
Posture is internal, so there is no shim and no dual path, and
`test_the_mcp_caller_is_migrated_with_no_shim` is what says so.

`corpus_total`, `scanned` and `not_examined` stay three fields. The scan is
bounded at 400 with no `order_by`; one number here would tell every downstream
stage that an unexamined corpus was examined.
"""

from __future__ import annotations

import json

import pytest
from loregarden.models.domain import Ticket, TicketState, WorkItemType, Workspace
from loregarden.models.domain.enums import MemoryStoreState, RetrievalEmptiness
from loregarden.services.prior_work import _MAX_SCANNED, search_prior_work
from loregarden.testing.prod_shape import build_prod_shape
from sqlmodel import Session, SQLModel, create_engine, select


def _session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'pw.db'}")
    SQLModel.metadata.create_all(engine)
    session = Session(engine)
    session.add(Workspace(id="ws", slug="ws", name="WS", repo_path="."))
    session.commit()
    return session


def _ticket(session, external_id, title, *, state=TicketState.DONE, description=""):
    ticket = Ticket(
        external_id=external_id,
        workspace_id="ws",
        title=title,
        description=description,
        state=state,
        work_item_type=WorkItemType.TASK,
    )
    session.add(ticket)
    session.commit()
    return ticket


# --- AC2(a) the three emptinesses become distinguishable --------------------


def test_a_query_that_tokenises_to_nothing_says_so(tmp_path):
    """`prior_work.py:96`. The corpus was never consulted; nothing about it is
    evidence of anything, and the basis must not imply a scan happened."""
    session = _session(tmp_path)
    _ticket(session, "30-smart-import", "Smart ticket import from markdown")

    answer = search_prior_work(session, "a an of", workspace_slug="ws")
    assert answer["results"] == []
    assert answer["basis"].emptiness is RetrievalEmptiness.NO_TERMS
    assert answer["basis"].scanned == 0


def test_an_unresolved_slug_says_so_rather_than_reading_as_no_prior_work(tmp_path):
    """`prior_work.py:104`. The most expensive of the three emptinesses.

    A typo in a slug is silently identical to a clean "nothing like this has
    been tried", and the caller acts on the second.
    """
    session = _session(tmp_path)
    _ticket(session, "30-smart-import", "Smart ticket import from markdown")

    answer = search_prior_work(session, "smart import markdown", workspace_slug="no-such-ws")
    assert answer["results"] == []
    assert answer["basis"].emptiness is RetrievalEmptiness.UNRESOLVED_SCOPE
    assert answer["basis"].corpus_total == 0
    assert answer["basis"].scanned == 0


def test_a_scan_that_matched_nothing_says_so_and_reports_what_it_read(tmp_path):
    session = _session(tmp_path)
    _ticket(session, "30-smart-import", "Smart ticket import from markdown")

    answer = search_prior_work(session, "quantum blockchain renderer", workspace_slug="ws")
    assert answer["results"] == []
    assert answer["basis"].emptiness is RetrievalEmptiness.NO_OVERLAP
    assert answer["basis"].corpus_total == 1
    assert answer["basis"].scanned == 1
    assert answer["basis"].not_examined == 0


def test_the_three_emptinesses_are_three_different_answers(tmp_path):
    """The whole ticket in one assertion: one value cannot carry three facts."""
    session = _session(tmp_path)
    _ticket(session, "30-smart-import", "Smart ticket import from markdown")
    seen = {
        search_prior_work(session, "a an of", workspace_slug="ws")["basis"].emptiness,
        search_prior_work(session, "smart import", workspace_slug="ghost")["basis"].emptiness,
        search_prior_work(session, "quantum renderer", workspace_slug="ws")["basis"].emptiness,
    }
    assert len(seen) == 3


def test_a_hit_still_carries_a_basis(tmp_path):
    """A basis is emitted on every reply, not only on an empty one."""
    session = _session(tmp_path)
    _ticket(session, "30-smart-import", "Smart ticket import from markdown")

    answer = search_prior_work(session, "smart import of markdown tickets", workspace_slug="ws")
    assert [hit["external_id"] for hit in answer["results"]] == ["30-smart-import"]
    assert answer["basis"].emptiness is RetrievalEmptiness.MATCHED
    assert answer["basis"].corpus_total == 1


def test_the_leg_states_come_from_the_shared_vocabulary(tmp_path):
    """Both basis producers read `MemoryStoreState`; a third vocabulary is the
    drift this pins. `prior_work` queries no memory store, so its legs are
    NOT_QUERIED rather than absent or READ."""
    session = _session(tmp_path)
    basis = search_prior_work(session, "anything at all", workspace_slug="ws")["basis"]
    assert all(isinstance(state, MemoryStoreState) for state in basis.legs.values())
    assert set(basis.legs.values()) <= {MemoryStoreState.NOT_QUERIED}


# --- AC2(a) the counts do not claim the scope was examined ------------------


def test_a_corpus_larger_than_the_scan_limit_reports_what_it_did_not_read(tmp_path):
    """The criterion's own trap: `scanned` is 400 and `corpus_total` is not.

    A single `rows_in_scope` field holding 400 would pass every other test in
    this file and tell the next stage that 450 settled tickets were examined.
    """
    session = _session(tmp_path)
    for n in range(_MAX_SCANNED + 50):
        _ticket(session, f"bulk-{n}", f"Bulk settled ticket {n} about plumbing")

    basis = search_prior_work(session, "quantum blockchain renderer", workspace_slug="ws")["basis"]
    assert basis.corpus_total == _MAX_SCANNED + 50
    assert basis.scanned == _MAX_SCANNED
    assert basis.corpus_total != basis.scanned
    assert basis.not_examined == 50


def test_the_corpus_count_covers_the_resolved_scope_only(tmp_path):
    """A COUNT over settled tickets *in the workspace*, not over the table.

    Counting every workspace would make `not_examined` a number about other
    people's work.
    """
    session = _session(tmp_path)
    session.add(Workspace(id="other", slug="other", name="Other", repo_path="."))
    session.commit()
    _ticket(session, "mine-1", "Settled work on plumbing")
    other = Ticket(
        external_id="theirs-1",
        workspace_id="other",
        title="Settled work on plumbing",
        state=TicketState.DONE,
        work_item_type=WorkItemType.TASK,
    )
    session.add(other)
    session.commit()

    basis = search_prior_work(session, "quantum renderer", workspace_slug="ws")["basis"]
    assert basis.corpus_total == 1


def test_in_flight_tickets_are_outside_the_corpus_total(tmp_path):
    """`corpus_total` must describe the scope actually queried, which is
    settled tickets. Counting in-flight ones would inflate `not_examined` with
    rows the scan was never going to read."""
    session = _session(tmp_path)
    _ticket(session, "done-1", "Settled plumbing work")
    _ticket(session, "live-1", "Live plumbing work", state=TicketState.IN_PROGRESS)

    basis = search_prior_work(session, "quantum renderer", workspace_slug="ws")["basis"]
    assert basis.corpus_total == 1


# --- AC2 the pin for the cache key deferred onto lg-code-knowledge-284 ------


def test_the_prior_work_path_memoizes_nothing(tmp_path):
    """There is no index row, no stamp and no cache in this tree to key.

    The three-component cache key went to lg-code-knowledge-284, which will own
    those timestamps. This is the pin that makes the deferral checkable: if
    anything here starts caching, the rule has to come back with it.
    """
    session = _session(tmp_path)
    _ticket(session, "first", "Smart import of markdown")
    first = search_prior_work(session, "smart import markdown", workspace_slug="ws")
    assert len(first["results"]) == 1

    _ticket(session, "second", "Smart import of markdown, again")
    second = search_prior_work(session, "smart import markdown", workspace_slug="ws")
    assert len(second["results"]) == 2
    assert second["basis"].corpus_total == 2


# --- AC7(a) scope is enforced at the join -----------------------------------


def test_an_artifact_scoped_by_ticket_alone_does_not_cross_workspaces(tmp_path):
    """`_artifact_digest` joins on `ticket_id`; ids are unique, but the join
    must carry `workspace_id` too and filter it again outside. A digest is the
    one part of a hit that quotes another ticket's error text."""
    from loregarden.models.domain import Artifact

    session = _session(tmp_path)
    session.add(Workspace(id="other", slug="other", name="Other", repo_path="."))
    session.commit()
    mine = _ticket(session, "mine-1", "Smart import of markdown")
    theirs = Ticket(
        external_id="theirs-1",
        workspace_id="other",
        title="Smart import of markdown",
        state=TicketState.DONE,
        work_item_type=WorkItemType.TASK,
    )
    session.add(theirs)
    session.commit()
    session.add(Artifact(ticket_id=theirs.id, kind="error", title="their secret failure"))
    session.add(Artifact(ticket_id=mine.id, kind="error", title="my failure"))
    session.commit()

    answer = search_prior_work(session, "smart import markdown", workspace_slug="ws")
    titles = [a["title"] for hit in answer["results"] for a in hit["artifacts"]]
    assert titles == ["my failure"]


# --- AC2(a) the one caller is migrated, with no shim ------------------------


def test_the_mcp_caller_is_migrated_with_no_shim(client):
    """`_search_prior_work` serializes the basis beside the results.

    The envelope is what an agent actually reads; a basis the service returns
    and the tool drops is the same empty answer with more code behind it.
    """
    from tests.mcp_helpers import call_mcp

    response = call_mcp(
        client,
        "loregarden_search_prior_work",
        {"query": "quantum blockchain renderer", "workspace_slug": "loregarden"},
    )
    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload["results"] == []
    basis = payload["basis"]
    assert {"corpus_total", "scanned", "not_examined", "legs"} <= set(basis)
    assert basis["exported_into_store"] is None
    assert basis["notes"]


def test_search_prior_work_no_longer_returns_a_bare_list(tmp_path):
    """Internal posture: the old shape is gone, not kept beside the new one."""
    session = _session(tmp_path)
    answer = search_prior_work(session, "anything at all", workspace_slug="ws")
    assert not isinstance(answer, list)
    assert set(answer) >= {"results", "basis"}


# --- AC8 the ceiling, at prod_shape volume ----------------------------------


@pytest.fixture(name="prod_shape_session")
def prod_shape_session_fixture(client, isolated_db):
    with Session(isolated_db) as session:
        build_prod_shape(session)
    with Session(isolated_db) as session:
        yield session


def test_the_ceiling_drops_an_overcommon_term_at_prod_shape_volume(prod_shape_session):
    """Measured against the real shape, not a three-row fixture.

    The calibrated scenario has no naturally over-common term — its titles are
    generated, so nothing clears a fifth of 300-odd settled tickets. The term
    is therefore planted, at volume: a quarter of the corpus is given one
    distinctive word, which is what a word like "migration" looks like in a
    real backlog. The volume is what makes the ceiling's arithmetic real; a
    three-row fixture would make the threshold meaningless.
    """
    slug = "loregarden"
    workspace = prod_shape_session.exec(select(Workspace).where(Workspace.slug == slug)).one()
    settled = prod_shape_session.exec(
        select(Ticket).where(
            Ticket.workspace_id == workspace.id,
            Ticket.state.in_([TicketState.DONE, TicketState.WONT_DO]),
        )
    ).all()
    assert len(settled) > 100, "the scenario must hold a corpus worth counting"

    planted = max(len(settled) // 4, 1)
    for ticket in settled[:planted]:
        ticket.title = f"{ticket.title} overcommonterm"
        prod_shape_session.add(ticket)
    prod_shape_session.commit()

    basis = search_prior_work(
        prod_shape_session, "overcommonterm distinctiverareterm", workspace_slug=slug
    )["basis"]
    assert basis.corpus_total == len(settled)
    assert "overcommonterm" in basis.dropped_terms
    assert basis.dropped_terms["overcommonterm"].strip()
    assert "distinctiverareterm" not in basis.dropped_terms


def test_the_document_frequency_is_counted_in_the_pass_that_already_tokenizes(
    prod_shape_session,
):
    """No second query, no index. The scan already tokenizes every candidate.

    Pinned behaviourally: the counts the ceiling uses can only describe rows
    the scan actually read, so they never exceed `scanned`.
    """
    answer = search_prior_work(
        prod_shape_session, "memory retrieval basis emptiness", workspace_slug="loregarden"
    )
    basis = answer["basis"]
    assert basis.scanned <= basis.corpus_total
    assert basis.scanned <= _MAX_SCANNED


# --- AC10 the cap is a reduction, and the omitted set travels inline -------


def test_the_result_cap_is_recorded_as_a_reduction_over_what_matched(tmp_path):
    """`scored[:limit]` recorded nothing: `len(scored)` was lost, so a caller
    could not tell a complete answer from a capped one.

    `not_examined` does not cover this. That counts rows the scan never
    reached; these were matched and then dropped, which is the fact a caller
    needs to know the answer understates itself.
    """
    session = _session(tmp_path)
    for n in range(8):
        _ticket(session, f"plumb-{n}", f"Smart import of markdown tickets number {n}")

    answer = search_prior_work(session, "smart import markdown", workspace_slug="ws", limit=3)
    reduction = answer["basis"].reductions["results"]

    assert len(answer["results"]) == 3
    assert (reduction.total, reduction.emitted, reduction.truncated) == (8, 3, 5)
    assert reduction.reason.strip()


def test_the_omitted_results_are_named_inline_so_the_cap_is_reversible(tmp_path):
    """AC10 inlines the omitted set rather than persisting it: there is no
    recovery store to read from, so ids that do not travel are unreachable."""
    session = _session(tmp_path)
    for n in range(6):
        _ticket(session, f"plumb-{n}", f"Smart import of markdown tickets number {n}")

    answer = search_prior_work(session, "smart import markdown", workspace_slug="ws", limit=2)
    emitted = {hit["external_id"] for hit in answer["results"]}
    omitted = set(answer["basis"].reductions["results"].omitted)

    assert len(omitted) == 4
    assert omitted.isdisjoint(emitted)
    assert emitted | omitted == {f"plumb-{n}" for n in range(6)}


def test_an_uncapped_answer_records_a_reduction_that_dropped_nothing(tmp_path):
    """Absence of truncation is a measurement too: a missing reduction would
    leave a reader unable to tell "nothing dropped" from "nobody counted"."""
    session = _session(tmp_path)
    _ticket(session, "only-one", "Smart import of markdown tickets")

    reduction = search_prior_work(session, "smart import markdown", workspace_slug="ws")[
        "basis"
    ].reductions["results"]

    assert (reduction.total, reduction.emitted, reduction.truncated) == (1, 1, 0)
    assert list(reduction.omitted) == []


def test_the_envelope_carries_the_omitted_ids_in_partial(client):
    """The agent reads `partial`, not the service return. A reduction the tool
    drops is the same truncation with more code behind it."""
    from tests.mcp_helpers import call_mcp

    response = call_mcp(
        client,
        "loregarden_search_prior_work",
        {"query": "quantum blockchain renderer", "workspace_slug": "loregarden"},
    )
    payload = json.loads(response["result"]["content"][0]["text"])
    results = payload["basis"]["reductions"]["results"]

    # A scan that matched nothing still reports its reduction rather than
    # omitting the key: "nothing was dropped" is a measurement, and an absent
    # key would be indistinguishable from nobody counting.
    assert results["omitted"] == []
    assert results["total"] == 0
    # Nothing was truncated, so `partial` says nothing — it lists what is
    # missing, and a reduction that dropped nothing is not missing anything.
    assert not any(key.startswith("truncated_") for key in payload["partial"])


def test_a_milestone_whose_only_match_fell_past_the_limit_is_not_called_untouched(tmp_path):
    """One basis cannot make two opposite claims about the same milestone.

    `matched_ids` was built from the emitted results, so a milestone whose
    only match was dropped by the cap was named in `untouched_sentence` as
    having nothing — in the same basis whose `reductions['results'].omitted`
    named that very ticket.
    """
    session = _session(tmp_path)
    for n in range(6):
        ticket = _ticket(session, f"a-{n}", "Smart import of markdown plumbing")
        ticket.milestone = "milestone-A"
        session.add(ticket)
    late = _ticket(session, "b-0", "Smart import of markdown")
    late.milestone = "milestone-B"
    session.add(late)
    session.commit()

    basis = search_prior_work(session, "smart import markdown", workspace_slug="ws", limit=5)[
        "basis"
    ]

    assert "b-0" in basis.reductions["results"].omitted, "the repro needs b-0 to be dropped"
    assert "milestone-B" not in basis.untouched_sentence
