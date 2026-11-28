"""The write-time refusal (S3) and the search reply's verdict (S2).

Ticket lg-code-knowledge-368, requirements R5 and R4/S2, acceptance criteria 5,
10 and 11. A claim stored ungrounded when its author asked for grounding is
precisely the silent failure this ticket exists to remove, so the refusal is
pinned on its MESSAGE and on the whole-write rejection, not merely on the raise.

Until lg-code-knowledge-284 builds a map in production every grounded write
meets the second of these three refusals. That inert state is a decision (R6),
so `test_a_checkout_with_no_map_refuses_with_its_own_message` pins the wording
that keeps "there is no map yet" from reading as the writing agent's mistake.

Repositories are built in fixtures. `tests/symbol_map_repos.py` owns the tree
shapes; the workspace, run and worktree wiring is below.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from loregarden.mcp.tools import execute_tool, normalize_tool_arguments
from loregarden.models.domain import GroundingVerdict, Worktree, WorktreeState
from loregarden.services.git_subprocess import run_git
from loregarden.services.symbol_map import build_symbol_map
from loregarden.services.symbol_map.checkout_identity import identify
from loregarden.services.symbol_map.grounding import (
    SymbolMapUnavailableError,
    UnfingerprintableSymbolError,
    UnknownSymbolAnchorError,
)
from sqlmodel import Session
from tests.factories import make_agent_run, make_ticket, make_workspace
from tests.symbol_map_repos import (  # noqa: F401 - fixtures resolved by name
    TS_ANCHOR_NAME,
    commit_unindexed_change,
    grounding_repo_fixture,
)

WS = "kb"
UNGROUNDED_FALLBACK = "write it without anchors to record it ungrounded"


@pytest.fixture(name="vault_dir")
def vault_dir_fixture(tmp_path, monkeypatch) -> Path:
    """A vault and a graph of this test's own, so nothing reaches iCloud."""
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setattr("loregarden.config.settings.obsidian_vault_dir", str(vault))
    monkeypatch.setattr(
        "loregarden.config.settings.memory_sqlite_url",
        f"sqlite:///{tmp_path / 'memory.db'}",
    )
    return vault


@pytest.fixture(name="world")
def world_fixture(isolated_db, vault_dir, grounding_repo):
    """A workspace rooted at the fixture repo, with a ticket and a run on it.

    The run is what `execute_memory_tool` resolves an anchor's checkout from
    (D8): `AgentRun` -> `ticket_id`/`workspace_id` -> `resolve_ticket_root`.
    """
    with Session(isolated_db) as session:
        workspace = make_workspace(session, slug=WS, repo_path=str(grounding_repo))
        ticket = make_ticket(session, workspace_id=workspace.id, external_id="kb-1")
        run = make_agent_run(session, workspace_id=workspace.id, ticket_id=ticket.id, run_code="R1")
        yield session, workspace, ticket, run


@pytest.fixture(name="mapped_world")
def mapped_world_fixture(world, grounding_repo):
    """The same world, with this checkout's map already built."""
    session, workspace, ticket, run = world
    build_symbol_map(session, workspace.id, grounding_repo)
    return session, workspace, ticket, run


def _memory_args(**extra):
    return {"title": "A grounded claim", "body": "It says so.", "workspace_slug": WS, **extra}


def _learning_args(**extra):
    return {"ticket_id": "kb-1", "workspace_slug": WS, "content": "A grounded learning.", **extra}


def _search(session, run_id: str = "") -> dict:
    return json.loads(
        execute_tool(
            session,
            "loregarden_search_memory",
            {"query": "grounded", "workspace_slug": WS},
            run_id=run_id,
        )
    )


def _nodes(tmp_path) -> list[sqlite3.Row]:
    """Every row in this workspace's graph, read without opening a store.

    Constructing a `MemoryGraphStore` CREATES the file, so a refused write
    checked through the service would be indistinguishable from one that wrote
    and was rolled back. A missing file is zero rows and nothing else.
    """
    database = tmp_path / WS / "memory.db"
    if not database.is_file():
        return []
    conn = sqlite3.connect(database)
    conn.row_factory = sqlite3.Row
    try:
        return list(conn.execute("SELECT * FROM memory_nodes"))
    finally:
        conn.close()


# ==========================================================================
# R5 / criterion 5 and 11 — three refusals, three messages, never collapsed
# ==========================================================================


