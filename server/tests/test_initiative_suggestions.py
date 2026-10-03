"""Suggesting initiatives from open work, and creating the ones the operator keeps."""

from __future__ import annotations

import json
from datetime import timedelta
from unittest import mock

import pytest
from loregarden.models.domain import (
    SuggestedItem,
    Ticket,
    TicketState,
    WorkItemType,
    Workspace,
)
from loregarden.services.hierarchy_service import reparent_ticket
from loregarden.services.initiative_suggestion_agent import GROUPER_CLI_PROFILE
from loregarden.services.initiative_suggestions import theme_suggestions
from loregarden.services.ticket_service import TicketService
from loregarden.testing.factories import make_ticket, utcnow
from sqlmodel import Session, select


@pytest.fixture(name="workspace")
def workspace_fixture(db_session: Session) -> Workspace:
    """Its own workspace: sprint budgets are per workspace, and the seeded one has work."""
    seeded = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()
    ws = Workspace(
        slug="elsewhere",
        name="Elsewhere",
        repo_path="/tmp/elsewhere",
        workflow_template_id=seeded.workflow_template_id,
    )
    db_session.add(ws)
    db_session.commit()
    return ws


def _mine(items: list[dict], workspace: Workspace) -> list[dict]:
    return [i for i in items if i["workspace_slug"] == workspace.slug]


def _ticket(
    session: Session,
    workspace: Workspace,
    title: str,
    work_item_type: WorkItemType,
    *,
    parent: Ticket | None = None,
    state: TicketState = TicketState.BACKLOG,
) -> Ticket:
    return make_ticket(
        session,
        workspace_id=workspace.id,
        external_id=title.lower().replace(" ", "-"),
        title=title,
        work_item_type=work_item_type,
        parent_ticket_id=parent.id if parent else None,
        state=state,
    )


@pytest.fixture(name="themed")
def themed_fixture(db_session: Session, workspace: Workspace) -> dict[str, Ticket]:
    """Two canvas milestones, one unrelated, and features in various states."""
    a = _ticket(db_session, workspace, "Canvas selection", WorkItemType.MILESTONE)
    b = _ticket(
        db_session, workspace, "Spatial canvas nodes", WorkItemType.MILESTONE,
        state=TicketState.IN_PROGRESS,
    )  # fmt: skip
    c = _ticket(db_session, workspace, "Billing export", WorkItemType.MILESTONE)
    done = _ticket(
        db_session, workspace, "Old canvas", WorkItemType.MILESTONE, state=TicketState.DONE
    )
    running = _ticket(
        db_session, workspace, "Drag nodes", WorkItemType.FEATURE, parent=b,
        state=TicketState.IN_PROGRESS,
    )  # fmt: skip
    queued = _ticket(db_session, workspace, "Zoom nodes", WorkItemType.FEATURE, parent=a)
    blocked = _ticket(
        db_session, workspace, "Stuck thing", WorkItemType.FEATURE, parent=c,
        state=TicketState.BLOCKED,
    )  # fmt: skip
    bug = _ticket(db_session, workspace, "Export crash", WorkItemType.BUG, parent=c)
    return {
        "a": a, "b": b, "c": c, "done": done, "running": running,
        "queued": queued, "blocked": blocked, "bug": bug,
    }  # fmt: skip


def test_feature_and_bug_may_sit_under_an_initiative(db_session: Session, themed):
    initiative = TicketService(db_session).create_ticket(
        title="Sprint", work_item_type=WorkItemType.INITIATIVE
    )
    reparent_ticket(db_session, themed["queued"], initiative.id)
    reparent_ticket(db_session, themed["bug"], initiative.id)
    db_session.commit()
    assert themed["queued"].parent_ticket_id == initiative.id
    assert themed["bug"].parent_ticket_id == initiative.id


def test_suggestions_group_shared_words_and_size_a_sprint(client, workspace, themed):
    res = client.get("/api/initiatives/suggestions", params={"sprint_days": 7})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["source"] == "heuristic"

    themes = [s for s in body["suggestions"] if s["kind"] == "theme"]
    assert [sorted(i["id"] for i in s["items"]) for s in themes] == [
        sorted([themed["a"].id, themed["b"].id])
    ]
    # No phrase most of them share, so the word is named as a topic.
    assert themes[0]["title"] == "Canvas work"
    ungrouped = {m["id"] for m in body["ungrouped"]}
    assert themed["c"].id in ungrouped
    assert not ungrouped & {themed["a"].id, themed["b"].id, themed["done"].id}

    [sprint] = [s for s in body["suggestions"] if s["kind"] == "sprint"]
    ids = [i["id"] for i in _mine(sprint["items"], workspace)]
    # In-progress first; blocked work never appears.
    states = [i["state"] for i in sprint["items"]]
    assert states == sorted(states, key=lambda st: st != "in_progress")
    assert ids[0] == themed["running"].id
    assert set(ids) == {themed["running"].id, themed["queued"].id, themed["bug"].id}
    by_id = {i["id"]: i for i in sprint["items"]}
    assert by_id[themed["running"].id]["from_milestone"] == themed["b"].external_id
    assert body["sprint"]["days"] == 7
    assert body["sprint"]["planned"] == sum(i["cost"] for i in sprint["items"])
    # Nothing finished in the window, so the pace is assumed and said to be.
    assert body["sprint"]["capacity"] is None
    assert body["sprint"]["basis"] == "none"


