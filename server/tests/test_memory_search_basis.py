"""`AgentMemoryService.search` ships its basis, and scope is enforced at the
join (AC2b, AC7b-d, AC11a, AC12).

Today `search` returns `{query, workspace_slug, obsidian[], graph[]}` and never
calls `store_readiness`. An unconfigured graph and a configured, empty graph
produce the same `[]`, so an agent reading an empty list cannot tell "there is
nothing to know" from "nobody wired the store up". `store_readiness` already
answers that from the path without opening the file — which matters, because
constructing a `MemoryGraphStore` CREATES one, and a wrong path would then read
back as a real store that happened to be empty.

The scope tests overturn the earlier reading that the per-workspace file is the
scope. `_graph_path_for_workspace` returns the shared BASE file for an empty
slug, so `memory_nodes` in that file carries rows for more than one
`workspace_slug`. There is a tenant column and it can collide.
"""

from __future__ import annotations

import json
import sqlite3

import pytest
from loregarden.models.domain.enums import (
    MemoryStoreKind,
    MemoryStoreState,
    RetrievalEmptiness,
)
from loregarden.services.memory_store import AgentMemoryService, MemoryGraphStore
from loregarden.services.retrieval_basis import RetrievalBasis
from sqlmodel import Session


@pytest.fixture(name="graph_base")
def graph_base_fixture(tmp_path):
    base = tmp_path / "Loregarden" / "memory.db"
    base.parent.mkdir(parents=True)
    return base


@pytest.fixture(name="service")
def service_fixture(graph_base):
    return AgentMemoryService(obsidian=None, graph_sqlite_base=graph_base)


def _node(service: AgentMemoryService, slug: str, title: str, **kw) -> dict:
    return service.require_graph(slug).upsert_node(
        title=title, workspace_slug=slug, node_type="memory", **kw
    )


# --- AC2(b) the basis beside obsidian and graph -----------------------------


def test_the_search_envelope_carries_a_retrieval_basis(service):
    _node(service, "ws", "A memory about retrieval")
    answer = service.search("retrieval", workspace_slug="ws")
    assert set(answer) >= {"query", "workspace_slug", "obsidian", "graph", "basis"}
    assert isinstance(answer["basis"], RetrievalBasis)


def test_an_unconfigured_leg_reports_unconfigured_and_not_read_and_empty(tmp_path):
    """The gap this criterion exists for.

    No graph has ever been written for this workspace, so the file does not
    exist. The hits are `[]` either way; only the leg state separates "the
    store said nothing" from "there was no store".
    """
    service = AgentMemoryService(obsidian=None, graph_sqlite_base=tmp_path / "nowhere" / "m.db")
    answer = service.search("retrieval", workspace_slug="ws")
    assert answer["graph"] == []
    assert answer["basis"].legs[MemoryStoreKind.GRAPH.value] is MemoryStoreState.UNCONFIGURED


def test_a_configured_and_empty_leg_reports_read(service):
    """Empty is a real answer when a real store gave it."""
    service.require_graph("ws")  # the store now exists and holds nothing
    answer = service.search("retrieval", workspace_slug="ws")
    assert answer["graph"] == []
    assert answer["basis"].legs[MemoryStoreKind.GRAPH.value] is MemoryStoreState.READ


def test_the_basis_is_built_without_opening_the_store(tmp_path):
    """`store_readiness` answers from the path. Constructing a graph creates it.

    If the basis were built by opening the store, this call would leave an
    empty SQLite file behind and the next read would report READ for a store
    that was never configured.
    """
    missing = tmp_path / "nowhere" / "m.db"
    service = AgentMemoryService(obsidian=None, graph_sqlite_base=missing)
    service.search("retrieval", workspace_slug="ws")
    assert not missing.exists()
    assert not (missing.parent / "ws" / missing.name).exists()


def test_the_leg_states_are_memory_store_state_members(service):
    _node(service, "ws", "A memory about retrieval")
    basis = service.search("retrieval", workspace_slug="ws")["basis"]
    assert basis.legs
    assert all(isinstance(state, MemoryStoreState) for state in basis.legs.values())