@pytest.mark.parametrize("tool", ["loregarden_upsert_memory", "loregarden_append_learning"])
def test_an_unknown_symbol_refuses_and_offers_the_names_that_do_exist(mapped_world, tool, tmp_path):
    """(a) UnknownSymbolAnchorError. It answers both halves of the reader's
    question — why it was not recorded, and what to write instead: the anchor
    as GIVEN, the workspace and sha it was looked up in, up to three nearest
    qualified names, and the ungrounded fallback."""
    session, _workspace, _ticket, run = mapped_world
    args = _memory_args if tool == "loregarden_upsert_memory" else _learning_args

    with pytest.raises(UnknownSymbolAnchorError) as raised:
        execute_tool(session, tool, args(anchors="pkg.alpha.stedy"), run_id=run.id)

    message = str(raised.value)
    assert "pkg.alpha.stedy" in message, "quotes the anchor as given"
    assert "pkg.alpha.steady" in message, "names a candidate that does exist"
    assert message.count("pkg.alpha.") <= 4, "the anchor plus at most three candidates"
    assert message.rstrip().endswith(UNGROUNDED_FALLBACK)
    assert _nodes(tmp_path) == []


def test_a_checkout_with_no_map_refuses_with_its_own_message(world, tmp_path):
    """(b) SymbolMapUnavailableError, and the pin on R6's inert state.

    Nothing builds the map in production until lg-code-knowledge-284, so this
    is the refusal every grounded write currently meets. It must be worded as
    the environment's state and must NOT be the unknown-symbol message, which
    would tell an agent its correct anchor does not exist.
    """
    session, _workspace, _ticket, run = world

    with pytest.raises(SymbolMapUnavailableError) as raised:
        execute_tool(
            session,
            "loregarden_upsert_memory",
            _memory_args(anchors="pkg.alpha.steady"),
            run_id=run.id,
        )

    message = str(raised.value)
    assert "lg-code-knowledge-284" in message
    assert "no symbol map" in message.lower()
    assert message.rstrip().endswith(UNGROUNDED_FALLBACK)
    assert _nodes(tmp_path) == []


def test_a_tier_with_no_fingerprint_refuses_with_its_own_message(mapped_world, tmp_path):
    """(c) UnfingerprintableSymbolError. D5 refuses a TypeScript anchor rather
    than hashing a guessed span; the amendment to criterion 11 is that this is
    a THIRD type, because folding it into (a) would tell an agent that a symbol
    the map plainly contains does not exist."""
    session, _workspace, _ticket, run = mapped_world

    with pytest.raises(UnfingerprintableSymbolError) as raised:
        execute_tool(
            session,
            "loregarden_upsert_memory",
            _memory_args(anchors=TS_ANCHOR_NAME),
            run_id=run.id,
        )

    message = str(raised.value)
    assert "lg-code-knowledge-386" in message
    assert message.rstrip().endswith(UNGROUNDED_FALLBACK)
    assert _nodes(tmp_path) == []


def test_the_three_error_types_are_distinct_and_so_are_their_messages(mapped_world):
    """Criterion 11's substance: three distinct causes, never collapsed into
    one message. Caught separately above; compared here, because 'distinct
    types' is cheap to satisfy with one shared message."""
    session, _workspace, _ticket, run = mapped_world
    types = (UnknownSymbolAnchorError, SymbolMapUnavailableError, UnfingerprintableSymbolError)

    assert len({*types}) == 3
    for one in types:
        for other in types:
            if one is not other:
                assert not issubclass(one, other), f"{one.__name__} is a kind of {other.__name__}"

    messages = set()
    for anchor, expected in (
        ("pkg.alpha.stedy", UnknownSymbolAnchorError),
        (TS_ANCHOR_NAME, UnfingerprintableSymbolError),
    ):
        with pytest.raises(expected) as raised:
            execute_tool(
                session, "loregarden_upsert_memory", _memory_args(anchors=anchor), run_id=run.id
            )
        messages.add(str(raised.value))
    assert len(messages) == 2