def test_measured_pace_bounds_the_sprint(client, db_session: Session, workspace, themed):
    # 21 finished tasks in three weeks: one a day, so a 2-day sprint fits two items.
    parent = _ticket(
        db_session, workspace, "Finished feature", WorkItemType.FEATURE, parent=themed["c"]
    )
    for n in range(21):
        make_ticket(
            db_session, workspace_id=workspace.id, title=f"done {n}", work_item_type=WorkItemType.TASK,
            parent_ticket_id=parent.id, state=TicketState.DONE, resolved_at=utcnow() - timedelta(days=1),
        )  # fmt: skip
    # Pace is per day the work has existed, so give the workspace three weeks of history.
    parent.created_at = utcnow() - timedelta(days=30)
    db_session.add(parent)
    db_session.commit()
    body = client.get("/api/initiatives/suggestions", params={"sprint_days": 2}).json()
    [sprint] = [s for s in body["suggestions"] if s["kind"] == "sprint"]
    assert body["sprint"]["basis"] == "workspace_throughput"
    assert body["sprint"]["capacity"] == 2
    # The in-progress feature always rides; then the best backlog item that fits.
    ids = [i["id"] for i in sprint["items"]]
    assert themed["running"].id in ids
    backlog = [i for i in sprint["items"] if i["state"] != "in_progress"]
    assert sum(i["cost"] for i in backlog) <= 2


def test_only_a_sprint_when_nothing_shares_a_theme(client, db_session: Session, workspace):
    m = _ticket(db_session, workspace, "Billing export", WorkItemType.MILESTONE)
    _ticket(db_session, workspace, "Export crash", WorkItemType.BUG, parent=m)
    body = client.get("/api/initiatives/suggestions").json()
    assert [s["kind"] for s in body["suggestions"]] == ["sprint"]


def test_sprint_days_out_of_range_is_rejected(client):
    assert client.get("/api/initiatives/suggestions", params={"sprint_days": 0}).status_code == 422
    assert client.get("/api/initiatives/suggestions", params={"sprint_days": 99}).status_code == 422


def test_apply_creates_initiatives_and_moves_items(client, db_session: Session, themed):
    res = client.post(
        "/api/initiatives/suggestions/apply",
        json={
            "initiatives": [
                {"title": "Canvas", "item_ids": [themed["a"].id, themed["b"].id]},
                {"title": "Sprint", "description": "Two weeks", "item_ids": [themed["bug"].id]},
            ]
        },
    )
    assert res.status_code == 201, res.text
    created = res.json()["created"]
    assert [(c["title"], c["attached"]) for c in created] == [("Canvas", 2), ("Sprint", 1)]
    for key in ("a", "b", "bug"):
        db_session.refresh(themed[key])
    assert themed["a"].parent_ticket_id == created[0]["id"]
    assert themed["bug"].parent_ticket_id == created[1]["id"]
    # The initiative rolls up its new children's state, as a manual attach does.
    sprint = db_session.get(Ticket, created[0]["id"])
    assert sprint is not None and sprint.state == TicketState.IN_PROGRESS

    listed = {r["id"]: r for r in client.get("/api/initiatives").json()}
    assert [m["work_item_type"] for m in listed[created[1]["id"]]["milestones"]] == ["bug"]


def test_apply_refuses_a_stale_batch_without_writing(client, db_session: Session, themed):
    first = client.post(
        "/api/initiatives/suggestions/apply",
        json={"initiatives": [{"title": "Taken", "item_ids": [themed["a"].id]}]},
    )
    assert first.status_code == 201
    before = len(db_session.exec(select(Ticket)).all())
    res = client.post(
        "/api/initiatives/suggestions/apply",
        json={
            "initiatives": [
                {"title": "Fresh", "item_ids": [themed["c"].id]},
                {"title": "Again", "item_ids": [themed["a"].id]},
            ]
        },
    )
    assert res.status_code == 409, res.text
    assert len(db_session.exec(select(Ticket)).all()) == before


