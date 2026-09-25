"""Prior decisions reach a stage without it having to go looking (#5)."""

from unittest.mock import Mock, patch

import pytest
from loregarden.agents.inherited_wisdom import _MAX_CHECKPOINTS, build_inherited_wisdom
from loregarden.services.memory_store import (
    AgentMemoryService,
    MemoryGraphStore,
    ObsidianMemoryStore,
)
from tests.memory_helpers import briefing_ticket as _ticket
from tests.memory_helpers import frozen_clock


def _memory(tmp_path) -> AgentMemoryService:
    return AgentMemoryService(obsidian=ObsidianMemoryStore(tmp_path), graph_sqlite_base=None)


def test_checkpoints_from_earlier_stages_are_surfaced(tmp_path):
    memory = _memory(tmp_path)
    ticket = _ticket()
    memory.append_checkpoint(
        ticket_id=ticket.external_id,
        workspace_slug="lg",
        run_id="run_1",
        entry="Chose a token bucket over a sliding window; simpler to reason about.",
    )

    text = build_inherited_wisdom(ticket, "lg", memory=memory).text
    assert "token bucket" in text
    assert "do not re-derive" in text


def test_checkpoints_are_found_under_either_identifier(tmp_path):
    """append_checkpoint slugs whatever id the caller passed, and the MCP tool
    accepts a UUID or an external id, so both spellings must resolve."""
    memory = _memory(tmp_path)
    ticket = _ticket()
    memory.append_checkpoint(
        ticket_id=ticket.id,
        workspace_slug="lg",
        run_id="run_1",
        entry="Recorded against the UUID form.",
    )
    assert "UUID form" in build_inherited_wisdom(ticket, "lg", memory=memory).text


def test_returns_empty_when_the_ticket_has_no_history(tmp_path):
    """An empty block drops out of the prompt entirely."""
    assert build_inherited_wisdom(_ticket(), "lg", memory=_memory(tmp_path)).text == ""


def test_output_is_capped(tmp_path):
    memory = _memory(tmp_path)
    ticket = _ticket()
    for i in range(12):
        memory.append_checkpoint(
            ticket_id=ticket.external_id,
            workspace_slug="lg",
            run_id=f"run_{i}",
            entry="x" * 900,
        )
    assert len(build_inherited_wisdom(ticket, "lg", memory=memory, max_chars=500).text) <= 500


def test_unreadable_memory_never_breaks_the_prompt():
    """The vault is optional and lives on synced network storage — a failure
    there must cost the section, not the run.

    The fake raises from `recall_related`, the entry point the briefing actually
    calls. It used to define `search` only: once the briefing stopped calling
    search(), the fake raised nothing, the test went green on an AttributeError,
    and the guard it exists to be stopped guarding anything.

    `== ""` alone still cannot tell those apart — `_safely` swallows an
    AttributeError from a missing method exactly as happily as the OSError this
    test is about. So the fake's entry point is a Mock and the call is asserted:
    that is what distinguishes 'the raise happened and was contained' from 'the
    briefing never got as far as raising'.
    """

    class Exploding(AgentMemoryService):
        """A real service — only its recall read is replaced.

        Subclassed rather than duck-typed: the briefing also samples
        `store_readiness`, and a bare stub would fail on the attribute rather
        than on the raise this test is about.
        """

        recall_related = Mock(side_effect=OSError("vault unavailable"))

    memory = Exploding(obsidian=None, graph_sqlite_base=None)
    assert build_inherited_wisdom(_ticket(), "lg", memory=memory).text == ""
    assert memory.recall_related.called


# ---------------------------------------------------------------------------
# R4 — the briefing queries by term overlap over title AND description.
#
# The old code passed ticket.title verbatim to a substring search, so a
# multi-word title essentially never matched and this section was dead. The
# tests below are parameterised over all three service shapes on purpose: every
# test above builds AgentMemoryService(graph_sqlite_base=None), and the graph
# half is where the live corpus mostly lives.
# ---------------------------------------------------------------------------

_REALISTIC_TITLE = "Cap how fast a trusted MCP server can be called"
_MATCHING_BODY = (
    "A throttled server returns early, so the trusted retry loop is called "
    "again before the fast path clears."
)
# The same content written the way agents actually write memory: opening with
# its own markdown heading, on top of the one `upsert_note` injects.
_HEADED_BODY = f"## Context\n{_MATCHING_BODY}"


