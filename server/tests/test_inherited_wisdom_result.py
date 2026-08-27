"""The briefing reports what the stores WERE, not what its row counts imply.

`build_inherited_wisdom` returns an `InheritedWisdom` fact record rather than a
string. These tests pin S3 (`store_readiness`, `MemoryStoreReadError`) and S4
(the twelve reported fields) of the ticket spec — the layer below the telemetry
row, so a defect here is diagnosed as a briefing defect rather than as an
aggregate that reads oddly.

Nothing in this module touches the control-plane database: the module under test
imports no DB module and must stay that way.
"""

import time
from functools import partial
from unittest.mock import patch

import pytest
from loregarden.agents.inherited_wisdom import InheritedWisdom, build_inherited_wisdom
from loregarden.models.domain import MemoryStoreKind, MemoryStoreState
from loregarden.services.memory_store import (
    AgentMemoryService,
    MemoryGraphStore,
    MemoryStoreReadError,
    ObsidianMemoryStore,
)
from tests.memory_helpers import briefing_ticket

_REALISTIC_TITLE = "Cap how fast a trusted MCP server can be called"
_MATCHING_BODY = (
    "A throttled server returns early, so the trusted retry loop is called "
    "again before the fast path clears."
)
# Overlaps nothing in _REALISTIC_TITLE: seeded to give the graph file a reason
# to exist without giving the briefing anything to surface.
_UNRELATED_BODY = "Sprite atlas packing prefers power-of-two pages for older drivers."


# The shared factory, defaulted to the title the R4 fixtures use: a multi-word
# title whose terms really overlap the seeded notes, so a briefing that comes
# back empty means the retrieval was empty rather than the query being unlucky.
_ticket = partial(briefing_ticket, title=_REALISTIC_TITLE)


def _both(tmp_path) -> AgentMemoryService:
    return AgentMemoryService(
        obsidian=ObsidianMemoryStore(tmp_path / "vault"),
        graph_sqlite_base=tmp_path / "graph" / "memory.db",
    )


def _nothing_configured() -> AgentMemoryService:
    """S8 case 4 — the fixture shape no existing test builds.

    `_memory`, `_obsidian_only` and `_graph_only` each leave at least one live
    store, so the most likely real degradation — a box where neither backend is
    configured at all — is invisible to the suite that predates this ticket.
    """
    return AgentMemoryService(obsidian=None, graph_sqlite_base=None)


def _wrong_graph_path(tmp_path) -> AgentMemoryService:
    """S8 case 4b — a graph path naming a file that does not exist."""
    return AgentMemoryService(obsidian=None, graph_sqlite_base=tmp_path / "nope" / "memory.db")


# ---------------------------------------------------------------------------
# S3 — store_readiness, and the ordering requirement it exists to satisfy.
# ---------------------------------------------------------------------------


def test_store_readiness_reports_a_graph_file_that_does_not_exist_as_unconfigured(tmp_path):
    """AC2 — a store that was never there must not read as a store that was."""
    memory = _wrong_graph_path(tmp_path)

    assert memory.store_readiness(workspace_slug="lg") == {
        MemoryStoreKind.CHECKPOINTS: MemoryStoreState.UNCONFIGURED,
        MemoryStoreKind.VAULT: MemoryStoreState.UNCONFIGURED,
        MemoryStoreKind.GRAPH: MemoryStoreState.UNCONFIGURED,
    }


def test_store_readiness_reports_a_real_graph_file_as_read(tmp_path):
    """The positive control for the test above. Without it an implementation
    that hardcodes UNCONFIGURED for every graph passes."""
    memory = _both(tmp_path)
    memory.upsert_memory(title="Retry budget", body=_MATCHING_BODY, workspace_slug="lg")

    assert memory.store_readiness(workspace_slug="lg") == {
        MemoryStoreKind.CHECKPOINTS: MemoryStoreState.READ,
        MemoryStoreKind.VAULT: MemoryStoreState.READ,
        MemoryStoreKind.GRAPH: MemoryStoreState.READ,
    }