def test_apply_rejects_an_item_in_two_initiatives(client, themed):
    res = client.post(
        "/api/initiatives/suggestions/apply",
        json={
            "initiatives": [
                {"title": "One", "item_ids": [themed["a"].id]},
                {"title": "Two", "item_ids": [themed["a"].id]},
            ]
        },
    )
    assert res.status_code == 400


def test_apply_rejects_a_type_an_initiative_cannot_hold(client, db_session, workspace, themed):
    cap = _ticket(
        db_session, workspace, "A capability", WorkItemType.CAPABILITY, parent=themed["queued"]
    )
    res = client.post(
        "/api/initiatives/suggestions/apply",
        json={"initiatives": [{"title": "Bad", "item_ids": [cap.id]}]},
    )
    assert res.status_code == 400


def _agent_reply(payload: dict) -> str:
    return "Here you go:\n```json\n" + json.dumps(payload) + "\n```"


def test_agent_regroup_validates_ids_and_reports_what_it_dropped(client, themed):
    reply = _agent_reply(
        {
            "suggestions": [
                {
                    "kind": "theme",
                    "title": "Canvas that scales",
                    "rationale": "Both are canvas work",
                    "item_ids": [themed["a"].external_id, themed["b"].id, "made-up-id"],
                },
                {"kind": "theme", "title": "Again", "item_ids": [themed["a"].id]},
                {
                    "kind": "sprint",
                    "title": "This fortnight",
                    "item_ids": [themed["bug"].id, themed["a"].id],
                },
            ]
        }
    )
    with mock.patch.dict("os.environ", {GROUPER_CLI_PROFILE.stub_env: reply}):
        res = client.post("/api/initiatives/suggestions/agent", json={"sprint_days": 14})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["source"] == "agent"
    assert [(s["kind"], s["title"]) for s in body["suggestions"]] == [
        ("theme", "Canvas that scales"),
        ("sprint", "This fortnight"),
    ]
    assert {i["id"] for i in body["suggestions"][0]["items"]} == {themed["a"].id, themed["b"].id}
    assert [i["id"] for i in body["suggestions"][1]["items"]] == [themed["bug"].id]
    joined = " ".join(body["warnings"])
    assert "made-up-id" in joined
    assert "Again" in joined
    assert themed["a"].id in joined  # a milestone is not a sprint candidate
    assert body["sprint"]["planned"] == 1


def test_agent_reply_without_json_is_a_502(client, themed):
    with mock.patch.dict("os.environ", {GROUPER_CLI_PROFILE.stub_env: "I could not decide."}):
        res = client.post("/api/initiatives/suggestions/agent", json={})
    assert res.status_code == 502


def test_sprint_that_takes_a_milestones_last_work_says_so(client, db_session, workspace, themed):
    # b also has finished work, so taking its one open feature leaves it all-resolved.
    _ticket(db_session, workspace, "Shipped", WorkItemType.FEATURE, parent=themed["b"],
            state=TicketState.DONE)  # fmt: skip
    body = client.get("/api/initiatives/suggestions").json()
    [sprint] = [s for s in body["suggestions"] if s["kind"] == "sprint"]
    assert themed["b"].external_id in sprint["empties"]
    # a would have no children at all, which keeps its state rather than finishing it.
    assert themed["a"].external_id not in sprint["empties"]
    # c keeps its blocked feature, which a sprint never takes.
    assert themed["c"].external_id not in sprint["empties"]


def test_work_already_in_a_sprint_is_charged_before_planning_another(
    client, db_session: Session, workspace, themed
):
    first = client.get("/api/initiatives/suggestions", params={"sprint_days": 7}).json()
    [sprint] = [s for s in first["suggestions"] if s["kind"] == "sprint"]
    mine = [i["id"] for i in _mine(sprint["items"], workspace)]
    res = client.post(
        "/api/initiatives/suggestions/apply",
        json={"initiatives": [{"title": "Sprint one", "item_ids": mine}]},
    )
    assert res.status_code == 201, res.text

    # More backlog work arrives; the unmeasured budget (3 a week) is already spent.
    extra = _ticket(db_session, workspace, "Fresh milestone", WorkItemType.MILESTONE)
    _ticket(db_session, workspace, "Fresh feature", WorkItemType.FEATURE, parent=extra)
    again = client.get("/api/initiatives/suggestions", params={"sprint_days": 7}).json()
    assert again["sprint"]["committed"] >= len(mine)
    sprints = [s for s in again["suggestions"] if s["kind"] == "sprint"]
    assert all(not _mine(s["items"], workspace) for s in sprints)