def _obsidian_only(tmp_path) -> AgentMemoryService:
    return AgentMemoryService(
        obsidian=ObsidianMemoryStore(tmp_path / "vault"), graph_sqlite_base=None
    )


def _graph_only(tmp_path) -> AgentMemoryService:
    return AgentMemoryService(obsidian=None, graph_sqlite_base=tmp_path / "graph" / "memory.db")


def _both(tmp_path) -> AgentMemoryService:
    return AgentMemoryService(
        obsidian=ObsidianMemoryStore(tmp_path / "vault"),
        graph_sqlite_base=tmp_path / "graph" / "memory.db",
    )


_SERVICE_SHAPES = [_obsidian_only, _graph_only, _both]


@pytest.mark.parametrize("build_service", _SERVICE_SHAPES)
def test_a_learning_sharing_distinctive_terms_is_surfaced(tmp_path, build_service):
    """AC1 / AC4.1 — the regression this ticket exists for, run against
    obsidian-only, graph-only and both. The graph-only case is the one a test
    copied from the fixtures above physically cannot fail."""
    memory = build_service(tmp_path)
    memory.upsert_memory(
        title="Retry budget for throttled tools",
        body=_MATCHING_BODY,
        workspace_slug="lg",
    )

    text = build_inherited_wisdom(_ticket(title=_REALISTIC_TITLE), "lg", memory=memory).text
    assert "Retry budget for throttled tools" in text


@pytest.mark.parametrize("build_service", _SERVICE_SHAPES)
def test_the_surfaced_learning_does_not_contain_the_title_verbatim(tmp_path, build_service):
    """AC4.2 — states what the test above is a regression against. If the
    seeded note happened to contain the whole title as one contiguous
    substring, the old broken search would have found it too and the fixture
    would prove nothing."""
    memory = build_service(tmp_path)
    memory.upsert_memory(
        title="Retry budget for throttled tools",
        body=_MATCHING_BODY,
        workspace_slug="lg",
    )
    haystack = f"Retry budget for throttled tools\n{_MATCHING_BODY}".lower()
    assert _REALISTIC_TITLE.lower() not in haystack

    text = build_inherited_wisdom(_ticket(title=_REALISTIC_TITLE), "lg", memory=memory).text
    assert "Retry budget for throttled tools" in text


def test_terms_from_the_description_alone_surface_a_learning(tmp_path):
    """AC4.3 — pins that the query is title + description. Catches an
    implementation that keeps reading ticket.title only: nothing in the title
    below overlaps the seeded note."""
    memory = _both(tmp_path)
    memory.upsert_memory(
        title="Retry budget for throttled tools",
        body=_MATCHING_BODY,
        workspace_slug="lg",
    )
    ticket = _ticket(
        title="Tidy the sprite atlas",
        description="The trusted server is called again once the throttled path clears.",
    )

    assert (
        "Retry budget for throttled tools"
        in build_inherited_wisdom(ticket, "lg", memory=memory).text
    )


def test_a_short_distinctive_note_outranks_a_long_generic_one(tmp_path):
    """AC4.4 — the anti-length-bias decoy. Overlap over an unbounded body ranks
    by note LENGTH, and the live vault spans 292B to 4157B. The long note is
    also the NEWER of the two, so an unbounded implementation ties on overlap
    and then wins the recency tiebreak — presence-only assertions cannot see
    that, ordering can."""
    memory = _both(tmp_path)
    padding = "generic filler prose about nothing much at all. " * 40
    with frozen_clock("2026-01-01T00:00:00+00:00"):
        memory.upsert_memory(
            title="Retry budget for throttled tools",
            body=_MATCHING_BODY,
            workspace_slug="lg",
        )
    with frozen_clock("2026-02-01T00:00:00+00:00"):
        memory.upsert_memory(
            title="Weekly notes",
            body="server " + padding + " trusted throttled called fast",
            workspace_slug="lg",
        )

    text = build_inherited_wisdom(_ticket(title=_REALISTIC_TITLE), "lg", memory=memory).text
    assert "Weekly notes" in text
    assert text.index("Retry budget for throttled tools") < text.index("Weekly notes")