def test_the_memory_path_memoizes_nothing(service):
    """The pin for the cache key deferred onto lg-code-knowledge-284."""
    _node(service, "ws", "First memory about retrieval")
    assert len(service.search("retrieval", workspace_slug="ws")["graph"]) == 1
    _node(service, "ws", "Second memory about retrieval")
    assert len(service.search("retrieval", workspace_slug="ws")["graph"]) == 2


# --- AC11(a) partiality is one field carrying values, not key names ---------


def test_a_degraded_leg_appears_in_partial_as_key_to_value(client):
    """The producers already hold the answer; naming only the key discards it.

    Read through the MCP envelope, because that is where an agent meets it.
    """
    from tests.mcp_helpers import call_mcp

    response = call_mcp(
        client,
        "loregarden_search_memory",
        {"query": "retrieval", "workspace_slug": "loregarden"},
    )
    payload = json.loads(response["result"]["content"][0]["text"])
    partial = payload["partial"]
    assert isinstance(partial, dict)
    assert not isinstance(partial, list)
    assert all(isinstance(value, (str, int, float, bool, dict, list)) for value in partial.values())
    assert set(partial) <= {kind.value for kind in MemoryStoreKind} | {
        "terms",
        "truncated_graph",
        "truncated_obsidian",
    }
    assert not any(key.startswith("truncated_") for key in partial), (
        "nothing was truncated here; the truncated form is pinned separately"
    )


def test_the_untouched_sentence_covers_the_queried_workspace_only(service):
    """Naming zero-hit areas in other workspaces would mean opening their
    SQLite files, which is the speculative work `store_readiness` avoids."""
    _node(service, "ws", "A memory about retrieval")
    for slug, kind in (("ws", "decision"), ("other", "gotcha")):
        service.require_graph(slug).upsert_node(
            title="Something unrelated", workspace_slug=slug, node_type=kind
        )
    sentence = service.search("retrieval", workspace_slug="ws")["basis"].untouched_sentence
    assert "decision" in sentence and "workspace ws" in sentence
    assert "gotcha" not in sentence and "other" not in sentence


# --- AC7(c) the slug is resolved once, at the boundary ----------------------


def test_an_unresolvable_slug_is_refused_at_the_agent_entry_point(client):
    """Today `workspace_slug` is passed straight through, unresolved.

    It must resolve to a `Workspace` row once, at the boundary, and raise the
    typed unknown-slug condition when it does not — which reaches the agent as
    a shaped reply rather than an empty hit list.
    """
    from tests.mcp_helpers import call_mcp

    response = call_mcp(
        client,
        "loregarden_search_memory",
        {"query": "retrieval", "workspace_slug": "no-such-workspace"},
    )
    payload = json.loads(response["result"]["content"][0]["text"])
    assert "isError" not in response["result"]
    assert "no-such-workspace" in payload["error"]
    assert payload["remedy"].strip()


# --- AC7(d) the graph path rejects a slug that is not a bare segment --------


@pytest.mark.parametrize("slug", ["../other", "a/b", "/absolute", "..", "other/../ws"])
def test_a_slug_that_is_not_a_bare_path_segment_is_rejected(service, graph_base, slug):
    """Reject, never slugify.

    Slugifying would quietly relocate an existing workspace's graph file and
    make a live workspace's memory read as empty — a worse outcome than the
    traversal, because nothing would report it.
    """
    with pytest.raises(ValueError):
        service.graph_path(slug)


def test_rejection_does_not_relocate_or_create_anything(service, graph_base):
    before = sorted(p.name for p in graph_base.parent.rglob("*"))
    with pytest.raises(ValueError):
        service.graph_path("../other")
    assert sorted(p.name for p in graph_base.parent.rglob("*")) == before


def test_a_bare_slug_still_resolves_to_its_own_file(service, graph_base):
    assert service.graph_path("ws") == graph_base.parent / "ws" / graph_base.name


