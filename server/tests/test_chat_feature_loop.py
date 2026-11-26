"""A chat's feature loop: Baxter proposes criteria, the operator decides, rounds run."""

from __future__ import annotations

import json
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from loregarden.db.versions.baxter_chat_focus_and_feature_loop import (
    m_baxter_chat_focus_and_feature_loop,
)
from loregarden.mcp.tool_ids import AUTO_APPROVED_MCP_TOOLS, McpTool
from loregarden.mcp.tool_registry import EXTENDED_TOOLS
from loregarden.models.domain import (
    BaxterChatSession,
    FeatureCriterion,
    FeatureCriterionStatus,
    Ticket,
    TicketState,
)
from loregarden.models.domain.chat_enums import FeatureLoopStatus
from loregarden.services.acceptance_criteria import MAX_ACCEPTANCE_CRITERIA, load_criteria
from loregarden.services.chat_feature_loop import (
    LoopAssessment,
    _assess_capacity,
    _capacity_message,
    advance_feature_loops,
)
from sqlalchemy import create_engine, text
from sqlmodel import Session, select

BASE = "/api/workspaces/loregarden/baxter-chat/sessions"


@pytest.fixture
def started_workflows():
    """Stand in for the orchestrator: record which tickets a round started."""
    with mock.patch("loregarden.services.chat_feature_loop._start_workflow") as start:
        yield start


@pytest.fixture
def loop(client: TestClient) -> dict:
    session_id = client.post(BASE, json={}).json()["id"]
    res = client.post(f"{BASE}/{session_id}/feature", json={"title": "Dark mode"})
    assert res.status_code == 200, res.text
    return res.json()


def _propose(db_session: Session, session_id: str, criteria: list[str]) -> dict:
    handler = EXTENDED_TOOLS[McpTool.PROPOSE_FEATURE_CRITERIA.value]
    return json.loads(handler(db_session, {"chat_session_id": session_id, "criteria": criteria}))


def _ticket(db_session: Session, snapshot: dict) -> Ticket:
    db_session.expire_all()
    ticket = db_session.get(Ticket, snapshot["feature"]["ticket"]["id"])
    assert ticket is not None
    return ticket


def _statuses(snapshot: dict) -> dict[str, str]:
    return {c["text"]: c["status"] for c in snapshot["feature"]["criteria"]}


def test_starting_a_loop_files_a_milestone_and_runs_nothing(loop: dict, started_workflows):
    feature = loop["feature"]

    assert feature["ticket"]["title"] == "Dark mode"
    assert feature["ticket"]["workspace_slug"] == "loregarden"
    assert feature["status"] == "idle"
    assert feature["criteria"] == []
    started_workflows.assert_not_called()


def test_proposals_wait_for_the_operator_and_skip_duplicates(
    client: TestClient, db_session: Session, loop: dict, started_workflows
):
    first = _propose(db_session, loop["id"], ["Toggle persists", "Toggle persists"])
    again = _propose(db_session, loop["id"], ["Toggle persists", "Respects OS setting"])

    snapshot = client.get(f"{BASE}/{loop['id']}").json()
    assert len(first["proposed"]) == 1
    assert [p["text"] for p in again["proposed"]] == ["Respects OS setting"]
    assert _statuses(snapshot) == {"Toggle persists": "proposed", "Respects OS setting": "proposed"}
    assert load_criteria(_ticket(db_session, snapshot).acceptance_criteria_json) == []
    started_workflows.assert_not_called()


def test_accepting_starts_a_round_and_dropping_does_not(
    client: TestClient, db_session: Session, loop: dict, started_workflows
):
    proposed = _propose(db_session, loop["id"], ["Toggle persists", "Ugly idea"])["proposed"]
    keep, drop = proposed[0]["id"], proposed[1]["id"]

    client.post(f"{BASE}/{loop['id']}/feature/decisions", json={"ids": [drop], "accept": False})
    res = client.post(
        f"{BASE}/{loop['id']}/feature/decisions", json={"ids": [keep], "accept": True}
    )

    assert res.status_code == 200, res.text
    snapshot = res.json()
    assert _statuses(snapshot) == {"Toggle persists": "in_round"}
    assert snapshot["feature"]["round"] == 1
    assert load_criteria(_ticket(db_session, snapshot).acceptance_criteria_json) == [
        "Toggle persists"
    ]
    started_workflows.assert_called_once()