def test_a_dual_written_learning_appears_once(tmp_path):
    """AC4.5 — append_learning writes the same content to both stores under two
    different uuid4s. Without a content-keyed dedupe the same learning burns two
    of the five briefing slots."""
    memory = _both(tmp_path)
    memory.append_learning(
        ticket_id="t-01",
        workspace_slug="lg",
        content=_MATCHING_BODY,
    )

    text = build_inherited_wisdom(_ticket(title=_REALISTIC_TITLE), "lg", memory=memory).text
    assert text.count("- **Learning — t-01**") == 1


def test_a_dual_written_learning_whose_body_opens_with_a_heading_appears_once(tmp_path):
    """AC4.7 — the heading-free fixtures above cannot see this. `upsert_note`
    injects a `# <title>` line the graph copy lacks, so a dedupe that strips
    "the first heading, whatever it is" over-strips the Obsidian side of a body
    that opens with its own `## Context`: the keys diverge and one learning
    burns two of the five slots. Agent-written memory routinely opens with a
    heading."""
    memory = _both(tmp_path)
    memory.append_learning(
        ticket_id="t-02",
        workspace_slug="lg",
        content=_HEADED_BODY,
    )

    text = build_inherited_wisdom(_ticket(title=_REALISTIC_TITLE), "lg", memory=memory).text
    assert text.count("- **Learning — t-02**") == 1


def test_both_copies_of_a_dual_written_learning_read_back_identically(tmp_path):
    """The dedupe collapses two records but keeps only one of them, and which
    one is a uuid4 coin flip. That only produces stable briefings if the two
    copies carry the same body — so pin the bodies, not just the key."""
    memory = _both(tmp_path)
    memory.append_learning(ticket_id="t-02", workspace_slug="lg", content=_HEADED_BODY)

    found = memory.search("throttled", workspace_slug="lg")
    assert [n["body"] for n in found["obsidian"]] == [_HEADED_BODY]
    assert [n["body"] for n in found["graph"]] == [_HEADED_BODY]


def test_the_briefing_reads_the_same_whichever_store_the_dedupe_kept(tmp_path):
    """The surviving record must not repeat its own title inside its summary.
    Running the two single-store shapes is the deterministic way to ask this:
    against `_both` the winner is drawn by uuid4, so the defect shows up in
    only about half of the runs."""
    obsidian = _obsidian_only(tmp_path / "o")
    graph = _graph_only(tmp_path / "g")
    for memory in (obsidian, graph):
        memory.append_learning(ticket_id="t-02", workspace_slug="lg", content=_HEADED_BODY)

    ticket = _ticket(title=_REALISTIC_TITLE)
    from_obsidian = build_inherited_wisdom(ticket, "lg", memory=obsidian).text
    from_graph = build_inherited_wisdom(ticket, "lg", memory=graph).text

    assert from_obsidian == from_graph
    assert from_obsidian.count("Learning — t-02") == 1


def test_the_newer_of_two_equally_matching_notes_comes_first(tmp_path):
    """AC4.6 / AC2 — the recency tiebreak, end to end. Catches an
    implementation with no tiebreak, whose order then falls out of list_notes'
    memory-directory-first concatenation."""
    memory = _both(tmp_path)
    with frozen_clock("2026-01-01T00:00:00+00:00"):
        memory.upsert_memory(title="Older note", body=_MATCHING_BODY, workspace_slug="lg")
    with frozen_clock("2026-02-01T00:00:00+00:00"):
        memory.upsert_memory(title="Newer note", body=_MATCHING_BODY, workspace_slug="lg")

    text = build_inherited_wisdom(_ticket(title=_REALISTIC_TITLE), "lg", memory=memory).text
    assert text.index("Newer note") < text.index("Older note")


def test_an_empty_title_and_description_surface_nothing(tmp_path):
    """AC4.7 / AC4 — no query, no hits, no exception."""
    memory = _both(tmp_path)
    memory.upsert_memory(title="Retry budget", body=_MATCHING_BODY, workspace_slug="lg")

    assert build_inherited_wisdom(_ticket(title="", description=""), "lg", memory=memory).text == ""


def test_an_all_stopword_query_surfaces_nothing(tmp_path):
    """AC4.8 / AC4 — a query that tokenises to nothing must return no hits
    rather than matching everything or raising."""
    memory = _both(tmp_path)
    memory.upsert_memory(title="Retry budget", body=_MATCHING_BODY, workspace_slug="lg")
    ticket = _ticket(title="That which should be used", description="This one could also update")

    assert build_inherited_wisdom(ticket, "lg", memory=memory).text == ""