def test_an_empty_slug_still_resolves_to_the_shared_base_file(service, graph_base):
    """Unchanged on purpose: it is why the base file is multi-tenant, which is
    what the relation-join tests below are about."""
    assert service.graph_path("") == graph_base


# --- AC7(b) the relation joins carry workspace_slug on both sides -----------


def _base_store_with_two_tenants(graph_base) -> MemoryGraphStore:
    """Two workspaces' nodes in the shared BASE file, with one colliding id.

    This is reachable today: an empty slug resolves to the base file, and
    `MemoryGraphStore.search` filters `workspace_slug` only when the slug is
    non-empty.
    """
    store = MemoryGraphStore(graph_base)
    store.upsert_node(node_id="anchor", title="Anchor", workspace_slug="ws", node_type="memory")
    store.upsert_node(node_id="mine", title="Mine", workspace_slug="ws", node_type="memory")
    store.upsert_node(node_id="theirs", title="Theirs", workspace_slug="other", node_type="memory")
    store.create_relation(source_id="anchor", target_id="mine")
    store.create_relation(source_id="anchor", target_id="theirs")
    store.create_relation(source_id="theirs", target_id="anchor")
    return store


def test_an_outbound_relation_does_not_surface_another_workspaces_node(graph_base):
    store = _base_store_with_two_tenants(graph_base)
    titles = {
        row["title"]
        for row in store.related_nodes(["anchor"], workspace_slug="ws")
        if row["direction"].value == "out"
    }
    assert titles == {"Mine"}


def test_an_inbound_relation_does_not_surface_another_workspaces_node(graph_base):
    """Both sides, because the `in` leg is a separate SELECT with its own join."""
    store = _base_store_with_two_tenants(graph_base)
    neighbours = store.related_nodes(["anchor"], workspace_slug="ws")
    assert all(row["title"] != "Theirs" for row in neighbours)


def test_related_nodes_requires_the_workspace_it_is_scoped_to(graph_base):
    """An optional scope is an unscoped query on every caller that forgets it."""
    store = _base_store_with_two_tenants(graph_base)
    with pytest.raises(TypeError):
        store.related_nodes(["anchor"])


def test_a_relation_written_across_two_slugs_in_the_base_file_does_not_cross(graph_base):
    """The base file's multi-tenancy, written out at the SQL level.

    `memory_nodes.id` is a primary key, so two workspaces cannot literally
    share an id — the reachable form of the surveyed collision is an edge whose
    two ends sit in different `workspace_slug` values inside one file. Without
    the tenant column in the join condition, one workspace's relation surfaces
    the other's title.
    """
    store = MemoryGraphStore(graph_base)
    with sqlite3.connect(graph_base) as conn:
        conn.execute(
            "INSERT INTO memory_nodes "
            "(id, title, body, tags_json, ticket_id, workspace_slug, node_type, "
            " created_at, updated_at, discredited, aliases_json) "
            "VALUES ('shared', 'Theirs', '', '[]', '', 'other', 'memory', "
            "'2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', 0, '[]')"
        )
        conn.execute(
            "INSERT INTO memory_nodes "
            "(id, title, body, tags_json, ticket_id, workspace_slug, node_type, "
            " created_at, updated_at, discredited, aliases_json) "
            "VALUES ('anchor', 'Anchor', '', '[]', '', 'ws', 'memory', "
            "'2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', 0, '[]')"
        )
        conn.execute(
            "INSERT INTO memory_relations (id, source_id, target_id, relation_type, created_at) "
            "VALUES ('rel-1', 'anchor', 'shared', 'related', '2026-01-01T00:00:00+00:00')"
        )

    assert store.related_nodes(["anchor"], workspace_slug="ws") == []


# --- AC12 legacy provenance reads unknown, never complete -------------------


