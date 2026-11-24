"""The memory leg's corners: a store that is neither absent nor readable, a
slug that is neither bare nor empty, and a join asked for nothing (AC2b, AC7).

`test_memory_search_basis.py` pins UNCONFIGURED against READ, which is the gap
the criterion names. The states in between are where an empty list goes back
to lying:

- A graph file that **exists and cannot be read** passes `is_file()`, so
  today's readiness sampler calls it READ. `MemoryStoreState.ERRORED` is in
  the vocabulary for exactly this, and an implementer wrapping the read to
  keep the basis resilient would turn a broken store into "read, and empty".
- A slug of **whitespace** is not empty and is not a bare segment. It strips
  to empty, which resolves to the shared BASE file — the multi-tenant one —
  and `MemoryGraphStore.search` then filters `workspace_slug` only when the
  slug is non-empty. That is a cross-tenant read reachable from one argument.
- `related_nodes([])` is an `IN ()` away from returning the table.
"""

from __future__ import annotations

import sqlite3

import pytest
from loregarden.models.domain.enums import MemoryStoreKind, MemoryStoreState
from loregarden.services.memory_store import AgentMemoryService, MemoryGraphStore


@pytest.fixture(name="graph_base")
def graph_base_fixture(tmp_path):
    base = tmp_path / "Loregarden" / "memory.db"
    base.parent.mkdir(parents=True)
    return base


@pytest.fixture(name="service")
def service_fixture(graph_base):
    return AgentMemoryService(obsidian=None, graph_sqlite_base=graph_base)


# --- AC2(b) a store that exists and cannot be read --------------------------


def test_a_graph_file_that_is_not_a_database_never_reads_as_read_and_empty(service, graph_base):
    """The third state, and the one a resilient implementation invents.

    `is_file()` is true of a corrupt file, so READ is the state a naive
    sampler reports. Either the read raises — and the shield turns it into an
    internal error, which is honest — or the leg says ERRORED. What it may
    not do is hand back `[]` under READ, because that is indistinguishable
    from a healthy store with nothing to say.
    """
    broken = graph_base.parent / "ws" / graph_base.name
    broken.parent.mkdir(parents=True)
    broken.write_bytes(b"this is not a SQLite database" * 64)

    try:
        answer = service.search("retrieval", workspace_slug="ws")
    except sqlite3.DatabaseError:
        return  # raised: it reaches the agent as an error, not as emptiness
    assert answer["basis"].legs[MemoryStoreKind.GRAPH.value] is not MemoryStoreState.READ


def test_a_graph_directory_where_the_file_should_be_is_not_read(service, graph_base):
    """A directory at the graph path is a misconfiguration, not an empty
    store. `is_file()` is false, so this must land UNCONFIGURED rather than
    crash the whole reply."""
    (graph_base.parent / "ws" / graph_base.name).mkdir(parents=True)
    try:
        answer = service.search("retrieval", workspace_slug="ws")
    except (sqlite3.DatabaseError, OSError):
        return  # raised: an error, not an empty answer
    assert answer["basis"].legs[MemoryStoreKind.GRAPH.value] is not MemoryStoreState.READ


def test_readiness_is_sampled_before_the_reply_even_when_the_store_is_written_during_it(service):
    """Ordering, stated as a property rather than as a file-existence check.

    AC2(b) exists because constructing a `MemoryGraphStore` creates the file.
    A first call against a workspace with no store must report UNCONFIGURED
    even if that same call goes on to create one.
    """
    first = service.search("retrieval", workspace_slug="fresh")
    assert first["basis"].legs[MemoryStoreKind.GRAPH.value] is MemoryStoreState.UNCONFIGURED


# --- AC7(d) the slugs that are neither bare nor empty -----------------------


@pytest.mark.parametrize(
    "slug",
    [".", "ws/", "./ws", "ws/sub", "\x00ws", "ws\x00", "~/other"],
    ids=[
        "dot",
        "trailing-separator",
        "leading-dot-slash",
        "nested",
        "leading-nul",
        "trailing-nul",
        "home-path",
    ],
)
def test_more_slugs_that_are_not_bare_path_segments_are_rejected(service, slug):
    """The parametrization next door covers traversal. These are the ones that
    are not traversal and are still not a single directory name. A NUL byte
    truncates a path at the C boundary, and `.` is the parent directory
    itself — a graph file written there is the shared, multi-tenant one."""
    with pytest.raises(ValueError):
        service.graph_path(slug)


