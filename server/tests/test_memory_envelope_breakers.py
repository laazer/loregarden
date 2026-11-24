"""The two caps on the agent memory path, read where an agent meets them
(AC10(d)).

`test_memory_search_basis.py` pins the *emit* cap: 5 graph matches at
`limit=2` reach the envelope as `truncated_graph` with three omitted ids. Two
corners of the same criterion are not pinned, and each is a way the emitted
set goes back to reading as the matched set:

- **The vault leg's SCAN cap has never been crossed by a test.** The vault has
  no predicate, so matching means reading, and `VAULT_SCAN_CAP` stops the read
  at 500 notes. Every vault test in the suite runs at 30 notes, where the cap
  cannot bind, so the leg is only ever exercised against its emit cap. AC10(d)
  asks for over-cap volume on *each* leg, and the two caps answer different
  questions: `truncated_obsidian` names matches that were read and dropped,
  `not_examined` names notes that were never read at all. A leg that reported
  one of them as the other would pass every existing test.
- **The relationship between a leg's numbers is pinned at single points, not
  as an invariant.** `(5, 2)` and `(8, 3)` are two samples of
  `emitted + omitted == total`; a cap that mis-accounts at 1, at 0, or above
  the match count passes both.

So: one volume test per leg over its own cap, both caps bound at once so they
cannot collapse into one number, and the invariant swept across the limits an
agent can actually pass.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from loregarden.models.domain.enums import MemoryStoreKind, MemoryStoreState
from loregarden.services.memory_agent_search import VAULT_SCAN_CAP
from loregarden.services.memory_store import AgentMemoryService, ObsidianMemoryStore
from tests.mcp_helpers import call_mcp

#: Every fixture here matches on this one allowlisted token, so what a leg
#: returns is decided by the caps and by nothing else.
TERM = "retrievalterm"


def _service(tmp_path, *, graph_nodes: int = 0, vault_posts: int = 0, slug: str = "loregarden"):
    vault = tmp_path / "vault"
    vault.mkdir()
    store = ObsidianMemoryStore(vault)
    for n in range(vault_posts):
        store.upsert_blog_post(
            ticket_id=f"t-{n}", workspace_slug=slug, title=f"{TERM} post {n}", body="body"
        )
    base = tmp_path / "Loregarden" / "memory.db"
    base.parent.mkdir(parents=True)
    service = AgentMemoryService(obsidian=store, graph_sqlite_base=base)
    if graph_nodes:
        graph = service.require_graph(slug)
        for n in range(graph_nodes):
            graph.upsert_node(title=f"{TERM} node {n}", workspace_slug=slug, node_type="memory")
    return service


def _envelope(client, service, *, limit: int) -> dict:
    with patch.object(AgentMemoryService, "from_settings", return_value=service):
        response = call_mcp(
            client,
            "loregarden_search_memory",
            {"query": TERM, "workspace_slug": "loregarden", "limit": limit},
        )
    assert "isError" not in response["result"], response
    return json.loads(response["result"]["content"][0]["text"])


# --- the vault leg over its own scan cap ------------------------------------


def test_the_vault_scan_cap_is_reported_as_unread_and_not_as_dropped(tmp_path):
    """Twenty notes past the cap: never read, and the only field that says so.

    Every other vault test runs at 30 notes, under a cap of 500, so this is
    the first time the scan cap binds anywhere in the suite. The twenty notes
    beyond it were never opened: they are not matches, they are not omissions,
    and the one honest place for them is `not_examined`. A leg that folded
    them into `truncated_obsidian` would be claiming it read and rejected
    rows it never touched, and a leg that dropped them entirely would report
    `not_examined == 0` on a scope it did not finish.
    """
    over = VAULT_SCAN_CAP + 20
    service = _service(tmp_path, vault_posts=over)

    basis = service.search(TERM, workspace_slug="loregarden", limit=5)["basis"]
    reduction = basis.reductions["obsidian"]

    assert (basis.corpus_total, basis.scanned) == (over, VAULT_SCAN_CAP)
    assert basis.not_examined == 20, "the notes past the cap are named as unread"
    assert reduction.total == VAULT_SCAN_CAP, (
        "the reduction covers what was read; folding the unread notes in here "
        "would claim they were matched and dropped"
    )
    assert reduction.emitted + reduction.truncated == reduction.total


def test_both_vault_caps_bind_at_once_and_stay_two_numbers(tmp_path):
    """Read-cap and emit-cap together; neither may absorb the other.

    This is the shape a real over-cap search has — more notes than the scan
    reads, and more matches than the reply carries — and the one where a
    single "rows we did not give you" number would look complete and be
    wrong by 20.
    """
    over = VAULT_SCAN_CAP + 20
    service = _service(tmp_path, vault_posts=over)

    answer = service.search(TERM, workspace_slug="loregarden", limit=5)
    basis = answer["basis"]
    reduction = basis.reductions["obsidian"]

    assert len(answer["obsidian"]) == reduction.emitted == 5
    assert reduction.truncated == VAULT_SCAN_CAP - 5
    assert basis.not_examined == 20
    assert reduction.truncated != basis.not_examined, "two caps, two numbers"
    assert reduction.emitted + reduction.truncated + basis.not_examined == basis.corpus_total


def test_the_unread_notes_reach_the_agent_through_the_envelope(client, tmp_path):
    """The scan cap, where an agent meets it rather than where it happens."""
    service = _service(tmp_path, vault_posts=VAULT_SCAN_CAP + 20)

    payload = _envelope(client, service, limit=5)

    assert payload["basis"]["not_examined"] == 20
    assert payload["basis"]["scanned"] < payload["basis"]["corpus_total"]
    assert payload["partial"]["truncated_obsidian"]["total"] == VAULT_SCAN_CAP


# --- the invariant, swept rather than sampled -------------------------------


@pytest.mark.parametrize("limit", [1, 2, 7, 19, 1000])
def test_every_leg_accounts_for_every_match_at_any_limit(client, tmp_path, limit):
    """`emitted + omitted == total`, per leg, as a sweep.

    The pinned cases are `limit=2` and `limit=3`. A cap that is off by one at
    1, or that stops recording once the limit exceeds the match count, passes
    both of them.
    """
    service = _service(tmp_path, graph_nodes=9, vault_posts=11)

    payload = _envelope(client, service, limit=limit)

    for leg, reduction in payload["basis"]["reductions"].items():
        emitted_ids = [hit["id"] for hit in payload[leg]]
        assert reduction["emitted"] == len(emitted_ids) == min(limit, reduction["total"])
        assert reduction["emitted"] + len(reduction["omitted"]) == reduction["total"]
        assert set(reduction["omitted"]).isdisjoint(emitted_ids)
    assert payload["basis"]["reductions"]["graph"]["total"] == 9
    assert payload["basis"]["reductions"]["obsidian"]["total"] == 11


@pytest.mark.parametrize("limit", [1, 7, 19, 1000])
def test_the_truncated_key_is_present_exactly_when_rows_were_dropped(client, tmp_path, limit):
    """`partial` lists what is missing, so an untruncated leg has no entry —
    and a truncated one may not be silent."""
    service = _service(tmp_path, graph_nodes=9, vault_posts=11)

    payload = _envelope(client, service, limit=limit)

    for leg, reduction in payload["basis"]["reductions"].items():
        dropped = reduction["total"] - reduction["emitted"]
        assert (f"truncated_{leg}" in payload["partial"]) is (dropped > 0)


def test_every_omitted_id_names_a_row_that_exists(client, tmp_path):
    """The omitted set travels inline because there is nowhere to look it up.

    So an id in it has to be an id: an index, a `None` or a truncated string
    would be unrecoverable, and AC10 rejected a persisted recovery store on
    the grounds that inlining leaves nothing to dangle.
    """
    service = _service(tmp_path, graph_nodes=9, vault_posts=11)
    everything = {
        row["id"]
        for row in service.require_graph("loregarden").search_terms(
            {TERM}, workspace_slug="loregarden"
        )
    } | {note.id for note in service.obsidian.list_notes(workspace_slug="loregarden", limit=100)}

    payload = _envelope(client, service, limit=2)

    for leg in ("graph", "obsidian"):
        omitted = payload["partial"][f"truncated_{leg}"]["omitted"]
        assert omitted, f"{leg} dropped rows and named none of them"
        assert all(isinstance(item, str) and item for item in omitted)
        assert set(omitted) <= everything


def test_a_degraded_leg_and_a_truncated_leg_are_both_in_partial(client, tmp_path):
    """Two different partialities in one reply; neither may evict the other.

    `partial` is built from a degraded-leg mapping updated with the
    truncation family, so a reply with both is where a key collision or a
    replaced dict would show. Nothing exercises that combination today.
    """
    service = _service(tmp_path, vault_posts=11)  # no graph file: that leg is UNCONFIGURED

    payload = _envelope(client, service, limit=2)
    partial = payload["partial"]

    assert partial[MemoryStoreKind.GRAPH.value] == MemoryStoreState.UNCONFIGURED.value
    assert partial["truncated_obsidian"]["emitted"] == 2
    assert "truncated_graph" not in partial, "a leg that was never read truncated nothing"
    assert "graph" not in payload["basis"]["reductions"]
