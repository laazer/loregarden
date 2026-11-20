"""Sibling tickets claiming the same files: derivation and the stage prompt block."""

from __future__ import annotations

import json
import logging

import pytest
from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.agents.plan_context import ARTIFACT_KIND as PLAN_KIND
from loregarden.agents.sibling_claims_context import (
    HEADING,
    MAX_SIBLINGS,
    build_sibling_claims_context,
)
from loregarden.models.domain import (
    AgentRun,
    MemoryBriefingAssembly,
    Ticket,
    TicketState,
    WorkItemType,
    Workspace,
)
from loregarden.models.domain.enums import utcnow
from loregarden.models.domain.path_claim_enums import PathClaimSource
from loregarden.services.path_claims import sibling_overlaps, ticket_claims
from loregarden.services.seed import seed_database
from loregarden.services.workspace_paths import resolve_agent_context_dir
from loregarden.testing.factories import make_artifact, make_ticket, make_workspace
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

LAND = "server/loregarden/services/land_ticket.py"


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture
def tree(session):
    """A milestone with a feature holding two tasks, and a second milestone."""
    ws = make_workspace(session)
    root = make_ticket(
        session, workspace_id=ws.id, external_id="m-1", work_item_type=WorkItemType.MILESTONE
    )
    feature = make_ticket(
        session,
        workspace_id=ws.id,
        external_id="f-2",
        work_item_type=WorkItemType.FEATURE,
        parent_ticket_id=root.id,
    )
    task = _task(session, ws, "t-3", feature)
    sibling = _task(session, ws, "t-4", feature)
    other_root = make_ticket(
        session, workspace_id=ws.id, external_id="m-9", work_item_type=WorkItemType.MILESTONE
    )
    return {
        "ws": ws,
        "root": root,
        "feature": feature,
        "task": task,
        "sibling": sibling,
        "other_root": other_root,
    }


def _task(session: Session, ws: Workspace, external_id: str, parent: Ticket) -> Ticket:
    return make_ticket(
        session,
        workspace_id=ws.id,
        external_id=external_id,
        parent_ticket_id=parent.id,
        state=TicketState.IN_PROGRESS,
    )


def _changed(session: Session, ticket: Ticket, *paths: str, raw: str | None = None) -> None:
    session.add(
        AgentRun(
            run_code=f"run-{ticket.external_id}-{len(paths)}",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            agent_id="backend_implementer",
            stage_key="implement",
            changed_paths_json=raw if raw is not None else json.dumps(list(paths)),
            changed_paths_recorded_at=utcnow(),
        )
    )
    session.commit()


def _plan(session: Session, ticket: Ticket, modules_touched: object) -> None:
    make_artifact(
        session,
        ticket_id=ticket.id,
        kind=PLAN_KIND,
        title="Plan",
        content={"approach": "x", "modules_touched": modules_touched},
    )


def test_a_sibling_that_changed_the_same_file_is_an_observed_overlap(session, tree):
    _changed(session, tree["task"], LAND, "server/a.py")
    _changed(session, tree["sibling"], LAND, "server/b.py")

    [overlap] = sibling_overlaps(session, tree["task"])

    assert overlap.sibling.id == tree["sibling"].id
    assert [(c.path, c.source) for c in overlap.shared] == [(LAND, PathClaimSource.OBSERVED)]


def test_annotated_plan_entries_are_read_as_their_leading_path(session, tree):
    _plan(session, tree["task"], [f"{LAND} — AC7's repair step", "client/x.tsx (new)", "tests"])
    _plan(session, tree["sibling"], [f"./{LAND}:363"])

    assert ticket_claims(session, tree["task"]) == {
        LAND: PathClaimSource.PLANNED,
        "client/x.tsx": PathClaimSource.PLANNED,
    }
    [overlap] = sibling_overlaps(session, tree["task"])
    assert overlap.shared[0].source is PathClaimSource.PLANNED


def test_a_changed_path_outranks_the_same_planned_path(session, tree):
    _plan(session, tree["task"], [LAND])
    _changed(session, tree["task"], LAND)

    assert ticket_claims(session, tree["task"]) == {LAND: PathClaimSource.OBSERVED}


def test_a_count_summary_plan_claims_nothing_and_is_not_reported(session, tree, caplog):
    _plan(session, tree["task"], {"new": 3, "edited": 10})

    with caplog.at_level(logging.WARNING):
        assert ticket_claims(session, tree["task"]) == {}
    assert not caplog.records


def test_a_plan_of_unknown_shape_is_reported(session, tree, caplog):
    _plan(session, tree["task"], "server/a.py")

    with caplog.at_level(logging.WARNING):
        assert ticket_claims(session, tree["task"]) == {}
    assert "modules_touched" in caplog.text


@pytest.mark.parametrize(
    "close",
    [
        lambda t: setattr(t, "landed_sha", "abc123"),
        lambda t: setattr(t, "state", TicketState.DONE),
        lambda t: setattr(t, "state", TicketState.WONT_DO),
    ],
    ids=["landed", "done", "wont_do"],
)
def test_a_landed_or_closed_sibling_claims_nothing(session, tree, close):
    _changed(session, tree["task"], LAND)
    _changed(session, tree["sibling"], LAND)
    close(tree["sibling"])
    session.add(tree["sibling"])
    session.commit()

    assert sibling_overlaps(session, tree["task"]) == []