def test_a_row_written_before_provenance_existed_reads_origin_kind_as_none(graph_base):
    """`_ensure_origin_columns` adds the columns `TEXT NULL` and names the
    forbidden inference in its own docstring. This is the pin that makes the
    criterion checkable rather than merely true today.

    The row is written into a schema that predates the columns, then read back
    through the migrating open — which is exactly the live upgrade path.
    """
    legacy = graph_base.parent / "legacy.db"
    with sqlite3.connect(legacy) as conn:
        conn.executescript(
            """
            CREATE TABLE memory_nodes (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '',
                tags_json TEXT NOT NULL DEFAULT '[]', ticket_id TEXT NOT NULL DEFAULT '',
                workspace_slug TEXT NOT NULL DEFAULT '', node_type TEXT NOT NULL DEFAULT 'memory',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE memory_relations (
                id TEXT PRIMARY KEY, source_id TEXT NOT NULL, target_id TEXT NOT NULL,
                relation_type TEXT NOT NULL DEFAULT 'related', created_at TEXT NOT NULL
            );
            INSERT INTO memory_nodes (id, title, workspace_slug, created_at, updated_at)
            VALUES ('old', 'Written before provenance', 'ws',
                    '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00');
            """
        )

    rows = MemoryGraphStore(legacy).search("provenance", workspace_slug="ws")
    assert [row["id"] for row in rows] == ["old"]
    assert rows[0]["origin_kind"] is None
    assert rows[0]["origin_ref"] is None


def test_no_backfill_in_this_change_defaults_a_provenance_column(graph_base):
    """Adding a column, or a default, to satisfy the wording would be worse
    than nothing: a guess stored as a fact on every old row."""
    MemoryGraphStore(graph_base)
    with sqlite3.connect(graph_base) as conn:
        columns = {row[1]: row[4] for row in conn.execute("PRAGMA table_info(memory_nodes)")}
    assert columns["origin_kind"] is None
    assert columns["origin_ref"] is None


# --- AC8 the agent's question is reduced before either leg is searched -----


def test_an_agent_question_matches_a_node_its_whole_phrase_never_would(service):
    """The regression this reduction exists to remove, as the reviewer found it.

    An agent does not type a substring, it asks a question, and a question
    LIKE'd whole essentially never matches. Before the reduction this exact
    pair returned 0 hits for the question and 1 for a single word out of it —
    which reads to the agent as "nothing is known".
    """
    _node(service, "ws", "Retrieval basis for memory store")

    asked = service.search(
        "how does the memory store build its retrieval basis", workspace_slug="ws"
    )
    assert len(asked["graph"]) == 1
    assert len(service.search("retrieval", workspace_slug="ws")["graph"]) == 1


def test_the_reduction_happens_at_the_agent_entry_point_not_in_the_graph_store(service, graph_base):
    """Spec A5: the operator Memory browser keeps substring matching.

    `services/memory_browser.py` calls `MemoryGraphStore.search` directly, so
    that method must stay a whole-phrase match — somebody typing a substring
    means a substring. Only the agent path reduces.
    """
    _node(service, "ws", "Retrieval basis for memory store")
    graph = service.require_graph("ws")

    assert (
        graph.search("how does the memory store build its retrieval basis", workspace_slug="ws")
        == []
    )
    assert len(graph.search("basis for memory", workspace_slug="ws")) == 1


def test_a_term_keeps_its_escape_so_an_underscore_is_not_a_wildcard(service):
    """The allowlist removed the metacharacters a query could carry, so this
    is the second line rather than the only one — but a term is still caller
    text, and our callers search for underscore-dense identifiers."""
    _node(service, "ws", "About memory_store internals")
    _node(service, "ws", "About memoryXstore internals")

    hits = service.search("memory_store", workspace_slug="ws")["graph"]
    assert [row["title"] for row in hits] == ["About memory_store internals"]


