"""What the AC13 pins do not say, and a wrong cap would satisfy.

`tests/test_memory_term_cap.py` pins the bound itself: that it exists, that it
is a named constant with a margin, that its drops travel into
`partial["terms"]`, and that the predicate count reaching SQLite is bounded.
Every one of those is read at 5,000 terms, which is where today's
`sqlite3.OperationalError` lives.

This file is the adversarial half. Each test here is a shape a cap can have
that turns that whole file green while the reduction is still wrong:

- **It binds a term too late or too early.** 5,000 says nothing about the
  boundary, and an implementation written with `>` where it meant `>=` drops
  one term from a question that fitted, or keeps one from a question that did
  not. Only `cap` and `cap + 1` can tell.
- **It is applied in the ceiling's branch.** `select_terms` returns early when
  no corpus count exists, so a cap written after that return binds the memory
  leg and leaves prior_work's path unbounded — and a cap written before it
  binds prior_work and leaves the leg that actually raises. The pinned file
  checks both call shapes in one test; this one checks that the two policies
  are still *distinguishable* when both fire, because a cap that relabels a
  ceiling drop has destroyed the reason AC13(c) exists to carry.
- **It slices a set.** `terms()` returns a `set`, so `list(built)[:cap]` and
  `sorted(built, key=len, reverse=True)[:cap]` with ties are not the same
  question twice: the same agent asking the same thing in two processes gets
  two different answers and two different `partial["terms"]`. Nothing in the
  pinned file runs twice, so nothing there can see it.
- **It keeps one long term and then stops caring.** The pinned longest-term
  test plants a single unique-length term among equal-length filler; keeping
  that one and slicing the rest arbitrarily passes it. The property is
  monotone: nothing longer than the shortest kept term may be dropped.
- **It accounts in raw tokens rather than terms.** A question repeating one
  word 5,000 times asks for one term and loses nothing; a cap counting words
  reports 4,999 drops the agent never made.
- **It lives on the graph leg's path only.** A vault-only install never
  reaches SQL, never raises, and must still report the same reduced question —
  the bound is a property of the question, not of the store that happened to
  be configured.

Where a test here is green against today's code, it says so in its docstring:
today nothing is dropped at all, so a property about *which* terms survive
cannot fail yet. Those are mutation guards, and each was checked against a
prototype cap before it was kept.
"""

from __future__ import annotations

import json
import subprocess
import sys
from unittest.mock import patch

from loregarden.models.domain.enums import MemoryStoreKind, MemoryStoreState
from loregarden.services.memory_store import AgentMemoryService, ObsidianMemoryStore
from loregarden.services.term_overlap import DEFAULT_FLOOR, select_terms, terms
from tests.mcp_helpers import call_mcp
from tests.test_memory_term_cap import HUGE, _cap, _query, _service

#: A scanned corpus big enough for the AC8 ceiling to run at all
#: (`MIN_CEILING_CORPUS` is 20) and round enough that a fifth of it is exact.
SCANNED = 1_000


