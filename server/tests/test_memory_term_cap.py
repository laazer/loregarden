"""A long agent query is bounded before it reaches SQL, and says what it lost
(AC13).

`term_overlap.select_terms` puts no ceiling on the memory leg — the AC8 ceiling
needs a corpus count the graph leg does not have — so today every tokenised
word of an agent's question survives, and `memory_agent_search.graph_rows_for_terms`
appends one four-predicate `OR` group per surviving term. Measured on this
branch: 995 terms search fine, 996 raises

    sqlite3.OperationalError: Expression tree is too large (maximum depth 1000)

in about 0.01s. AC4 is correct to refuse to shape an `OperationalError` — a
genuine fault must not be reassured away — so that message reaches the agent as
a bare `isError` with no remedy and no basis. That is this ticket's own failure
class, arriving through this ticket's own new code.

What these tests pin, and why each is a separate fact:

- **The cap is where the terms are SELECTED**, not inside the graph leg. A cap
  applied in `graph_rows_for_terms` would leave the vault leg searching on the
  full term set, and the two legs would then disagree about what was asked.
  `test_both_legs_search_the_same_reduced_question` is the one that fails if
  the cap is pushed down into the leg.
- **The number is a constant with a reason**, and sits well under 995. The
  depth limit is a SQLite *build* constant, not a guarantee, so a cap chosen
  at 990 would be green here and red on another machine.
- **The drops travel the route AC8's ceiling drops already take** —
  `TermSelection.dropped` -> `basis.dropped_terms` -> `partial["terms"]`. A cap
  that drops terms silently fails AC13(c) by this ticket's own argument.
- **The predicate count is bounded regardless of query length**, asserted on
  the SQL that is actually executed rather than on the term count, so a later
  change to the predicate shape (five columns instead of four, a different
  grouping) cannot quietly re-open the depth ceiling.

Volume is the point of three of these: they run at 5,000 distinct allowlisted
words, which is where the defect lives, and not on a three-word fixture, where
every one of them would pass against the current code.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from loregarden.services import term_overlap
from loregarden.services.memory_agent_search import graph_rows_for_terms
from loregarden.services.memory_store import AgentMemoryService, ObsidianMemoryStore
from loregarden.services.term_overlap import select_terms
from tests.mcp_helpers import call_mcp

#: Far past the measured breaking point of 996, and past any plausible cap, so
#: a test at this volume is reading the cap rather than the SQLite build.
HUGE = 5_000

#: The SQLite expression-depth limit measured on this branch. Named here so the
#: margin assertions below say what they are a margin *against*.
MEASURED_DEPTH_BREAK = 996


def _query(count: int = HUGE, *, prefix: str = "wordaa") -> str:
    """`count` distinct allowlisted tokens: >= 4 chars, no stopword, no
    metacharacter the allowlist would have stripped anyway."""
    return " ".join(f"{prefix}{n:05d}" for n in range(count))


def _service(tmp_path, *, slug: str = "loregarden"):
    vault = tmp_path / "vault"
    vault.mkdir()
    store = ObsidianMemoryStore(vault)
    base = tmp_path / "Loregarden" / "memory.db"
    base.parent.mkdir(parents=True)
    service = AgentMemoryService(obsidian=store, graph_sqlite_base=base)
    service.require_graph(slug)
    return service


def _declared_cap() -> int | None:
    """The cap as a named constant in `term_overlap`, or None if there is
    none. AC13(b) requires the constant; the behavioural tests below do not
    depend on its name, so discovery failing here must not make them stop
    discriminating."""
    candidates = {
        name: getattr(term_overlap, name)
        for name in dir(term_overlap)
        if not name.startswith("_")
        and name.isupper()
        and "TERM" in name
        and any(word in name for word in ("MAX", "CAP", "LIMIT"))
        and isinstance(getattr(term_overlap, name), int)
    }
    caps = {name: value for name, value in candidates.items() if 1 < value < MEASURED_DEPTH_BREAK}
    assert len(caps) <= 1, f"exactly one term cap constant, found {caps}"
    return next(iter(caps.values()), None)


def _cap() -> int:
    """The bound actually in force, declared or observed.

    Falls back to measuring `select_terms` so that a cap named something this
    file did not guess still reads correctly, and so that today's red is the
    *absence of a bound* rather than the absence of a name.
    """
    declared = _declared_cap()
    if declared is not None:
        return declared
    observed = len(select_terms(_query()).kept)
    assert observed < HUGE, (
        "AC13: select_terms kept all 5,000 terms — nothing bounds an agent's "
        "question before it becomes one OR group per term in SQL"
    )
    return observed


# --- AC13(b): the constant, and its margin -----------------------------------


def test_the_cap_is_a_named_constant_with_a_real_margin_under_the_depth_limit():
    """995 is a SQLite build constant, not a contract.

    A cap of 990 passes every behavioural test in this file on this machine
    and raises on a SQLite built with a lower `SQLITE_MAX_EXPR_DEPTH`. The
    criterion says "well under 995"; at least a factor of two is what makes
    that checkable rather than a feeling.
    """
    declared = _declared_cap()

    assert declared is not None, (
        "AC13(b): the cap is a named constant in services/term_overlap.py with "
        "its reason in a docstring, not a number spelled at a call site"
    )
    cap = declared
    assert cap >= 1, "a cap of zero would make every query ask nothing"
    assert cap <= MEASURED_DEPTH_BREAK // 2, (
        f"cap {cap} leaves less than a 2x margin against the measured depth break "
        f"at {MEASURED_DEPTH_BREAK}; that limit is a build constant, so a thin "
        "margin is green here and red on another machine"
    )


def test_select_terms_bounds_the_kept_set_whatever_the_query_length():
    """AC13(a): the bound is at selection, and it binds on both call paths.

    Asserted with and without `document_frequency`, because the two legs reach
    this function differently: prior_work passes corpus counts and runs the
    AC8 ceiling, the memory leg passes none and runs no ceiling at all. A cap
    written inside the ceiling branch would leave the memory leg — the leg
    that actually raises — unbounded.
    """
    cap = _cap()
    query = _query()

    no_counts = select_terms(query)
    assert len(no_counts.kept) <= cap
    assert no_counts.ceiling_applied is False, (
        "the memory leg still has no corpus count; a cap must not fake one"
    )

    tokens = term_overlap.terms(query)
    with_counts = select_terms(
        query,
        document_frequency=dict.fromkeys(tokens, 1),
        corpus_total=10_000,
    )
    assert len(with_counts.kept) <= cap
    assert with_counts.kept, "a bound is not a deletion: the query still asks something"


def test_the_cap_keeps_the_longest_terms_rather_than_an_arbitrary_slice():
    """AC13(b): "it keeps the rarest/longest terms".

    No corpus count exists on the memory leg, so rarity is unavailable and
    length is the standing proxy. The failure this refuses is a cap
    implemented as `sorted(terms)[:cap]`, which asks with whatever the
    alphabet put first and is indistinguishable from a bound that works until
    somebody reads the answers.
    """
    cap = _cap()
    distinctive = "thisisadeliberatelyverylongdistinctiveterm"
    query = f"{distinctive} {_query(cap * 2, prefix='aaaa')}"

    kept = select_terms(query).kept

    assert len(kept) <= cap
    assert distinctive in kept, (
        "the longest term in the question is the most discriminative thing it "
        "has; dropping it while keeping 64 near-identical short tokens is a "
        "bound that answers a different question"
    )


# --- AC13(c): the drops travel the existing route ----------------------------


def test_every_term_the_cap_dropped_carries_a_reason():
    """A cap that drops terms silently fails this criterion by the ticket's
    own argument: the first question an empty-ish answer raises is whether the
    query was used at all."""
    cap = _cap()
    selection = select_terms(_query())

    assert len(selection.dropped) == HUGE - len(selection.kept)
    assert len(selection.kept) == cap, (
        "a query of 5,000 distinct terms should be reduced to exactly the cap, "
        "not to some smaller number nothing explains"
    )
    for term, reason in selection.dropped.items():
        assert reason.strip(), f"{term} was dropped with no reason"
    reasons = set(selection.dropped.values())
    assert len(reasons) <= 2, (
        "cap drops share one reason; 5,000 bespoke strings would be a per-term "
        "message rather than a stated policy"
    )
    assert any(str(cap) in reason for reason in reasons), (
        "the reason names the bound the agent hit, so the drop is explicable "
        "rather than merely declared"
    )


def test_a_query_under_the_cap_reports_no_cap_drops():
    """The negative direction, so the pin above can actually fail.

    Without this, a `select_terms` that reported every term as dropped would
    satisfy every positive assertion here.
    """
    selection = select_terms("retrievalterm anotherterm thirdterm")

    assert selection.kept == {"retrievalterm", "anotherterm", "thirdterm"}
    assert selection.dropped == {}


# --- AC13: the whole path, at volume, where an agent meets it ----------------


def test_a_five_thousand_word_search_answers_instead_of_raising(client, tmp_path):
    """`loregarden_search_memory` at the volume that raises today.

    Today this returns `isError: true` carrying
    "Expression tree is too large (maximum depth 1000)" — an internal SQLite
    message with no remedy, reaching an agent through the path this ticket
    added. The reply must instead be an ordinary answer: the hits the reduced
    question found, and a basis saying what the question lost.
    """
    service = _service(tmp_path)
    graph = service.require_graph("loregarden")
    graph.upsert_node(title="wordaa00000 node", workspace_slug="loregarden", node_type="memory")

    with patch.object(AgentMemoryService, "from_settings", return_value=service):
        response = call_mcp(
            client,
            "loregarden_search_memory",
            {"query": _query(), "workspace_slug": "loregarden", "limit": 5},
        )

    assert "isError" not in response["result"], response
    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload["basis"], "a reply with no basis is the shape this ticket removes"


def test_the_dropped_terms_reach_the_agent_in_partial_terms(client, tmp_path):
    """AC13(c): the same route AC8's ceiling drops already take.

    `TermSelection.dropped` -> `basis.dropped_terms` -> `partial["terms"]`, as
    key -> VALUE. Naming only the keys would discard the reason the producer
    already holds, which is what AC10(b) refuses.
    """
    cap = _cap()
    service = _service(tmp_path)
    service.require_graph("loregarden").upsert_node(
        title="wordaa00000 node", workspace_slug="loregarden", node_type="memory"
    )

    with patch.object(AgentMemoryService, "from_settings", return_value=service):
        response = call_mcp(
            client,
            "loregarden_search_memory",
            {"query": _query(), "workspace_slug": "loregarden", "limit": 5},
        )

    payload = json.loads(response["result"]["content"][0]["text"])
    dropped = payload["partial"]["terms"]
    assert isinstance(dropped, dict), "key -> VALUE, never a list of key names"
    assert len(dropped) == HUGE - cap
    assert all(str(reason).strip() for reason in dropped.values())
    assert payload["basis"]["dropped_terms"] == dropped, (
        "one answer, carried in both places rather than computed twice"
    )


def test_both_legs_search_the_same_reduced_question(tmp_path):
    """AC13(a): the cap sits at selection, so neither leg sees a term the
    other does not.

    Built from `select_terms`' own answer rather than from a guess about which
    terms survive, so it holds whatever ordering policy the implementer picks.
    A cap pushed down into `graph_rows_for_terms` fails here: the vault leg
    would go on matching a dropped term.
    """
    query = _query()
    selection = select_terms(query)
    assert selection.dropped, (
        "AC13: nothing was dropped from a 5,000-term question, so there is no "
        "reduced question for the two legs to agree on"
    )
    kept = sorted(selection.kept)[0]
    dropped = sorted(selection.dropped)[0]

    service = _service(tmp_path)
    graph = service.require_graph("loregarden")
    graph.upsert_node(title=f"{kept} node", workspace_slug="loregarden", node_type="memory")
    graph.upsert_node(title=f"{dropped} node", workspace_slug="loregarden", node_type="memory")
    service.obsidian.upsert_blog_post(
        ticket_id="t-kept", workspace_slug="loregarden", title=f"{kept} post", body="b"
    )
    service.obsidian.upsert_blog_post(
        ticket_id="t-dropped", workspace_slug="loregarden", title=f"{dropped} post", body="b"
    )

    answer = service.search(query, workspace_slug="loregarden", limit=20)
    titles = [hit["title"] for hit in answer["graph"]] + [
        hit["title"] for hit in answer["obsidian"]
    ]

    assert f"{kept} node" in titles and f"{kept} post" in titles, (
        "a term the selection kept must reach both legs"
    )
    assert f"{dropped} node" not in titles and f"{dropped} post" not in titles, (
        "a term the cap dropped must reach neither; one leg honouring the cap "
        "and the other not is two legs answering different questions"
    )


# --- the regression that keeps the depth ceiling shut ------------------------


class _SpyConnection:
    """Records the SQL executed and delegates everything else."""

    def __init__(self, inner, seen: list[str]) -> None:
        self._inner = inner
        self._seen = seen

    def execute(self, sql, parameters=()):  # noqa: ANN001 - sqlite3 signature
        self._seen.append(sql)
        return self._inner.execute(sql, parameters)

    def __enter__(self):
        self._inner.__enter__()
        return self

    def __exit__(self, *exc):
        return self._inner.__exit__(*exc)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def test_the_predicate_count_is_bounded_regardless_of_query_length(tmp_path):
    """The regression pin, asserted on the SQL that actually ran.

    Counting *terms* would be satisfied by a cap and then silently re-opened
    by a predicate change — five columns per term instead of four moves the
    break from 996 terms to about 800. Counting the LIKE groups in the
    executed statement is the quantity the SQLite limit is actually about, so
    this stays true across a change to either.
    """
    cap = _cap()
    service = _service(tmp_path)
    graph = service.require_graph("loregarden")
    graph.upsert_node(title="wordaa00000 node", workspace_slug="loregarden", node_type="memory")

    seen: list[str] = []
    real = graph.connection

    def spy():
        return _SpyConnection(real(), seen)

    graph.connection = spy
    rows = graph_rows_for_terms(graph, select_terms(_query()).kept, workspace_slug="loregarden")

    assert rows, "the reduced question still matched the planted node"
    statements = [sql for sql in seen if "memory_nodes" in sql and "LIKE" in sql]
    assert statements, "the graph leg ran no LIKE query at all"
    for sql in statements:
        groups = sql.count("title LIKE ?")
        assert groups <= cap, (
            f"{groups} per-term predicate groups reached SQL; the bound at "
            "selection is the only thing standing between an agent's question "
            "and SQLITE_MAX_EXPR_DEPTH"
        )
        assert sql.count("LIKE ?") <= cap * 8, (
            "the depth limit counts predicates, not terms: a wider per-term "
            "group re-opens it without changing the term count"
        )


@pytest.mark.parametrize("count", [MEASURED_DEPTH_BREAK, 2_000, HUGE])
def test_the_leg_never_raises_at_any_length_past_the_measured_break(tmp_path, count):
    """The three volumes measured on this branch, swept rather than sampled.

    996 is the first failing length and 2,000 and 5,000 are past it; all three
    raise `sqlite3.OperationalError` today. A bound that happened to work at
    5,000 and not at 996 would be arithmetic nobody checked.
    """
    service = _service(tmp_path)
    graph = service.require_graph("loregarden")
    graph.upsert_node(title="wordaa00000 node", workspace_slug="loregarden", node_type="memory")

    answer = service.search(_query(count), workspace_slug="loregarden", limit=5)

    assert [hit["title"] for hit in answer["graph"]] == ["wordaa00000 node"]
    assert answer["basis"].dropped_terms, (
        "a query this long cannot have been asked whole; the drops are what "
        "makes the reduction visible"
    )