def test_the_basis_says_the_ceiling_did_not_run_rather_than_reporting_a_zero(service):
    """AC8 names this explicitly. There is no corpus count on the memory path,
    so an empty `dropped_terms` must be distinguishable from a ceiling that
    measured nothing — otherwise "no term was too common" and "nobody looked"
    are the same answer."""
    _node(service, "ws", "Retrieval basis for memory store")
    basis = service.search("retrieval basis", workspace_slug="ws")["basis"]

    assert basis.ceiling_applied is False
    assert basis.dropped_terms == {}
    assert basis.as_dict()["ceiling_applied"] is False


def test_a_question_of_only_stopwords_reports_no_terms_rather_than_no_overlap(service):
    """Reducing to nothing is its own emptiness, not a failed match."""
    _node(service, "ws", "Retrieval basis for memory store")
    # Every word here is either a stopword or under the four-character floor,
    # so the allowlist builds no term at all.
    basis = service.search("this that when then", workspace_slug="ws")["basis"]

    assert basis.emptiness is RetrievalEmptiness.NO_TERMS


def test_the_graph_cap_is_reported_as_a_reduction_with_its_omitted_ids(service):
    """AC10's field family on the memory leg: the `limit` cap drops matches
    that were found, which is a different fact from rows never scanned."""
    for n in range(5):
        _node(service, "ws", f"Retrieval note number {n}")

    answer = service.search("retrieval", workspace_slug="ws", limit=2)
    reduction = answer["basis"].reductions["graph"]

    assert len(answer["graph"]) == 2
    assert (reduction.total, reduction.emitted, reduction.truncated) == (5, 2, 3)
    assert len(set(reduction.omitted)) == 3
    assert set(reduction.omitted).isdisjoint({row["id"] for row in answer["graph"]})


# --- AC7(d) at the MCP boundary, not only at the path rule ----------------


def _resolve(session, slug: str) -> str:
    from loregarden.mcp.memory_tools import _resolved_slug

    return _resolved_slug(session, slug)


@pytest.mark.parametrize("slug", ["   ", "\t", "\n", " ", " loregarden ", "loregarden "], ids=repr)
def test_a_slug_that_is_not_exactly_empty_is_resolved_or_refused(client, isolated_db, slug):
    """The boundary used to `.strip()` before deciding anything.

    That turned a slug of spaces into "no slug given", and an empty slug
    resolves to the shared BASE graph file — which holds every tenant's rows
    and filters `workspace_slug` only when it is non-empty. So one MCP
    argument read another workspace's memory, even though
    `graph_path_for_workspace` rejects whitespace correctly: stripping here
    meant it was never asked.

    Padding is refused for the same reason rather than repaired. Quietly
    fixing a slug on the agent's behalf is how the wrong scope gets queried
    without anyone noticing; a shaped reply naming the slug is recoverable.
    """
    from loregarden.mcp.shaped_errors import UnknownWorkspaceSlug

    with Session(isolated_db) as session, pytest.raises(UnknownWorkspaceSlug) as raised:
        _resolve(session, slug)
    assert raised.value.remedy.strip()


def test_a_real_slug_resolves_to_the_row_it_names(client, isolated_db):
    """The only accepted input is a slug a `Workspace` row answers to."""
    with Session(isolated_db) as session:
        assert _resolve(session, "loregarden") == "loregarden"


def test_a_whitespace_slug_through_the_tool_cannot_read_another_tenant(client):
    """The leak as an agent would reach it: one `tools/call` argument.

    Asserted through `loregarden_search_memory` rather than through
    `AgentMemoryService.search`, because the service was never the hole — the
    boundary in front of it was, and a test that starts after the boundary is
    exactly the test that let this ship.
    """
    from tests.mcp_helpers import call_mcp

    response = call_mcp(
        client, "loregarden_search_memory", {"query": "retrieval", "workspace_slug": "   "}
    )
    payload = json.loads(response["result"]["content"][0]["text"])

    assert "isError" not in response["result"]
    assert "graph" not in payload, "a whitespace slug must not reach a store at all"
    assert payload["remedy"].strip()