def _frequencies(common: list[str], rare: list[str]) -> dict[str, int]:
    """Document frequencies that put `common` over the ceiling and `rare` far
    under it, on a corpus of `SCANNED` scanned rows."""
    return {**dict.fromkeys(common, SCANNED // 2), **dict.fromkeys(rare, 1)}


# --- the boundary, which 5,000 cannot see ------------------------------------


def test_a_question_of_exactly_the_cap_loses_nothing():
    """The bound is a ceiling, not a target.

    A cap written `if len(kept) >= cap` drops a term from a question that
    fitted exactly, and reports a reduction that did not happen. At 5,000
    terms that is invisible: 4,936 drops and 4,937 drops both read as "the
    cap bound it".
    """
    cap = _cap()
    selection = select_terms(_query(cap))

    assert len(selection.kept) == cap
    assert selection.dropped == {}, (
        "a question that fits under the bound was reduced anyway; the drops a "
        "reply reports are the ones an agent will go looking for"
    )


def test_one_term_over_the_cap_drops_exactly_one_term():
    """The other side of the same off-by-one.

    `cap + 1` is the only length at which a bound that is one too generous
    still answers — and, on a SQLite built with a lower `SQLITE_MAX_EXPR_DEPTH`
    than this machine's, the one at which it stops.
    """
    cap = _cap()
    query = _query(cap + 1)

    selection = select_terms(query)

    assert len(selection.kept) == cap
    assert len(selection.dropped) == 1
    assert selection.kept | set(selection.dropped) == terms(query), (
        "the one term that did not fit must be named, not simply absent"
    )


# --- the two policies, when both of them fire --------------------------------


def test_the_ceiling_and_the_cap_both_bind_and_say_which_one_dropped_a_term():
    """AC8's ceiling and AC13's cap on one call.

    prior_work reaches `select_terms` with corpus counts, so a question long
    enough to hit the cap can also carry terms common enough to hit the
    ceiling. Two policies, two different things an agent can do about it: a
    ceiling drop means the word separates nothing and a shorter question would
    not help, a cap drop means the question was too long and asking the rest
    again would. One shared reason string collapses that into "something was
    dropped", which is the shape this ticket exists to remove.
    """
    cap = _cap()
    tokens = sorted(terms(_query()))
    common, rare = tokens[:100], tokens[100:]

    selection = select_terms(
        " ".join(tokens),
        document_frequency=_frequencies(common, rare),
        corpus_total=SCANNED,
    )

    assert selection.ceiling_applied is True
    assert len(selection.kept) <= cap, (
        "the cap must bind on the corpus-counted path too; a bound written "
        "after `select_terms`' early return leaves prior_work unbounded"
    )
    assert not (selection.kept & set(common)), (
        "a term the ceiling rejected as matching more than a fifth of the "
        "corpus must not be kept because the cap happened to reach it first"
    )
    ceiling_reasons = {selection.dropped[term] for term in common}
    cap_dropped = sorted(set(rare) - selection.kept)
    assert cap_dropped, "nothing was cap-dropped, so this test proves nothing"
    cap_reasons = {selection.dropped[term] for term in cap_dropped}
    assert not (ceiling_reasons & cap_reasons), (
        "a ceiling drop and a cap drop read identically; the agent cannot tell "
        "a word that separates nothing from a question that was too long"
    )
    assert all(str(cap) in reason for reason in cap_reasons)


def test_the_floor_still_fires_when_the_cap_is_in_force():
    """A question written entirely out of common words keeps its rarest terms.

    Green today, because no cap exists to interact with the floor. It fails
    against a cap applied *after* the floor on a term set the ceiling had
    already emptied — the floor's two survivors are the whole question, and a
    bound that re-drops them answers nothing while reporting that it narrowed.
    """
    tokens = sorted(terms(_query()))

    selection = select_terms(
        " ".join(tokens),
        document_frequency=dict.fromkeys(tokens, SCANNED // 2),
        corpus_total=SCANNED,
    )

    assert len(selection.kept) == DEFAULT_FLOOR
    assert selection.ceiling_applied is True
    assert not (selection.kept & set(selection.dropped)), (
        "a term cannot be both the answer and the loss"
    )


# --- which terms survive, not how many ---------------------------------------


def test_nothing_longer_than_the_shortest_kept_term_is_dropped():
    """AC13(b)'s "keeps the longest" as the monotone property it is.

    The pinned test plants one unique-length term among equal-length filler,
    so `{longest} | arbitrary_slice(rest)` passes it. Here the lengths are
    graded: if any dropped term is longer than something kept, the policy is
    not length at all, and the question was narrowed by whatever order a set
    happened to iterate in.
    """
    cap = _cap()
    graded = [("z" * length) + f"{n:04d}" for length in range(4, 40) for n in range(cap)]
    selection = select_terms(" ".join(graded))

    assert len(selection.kept) == cap
    shortest_kept = min(len(term) for term in selection.kept)
    too_long_to_drop = {term for term in selection.dropped if len(term) > shortest_kept}
    assert not too_long_to_drop, (
        f"{len(too_long_to_drop)} dropped terms are longer than the shortest "
        "kept one; length is the stated proxy for rarity on a leg with no "
        "corpus count, and a slice is not that proxy"
    )


def test_the_same_question_asked_in_two_processes_keeps_the_same_terms():
    """Determinism across processes, which one process cannot observe.

    `terms()` returns a `set`, and a cap that slices one — directly, or
    through a sort whose key ties — depends on `PYTHONHASHSEED`. The same
    agent asking the same thing twice then gets two different answers and two
    different `partial["terms"]`, and no test that runs in a single
    interpreter can see it.

    Green today: nothing is dropped, so every seed keeps all 5,000. It is red
    against `list(built)[:cap]`, which is the shortest way to write this cap.
    """
    script = (
        "from loregarden.services.term_overlap import select_terms\n"
        "import sys\n"
        f"print(' '.join(sorted(select_terms({_query(300)!r}).kept)))\n"
    )
    answers = {
        seed: subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            check=True,
            env={"PYTHONHASHSEED": seed, "PATH": "/usr/bin:/bin"},
        ).stdout.strip()
        for seed in ("0", "1", "12345")
    }

    assert len(set(answers.values())) == 1, (
        "the kept set differs between hash seeds: the same question asked "
        f"twice narrows to different terms ({ {k: len(v.split()) for k, v in answers.items()} })"
    )


# --- accounting: terms, not words --------------------------------------------


def test_a_question_that_repeats_one_word_loses_nothing():
    """5,000 words is not 5,000 terms.

    Green today. It fails against a cap that bounds the tokenised *list*
    before de-duplication, which reports 4,999 drops for a question that asked
    for exactly one thing — a reply telling an agent its question was
    truncated when every word of it was used.
    """
    selection = select_terms(" ".join(["retrievalterm"] * HUGE))

    assert selection.kept == {"retrievalterm"}
    assert selection.dropped == {}


def test_kept_and_dropped_partition_the_tokenised_question():
    """Every term is either asked with or explained, never neither.

    Case and repetition included, because `terms()` lowercases and de-dupes:
    a cap accounting on raw tokens double-counts `Wordaa00001` and
    `wordaa00001`, and a cap that silently discards is the exact failure
    AC13(c) names.
    """
    query = " ".join([_query(400), _query(400).upper(), _query(400)])
    selection = select_terms(query)

    assert selection.kept | set(selection.dropped) == terms(query)
    assert not (selection.kept & set(selection.dropped))


# --- the bound is a property of the question, not of the store ---------------


def test_a_vault_only_install_reports_the_same_reduction(tmp_path):
    """No graph is configured, so nothing can raise — and the question is
    still too long to ask whole.

    A cap implemented on the graph leg's path (or guarded by "only when the
    graph is READ") leaves this install asking 5,000 terms of a vault scan and
    reporting a question it did not ask. The leg state is the thing that must
    differ between installs; the reduced question is not.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    service = AgentMemoryService(
        obsidian=ObsidianMemoryStore(vault),
        graph_sqlite_base=tmp_path / "nowhere" / "memory.db",
    )
    service.obsidian.upsert_blog_post(
        ticket_id="t-1", workspace_slug="loregarden", title="wordaa00000 post", body="b"
    )

    answer = service.search(_query(), workspace_slug="loregarden", limit=5)

    assert answer["basis"].legs[MemoryStoreKind.GRAPH.value] is MemoryStoreState.UNCONFIGURED
    assert answer["basis"].dropped_terms, (
        "the vault leg searched the whole 5,000-term question; the bound "
        "belongs to the question, not to whichever store was wired up"
    )
    assert len(answer["basis"].dropped_terms) == HUGE - _cap()


def test_a_question_under_the_cap_carries_no_terms_entry_in_partial(client, tmp_path):
    """The negative direction through the real transport.

    `partial` is a list of what is missing, so an entry that is present and
    empty says a reduction happened. Without this, a cap that always reported
    `terms: {}` would satisfy every positive assertion in the pinned file.
    """
    service = _service(tmp_path)
    service.require_graph("loregarden").upsert_node(
        title="retrievalterm node", workspace_slug="loregarden", node_type="memory"
    )

    with patch.object(AgentMemoryService, "from_settings", return_value=service):
        response = call_mcp(
            client,
            "loregarden_search_memory",
            {"query": "retrievalterm anotherterm", "workspace_slug": "loregarden", "limit": 5},
        )

    payload = json.loads(response["result"]["content"][0]["text"])
    assert "terms" not in payload.get("partial", {}), (
        "a question that was asked whole must not advertise a reduction"
    )
    assert payload["basis"]["dropped_terms"] == {}


def test_a_long_question_with_no_allowlisted_word_answers_without_a_reduction(client, tmp_path):
    """5,000 tokens the allowlist removes entirely.

    The empty end of the same axis: nothing survives tokenisation, so the cap
    has nothing to bind and must not invent a drop, and the reply must stay an
    ordinary answer rather than becoming an error about an empty predicate.
    """
    service = _service(tmp_path)
    service.require_graph("loregarden").upsert_node(
        title="retrievalterm node", workspace_slug="loregarden", node_type="memory"
    )
    query = " ".join(["ко"] * 2_500 + ["日本語"] * 2_500)

    with patch.object(AgentMemoryService, "from_settings", return_value=service):
        response = call_mcp(
            client,
            "loregarden_search_memory",
            {"query": query, "workspace_slug": "loregarden", "limit": 5},
        )

    assert "isError" not in response["result"], response
    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload["graph"] == []
    assert payload["basis"]["dropped_terms"] == {}
    assert payload["basis"]["emptiness"], (
        "an empty answer to a question that tokenised to nothing must say so"
    )