def test_criteria_queued_mid_round_wait_then_run_on_a_reopened_ticket(
    client: TestClient, db_session: Session, loop: dict, started_workflows
):
    client.post(f"{BASE}/{loop['id']}/feature/criteria", json={"text": "Toggle persists"})
    with mock.patch("loregarden.services.chat_feature_loop.activity_for", return_value="running"):
        mid = client.post(f"{BASE}/{loop['id']}/feature/criteria", json={"text": "Respects OS"})
    assert _statuses(mid.json()) == {"Toggle persists": "in_round", "Respects OS": "queued"}

    ticket = _ticket(db_session, loop)
    ticket.state = TicketState.DONE
    db_session.add(ticket)
    db_session.commit()
    advance_feature_loops(db_session)

    snapshot = client.get(f"{BASE}/{loop['id']}").json()
    assert _statuses(snapshot) == {"Toggle persists": "met", "Respects OS": "in_round"}
    assert snapshot["feature"]["round"] == 2
    ticket = _ticket(db_session, snapshot)
    assert ticket.state != TicketState.DONE
    assert load_criteria(ticket.acceptance_criteria_json) == ["Toggle persists", "Respects OS"]
    assert started_workflows.call_count == 2


def test_a_blocked_ticket_holds_the_queue_and_says_why(
    client: TestClient, db_session: Session, loop: dict, started_workflows
):
    ticket = _ticket(db_session, loop)
    ticket.state = TicketState.BLOCKED
    ticket.blocking_issues = "Gate failed: lint"
    db_session.add(ticket)
    db_session.commit()

    snapshot = client.post(
        f"{BASE}/{loop['id']}/feature/criteria", json={"text": "Toggle persists"}
    ).json()

    assert snapshot["feature"]["status"] == "blocked"
    assert snapshot["feature"]["reason"] == "Gate failed: lint"
    assert _statuses(snapshot) == {"Toggle persists": "queued"}
    started_workflows.assert_not_called()


def test_a_queue_the_round_outgrew_holds_and_says_why(
    client: TestClient, db_session: Session, loop: dict, started_workflows
):
    client.post(f"{BASE}/{loop['id']}/feature/criteria", json={"text": "Toggle persists"})
    with mock.patch("loregarden.services.chat_feature_loop.activity_for", return_value="running"):
        client.post(f"{BASE}/{loop['id']}/feature/criteria", json={"text": "Respects OS"})
    # The round's own stages fill the ticket after "Respects OS" was accepted.
    ticket = _ticket(db_session, loop)
    ticket.acceptance_criteria_json = json.dumps(
        [f"Spec criterion {n}" for n in range(MAX_ACCEPTANCE_CRITERIA)]
    )
    ticket.state = TicketState.DONE
    db_session.add(ticket)
    db_session.commit()

    with mock.patch("loregarden.services.chat_feature_loop.logger") as log:
        advance_feature_loops(db_session)
    log.exception.assert_not_called()

    snapshot = client.get(f"{BASE}/{loop['id']}").json()
    assert snapshot["feature"]["status"] == "blocked"
    # Equality, not "contains": criterion 11 folds this reason into the one
    # builder, and a substring match would survive a second wording that
    # happened to carry the same clause.
    assert snapshot["feature"]["reason"] == _capacity_message(MAX_ACCEPTANCE_CRITERIA, 1, None)
    assert _statuses(snapshot) == {"Toggle persists": "met", "Respects OS": "queued"}
    assert started_workflows.call_count == 1