def test_the_argument_coercer_strips_too_which_is_why_empty_is_refused(client):
    """Why `_resolved_slug` refuses `""` and not just whitespace.

    `tool_args.coerce_optional_string` strips every string argument, so a
    slug of spaces arrives at the handler already collapsed to `""`. Guarding
    only against whitespace inside the handler would therefore have left the
    leak reachable through the real transport while looking fixed.
    """
    from loregarden.mcp.tools import normalize_tool_arguments

    coerced = normalize_tool_arguments(
        "loregarden_search_memory", {"query": "retrieval", "workspace_slug": "   "}
    )
    assert coerced["workspace_slug"] == ""


# --- the emitted set is never reported as the matched set ------------------


def _bulk_nodes(service, slug: str, count: int, title: str) -> None:
    """Rows written straight to SQLite: 520 upserts is a slow way to say 520."""
    service.require_graph(slug)
    with sqlite3.connect(service.graph_path(slug)) as conn:
        conn.executemany(
            "INSERT INTO memory_nodes (id, title, body, tags_json, ticket_id, workspace_slug,"
            " node_type, created_at, updated_at, discredited, aliases_json)"
            " VALUES (?, ?, '', '[]', '', ?, 'memory',"
            " '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', 0, '[]')",
            [(f"n-{i}", f"{title} {i}", slug) for i in range(count)],
        )


def test_every_graph_match_is_read_and_counted_not_just_the_first_page(service):
    """The SQL cap used to drop matches nobody ever heard about.

    `LIMIT 500` with `ORDER BY updated_at DESC` returned the 500 most recent
    matches and ranked only those, while the basis reported
    `scanned == corpus_total` and `not_examined == 0`. With 520 matches,
    twenty were never read, never ranked and named nowhere — the emitted set
    reported as the matched set, which is this ticket's own subject.
    """
    _bulk_nodes(service, "ws", 520, "retrievalterm node")

    answer = service.search("retrievalterm", workspace_slug="ws", limit=20)
    basis = answer["basis"]
    reduction = basis.reductions["graph"]

    assert reduction.total == 520, "the reduction must count matches, not the page"
    assert reduction.emitted == len(answer["graph"]) == 20
    assert reduction.emitted + reduction.truncated == 520
    assert len(set(reduction.omitted)) == 500, "every dropped match is named"
    assert (basis.corpus_total, basis.scanned, basis.not_examined) == (520, 520, 0)


def test_the_vault_cap_is_reported_like_the_graph_cap(vault_dir_for_terms):
    """The graph leg's cap was made reportable and the identical vault one
    was not: 30 matching blog posts at limit=5 gave five hits and an empty
    `reductions`."""
    service = vault_dir_for_terms

    answer = service.search("retrievalterm", workspace_slug="ws", limit=5)
    reduction = answer["basis"].reductions["obsidian"]

    assert len(answer["obsidian"]) == 5
    assert (reduction.total, reduction.emitted, reduction.truncated) == (30, 5, 25)
    assert len(set(reduction.omitted)) == 25


def test_a_vault_only_reply_does_not_report_a_zero_corpus_beside_its_hits(vault_dir_for_terms):
    """`corpus_total` was the graph's node count alone, so a vault-only reply
    said `corpus_total 0` next to five hits — firing the basis's own
    "zero corpus carries no information" note against an answer that matched.
    """
    basis = vault_dir_for_terms.search("retrievalterm", workspace_slug="ws", limit=5)["basis"]

    assert basis.corpus_total >= 30
    assert basis.scanned >= 30
    assert basis.emptiness is RetrievalEmptiness.MATCHED


@pytest.fixture(name="vault_dir_for_terms")
def vault_dir_for_terms_fixture(tmp_path):
    from loregarden.services.memory_store import ObsidianMemoryStore

    vault = tmp_path / "vault"
    vault.mkdir()
    store = ObsidianMemoryStore(vault)
    for n in range(30):
        store.upsert_blog_post(
            ticket_id=f"t-{n}", workspace_slug="ws", title=f"retrievalterm post {n}", body="body"
        )
    return AgentMemoryService(obsidian=store, graph_sqlite_base=None)