def test_readiness_is_sampled_before_the_lookup_creates_the_graph_file(tmp_path):
    """AC2, and the single easiest way to silently fail this ticket.

    `MemoryGraphStore.__init__` mkdirs its parent and runs `_init_schema`, so the
    briefing's own lookup CREATES the database it was asked to read. A readiness
    sample taken after the lookups therefore always reports GRAPH=read, and a
    silently absent store becomes indistinguishable from a real empty one — the
    exact defect this ticket exists to remove.

    The second assertion is what makes the first mean something: it proves the
    lookup really did create the file, so UNCONFIGURED can only have come from a
    sample taken before it.
    """
    graph_path = tmp_path / "nope" / "memory.db"
    memory = AgentMemoryService(obsidian=None, graph_sqlite_base=graph_path)
    assert not (tmp_path / "nope" / "lg" / "memory.db").exists()

    result = build_inherited_wisdom(_ticket(), "lg", memory=memory)

    assert result.store_states[MemoryStoreKind.GRAPH] == MemoryStoreState.UNCONFIGURED
    assert (tmp_path / "nope" / "lg" / "memory.db").exists()


def test_a_service_with_no_configured_store_reports_every_store_unconfigured(tmp_path):
    """AC2 / S8 case 4 — no vault, no graph, no errors, and no pretence."""
    result = build_inherited_wisdom(_ticket(), "lg", memory=_nothing_configured())

    assert result.store_states == {
        MemoryStoreKind.CHECKPOINTS: MemoryStoreState.UNCONFIGURED,
        MemoryStoreKind.VAULT: MemoryStoreState.UNCONFIGURED,
        MemoryStoreKind.GRAPH: MemoryStoreState.UNCONFIGURED,
    }
    assert result.store_errors == ()
    assert result.text == ""


# ---------------------------------------------------------------------------
# S3 — MemoryStoreReadError labels the store that failed, at the raise point.
# ---------------------------------------------------------------------------


def test_recall_related_labels_a_failing_vault_read(tmp_path):
    memory = _both(tmp_path)
    memory.upsert_memory(title="Retry budget", body=_MATCHING_BODY, workspace_slug="lg")

    with patch.object(ObsidianMemoryStore, "list_notes", side_effect=OSError("vault unavailable")):
        with pytest.raises(MemoryStoreReadError) as caught:
            memory.recall_related(_MATCHING_BODY, workspace_slug="lg", limit=5)

    assert caught.value.store == MemoryStoreKind.VAULT
    assert type(caught.value.__cause__) is OSError


def test_recall_related_labels_a_failing_graph_read(tmp_path):
    memory = _both(tmp_path)
    memory.upsert_memory(title="Retry budget", body=_MATCHING_BODY, workspace_slug="lg")

    with patch.object(MemoryGraphStore, "list_nodes", side_effect=OSError("graph unavailable")):
        with pytest.raises(MemoryStoreReadError) as caught:
            memory.recall_related(_MATCHING_BODY, workspace_slug="lg", limit=5)

    assert caught.value.store == MemoryStoreKind.GRAPH
    assert type(caught.value.__cause__) is OSError


# ---------------------------------------------------------------------------
# S4 / S8 — the reported fields, case by case.
# ---------------------------------------------------------------------------


def test_a_briefing_with_content_reports_its_size_and_healthy_stores(tmp_path):
    """S8 case 1 — BUILT. Every figure AC1 names is populated."""
    memory = _both(tmp_path)
    memory.upsert_memory(
        title="Retry budget for throttled tools", body=_MATCHING_BODY, workspace_slug="lg"
    )

    result = build_inherited_wisdom(_ticket(), "lg", memory=memory)

    assert result.chars_injected > 0
    assert result.chars_injected == len(result.text)
    assert result.pre_truncation_chars >= result.chars_injected
    assert result.truncated is False
    assert result.learnings_injected == 1
    assert result.query_had_terms is True
    assert result.store_errors == ()
    assert result.elapsed_ms >= 0
    assert result.store_states == {
        MemoryStoreKind.CHECKPOINTS: MemoryStoreState.READ,
        MemoryStoreKind.VAULT: MemoryStoreState.READ,
        MemoryStoreKind.GRAPH: MemoryStoreState.READ,
    }