def test_accepting_past_the_criteria_cap_is_refused_before_anything_changes(
    client: TestClient, db_session: Session, loop: dict, started_workflows
):
    many = [f"Criterion {n}" for n in range(MAX_ACCEPTANCE_CRITERIA + 1)]
    ids = [p["id"] for p in _propose(db_session, loop["id"], many)["proposed"]]

    res = client.post(f"{BASE}/{loop['id']}/feature/decisions", json={"ids": ids, "accept": True})

    assert res.status_code == 400
    assert str(MAX_ACCEPTANCE_CRITERIA) in res.json()["detail"]
    db_session.expire_all()
    statuses = {
        row.status
        for row in db_session.exec(
            select(FeatureCriterion).where(FeatureCriterion.session_id == loop["id"])
        ).all()
    }
    assert statuses == {FeatureCriterionStatus.PROPOSED}
    started_workflows.assert_not_called()


def _set_counts(
    db_session: Session,
    loop: dict,
    *,
    on_ticket: int,
    queued: int,
    proposed: int = 0,
) -> Ticket:
    """Put the loop in an exact capacity state, replacing whatever it held."""
    ticket = _ticket(db_session, loop)
    ticket.acceptance_criteria_json = json.dumps([f"On ticket {n}" for n in range(on_ticket)])
    db_session.add(ticket)
    for row in db_session.exec(
        select(FeatureCriterion).where(FeatureCriterion.session_id == loop["id"])
    ).all():
        db_session.delete(row)
    for status, count in (
        (FeatureCriterionStatus.QUEUED, queued),
        (FeatureCriterionStatus.PROPOSED, proposed),
    ):
        for n in range(count):
            db_session.add(
                FeatureCriterion(
                    session_id=loop["id"],
                    ticket_id=ticket.id,
                    text=f"{status.value.title()} {n}",
                    status=status,
                )
            )
    db_session.commit()
    return ticket


def _capacity(client: TestClient, loop: dict) -> dict:
    return client.get(f"{BASE}/{loop['id']}").json()["feature"]["capacity"]


def test_the_feature_card_is_told_there_is_room(
    client: TestClient, db_session: Session, loop: dict, started_workflows
):
    _set_counts(db_session, loop, on_ticket=MAX_ACCEPTANCE_CRITERIA - 4, queued=2)

    capacity = _capacity(client, loop)

    assert capacity["on_ticket"] == MAX_ACCEPTANCE_CRITERIA - 4
    assert capacity["queued"] == 2
    assert capacity["max"] == MAX_ACCEPTANCE_CRITERIA
    assert capacity["at_cap"] is False
    # "There is room" and "there is a reason you cannot" must not collapse into
    # one answer: a message under the cap would render as a dead-controls notice.
    assert capacity["message"] is None


def test_the_feature_card_is_told_the_cap_is_reached_in_the_servers_own_words(
    client: TestClient, db_session: Session, loop: dict, started_workflows
):
    on_ticket, queued = MAX_ACCEPTANCE_CRITERIA - 1, 1
    _set_counts(db_session, loop, on_ticket=on_ticket, queued=queued)

    capacity = _capacity(client, loop)

    assert capacity["at_cap"] is True
    # Exact equality, not "contains": the card renders the string it is given and
    # composes none of its own, so this is the only place the wording exists.
    assert capacity["message"] == _capacity_message(on_ticket, queued, None)


@pytest.mark.parametrize(
    ("on_ticket", "queued", "at_cap"),
    [
        (MAX_ACCEPTANCE_CRITERIA - 2, 1, False),  # one below: still room for one
        (MAX_ACCEPTANCE_CRITERIA - 1, 1, True),  # exactly at it
        (MAX_ACCEPTANCE_CRITERIA, 0, True),  # full with nothing queued
        # Over the cap already. `would_exceed` (before <= cap and after > cap)
        # answers False here, which would read as "there is room" on a ticket
        # that is past the limit; `would_grow_past` answers True. The two
        # predicates disagree only at (cap+1, cap+2), which is this row.
        (MAX_ACCEPTANCE_CRITERIA + 1, 0, True),
    ],
)
def test_the_cap_boundary_is_the_growth_guards_and_not_this_modules(
    client: TestClient,
    db_session: Session,
    loop: dict,
    started_workflows,
    on_ticket: int,
    queued: int,
    at_cap: bool,
):
    _set_counts(db_session, loop, on_ticket=on_ticket, queued=queued)

    capacity = _capacity(client, loop)

    assert capacity["at_cap"] is at_cap
    assert (capacity["message"] is not None) is at_cap