def test_theme_is_named_by_a_phrase_most_members_share():
    items = [
        {"id": str(n), "title": title}
        for n, title in enumerate(
            ["Agent prompt contract", "Agent prompts are versioned", "Agent sandbox"]
        )
    ]
    from_items = [
        SuggestedItem(
            id=i["id"],
            external_id=i["id"],
            title=i["title"],
            state=TicketState.BACKLOG,
            work_item_type=WorkItemType.MILESTONE,
            workspace_slug="w",
            from_milestone="",
            cost=1,
        )  # fmt: skip
        for i in items
    ]
    [theme] = theme_suggestions(from_items)
    assert theme.title == "Agent prompt"
    # A phrase only one of three shares does not name the group.
    from_items[1] = from_items[1].model_copy(update={"title": "Agent sandbox two"})
    [theme] = theme_suggestions(from_items)
    assert theme.title == "Agent sandbox"


def test_sprint_end_becomes_the_initiatives_plan_target(client, workspace, themed):
    body = client.get("/api/initiatives/suggestions", params={"sprint_days": 7}).json()
    [sprint] = [s for s in body["suggestions"] if s["kind"] == "sprint"]
    assert sprint["target_date"]
    res = client.post(
        "/api/initiatives/suggestions/apply",
        json={
            "initiatives": [
                {
                    "title": sprint["title"],
                    "item_ids": [themed["bug"].id],
                    "target_date": sprint["target_date"],
                }
            ]
        },
    )
    assert res.status_code == 201, res.text
    plan = client.get(f"/api/initiatives/{res.json()['created'][0]['id']}/plan").json()
    assert plan["target_date"] == sprint["target_date"]


def test_a_failed_agent_turn_names_the_runtime_it_ran_under(client, themed):
    with (
        mock.patch(
            "loregarden.services.initiative_suggestion_agent.run_cli_agent_turn",
            side_effect=RuntimeError("OAuth session expired"),
        ),
        mock.patch(
            "loregarden.services.initiative_suggestion_agent.resolve_workspace_root",
            return_value=mock.Mock(is_dir=mock.Mock(return_value=True)),
        ),
    ):
        res = client.post("/api/initiatives/suggestions/agent", json={})
    assert res.status_code == 502
    detail = res.json()["detail"]
    assert "OAuth session expired" in detail
    assert "runtime" in detail


def test_moving_sprint_work_back_reopens_the_milestone_it_emptied(
    client, db_session, workspace, themed
):
    """The undo path: a milestone the sprint emptied rolled up as done; work returning reopens it."""
    _ticket(db_session, workspace, "Shipped", WorkItemType.FEATURE, parent=themed["b"],
            state=TicketState.DONE)  # fmt: skip
    res = client.post(
        "/api/initiatives/suggestions/apply",
        json={"initiatives": [{"title": "Sprint", "item_ids": [themed["running"].id]}]},
    )
    assert res.status_code == 201, res.text
    db_session.refresh(themed["b"])
    assert themed["b"].state == TicketState.DONE

    back = client.patch(
        f"/api/tickets/{themed['running'].id}", json={"parent_ticket_id": themed["b"].id}
    )
    assert back.status_code == 200, back.text
    db_session.refresh(themed["b"])
    assert themed["b"].state == TicketState.IN_PROGRESS


def test_addable_work_leaves_out_reviews_and_work_already_in_an_initiative(
    client, db_session, workspace, themed
):
    review = _ticket(
        db_session, workspace, "Nodes review", WorkItemType.FEATURE, parent=themed["a"]
    )
    review.is_integration_review = True
    db_session.add(review)
    db_session.commit()
    sprint = TicketService(db_session).create_ticket(
        title="Sprint", work_item_type=WorkItemType.INITIATIVE
    )
    reparent_ticket(db_session, themed["running"], sprint.id)
    db_session.commit()

    res = client.get(f"/api/initiatives/{sprint.id}/addable-work", params={"search": "nodes"})
    assert res.status_code == 200, res.text
    rows = {r["id"]: r for r in res.json()}
    assert set(rows) == {themed["queued"].id}
    assert rows[themed["queued"].id]["from_milestone"] == themed["a"].external_id
    assert (
        client.get(f"/api/initiatives/{sprint.id}/addable-work", params={"search": "n"}).status_code
        == 422
    )

    # Every write path refuses it, not just the search.
    moved = client.patch(f"/api/tickets/{review.id}", json={"parent_ticket_id": sprint.id})
    assert moved.status_code == 400
    applied = client.post(
        "/api/initiatives/suggestions/apply",
        json={"initiatives": [{"title": "Bad", "item_ids": [review.id]}]},
    )
    assert applied.status_code == 400