# --- AC10(d) the truncated leg reaches the agent in the envelope -----------


def _memory_service_for_mcp(tmp_path, *, graph_nodes: int, vault_posts: int, slug: str):
    """A two-legged service `from_settings` can be replaced by.

    Both legs are filled so one `tools/call` can assert what AC10(d) asks
    for: `basis.reductions['obsidian']` BESIDE `basis.reductions['graph']`,
    each carrying its own inline omitted set through the envelope.
    """
    from loregarden.services.memory_store import ObsidianMemoryStore

    vault = tmp_path / "vault"
    vault.mkdir()
    store = ObsidianMemoryStore(vault)
    for n in range(vault_posts):
        store.upsert_blog_post(
            ticket_id=f"t-{n}", workspace_slug=slug, title=f"retrievalterm post {n}", body="body"
        )
    base = tmp_path / "Loregarden" / "memory.db"
    base.parent.mkdir(parents=True)
    service = AgentMemoryService(obsidian=store, graph_sqlite_base=base)
    graph = service.require_graph(slug)
    for n in range(graph_nodes):
        graph.upsert_node(
            title=f"retrievalterm node {n}",
            workspace_slug=slug,
            node_type="memory",
        )
    return service


def _truncated_search_envelope(client, service, *, limit: int) -> dict:
    from unittest.mock import patch

    from tests.mcp_helpers import call_mcp

    with patch.object(AgentMemoryService, "from_settings", return_value=service):
        response = call_mcp(
            client,
            "loregarden_search_memory",
            {"query": "retrievalterm", "workspace_slug": "loregarden", "limit": limit},
        )
    assert "isError" not in response["result"], response
    return json.loads(response["result"]["content"][0]["text"])


def test_the_envelope_carries_truncated_graph_with_its_omitted_ids(client, tmp_path):
    """A TRUNCATED agent memory search, read where an agent meets it.

    Only the untruncated form was pinned
    (`test_a_degraded_leg_appears_in_partial_as_key_to_value` asserts no
    `truncated_` key at all), so nothing stopped the envelope from dropping
    the key family that carries a cap's omitted rows. The omitted set travels
    inline and there is no store to look those ids up in later, so an
    envelope that omits it loses them permanently — this test must fail
    against such an envelope.
    """
    service = _memory_service_for_mcp(tmp_path, graph_nodes=5, vault_posts=0, slug="loregarden")

    payload = _truncated_search_envelope(client, service, limit=2)
    entry = payload["partial"]["truncated_graph"]

    assert (entry["total"], entry["emitted"]) == (5, 2)
    assert len(set(entry["omitted"])) == 3
    assert set(entry["omitted"]).isdisjoint({hit["id"] for hit in payload["graph"]})
    assert entry["reason"].strip(), "a truncation names why it truncated"


def test_the_envelope_carries_both_legs_reductions_not_only_the_graph(client, tmp_path):
    """`reductions['obsidian']` beside `reductions['graph']`, same shape.

    The vault leg's cap was the one that went unreported for a round, so the
    pin asserts the two legs agree rather than that the graph leg works.
    """
    service = _memory_service_for_mcp(tmp_path, graph_nodes=8, vault_posts=30, slug="loregarden")

    payload = _truncated_search_envelope(client, service, limit=3)
    partial = payload["partial"]

    assert {"truncated_graph", "truncated_obsidian"} <= set(partial)
    for key in ("truncated_graph", "truncated_obsidian"):
        entry = partial[key]
        assert set(entry) == {"total", "emitted", "omitted", "reason"}
        assert entry["emitted"] == 3
        assert entry["emitted"] + len(entry["omitted"]) == entry["total"]
    assert payload["basis"]["reductions"]["obsidian"]["truncated"] == 27
    assert payload["basis"]["reductions"]["graph"]["truncated"] == 5