def test_proposals_waiting_on_the_operator_do_not_fill_the_ticket(
    client: TestClient, db_session: Session, loop: dict, started_workflows
):
    # `add_criterion` counts QUEUED rows, not every open row: a proposal is a
    # draft and reaches the ticket only once accepted. Counting proposals would
    # disable the controls that accept them.
    _set_counts(db_session, loop, on_ticket=MAX_ACCEPTANCE_CRITERIA - 1, queued=0, proposed=4)

    capacity = _capacity(client, loop)

    assert capacity["queued"] == 0
    assert capacity["at_cap"] is False
    assert capacity["message"] is None


def test_the_cards_message_and_the_refusal_an_accept_raises_are_one_helper(
    client: TestClient, db_session: Session, loop: dict, started_workflows
):
    """The test that makes "the same words as the server's refusal" falsifiable."""
    on_ticket = MAX_ACCEPTANCE_CRITERIA
    _set_counts(db_session, loop, on_ticket=on_ticket, queued=0)
    [proposal] = _propose(db_session, loop["id"], ["One more thing"])["proposed"]

    card_message = _capacity(client, loop)["message"]
    res = client.post(
        f"{BASE}/{loop['id']}/feature/decisions", json={"ids": [proposal["id"]], "accept": True}
    )

    assert res.status_code == 400
    refusal = res.json()["detail"]
    assert refusal == _capacity_message(on_ticket, 0, 1)
    assert card_message == _capacity_message(on_ticket, 0, None)
    # One sentence, two audiences. The clause that differs is the only thing
    # that may differ: a reword of either reds this.
    opening = f"The ticket holds at most {MAX_ACCEPTANCE_CRITERIA} criteria: it has {on_ticket}"
    closing = (
        "Remove criteria from the ticket, or finish this feature and start a new loop for the rest."
    )
    for message in (refusal, card_message):
        assert message.startswith(opening)
        assert message.endswith(closing)
    assert "this would add 1" in refusal
    assert "would add" not in card_message
    started_workflows.assert_not_called()


def test_adding_a_criterion_at_the_cap_is_refused_in_the_same_words(
    client: TestClient, db_session: Session, loop: dict, started_workflows
):
    # The add path calls the same `_check_capacity` with adding=1, which is why
    # the card disables the Queue field alongside Accept.
    on_ticket = MAX_ACCEPTANCE_CRITERIA - 1
    _set_counts(db_session, loop, on_ticket=on_ticket, queued=1)

    res = client.post(f"{BASE}/{loop['id']}/feature/criteria", json={"text": "Works offline"})

    assert res.status_code == 400
    assert res.json()["detail"] == _capacity_message(on_ticket, 1, 1)
    assert _capacity(client, loop)["at_cap"] is True


def test_the_capacity_payload_is_on_every_loop_snapshot(loop: dict):
    # A derived field that may go missing reads as "not at cap" on the client,
    # which is the exact lie this ticket removes. It is required, so a fresh
    # loop with nothing in it carries it too.
    capacity = loop["feature"]["capacity"]

    assert set(capacity) == {"on_ticket", "queued", "max", "at_cap", "message"}
    assert capacity == {
        "on_ticket": 0,
        "queued": 0,
        "max": MAX_ACCEPTANCE_CRITERIA,
        "at_cap": False,
        "message": None,
    }


def test_the_capacity_message_says_nothing_about_adding_when_nobody_clicked():
    # Pure, so the two forms can be compared without a database. The card has
    # not clicked anything, so there is no N to name; everything else is the
    # refusal's sentence verbatim.
    with_adding = _capacity_message(7, 5, 2)
    without = _capacity_message(7, 5, None)

    assert with_adding == (
        f"The ticket holds at most {MAX_ACCEPTANCE_CRITERIA} criteria: it has 7, "
        "5 are queued, and this would add 2. Remove criteria from the ticket, or "
        "finish this feature and start a new loop for the rest."
    )
    assert without == (
        f"The ticket holds at most {MAX_ACCEPTANCE_CRITERIA} criteria: it has 7, "
        "and 5 are queued. Remove criteria from the ticket, or "
        "finish this feature and start a new loop for the rest."
    )
    # Deterministic: same counts, same sentence, every call.
    assert _capacity_message(7, 5, None) == without