def test_a_read_store_with_nothing_to_say_reports_read_and_zero(tmp_path):
    """S8 case 2 — EMPTY. The stores were genuinely read; they held no match.

    Its positive control is the BUILT test above: same fixture shape, same
    query, one seeded matching note apart. Without that pairing an
    implementation that reports 'read and zero' whenever the text is empty
    passes this on its own.
    """
    memory = _both(tmp_path)
    memory.upsert_memory(title="Atlas packing", body=_UNRELATED_BODY, workspace_slug="lg")

    result = build_inherited_wisdom(_ticket(), "lg", memory=memory)

    assert result.text == ""
    assert result.chars_injected == 0
    assert result.pre_truncation_chars == 0
    assert result.truncated is False
    assert result.checkpoints_injected == 0
    assert result.learnings_injected == 0
    assert result.query_had_terms is True
    assert result.store_errors == ()
    assert result.store_states == {
        MemoryStoreKind.CHECKPOINTS: MemoryStoreState.READ,
        MemoryStoreKind.VAULT: MemoryStoreState.READ,
        MemoryStoreKind.GRAPH: MemoryStoreState.READ,
    }


def test_an_all_stopword_query_leaves_the_recall_stores_not_queried(tmp_path):
    """S8 case 2b — `recall_related` returns before touching either store when
    the query tokenises to no terms, so neither 'read' nor 'unconfigured' is
    true of the vault and the graph. Recording READ here would let an
    all-stopword title read as 'both stores read and empty'.

    CHECKPOINTS stays READ: the checkpoint lookup does not consult the query and
    really did run.
    """
    memory = _both(tmp_path)
    memory.upsert_memory(title="Retry budget", body=_MATCHING_BODY, workspace_slug="lg")
    ticket = _ticket(title="That which should be used", description="This one could also update")

    result = build_inherited_wisdom(ticket, "lg", memory=memory)

    assert result.query_had_terms is False
    assert result.store_errors == ()
    assert result.store_states == {
        MemoryStoreKind.CHECKPOINTS: MemoryStoreState.READ,
        MemoryStoreKind.VAULT: MemoryStoreState.NOT_QUERIED,
        MemoryStoreKind.GRAPH: MemoryStoreState.NOT_QUERIED,
    }


def test_an_empty_title_and_description_report_no_query_terms(tmp_path):
    memory = _both(tmp_path)
    memory.upsert_memory(title="Retry budget", body=_MATCHING_BODY, workspace_slug="lg")

    result = build_inherited_wisdom(_ticket(title="", description=""), "lg", memory=memory)

    assert result.text == ""
    assert result.query_had_terms is False


def test_an_unreadable_vault_is_named_as_the_vault(tmp_path):
    """S8 case 3 — AC2. The label must send an operator at the right system."""
    memory = _both(tmp_path)
    memory.upsert_memory(title="Retry budget", body=_MATCHING_BODY, workspace_slug="lg")

    with patch.object(ObsidianMemoryStore, "list_notes", side_effect=OSError("vault unavailable")):
        result = build_inherited_wisdom(_ticket(), "lg", memory=memory)

    assert result.store_errors == ("vault:OSError",)
    assert result.store_states[MemoryStoreKind.VAULT] == MemoryStoreState.ERRORED
    assert result.store_states[MemoryStoreKind.GRAPH] == MemoryStoreState.READ
    # The section still goes down whole — one failing store costs both halves.
    assert "Retry budget" not in result.text


def test_an_unreadable_graph_is_named_as_the_graph(tmp_path):
    """S8 case 3b — AC2."""
    memory = _both(tmp_path)
    memory.upsert_memory(title="Retry budget", body=_MATCHING_BODY, workspace_slug="lg")

    with patch.object(MemoryGraphStore, "list_nodes", side_effect=OSError("graph unavailable")):
        result = build_inherited_wisdom(_ticket(), "lg", memory=memory)

    assert result.store_errors == ("graph:OSError",)
    assert result.store_states[MemoryStoreKind.GRAPH] == MemoryStoreState.ERRORED
    assert result.store_states[MemoryStoreKind.VAULT] == MemoryStoreState.READ
    assert "Retry budget" not in result.text