def test_a_whitespace_slug_does_not_fall_through_to_the_shared_base_file(service, graph_base):
    """`_graph_path_for_workspace` strips, and an empty slug returns the BASE
    file, which holds every tenant's rows. A slug of spaces is caller text,
    not an omitted argument, so collapsing the two makes one argument a
    cross-tenant read.
    """
    with pytest.raises(ValueError):
        service.graph_path("   ")


def test_a_whitespace_slug_cannot_read_another_workspaces_nodes(service, graph_base):
    """The same hazard at the surface an agent actually calls.

    `MemoryGraphStore.search` filters `workspace_slug` only when it is
    non-empty, so a whitespace slug reaching the base file returns every
    tenant's rows unfiltered.
    """
    base = MemoryGraphStore(graph_base)
    base.upsert_node(title="Theirs about retrieval", workspace_slug="other", node_type="memory")
    base.upsert_node(title="Mine about retrieval", workspace_slug="ws", node_type="memory")

    try:
        answer = service.search("retrieval", workspace_slug="   ")
    except ValueError:
        return  # refused at the boundary, which is the stronger answer
    assert all("Theirs" not in hit["title"] for hit in answer["graph"])


def test_a_rejected_slug_leaves_no_directory_behind(service, graph_base):
    """Reject, never slugify — and never `mkdir` on the way to deciding."""
    for slug in ("../other", "ws/sub", "   "):
        with pytest.raises(ValueError):
            service.graph_path(slug)
    assert [p.name for p in graph_base.parent.rglob("*")] == []


# --- AC7(b) the relation join asked for nothing, or for everything ----------


def _two_tenants(graph_base) -> MemoryGraphStore:
    store = MemoryGraphStore(graph_base)
    store.upsert_node(node_id="anchor", title="Anchor", workspace_slug="ws", node_type="memory")
    store.upsert_node(node_id="mine", title="Mine", workspace_slug="ws", node_type="memory")
    store.upsert_node(node_id="theirs", title="Theirs", workspace_slug="other", node_type="memory")
    store.create_relation(source_id="anchor", target_id="mine")
    store.create_relation(source_id="anchor", target_id="theirs")
    return store


def test_no_anchors_returns_nothing_rather_than_the_whole_edge_table(graph_base):
    """`IN ()` is a syntax error in SQLite, so the usual repair is to skip the
    predicate — which returns every edge in a multi-tenant file."""
    assert _two_tenants(graph_base).related_nodes([], workspace_slug="ws") == []


def test_an_unknown_workspace_sees_no_neighbours_rather_than_all_of_them(graph_base):
    """An empty filter value must narrow to nothing, not widen to everything."""
    assert _two_tenants(graph_base).related_nodes(["anchor"], workspace_slug="ghost") == []


def test_an_anchor_belonging_to_another_workspace_surfaces_nothing(graph_base):
    """Scope is on both ends of the edge, so the anchor is scoped too.

    Passing a node id read from one workspace's reply into another
    workspace's call is one line of agent code away.
    """
    assert _two_tenants(graph_base).related_nodes(["theirs"], workspace_slug="ws") == []


def test_a_duplicate_anchor_does_not_duplicate_its_neighbours(graph_base):
    """Determinism: the same reply built from a list with a repeat in it."""
    store = _two_tenants(graph_base)
    once = store.related_nodes(["anchor"], workspace_slug="ws")
    twice = store.related_nodes(["anchor", "anchor"], workspace_slug="ws")
    assert twice == once


# --- AC12 the write side of the provenance pin ------------------------------


def test_a_node_written_with_no_provenance_stores_null_and_not_a_default(graph_base):
    """The read-side pin covers rows written before the columns existed. This
    is the same claim about rows written *now*: a writer that defaulted
    `origin_kind` would make every legacy row the only honest one in the
    table, and the next reader would conclude the opposite."""
    store = MemoryGraphStore(graph_base)
    store.upsert_node(node_id="fresh", title="No provenance given", workspace_slug="ws")
    with sqlite3.connect(graph_base) as conn:
        row = conn.execute(
            "SELECT origin_kind, origin_ref FROM memory_nodes WHERE id = 'fresh'"
        ).fetchone()
    assert row == (None, None)
