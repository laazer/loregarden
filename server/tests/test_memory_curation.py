"""Curation: proposals that change nothing, and the two actions a person can take."""

from __future__ import annotations

import pytest
from loregarden.models.domain import LearningApplication, MemoryRelationType, RunStatus
from loregarden.services import memory_curation as curation
from loregarden.services.memory_curation import MergeConflictError, ProposalKind
from loregarden.services.memory_store import AgentMemoryService
from sqlmodel import select
from tests.factories import make_agent_run, make_workspace
from tests.memory_helpers import frozen_clock

WS = "lg"
SAME = "Retry only idempotent calls; a retried POST double-charges the customer card."


@pytest.fixture
def memory() -> AgentMemoryService:
    return AgentMemoryService.from_settings()


def _node(memory, title, body="unrelated words entirely", **kwargs):
    return memory.upsert_memory(title=title, body=body, workspace_slug=WS, **kwargs)["graph"]["id"]


def _kinds(found):
    return sorted((p.kind, tuple(sorted(p.node_ids))) for p in found)


def test_proposals_find_each_kind_and_write_nothing(db_session, memory, tmp_path):
    graph = memory.require_graph(WS)
    legacy = graph.upsert_node(
        title="Learning — t-1", body="Pin the retry budget.\nMore.", workspace_slug=WS
    )["id"]
    left, right = _node(memory, "Idempotent retries", SAME), _node(memory, "Retry safety", SAME)
    pro, con = _node(memory, "Cache everything", "a b c"), _node(memory, "Cache nothing", "d e f")
    memory.create_relation(
        source_id=pro,
        target_id=con,
        relation_type=MemoryRelationType.CONTRADICTS,
        workspace_slug=WS,
    )
    with frozen_clock("2020-01-01T00:00:00+00:00"):
        cold = _node(memory, "Forgotten note", "zzz qqq")
    before = graph.db_path.stat().st_mtime_ns

    found = curation.proposals(db_session, memory, WS)

    kinds = _kinds(found)
    assert (ProposalKind.GENERIC_TITLE, (legacy,)) in kinds
    assert (ProposalKind.NEAR_DUPLICATE, tuple(sorted((left, right)))) in kinds
    assert (ProposalKind.CONTESTED, tuple(sorted((pro, con)))) in kinds
    assert (ProposalKind.COLD, (cold,)) in kinds
    generic = next(p for p in found if p.kind is ProposalKind.GENERIC_TITLE)
    assert generic.suggested_title == "Pin the retry budget"
    assert graph.db_path.stat().st_mtime_ns == before


def test_learnings_that_contradict_are_never_proposed_as_duplicates(db_session, memory):
    a, b = _node(memory, "Retries one", SAME), _node(memory, "Retries two", SAME)
    memory.create_relation(
        source_id=a, target_id=b, relation_type=MemoryRelationType.CONTRADICTS, workspace_slug=WS
    )

    kinds = [p.kind for p in curation.proposals(db_session, memory, WS)]
    assert ProposalKind.NEAR_DUPLICATE not in kinds


def test_retitle_keeps_the_old_title_as_an_alias_and_records_why(memory):
    graph = memory.require_graph(WS)
    node_id = graph.upsert_node(title="Learning — t-1", body="b", workspace_slug=WS)["id"]

    renamed = curation.retitle(
        memory,
        node_id=node_id,
        workspace_slug=WS,
        title="Pin the retry budget",
        aliases=None,
        reason="real name",
        writer="op",
    )

    assert renamed["title"] == "Pin the retry budget"
    assert renamed["aliases"] == ["Learning — t-1"]
    assert graph.node_versions(node_id)[-1]["change_note"] == "real name"


def _surface(session, node_id, code):
    workspace = make_workspace(session, slug="curation-ws")
    run = make_agent_run(
        session, workspace_id=workspace.id, run_code=code, status=RunStatus.SUCCEEDED
    )
    session.add(LearningApplication(run_id=run.id, node_id=node_id, workspace_id=workspace.id))
    session.commit()
    return run.id


def test_merge_keeps_every_claim_and_name_and_carries_the_evidence(db_session, memory):
    survivor = _node(memory, "Idempotent retries", "Retry only idempotent calls.", aliases=["IR"])
    absorbed = _node(memory, "Retry safety", "A retried POST double-charges.", aliases=["RS"])
    neighbour = _node(memory, "Payments")
    memory.create_relation(
        source_id=absorbed,
        target_id=neighbour,
        relation_type=MemoryRelationType.PART_OF,
        workspace_slug=WS,
    )
    run_id = _surface(db_session, absorbed, "R1")

    result = curation.merge(
        db_session,
        memory,
        survivor_id=survivor,
        absorbed_id=absorbed,
        workspace_slug=WS,
        reason="same idea",
        writer="op",
    )

    merged = result["survivor"]
    assert "Retry only idempotent calls." in merged["body"]
    assert "A retried POST double-charges." in merged["body"]
    assert merged["aliases"] == ["IR", "Retry safety", "RS"]
    assert (result["edges_moved"], result["outcomes_moved"]) == (1, 1)

    detail = memory.node_detail(node_id=survivor, workspace_slug=WS)
    edges = {(e["relation_type"], e["direction"], e["node_id"]) for e in detail["relations"]}
    assert ("part_of", "out", neighbour) in edges
    assert ("supersedes", "out", absorbed) in edges
    withdrawn = memory.node_detail(node_id=absorbed, workspace_slug=WS)
    assert withdrawn["discredited"] is True
    assert "same idea" in withdrawn["versions"][-1]["change_note"]
    db_session.expire_all()
    (application,) = db_session.exec(
        select(LearningApplication).where(LearningApplication.run_id == run_id)
    ).all()
    assert application.node_id == survivor
    # Withdrawn from recall; the survivor answers for both names.
    titles = [r["title"] for r in memory.recall_related("retry safety", workspace_slug=WS)]
    assert titles == ["Idempotent retries"]


def test_merging_learnings_that_contradict_is_refused_and_changes_nothing(db_session, memory):
    a, b = _node(memory, "A", "one"), _node(memory, "B", "two")
    memory.create_relation(
        source_id=a, target_id=b, relation_type=MemoryRelationType.CONTRADICTS, workspace_slug=WS
    )
    _surface(db_session, b, "R1")

    with pytest.raises(MergeConflictError, match="contradict"):
        curation.merge(
            db_session,
            memory,
            survivor_id=a,
            absorbed_id=b,
            workspace_slug=WS,
            reason="r",
            writer="op",
        )

    assert memory.node_detail(node_id=b, workspace_slug=WS)["discredited"] is False
    db_session.expire_all()
    assert {r.node_id for r in db_session.exec(select(LearningApplication)).all()} == {b}


def test_merging_a_discredited_learning_is_refused(db_session, memory):
    a, b = _node(memory, "A"), _node(memory, "B")
    memory.set_discredited(node_id=b, workspace_slug=WS, discredited=True, reason="r", writer="t")
    with pytest.raises(MergeConflictError, match="discredited"):
        curation.merge(
            db_session,
            memory,
            survivor_id=a,
            absorbed_id=b,
            workspace_slug=WS,
            reason="r",
            writer="op",
        )