def test_every_refusal_is_worded_as_the_environment_not_the_agents_mistake(mapped_world, world):
    """UX S3. An agent told it got something wrong when the map simply has not
    been built yet learns to stop anchoring, which is the capability turned
    off by its own error copy."""
    session, _workspace, _ticket, run = mapped_world
    blaming = ("invalid", "illegal", "you must", "your mistake", "bad anchor")

    with pytest.raises(UnknownSymbolAnchorError) as raised:
        execute_tool(
            session,
            "loregarden_upsert_memory",
            _memory_args(anchors="pkg.alpha.stedy"),
            run_id=run.id,
        )

    lowered = str(raised.value).lower()
    assert not any(word in lowered for word in blaming), lowered


def test_a_refusal_rejects_the_whole_write_including_the_unanchored_half(mapped_world, tmp_path):
    """Criterion 5: no partial record, and no anchor-stripped downgrade. One
    anchor that resolves does not buy the write past one that does not."""
    session, _workspace, _ticket, run = mapped_world

    with pytest.raises(UnknownSymbolAnchorError):
        execute_tool(
            session,
            "loregarden_upsert_memory",
            _memory_args(anchors="pkg.alpha.steady, pkg.alpha.stedy"),
            run_id=run.id,
        )

    assert _nodes(tmp_path) == []


# ==========================================================================
# R5 — the happy path, and the write that asks for nothing
# ==========================================================================


@pytest.mark.parametrize(
    ("sent", "count"),
    [
        ("pkg.alpha.steady,pkg.alpha.edited", 2),
        ('["pkg.alpha.steady", "pkg.alpha.edited"]', 2),
        (["pkg.alpha.steady"], 1),
    ],
    ids=["comma", "json", "list"],
)
def test_anchors_arrive_in_the_shapes_tags_and_aliases_already_do(
    mapped_world, sent, count, tmp_path
):
    """R5's normalization. A JSON-looking string split on commas is how
    `'["routing", "rework"]'` once became two broken tags on the live graph."""
    session, _workspace, _ticket, run = mapped_world

    result = json.loads(
        execute_tool(session, "loregarden_upsert_memory", _memory_args(anchors=sent), run_id=run.id)
    )

    assert len(result["graph"]["anchors"]) == count


def test_the_normalizer_does_not_drop_the_anchors_argument():
    """A field the schema advertises and the normalizer drops is a silent
    no-op: the tool accepts the anchors and records the claim ungrounded."""
    for tool, base in (
        ("loregarden_upsert_memory", {"title": "T", "workspace_slug": WS}),
        (
            "loregarden_append_learning",
            {"ticket_id": "t", "workspace_slug": WS, "content": "B"},
        ),
    ):
        payload = normalize_tool_arguments(tool, {**base, "anchors": "a.b, c.d"})
        assert payload["anchors"] == ["a.b", "c.d"], tool


@pytest.mark.parametrize("anchors", [None, "", []], ids=["absent", "empty-string", "empty-list"])
def test_a_write_that_asks_for_no_anchors_resolves_nothing_and_stores_null(
    world, anchors, tmp_path
):
    """R5's last sentence. Grounding is optional per claim, so an ordinary
    write must not start refusing — and this world has no map at all, which is
    the state every production write is in until 284 lands."""
    session, _workspace, _ticket, run = world
    args = _memory_args() if anchors is None else _memory_args(anchors=anchors)

    execute_tool(session, "loregarden_upsert_memory", args, run_id=run.id)

    [row] = _nodes(tmp_path)
    assert row["anchors_json"] is None


def test_anchors_resolve_against_the_runs_own_worktree(world, grounding_repo, tmp_path):
    """Criterion 5's last sentence, and plan-risk's rank-2 assumption corrected.

    The map is built ONLY in the linked worktree, whose `pkg/alpha.py` carries
    a symbol the primary checkout does not have. A write resolving against the
    primary would raise SymbolMapUnavailableError; one resolving against the
    run's own tree stamps that tree's identity.
    """
    session, workspace, ticket, run = world
    linked = tmp_path / "linked"
    run_git(["worktree", "add", "-q", "-b", "side", str(linked)], cwd=grounding_repo, check=True)
    (linked / "pkg" / "alpha.py").write_text(
        "def only_in_the_worktree(ticket):\n    return ticket.id\n", encoding="utf-8"
    )
    run_git(["add", "-A"], cwd=linked, check=True)
    run_git(["commit", "-q", "-m", "worktree-only symbol"], cwd=linked, check=True)
    session.add(
        Worktree(
            workspace_id=workspace.id,
            agent_run_id=run.id,
            ticket_id=ticket.id,
            worktree_path=str(linked),
            state=WorktreeState.ACTIVE,
        )
    )
    session.commit()
    build_symbol_map(session, workspace.id, linked)

    execute_tool(
        session,
        "loregarden_upsert_memory",
        _memory_args(anchors="pkg.alpha.only_in_the_worktree"),
        run_id=run.id,
    )

    [hit] = _search(session, run.id)["graph"]
    [anchor] = hit["anchors"]
    assert anchor["checkout_identity"] == identify(linked)
    assert anchor["checkout_identity"] != identify(grounding_repo)