# --- one wording, three audiences (criterion 11) --------------------------------
#
# `_assess_capacity` (the reconcile pass's reason, rendered as `feature.reason`),
# `feature_loop_view` (the card's `capacity.message`) and `_check_capacity` (the
# refusal a write raises) each said the cap in their own words. Folding them into
# `_capacity_message` is only worth anything if a reword of one reds the suite.


def _stalled(on_ticket: int, queued: int) -> LoopAssessment:
    """`_assess_capacity` over two counts. It reads nothing else, so no database."""
    ticket = Ticket(
        external_id="lg-dark-1",
        title="Dark mode",
        acceptance_criteria_json=json.dumps([f"On ticket {n}" for n in range(on_ticket)]),
    )
    rows = [
        FeatureCriterion(
            session_id="s1",
            ticket_id=ticket.id,
            text=f"Queued {n}",
            status=FeatureCriterionStatus.QUEUED,
        )
        for n in range(queued)
    ]
    return _assess_capacity(ticket, rows)


def test_a_held_queues_reason_is_the_helper_the_card_and_the_refusal_also_use():
    # Asymmetric counts on purpose: a reason built with on_ticket and queued
    # swapped still reads plausibly at 12 and 12, and would survive an equality
    # written against the helper called the same wrong way.
    reading = _stalled(on_ticket=MAX_ACCEPTANCE_CRITERIA, queued=3)

    assert reading.status == FeatureLoopStatus.BLOCKED
    assert reading.reason == _capacity_message(MAX_ACCEPTANCE_CRITERIA, 3, None)


def test_a_held_queue_names_no_added_count_because_nobody_clicked():
    # `adding` is what a *write* asked for. `_assess_capacity` runs on a
    # reconcile pass with nothing clicked, so passing `queued` (or a literal 1)
    # there prints "and this would add 3" on a card the operator never touched —
    # a mutant the equality above would catch only because the helper is called
    # correctly here, and which this states outright.
    assert "would add" not in _stalled(MAX_ACCEPTANCE_CRITERIA, 3).reason


def test_the_next_round_clause_is_carried_by_the_status_not_a_second_sentence():
    # R8, assumption 5: "so the next round cannot start" moves to the BLOCKED
    # status the pill renders as "stalled". A sentence that still says it is the
    # second wording this criterion exists to remove — and the status is what
    # has to carry it, so both halves are asserted.
    reading = _stalled(MAX_ACCEPTANCE_CRITERIA, 1)

    assert reading.status == FeatureLoopStatus.BLOCKED
    assert "next round" not in reading.reason


def test_a_queue_that_still_fits_is_idle_with_nothing_to_say():
    # The boundary the folding must not move: below the cap there is no
    # refusal, so there is no sentence either.
    reading = _stalled(MAX_ACCEPTANCE_CRITERIA - 2, 1)

    assert reading.status == FeatureLoopStatus.IDLE
    assert reading.reason == ""


@pytest.mark.parametrize("adding", [None, 1, 3])
def test_the_one_wording_names_both_ways_out(adding: int | None):
    # Folding the builders drops `_assess_capacity`'s "Remove criteria from the
    # ticket" escape unless the surviving wording gains it. Lowering on_ticket
    # really does clear the refusal, and every audience reads this string —
    # including the agent that gets a raised FeatureLoopError as text.
    message = _capacity_message(MAX_ACCEPTANCE_CRITERIA, 1, adding)

    assert message.endswith(
        "Remove criteria from the ticket, or finish this feature and start a new loop for the rest."
    )