def test_no_envelope_field_claims_the_scope_was_exhausted_when_a_cap_bound(client, tmp_path):
    """Over-cap on both legs: nothing may read as "that was all of it"."""
    service = _memory_service_for_mcp(tmp_path, graph_nodes=8, vault_posts=30, slug="loregarden")

    payload = _truncated_search_envelope(client, service, limit=3)
    basis = payload["basis"]

    assert len(payload["graph"]) + len(payload["obsidian"]) == 6
    assert basis["reductions"]["graph"]["total"] == 8
    assert basis["reductions"]["obsidian"]["total"] == 30
    assert basis["emptiness"] == RetrievalEmptiness.MATCHED.value


# --- AC7(e) the slug rule exists once -------------------------------------


_SLUG_SHAPES = ["../other", "../../other", "   ", "..", "a/b", ""]


@pytest.fixture(name="configured_base")
def configured_base_fixture(tmp_path):
    """Point `config._memory_sqlite_base_path` at a real vault-shaped base.

    The agreement below is only meaningful with a base configured: with none,
    both entry points answer `None` for every slug and agree vacuously.
    """
    from unittest.mock import patch

    base = tmp_path / "Loregarden" / "memory.db"
    base.parent.mkdir(parents=True)
    with patch("loregarden.config._memory_sqlite_base_path", return_value=base):
        yield base


def _graph_path_answer(base, slug):
    """What `memory_scope` answers for `slug`: a path, or the refusal."""
    from loregarden.services.memory_scope import graph_path_for_workspace

    try:
        return graph_path_for_workspace(base, slug)
    except ValueError:
        return ValueError


def _config_answer(slug):
    from loregarden.config import resolved_memory_sqlite_path

    try:
        return resolved_memory_sqlite_path(slug)
    except ValueError:
        return ValueError


@pytest.mark.parametrize("slug", _SLUG_SHAPES, ids=repr)
def test_the_two_entry_points_agree_on_every_slug_shape(configured_base, slug):
    """One slug rule, not two.

    `config.resolved_memory_sqlite_path` is a verbatim unguarded copy of the
    rule (`base.parent / slug / base.name`), so it answers with a path for
    slugs every read and write path refuses. It must delegate to
    `memory_scope.graph_path_for_workspace`; a private second copy is the
    second copy by construction, which is the argument `services/sql_like.py`
    exists for.
    """
    assert _config_answer(slug) == _graph_path_answer(configured_base, slug)


def test_a_bare_slug_still_resolves_to_its_own_file_through_config(configured_base):
    """Delegating must not move a live workspace's graph file."""
    assert _config_answer("loregarden") == configured_base.parent / "loregarden" / "memory.db"


def test_an_empty_slug_still_resolves_to_the_shared_base_file_through_config(configured_base):
    """`''` legitimately means the shared base and keeps working."""
    assert _config_answer("") == configured_base


def test_status_raises_rather_than_reporting_a_path_outside_the_vault(configured_base):
    """`AgentMemoryService.status()` is how the unguarded copy is reached.

    `loregarden_memory_status(workspace_slug='../../other')` hands an agent a
    `memory_sqlite_path` outside the vault for a slug `search`,
    `upsert_memory` and `append_learning` all refuse.
    """
    service = AgentMemoryService(obsidian=None, graph_sqlite_base=None)
    with pytest.raises(ValueError, match="bare path segment"):
        service.status(workspace_slug="../../other")


def test_status_still_answers_for_a_slug_the_rule_accepts(configured_base):
    """The guard must refuse traversal without refusing ordinary workspaces."""
    service = AgentMemoryService(obsidian=None, graph_sqlite_base=None)
    reported = service.status(workspace_slug="loregarden")["memory_sqlite_path"]
    assert reported == str(configured_base.parent / "loregarden" / "memory.db")


def test_the_memory_status_tool_never_reports_a_path_outside_the_vault(client, configured_base):
    """The leak as an agent reaches it: one `tools/call` argument."""
    from tests.mcp_helpers import call_mcp

    response = call_mcp(client, "loregarden_memory_status", {"workspace_slug": "../../other"})
    payload = json.dumps(response)

    assert "other/memory.db" not in payload