# ---------------------------------------------------------------------------
# R5 — the degradation guards, exercised over the new call path.
# ---------------------------------------------------------------------------


def test_an_unreadable_obsidian_vault_costs_only_the_section(tmp_path):
    """AC5.2 — the seeded note WOULD be surfaced, so its absence proves the
    raise really happened and _safely swallowed it. Asserting only that the
    result is a string passes even when nothing raised.

    The service here has BOTH backends, and the note is written to both, so the
    still-readable graph copy could have been returned. Its absence is deliberate,
    not an oversight: AC5.2 puts the guard at the section, so one unreadable store
    costs the whole section. Do not 'fix' this into a per-store try/except that
    lets the surviving half through — that is a different behaviour than the one
    the spec pins, and this assertion is what says so."""
    memory = _both(tmp_path)
    memory.upsert_memory(title="Retry budget", body=_MATCHING_BODY, workspace_slug="lg")

    with patch.object(ObsidianMemoryStore, "list_notes", side_effect=OSError("vault unavailable")):
        text = build_inherited_wisdom(_ticket(title=_REALISTIC_TITLE), "lg", memory=memory).text

    assert "Retry budget" not in text
    assert "### Related learnings" not in text


def test_an_unreadable_memory_graph_costs_only_the_section(tmp_path):
    """AC5.3 — the Obsidian-side guard cannot cover the graph read, which is
    new surface on a per-workspace SQLite file that may not exist yet."""
    memory = _both(tmp_path)
    memory.upsert_memory(title="Retry budget", body=_MATCHING_BODY, workspace_slug="lg")

    with patch.object(MemoryGraphStore, "list_nodes", side_effect=OSError("graph unavailable")):
        text = build_inherited_wisdom(_ticket(title=_REALISTIC_TITLE), "lg", memory=memory).text

    assert "Retry budget" not in text
    assert "### Related learnings" not in text


@pytest.mark.parametrize("build_service", _SERVICE_SHAPES)
def test_a_discredited_learning_is_absent_from_the_briefing(tmp_path, build_service):
    """A node marked wrong must not flow into inherited wisdom, on every store shape."""
    memory = build_service(tmp_path)
    created = memory.upsert_memory(
        title="Retry budget for throttled tools",
        body=_MATCHING_BODY,
        workspace_slug="lg",
    )
    node_id = (created.get("graph") or created["obsidian"])["id"]
    memory.upsert_memory(
        node_id=node_id,
        title="Retry budget for throttled tools",
        body=_MATCHING_BODY,
        workspace_slug="lg",
        discredited=True,
    )

    text = build_inherited_wisdom(_ticket(title=_REALISTIC_TITLE), "lg", memory=memory).text
    assert "Retry budget for throttled tools" not in text


#: The checkpoint protocol's own entry template, written the way an agent does
#: when it emits markdown rather than the template's `\n`-joined literal. Four
#: fields separated by blank lines — which was four entries until they were
#: delimited.
def _protocol_entry(label: str) -> str:
    return (
        f"### [42-add-rate-limiting] Implement — {label}\n\n"
        f"**Would have asked:** what should the {label} bound be?\n\n"
        f"**Assumption made:** the conservative one\n\n"
        "**Confidence:** high"
    )


def test_a_multi_paragraph_checkpoint_is_one_entry(tmp_path):
    """A blank line inside an entry is not an entry boundary.

    It was: `append_checkpoint` separated entries with a blank line and the read
    split on one, so a protocol-shaped checkpoint arrived as four entries — a
    bare `###` heading, two fields, and `**Confidence:** high` — three of which
    carry no information and all four of which spend a slot of the cap.
    """
    memory = _memory(tmp_path)
    ticket = _ticket()
    memory.append_checkpoint(
        ticket_id=ticket.external_id,
        workspace_slug="lg",
        run_id="run_1",
        entry=_protocol_entry("token bucket"),
    )

    result = build_inherited_wisdom(ticket, "lg", memory=memory)
    assert result.checkpoints_injected == 1
    assert "**Confidence:** high" in result.text