def test_the_three_at_cap_sentences_are_one_helpers_output(
    client: TestClient, db_session: Session, loop: dict, started_workflows
):
    """Criterion 11's named test: the card, the card's reason and the refusal.

    All three read off one response and one write, so nothing here can agree by
    being generated twice from the same copied literal.
    """
    on_ticket, queued = MAX_ACCEPTANCE_CRITERIA, 1
    _set_counts(db_session, loop, on_ticket=on_ticket, queued=queued)
    [proposal] = _propose(db_session, loop["id"], ["One more thing"])["proposed"]

    feature = client.get(f"{BASE}/{loop['id']}").json()["feature"]
    refused = client.post(
        f"{BASE}/{loop['id']}/feature/decisions", json={"ids": [proposal["id"]], "accept": True}
    )

    assert feature["status"] == "blocked"
    assert feature["reason"] == _capacity_message(on_ticket, queued, None)
    assert feature["capacity"]["message"] == _capacity_message(on_ticket, queued, None)
    assert refused.status_code == 400
    assert refused.json()["detail"] == _capacity_message(on_ticket, queued, 1)
    # At the cap the card is handed the same sentence twice. That is correct on
    # the wire and a stutter on screen; criterion 12 is the client half that
    # renders it once, and this is the fixture shape it renders.
    assert feature["reason"] == feature["capacity"]["message"]
    started_workflows.assert_not_called()


def test_the_turn_is_told_its_loop_and_granted_only_the_proposal_tool(
    client: TestClient, db_session: Session, loop: dict, monkeypatch
):
    from loregarden.services import agent_turn_runner, baxter_chat_service

    captured: dict[str, object] = {}

    def fake_run(_profile, *, prompt, granted_tools=None, **_kwargs):
        captured["prompt"] = prompt
        captured["tools"] = list(granted_tools or [])
        return "ok"

    monkeypatch.delenv("LOREGARDEN_BAXTER_CHAT_STUB_RESPONSE", raising=False)
    monkeypatch.setattr(baxter_chat_service, "resolve_effective_adapter", lambda **_: "cursor")
    monkeypatch.setattr(
        baxter_chat_service, "get_agent", lambda _id: {"adapter": "cursor", "mcp_tools": []}
    )
    monkeypatch.setattr(agent_turn_runner, "run_cli_agent_turn", fake_run)

    client.post(f"{BASE}/{loop['id']}/messages", json={"content": "Add a toggle"})

    prompt = str(captured["prompt"])
    assert "## Feature loop" in prompt
    assert f"chat_session_id `{loop['id']}`" in prompt
    assert captured["tools"] == [McpTool.PROPOSE_FEATURE_CRITERIA.value]
    assert McpTool.PROPOSE_FEATURE_CRITERIA in AUTO_APPROVED_MCP_TOOLS


def test_deleting_a_looping_chat_keeps_its_ticket(
    client: TestClient, db_session: Session, loop: dict, started_workflows
):
    client.post(f"{BASE}/{loop['id']}/feature/criteria", json={"text": "Toggle persists"})
    ticket_id = loop["feature"]["ticket"]["id"]

    res = client.delete(f"{BASE}/{loop['id']}")

    assert res.status_code == 200, res.text
    db_session.expire_all()
    assert db_session.get(BaxterChatSession, loop["id"]) is None
    assert db_session.get(Ticket, ticket_id) is not None


def test_migration_is_idempotent_and_leaves_threads_without_a_loop():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE tickets (id TEXT PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE baxter_chat_sessions (id TEXT PRIMARY KEY, title TEXT)"))
        conn.execute(text("INSERT INTO baxter_chat_sessions (id, title) VALUES ('old', 't')"))
    with engine.begin() as conn:
        m_baxter_chat_focus_and_feature_loop(conn)
        m_baxter_chat_focus_and_feature_loop(conn)
        row = conn.execute(
            text("SELECT feature_ticket_id FROM baxter_chat_sessions WHERE id = 'old'")
        ).one()
        focus = conn.execute(text("SELECT count(*) FROM baxter_chat_focus")).scalar()

    assert tuple(row) == (None,)
    assert focus == 0