def test_ancestors_descendants_and_other_trees_are_not_siblings(session, tree):
    ws = tree["ws"]
    child = _task(session, ws, "t-5", tree["task"])
    elsewhere = _task(session, ws, "t-6", tree["other_root"])
    for ticket in (tree["task"], tree["feature"], tree["root"], child, elsewhere):
        _changed(session, ticket, LAND)

    assert sibling_overlaps(session, tree["task"]) == []


def test_a_cousin_in_the_same_landing_tree_is_a_sibling(session, tree):
    ws = tree["ws"]
    other_feature = make_ticket(
        session,
        workspace_id=ws.id,
        external_id="f-7",
        work_item_type=WorkItemType.FEATURE,
        parent_ticket_id=tree["root"].id,
    )
    cousin = _task(session, ws, "t-8", other_feature)
    _changed(session, tree["task"], LAND)
    _changed(session, cousin, LAND)

    assert [o.sibling.id for o in sibling_overlaps(session, tree["task"])] == [cousin.id]


def test_a_split_parent_is_not_a_sibling_but_its_children_are(session, tree):
    ws = tree["ws"]
    split = make_ticket(
        session,
        workspace_id=ws.id,
        external_id="f-7",
        work_item_type=WorkItemType.FEATURE,
        parent_ticket_id=tree["root"].id,
        state=TicketState.IN_PROGRESS,
    )
    child = _task(session, ws, "t-8", split)
    _changed(session, tree["task"], LAND)
    _plan(session, split, [LAND])
    _changed(session, child, LAND)

    assert [o.sibling.id for o in sibling_overlaps(session, tree["task"])] == [child.id]


def test_a_directory_claim_overlaps_a_file_beneath_it(session, tree):
    _changed(session, tree["task"], LAND)
    _plan(session, tree["sibling"], ["server/loregarden/services/"])

    [overlap] = sibling_overlaps(session, tree["task"])
    assert overlap.shared[0].path == "server/loregarden/services/"


def test_a_ticket_that_claims_nothing_has_no_overlaps(session, tree):
    _changed(session, tree["sibling"], LAND)

    assert sibling_overlaps(session, tree["task"]) == []


def test_an_unreadable_changed_paths_row_is_skipped_and_reported(session, tree, caplog):
    _changed(session, tree["task"], LAND)
    _changed(session, tree["sibling"], raw="{not json")
    _changed(session, tree["sibling"], LAND, "x.py")

    with caplog.at_level(logging.WARNING):
        [overlap] = sibling_overlaps(session, tree["task"])
    assert overlap.shared[0].path == LAND
    assert "changed_paths_json" in caplog.text


def test_the_block_names_each_sibling_and_caps_the_list(session, tree):
    ws = tree["ws"]
    _changed(session, tree["task"], LAND)
    for n in range(MAX_SIBLINGS + 1):
        _changed(session, _task(session, ws, f"s-{n:02}", tree["feature"]), LAND)

    body = build_sibling_claims_context(session, tree["task"])

    assert "s-00" in body and f"s-{MAX_SIBLINGS - 1:02}" in body
    assert f"s-{MAX_SIBLINGS:02}" not in body
    assert f"`{LAND}`" in body


def test_the_block_is_empty_when_nothing_overlaps(session, tree):
    _changed(session, tree["task"], LAND)
    _changed(session, tree["sibling"], "server/elsewhere.py")

    assert build_sibling_claims_context(session, tree["task"]) == ""


def _prompt(session: Session, ticket: Ticket) -> str:
    workspace = session.get(Workspace, ticket.workspace_id)
    run = AgentRun(
        run_code="run_claims",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="spec",
        stage_key="implement",
    )
    executor = CliAgentExecutor(session)
    return executor._build_prompt(
        ticket,
        run,
        {"role_file": "agents/9_static_qa/static_qa_v1.md"},
        resolve_agent_context_dir(workspace),
        workspace,
        executor._resolve_stage_def(ticket, run),
        assembly_source=MemoryBriefingAssembly.DISPATCH,
    )


def test_a_stage_prompt_carries_the_sibling_block_only_when_siblings_overlap(session):
    seed_database(session)
    ticket = session.exec(
        select(Ticket).where(Ticket.legacy_external_id == "03-wire-cli-agent-runner")
    ).first()
    root = make_ticket(
        session,
        workspace_id=ticket.workspace_id,
        external_id="m-claims",
        work_item_type=WorkItemType.MILESTONE,
    )
    ticket.parent_ticket_id = root.id
    session.add(ticket)
    session.commit()
    sibling = make_ticket(
        session,
        workspace_id=ticket.workspace_id,
        external_id="t-sibling",
        parent_ticket_id=root.id,
        state=TicketState.IN_PROGRESS,
    )
    _changed(session, ticket, LAND)

    assert HEADING not in _prompt(session, ticket)

    _changed(session, sibling, LAND)
    prompt = _prompt(session, ticket)
    assert HEADING in prompt
    assert "t-sibling" in prompt.split(HEADING, 1)[1]


def test_an_initiative_has_no_siblings(session, tree):
    initiative = make_ticket(
        session, workspace_id=None, external_id="i-1", work_item_type=WorkItemType.INITIATIVE
    )
    _changed(session, tree["task"], LAND)
    session.add(
        AgentRun(
            run_code="run-i",
            ticket_id=initiative.id,
            workspace_id=tree["ws"].id,
            agent_id="planner",
            stage_key="plan",
            changed_paths_json=json.dumps([LAND]),
            changed_paths_recorded_at=utcnow(),
        )
    )
    session.commit()

    assert sibling_overlaps(session, initiative) == []