def test_the_cap_counts_checkpoints_not_paragraphs(tmp_path):
    """Six protocol-shaped checkpoints all reach the prompt.

    Splitting on blank lines spent the whole six-slot cap on the first one and a
    half, so the five earlier decisions this exists to carry were evicted by
    fragments of the sixth.
    """
    memory = _memory(tmp_path)
    ticket = _ticket()
    labels = [f"decision-{i}" for i in range(6)]
    for i, label in enumerate(labels):
        memory.append_checkpoint(
            ticket_id=ticket.external_id,
            workspace_slug="lg",
            run_id=f"run_{i}",
            entry=_protocol_entry(label),
        )

    result = build_inherited_wisdom(ticket, "lg", memory=memory, max_chars=100_000)
    assert result.checkpoints_injected == 6
    for label in labels:
        assert label in result.text


def test_a_log_written_before_entries_were_delimited_still_reads(tmp_path):
    """The 815 logs already in the vault carry no delimiter.

    They are split on blank lines as before — imperfectly, which is what they
    were written as, and better than reading them as one blob or not at all.
    """
    memory = _memory(tmp_path)
    ticket = _ticket()
    log = tmp_path / "Loregarden" / "Checkpoints" / "lg" / "42-add-rate-limiting" / "run-old.md"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        '---\ntype: "checkpoint"\n---\n\n'
        "# Checkpoint log — 42-add-rate-limiting / run-old\n\n"
        "Chose a token bucket.\n\nChose a 60s window.\n\n",
        encoding="utf-8",
    )

    result = build_inherited_wisdom(ticket, "lg", memory=memory)
    assert result.checkpoints_injected == 2
    assert "token bucket" in result.text
    assert "60s window" in result.text


def test_a_log_open_across_the_change_keeps_both_halves(tmp_path):
    """A run in flight when the delimiter landed has an undelimited prefix.

    That prefix is the earlier stages of the run being briefed — the most
    valuable part — so it is split the old way rather than dropped or returned
    as one blob with the delimited entries.
    """
    memory = _memory(tmp_path)
    ticket = _ticket()
    log = tmp_path / "Loregarden" / "Checkpoints" / "lg" / "42-add-rate-limiting" / "run-mixed.md"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        '---\ntype: "checkpoint"\n---\n\n'
        "# Checkpoint log — 42-add-rate-limiting / run-mixed\n\n"
        "Written before the delimiter.\n\n",
        encoding="utf-8",
    )
    memory.append_checkpoint(
        ticket_id=ticket.external_id,
        workspace_slug="lg",
        run_id="run-mixed",
        entry=_protocol_entry("written after"),
    )

    result = build_inherited_wisdom(ticket, "lg", memory=memory)
    assert result.checkpoints_injected == 2
    assert "Written before the delimiter." in result.text
    assert "written after" in result.text


def test_checkpoints_under_two_slugs_order_by_recency_not_by_directory(tmp_path):
    """23 tickets in the live vault have logs under both an id and an external-id
    directory, from either side of the id restructure. The cap used to be filled
    from whichever directory the candidate *set* yielded first, so which run
    reached the prompt was set ordering — not recency, and not stable."""
    memory = _memory(tmp_path)
    ticket = _ticket()
    for i in range(_MAX_CHECKPOINTS):
        memory.append_checkpoint(
            ticket_id=ticket.id,
            workspace_slug="lg",
            run_id=f"old_run_{i}",
            entry=f"older decision {i}",
        )
    memory.append_checkpoint(
        ticket_id=ticket.external_id,
        workspace_slug="lg",
        run_id="new_run",
        entry="newest decision",
    )

    result = build_inherited_wisdom(ticket, "lg", memory=memory, max_chars=100_000)
    assert result.text.index("newest decision") < result.text.index("older decision")


def test_each_checkpoint_renders_as_one_list_item(tmp_path):
    """Continuation lines are indented under the bullet.

    A multi-line entry with unindented continuations ends the markdown list, and
    its fields read as prose belonging to nothing.
    """
    memory = _memory(tmp_path)
    ticket = _ticket()
    memory.append_checkpoint(
        ticket_id=ticket.external_id,
        workspace_slug="lg",
        run_id="run_1",
        entry=_protocol_entry("token bucket"),
    )

    body = build_inherited_wisdom(ticket, "lg", memory=memory).text
    section = body.split("### Checkpoints from earlier stages\n", 1)[1]
    assert section.startswith("- ### [42-add-rate-limiting]")
    assert "\n  **Confidence:** high" in section
    assert section.count("\n- ") == 0