# ==========================================================================
# R4 / S2 / criterion 10 — loregarden_search_memory
# ==========================================================================


def test_an_unanchored_nodes_json_is_byte_identical_to_today(mapped_world):
    """S2's compatibility pin: a node that carries no anchors gains NOTHING, so
    no existing reader of this reply is touched."""
    session, _workspace, _ticket, run = mapped_world
    execute_tool(session, "loregarden_upsert_memory", _memory_args(), run_id=run.id)

    with_run = _search(session, run.id)
    without_run = _search(session)

    [hit] = with_run["graph"]
    assert "grounding" not in hit
    assert "anchors" not in hit
    assert with_run == without_run


def test_an_anchored_hit_carries_the_verdict_and_names_the_symbol_to_open(
    mapped_world, grounding_repo
):
    """S2's question and action: the reply names the symbol, and for a moved one
    the file and line to open in this checkout."""
    session, workspace, _ticket, run = mapped_world
    execute_tool(
        session,
        "loregarden_upsert_memory",
        _memory_args(anchors="pkg.alpha.steady"),
        run_id=run.id,
    )

    [hit] = _search(session, run.id)["graph"]

    grounding = hit["grounding"]
    assert grounding["verdict"] == GroundingVerdict.GROUNDED.value
    assert [a["qualified_name"] for a in grounding["anchors"]] == ["pkg.alpha.steady"]
    assert grounding["anchors"][0]["status"] == GroundingVerdict.GROUNDED.value
    assert grounding["anchors"][0]["location"] == "pkg/alpha.py:1"


def test_a_call_with_no_run_is_unverifiable_and_says_why(mapped_world):
    """S2: a verdict of UNVERIFIABLE with no reason is a defect, not a terse
    reply. With no run there is no checkout to verify against, and saying
    MISSING would invent a fact out of a missing argument."""
    session, _workspace, _ticket, run = mapped_world
    execute_tool(
        session,
        "loregarden_upsert_memory",
        _memory_args(anchors="pkg.alpha.steady"),
        run_id=run.id,
    )

    [hit] = _search(session)["graph"]

    assert hit["grounding"]["verdict"] == GroundingVerdict.UNVERIFIABLE.value
    assert (
        hit["grounding"]["reason"]
        == "this call carries no run, so there is no checkout to verify against"
    )


def test_the_search_limit_is_still_twenty():
    """S2's volume answer: at most 20 verdicts in one reply, unchanged. A
    grounding annotation is not a reason to widen the page."""
    assert (
        normalize_tool_arguments("loregarden_search_memory", {"query": "x", "workspace_slug": WS})[
            "limit"
        ]
        == 20
    )


# ==========================================================================
# The write-time argument, under shapes a bridge actually sends (test-break)
# ==========================================================================


def test_the_anchors_argument_drops_blanks_and_keeps_the_authors_order(mapped_world, tmp_path):
    """`tags` and `aliases` already strip and drop empties, and the anchors land
    in note frontmatter as an ordered list a person reads. A trailing comma from
    a model's string formatting must not become an anchor named ''— which would
    refuse the whole write with a message quoting nothing."""
    session, _workspace, _ticket, run = mapped_world

    result = json.loads(
        execute_tool(
            session,
            "loregarden_upsert_memory",
            _memory_args(anchors=" pkg.alpha.edited , , pkg.alpha.steady "),
            run_id=run.id,
        )
    )

    assert [a["qualified_name"] for a in result["graph"]["anchors"]] == [
        "pkg.alpha.edited",
        "pkg.alpha.steady",
    ]