def test_a_failing_vault_and_a_failing_graph_are_distinguishable(tmp_path):
    """AC2, stated as the property rather than as two separate strings.

    A label that collapses the two sends an operator to restart iCloud when the
    SQLite file is the thing that is broken.
    """
    memory = _both(tmp_path)
    memory.upsert_memory(title="Retry budget", body=_MATCHING_BODY, workspace_slug="lg")

    with patch.object(ObsidianMemoryStore, "list_notes", side_effect=OSError("boom")):
        from_vault = build_inherited_wisdom(_ticket(), "lg", memory=memory)
    with patch.object(MemoryGraphStore, "list_nodes", side_effect=OSError("boom")):
        from_graph = build_inherited_wisdom(_ticket(), "lg", memory=memory)

    assert from_vault.store_errors != from_graph.store_errors


def test_an_unreadable_checkpoint_directory_is_named_as_the_checkpoints_store(tmp_path):
    """S8 case 3c — the checkpoint lookup raises a bare exception rather than a
    labelled one, so the fallback label must still name its own store."""
    memory = _both(tmp_path)

    with patch.object(ObsidianMemoryStore, "checkpoints_dir", side_effect=OSError("vault stalled")):
        result = build_inherited_wisdom(_ticket(), "lg", memory=memory)

    assert result.store_errors == ("checkpoints:OSError",)
    assert result.store_states[MemoryStoreKind.CHECKPOINTS] == MemoryStoreState.ERRORED


def test_a_service_that_cannot_be_built_reports_every_store_errored():
    """S8 case 3d — AC2. Today this path returns "", which is exactly the
    ticket's opening scenario: a box whose vault env var is unset reports zero
    errors forever."""
    with patch.object(AgentMemoryService, "from_settings", side_effect=RuntimeError("no vault")):
        result = build_inherited_wisdom(_ticket(), "lg")

    assert result.store_errors == ("service:RuntimeError",)
    assert result.store_states == {
        MemoryStoreKind.CHECKPOINTS: MemoryStoreState.ERRORED,
        MemoryStoreKind.VAULT: MemoryStoreState.ERRORED,
        MemoryStoreKind.GRAPH: MemoryStoreState.ERRORED,
    }
    assert result.text == ""
    assert result.chars_injected == 0


def test_a_working_service_factory_reports_no_store_errors(tmp_path):
    """The positive control for the test above."""
    memory = _both(tmp_path)

    with patch.object(AgentMemoryService, "from_settings", return_value=memory):
        result = build_inherited_wisdom(_ticket(), "lg")

    assert result.store_errors == ()


# ---------------------------------------------------------------------------
# S4 — truncation is measured against the pre-truncation length, never against
# the sliced one.
# ---------------------------------------------------------------------------


def _seeded_checkpoint_service(tmp_path, *, entries: int, entry: str) -> AgentMemoryService:
    memory = _both(tmp_path)
    for i in range(entries):
        memory.append_checkpoint(
            ticket_id="42-add-rate-limiting",
            workspace_slug="lg",
            run_id=f"run_{i}",
            entry=entry,
        )
    return memory


def test_a_briefing_landing_exactly_on_the_bound_is_not_truncated(tmp_path):
    """S4 — `truncated` is `pre_truncation_chars > max_chars`. Writing it as
    `len(text) == max_chars` reports every briefing that happens to land on the
    bound as truncated, and the flag stops meaning 'context was lost'."""
    memory = _seeded_checkpoint_service(tmp_path, entries=3, entry="x" * 200)
    full = build_inherited_wisdom(_ticket(), "lg", memory=memory, max_chars=100_000)
    exact = full.pre_truncation_chars
    assert exact > 0

    result = build_inherited_wisdom(_ticket(), "lg", memory=memory, max_chars=exact)

    assert result.truncated is False
    assert result.chars_injected == exact
    assert result.pre_truncation_chars == exact