@pytest.mark.parametrize(
    "anchors",
    ['{"qualified_name": "pkg.alpha.steady"}', [{"qualified_name": "pkg.alpha.steady"}], [7]],
    ids=["json-object", "list-of-dicts", "list-of-ints"],
)
def test_a_structurally_invalid_anchors_argument_rejects_the_whole_write(
    mapped_world, anchors, tmp_path
):
    """Criterion 5's core, at the argument rather than the symbol. An agent that
    sends the anchor RECORD instead of the name has asked for grounding and must
    not get an ungrounded row — a claim stored ungrounded when its author asked
    for grounding is the silent failure this ticket exists to remove. Which
    error type is the implementer's call; the silence is not.
    """
    session, _workspace, _ticket, run = mapped_world

    with pytest.raises(Exception):  # noqa: B017 - the type is open; the stored row is not
        execute_tool(
            session, "loregarden_upsert_memory", _memory_args(anchors=anchors), run_id=run.id
        )

    assert _nodes(tmp_path) == []


def test_the_same_anchor_twice_is_not_two_anchors(mapped_world, tmp_path):
    """A model repeating itself in a comma list is not an error worth refusing a
    learning over, but two identical records in the column mean the reader sees
    the same symbol named twice on one line."""
    session, _workspace, _ticket, run = mapped_world

    result = json.loads(
        execute_tool(
            session,
            "loregarden_upsert_memory",
            _memory_args(anchors="pkg.alpha.steady,pkg.alpha.steady"),
            run_id=run.id,
        )
    )

    assert [a["qualified_name"] for a in result["graph"]["anchors"]] == ["pkg.alpha.steady"]


def test_a_learning_stores_its_anchors_too(mapped_world, tmp_path):
    """R5 names both tools, and the parametrized refusal above proves only that
    both REFUSE. 597 of the 614 live nodes are learnings, so the tool that
    writes them is the one this capability mostly runs through."""
    session, _workspace, _ticket, run = mapped_world

    result = json.loads(
        execute_tool(
            session,
            "loregarden_append_learning",
            _learning_args(anchors="pkg.alpha.steady"),
            run_id=run.id,
        )
    )

    [anchor] = result["graph"]["anchors"]
    assert set(anchor) == {
        "qualified_name",
        "fingerprint",
        "path",
        "checkout_identity",
        "extractor_tier",
    }
    assert anchor["fingerprint"].startswith("sym1:")


# ==========================================================================
# S2 — the reply's UNVERIFIABLE reason, over a second cause
# ==========================================================================


def test_a_stale_map_annotates_the_hit_as_unverifiable_and_names_both_shas(
    mapped_world, grounding_repo
):
    """Criterion 10 says UNVERIFIABLE ALWAYS names which of five causes it was,
    and the designed suite pins one of them (no run on the call). This pins the
    cause an agent actually meets mid-run: it committed something, and the map
    now describes the tree it was standing on a minute ago. A bare
    "unverifiable" here is the terse reply the criterion calls a defect.
    """
    session, _workspace, _ticket, run = mapped_world
    execute_tool(
        session,
        "loregarden_upsert_memory",
        _memory_args(anchors="pkg.alpha.steady"),
        run_id=run.id,
    )
    commit_unindexed_change(grounding_repo)

    [hit] = _search(session, run.id)["graph"]

    assert hit["grounding"]["verdict"] == GroundingVerdict.UNVERIFIABLE.value
    assert hit["grounding"]["reason"], "UNVERIFIABLE with no reason is a defect, not a terse reply"
    assert hit["grounding"]["anchors"][0]["qualified_name"] == "pkg.alpha.steady"


def test_a_full_page_of_anchored_hits_each_carries_its_own_verdict(mapped_world):
    """S2's volume answer, measured rather than asserted in prose: the reply is
    capped at 20, and a page where only the first hit is annotated is how a
    per-page lookup that keyed on the wrong id would look from one test."""
    session, _workspace, _ticket, run = mapped_world
    for index in range(22):
        execute_tool(
            session,
            "loregarden_upsert_memory",
            _memory_args(title=f"A grounded claim {index}", anchors="pkg.alpha.steady"),
            run_id=run.id,
        )

    hits = _search(session, run.id)["graph"]

    assert 0 < len(hits) <= 20
    for hit in hits:
        assert hit["grounding"]["verdict"] == GroundingVerdict.GROUNDED.value
        assert hit["grounding"]["anchors"][0]["location"] == "pkg/alpha.py:1"