def test_a_briefing_one_character_over_the_bound_is_truncated(tmp_path):
    memory = _seeded_checkpoint_service(tmp_path, entries=3, entry="x" * 200)
    full = build_inherited_wisdom(_ticket(), "lg", memory=memory, max_chars=100_000)
    exact = full.pre_truncation_chars

    result = build_inherited_wisdom(_ticket(), "lg", memory=memory, max_chars=exact - 1)

    assert result.truncated is True
    assert result.pre_truncation_chars == exact
    assert result.chars_injected == exact - 1


# ---------------------------------------------------------------------------
# S4 — the counters plateau at their caps, so saturation is what stops a flat
# five reading as a healthy corpus.
# ---------------------------------------------------------------------------


def test_checkpoints_saturated_is_false_below_the_cap(tmp_path):
    memory = _seeded_checkpoint_service(tmp_path, entries=1, entry="Chose a token bucket.")

    result = build_inherited_wisdom(_ticket(), "lg", memory=memory)

    assert result.checkpoints_injected == 1
    assert result.checkpoints_saturated is False


def test_checkpoints_saturated_is_true_at_the_cap(tmp_path):
    memory = _seeded_checkpoint_service(tmp_path, entries=9, entry="Chose a token bucket.")

    result = build_inherited_wisdom(_ticket(), "lg", memory=memory)

    assert result.checkpoints_injected == 6
    assert result.checkpoints_saturated is True


def test_learnings_saturated_is_true_at_the_cap(tmp_path):
    memory = _both(tmp_path)
    for i in range(7):
        memory.upsert_memory(title=f"Retry budget {i}", body=_MATCHING_BODY, workspace_slug="lg")

    result = build_inherited_wisdom(_ticket(), "lg", memory=memory)

    assert result.learnings_injected == 5
    assert result.learnings_saturated is True


def test_learnings_saturated_is_false_below_the_cap(tmp_path):
    memory = _both(tmp_path)
    memory.upsert_memory(title="Retry budget", body=_MATCHING_BODY, workspace_slug="lg")

    result = build_inherited_wisdom(_ticket(), "lg", memory=memory)

    assert result.learnings_injected == 1
    assert result.learnings_saturated is False


# ---------------------------------------------------------------------------
# S4 — the canonical not-attempted value, and the never-raises property.
# ---------------------------------------------------------------------------


def test_not_attempted_is_a_zeroed_record_with_no_store_states():
    """The single value the verify seam records. Its store_states is empty
    because no store was consulted — not because three stores read as nothing.
    """
    result = InheritedWisdom.not_attempted()

    assert result.text == ""
    assert result.checkpoints_injected == 0
    assert result.learnings_injected == 0
    assert result.checkpoints_saturated is False
    assert result.learnings_saturated is False
    assert result.query_had_terms is False
    assert result.chars_injected == 0
    assert result.pre_truncation_chars == 0
    assert result.truncated is False
    assert result.store_states == {}
    assert result.store_errors == ()
    assert result.elapsed_ms == 0


def test_the_briefing_still_never_raises_when_every_lookup_explodes(tmp_path):
    """The never-fatal property survives the return-type change."""
    memory = _both(tmp_path)

    with (
        patch.object(ObsidianMemoryStore, "list_notes", side_effect=OSError("boom")),
        patch.object(ObsidianMemoryStore, "checkpoints_dir", side_effect=OSError("boom")),
    ):
        result = build_inherited_wisdom(_ticket(), "lg", memory=memory)

    assert result.text == ""
    assert result.store_states[MemoryStoreKind.CHECKPOINTS] == MemoryStoreState.ERRORED


def test_elapsed_ms_covers_the_assembly_it_measures(tmp_path):
    """AC1 names elapsed ms as one of the six figures. A slow store must move it,
    so a hardcoded zero cannot pass."""
    memory = _both(tmp_path)
    memory.upsert_memory(title="Retry budget", body=_MATCHING_BODY, workspace_slug="lg")
    real_list_nodes = MemoryGraphStore.list_nodes

    def slow(self, **kwargs):
        time.sleep(0.25)
        return real_list_nodes(self, **kwargs)

    with patch.object(MemoryGraphStore, "list_nodes", slow):
        result = build_inherited_wisdom(_ticket(), "lg", memory=memory)

    assert result.elapsed_ms >= 200
