"""The write-time size caps hold on every writer, and refuse the growth, not the size.

Two caps, two rules, deliberately different (lg-milestone-that-2250):

* **Criteria** refuse *growth* past 12 — ``after > cap and after > before``. The
  writers have an append mode whose ``merge_criteria`` dedupes, so growth is the
  thing to refuse and a re-run stage must stay legal.
* **Descriptions** refuse only the *crossing* — ``before <= cap and after > cap``.
  No writer has a description append mode; every write is a full replace, so a
  growth rule would refuse a shortening rewrite that overshot and destroy the
  agent's only copy of the text.

Measured read-only on the live database 2026-11-05: 2,733 tickets; 358 at exactly
12 criteria; 78 over 12; 49 over 15; 29 at 13-15; max 62 criteria; 34 descriptions
over 15,000 chars; max 159,960. 0 of the 78 over-12 tickets were created on or
after 2026-10-21, when the criteria guard landed — zero measured re-entry through
that half. 5 of the 34 over-cap descriptions were, which is the half with no guard.

Every test here is named literally — no f-strings, no ``.each``, no skips —
because ``invariant_registry`` names one of them in ``prevented_by`` and
``test_invariants.py`` looks that name up by literal text.
"""

from __future__ import annotations

import ast
import json
import logging
import pathlib
import re
from types import SimpleNamespace

import pytest
from loregarden.models.domain import (
    Ticket,
    UpdateTicketRequest,
    WorkItemType,
    Workspace,
)
from loregarden.services import monitor_escalation
from loregarden.services.acceptance_criteria import (
    MAX_ACCEPTANCE_CRITERIA,
    AcceptanceCriteriaCapError,
    guard_criteria_growth,
    load_criteria,
    serialize_criteria,
)
from loregarden.services.finalize_hierarchy import _build_ticket
from loregarden.services.invariant_registry import INVARIANTS
from loregarden.services.memory_store import AgentMemoryService
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.oversized_ticket_census import tickets_over_a_write_cap
from loregarden.services.reference_survey import ticket_writer
from loregarden.services.seed import seed_database
from loregarden.services.ticket_growth_guard import (
    MAX_DESCRIPTION_CHARS,
    DescriptionCapError,
    guard_description_growth,
    would_exceed,
)
from loregarden.services.ticket_service import TicketService
from loregarden.services.ticket_size_limits import (
    SPLIT_AT_ACCEPTANCE_CRITERIA,
    SPLIT_AT_DESCRIPTION_CHARS,
)
from loregarden.testing.prod_shape import CALIBRATION, build_prod_shape, prod_shape_id
from sqlmodel import Session, select
from tests.factories import make_ticket
from tests.mcp_helpers import call_mcp

SERVER_ROOT = pathlib.Path(__file__).resolve().parents[1]
PACKAGE_ROOT = SERVER_ROOT / "loregarden"
CLIENT_ROOT = SERVER_ROOT.parent / "client"


def _criteria(n: int) -> list[str]:
    return [f"criterion {i}" for i in range(n)]


@pytest.fixture(name="ws")
def ws_fixture(isolated_db):
    with Session(isolated_db) as session:
        seed_database(session)
        ws = session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()
        milestone = make_ticket(
            session,
            workspace_id=ws.id,
            external_id="proj-m-1",
            work_item_type=WorkItemType.MILESTONE,
        )
        return SimpleNamespace(id=ws.id, slug=ws.slug, milestone_id=milestone.id)


# ---- the two rules ----------------------------------------------------


def test_guard_refuses_growth_past_the_cap_and_nothing_else():
    guard_criteria_growth(_criteria(11), _criteria(12))
    guard_criteria_growth(_criteria(20), _criteria(20))
    guard_criteria_growth(_criteria(20), _criteria(14))
    with pytest.raises(AcceptanceCriteriaCapError, match="from 12 to 13"):
        guard_criteria_growth(_criteria(12), _criteria(13))
    with pytest.raises(AcceptanceCriteriaCapError):
        guard_criteria_growth(_criteria(20), _criteria(21))


def test_description_guard_refuses_only_the_crossing():
    """The truth table, straight off ``would_exceed(before, after, cap)``.

    Separate from the criteria rule on purpose: ``(15001, 15002)`` passes here
    and the criteria equivalent refuses. See the module docstring.
    """
    cap = MAX_DESCRIPTION_CHARS
    assert would_exceed(0, cap, cap) is False
    assert would_exceed(0, cap + 1, cap) is True
    assert would_exceed(cap + 1, cap + 2, cap) is False
    assert would_exceed(159_960, 14_000, cap) is False
    assert would_exceed(14_000, 14_000, cap) is False

    # `would_exceed` takes lengths; the guard takes the text, because every
    # caller has the text and none of them has a length. Keeping the two
    # signatures apart here is deliberate: a guard that also accepted an int
    # would need a runtime type switch in a module whose whole point is that it
    # holds no types.
    guard_description_growth("", "x" * cap)
    guard_description_growth("x" * (cap + 1), "y" * (cap + 2))
    guard_description_growth("x" * 159_960, "y" * 14_000)
    guard_description_growth(None, "x" * cap)
    guard_description_growth(None, None)
    guard_description_growth(None, "")
    guard_description_growth("x" * (cap + 1), None)
    with pytest.raises(DescriptionCapError):
        guard_description_growth("", "x" * (cap + 1))
    with pytest.raises(DescriptionCapError):
        guard_description_growth(None, "x" * (cap + 1))


def test_would_exceed_is_the_crossing_predicate_at_every_boundary():
    """The whole 2-D neighbourhood of the cap, not five sampled points.

    `would_exceed` is four characters of logic with four ways to be subtly
    wrong: `<` for `<=`, `>` for `>=`, the two arguments transposed, and `or`
    for `and`. A handful of examples leaves at least two of those alive. This
    enumerates every (before, after) pair around a small cap and compares
    against the rule written out longhand, so any one of the four reds it.
    """
    cap = 4
    for before in range(cap + 4):
        for after in range(cap + 4):
            assert would_exceed(before, after, cap) is (before <= cap and after > cap), (
                before,
                after,
            )

    # Transposing the arguments is not a no-op, and the test must be able to see
    # that: at least one pair has to disagree with its mirror.
    assert any(
        would_exceed(a, b, cap) != would_exceed(b, a, cap)
        for a in range(cap + 3)
        for b in range(cap + 3)
    )
    # The real cap, at its own three boundary points.
    real = MAX_DESCRIPTION_CHARS
    assert would_exceed(real - 1, real, real) is False
    assert would_exceed(real, real + 1, real) is True
    assert would_exceed(real + 1, real + 1, real) is False


def test_the_description_cap_counts_characters_not_bytes():
    """``MAX_DESCRIPTION_CHARS``, and a ticket description is text.

    ``len(text.encode())`` passes every ASCII test in this module and then
    refuses a 5,000-character description written in anything outside Latin-1 —
    an emoji is four bytes, so a cap measured in bytes is a cap of 3,750 for
    that author. Both halves matter: the astral-plane string at the cap is
    accepted, and one character past it is refused, so this cannot be satisfied
    by widening the cap.
    """
    cap = MAX_DESCRIPTION_CHARS
    emoji = "\N{ROCKET}"
    assert len(emoji.encode("utf-8")) == 4
    guard_description_growth(None, emoji * cap)
    with pytest.raises(DescriptionCapError) as refused:
        guard_description_growth(None, emoji * (cap + 1))
    assert refused.value.payload["after"] == cap + 1

    # A combining sequence is two code points and reads as one glyph; the cap is
    # code points, which is what `len` and the editor's character count agree on.
    guard_description_growth(None, "e\N{COMBINING ACUTE ACCENT}" * (cap // 2))


def test_the_criteria_guard_counts_the_list_that_would_be_stored():
    """Blank and duplicate-looking rows are not criteria, and must not count.

    ``normalize_criteria`` strips and drops blanks, and it runs on the way to
    the column — so a 14-row request that stores 12 criteria has not grown the
    ticket past the cap and must not be refused. A guard that counts the list it
    was handed, rather than the list that would be stored, refuses a legal write
    and the agent cannot tell why: the count in the refusal would not match
    anything it sent.
    """
    fourteen_rows_twelve_criteria = [*_criteria(12), "   ", "\n\t "]
    guard_criteria_growth(_criteria(12), fourteen_rows_twelve_criteria)
    guard_criteria_growth([], fourteen_rows_twelve_criteria)

    # Whitespace around a criterion does not make it a new one, so this is a
    # no-op write on an at-cap ticket rather than a thirteenth criterion.
    guard_criteria_growth(_criteria(12), [f"  {line}  " for line in _criteria(12)])

    # And the refusal still counts what would be stored: 12 real rows plus two
    # blanks plus one real row is 13 criteria, not 15.
    with pytest.raises(AcceptanceCriteriaCapError, match="from 12 to 13") as refused:
        guard_criteria_growth(_criteria(12), [*fourteen_rows_twelve_criteria, "criterion 12"])
    assert refused.value.payload["after"] == 13
    assert refused.value.payload["before"] == 12


def test_the_split_constants_are_not_the_write_caps():
    """Two numbers, two names, and the rename is what keeps them apart.

    ``is_oversized`` is the trigger the orchestrator consults at every stage
    boundary to invoke a model that creates real child tickets, and 29 live
    tickets sit at 13-15 criteria. Lowering the trigger to 12 auto-splits them,
    and a revert does not delete the children. The 15,000 is genuinely one
    number, so it is imported rather than respelled.
    """
    assert SPLIT_AT_ACCEPTANCE_CRITERIA == 15
    assert SPLIT_AT_ACCEPTANCE_CRITERIA != MAX_ACCEPTANCE_CRITERIA
    assert MAX_ACCEPTANCE_CRITERIA == 12
    assert SPLIT_AT_DESCRIPTION_CHARS == MAX_DESCRIPTION_CHARS


# ---- the refusals a caller reads -------------------------------------


def _refusal(before: int, after: int) -> AcceptanceCriteriaCapError:
    with pytest.raises(AcceptanceCriteriaCapError) as caught:
        guard_criteria_growth(_criteria(before), _criteria(after))
    return caught.value


def _description_refusal(before: int, after: int) -> DescriptionCapError:
    with pytest.raises(DescriptionCapError) as caught:
        guard_description_growth("x" * before, "x" * after)
    return caught.value


def test_every_refusal_names_the_count_the_cap_and_the_next_action():
    """AC2, on all four strings, and on substance rather than prose.

    The agent wording must name ``depends_on`` — MCP's only channel is
    ``str(exc)`` — and the operator wording must read as a next action at a
    screen. Neither may be readable as "nothing happened".
    """
    criteria_exc = _refusal(12, 13)
    agent_text = str(criteria_exc)
    assert "13" in agent_text
    assert str(MAX_ACCEPTANCE_CRITERIA) in agent_text
    assert "depends_on" in agent_text
    assert "parent" in agent_text.lower()

    description_exc = _description_refusal(14_990, MAX_DESCRIPTION_CHARS + 210)
    agent_description = str(description_exc)
    assert f"{MAX_DESCRIPTION_CHARS + 210:,}" in agent_description
    assert f"{MAX_DESCRIPTION_CHARS:,}" in agent_description
    assert "depends_on" in agent_description
    assert "parent" in agent_description.lower()

    for exc, code, cap, before, after in (
        (criteria_exc, "acceptance_criteria_cap", MAX_ACCEPTANCE_CRITERIA, 12, 13),
        (
            description_exc,
            "description_cap",
            MAX_DESCRIPTION_CHARS,
            14_990,
            MAX_DESCRIPTION_CHARS + 210,
        ),
    ):
        payload = exc.payload
        assert payload["error"] == code
        assert payload["cap"] == cap
        assert payload["before"] == before
        assert payload["after"] == after
        operator = payload["message"]
        # A person's message, so no agent tool names and no `depends_on`.
        assert "depends_on" not in operator
        assert "loregarden_create_ticket" not in operator
        assert "parent" in operator.lower()


# ---- the writers -----------------------------------------------------


@pytest.mark.parametrize(
    "count, refused", [(MAX_ACCEPTANCE_CRITERIA, False), (MAX_ACCEPTANCE_CRITERIA + 1, True)]
)
def test_create_ticket_path(isolated_db, ws, count, refused):
    with Session(isolated_db) as session:

        def create():
            return TicketService(session).create_ticket(
                workspace_slug=ws.slug,
                title="capped",
                work_item_type=WorkItemType.FEATURE,
                parent_ticket_id=ws.milestone_id,
                acceptance_criteria=_criteria(count),
            )

        if refused:
            with pytest.raises(AcceptanceCriteriaCapError):
                create()
        else:
            assert len(load_criteria(create().acceptance_criteria_json)) == count


def test_manual_edit_path_covers_mcp_and_patch(isolated_db, ws):
    """MCP update_ticket and REST PATCH both reach update_ticket_manual."""
    with Session(isolated_db) as session:
        ticket = make_ticket(
            session, workspace_id=ws.id, external_id="proj-1", parent_ticket_id=ws.milestone_id
        )
        orch = OrchestrationService(session)
        orch.update_ticket_manual(ticket, UpdateTicketRequest(acceptance_criteria=_criteria(12)))
        with pytest.raises(AcceptanceCriteriaCapError):
            orch.update_ticket_manual(
                ticket, UpdateTicketRequest(acceptance_criteria=_criteria(13))
            )
        session.refresh(ticket)
        assert len(load_criteria(ticket.acceptance_criteria_json)) == 12


def test_a_ticket_already_over_the_cap_stays_editable(isolated_db, ws):
    with Session(isolated_db) as session:
        ticket = make_ticket(
            session, workspace_id=ws.id, external_id="proj-2", parent_ticket_id=ws.milestone_id
        )
        ticket.acceptance_criteria_json = serialize_criteria(_criteria(20))
        session.add(ticket)
        session.commit()
        orch = OrchestrationService(session)
        reworded = [*_criteria(19), "criterion 19, reworded"]
        orch.update_ticket_manual(ticket, UpdateTicketRequest(acceptance_criteria=reworded))
        orch.update_ticket_manual(ticket, UpdateTicketRequest(acceptance_criteria=_criteria(15)))
        with pytest.raises(AcceptanceCriteriaCapError):
            orch.update_ticket_manual(
                ticket, UpdateTicketRequest(acceptance_criteria=_criteria(16))
            )


def test_finalize_hierarchy_path(isolated_db, ws):
    with Session(isolated_db) as session:
        workspace = session.get(Workspace, ws.id)
        item = SimpleNamespace(
            external_id="proj-final-1",
            work_item_type=WorkItemType.FEATURE,
            acceptance_criteria=_criteria(13),
            description="",
            priority=3,
        )
        with pytest.raises(AcceptanceCriteriaCapError):
            _build_ticket(session, item, ws=workspace, parent_id=ws.milestone_id, title="t")


def test_a_description_crossing_is_refused_on_create_and_on_manual_edit(isolated_db, ws):
    over = "x" * (MAX_DESCRIPTION_CHARS + 1)
    at_cap = "x" * MAX_DESCRIPTION_CHARS
    with Session(isolated_db) as session:
        svc = TicketService(session)
        with pytest.raises(DescriptionCapError):
            svc.create_ticket(
                workspace_slug=ws.slug,
                title="long",
                work_item_type=WorkItemType.FEATURE,
                parent_ticket_id=ws.milestone_id,
                description=over,
            )
        allowed = svc.create_ticket(
            workspace_slug=ws.slug,
            title="exactly at the cap",
            work_item_type=WorkItemType.FEATURE,
            parent_ticket_id=ws.milestone_id,
            description=at_cap,
        )
        assert len(allowed.description) == MAX_DESCRIPTION_CHARS

        orch = OrchestrationService(session)
        with pytest.raises(DescriptionCapError):
            orch.update_ticket_manual(allowed, UpdateTicketRequest(description=over))
        session.refresh(allowed)
        assert len(allowed.description) == MAX_DESCRIPTION_CHARS

        workspace = session.get(Workspace, ws.id)
        item = SimpleNamespace(
            external_id="proj-final-2",
            work_item_type=WorkItemType.FEATURE,
            acceptance_criteria=[],
            description=over,
            priority=3,
        )
        with pytest.raises(DescriptionCapError):
            _build_ticket(session, item, ws=workspace, parent_id=ws.milestone_id, title="t")


def test_an_already_oversized_description_can_be_rewritten_longer_still(isolated_db, ws):
    """The canary for the crossing rule drifting back to a growth rule.

    159,960 characters is the real largest description in the database. An agent
    rewriting one of the 34 live over-cap descriptions has no other copy of the
    text, and a shortening attempt that overshoots must not lose it — so a write
    that starts over the cap succeeds even when it ends up longer.
    """
    with Session(isolated_db) as session:
        ticket = make_ticket(
            session, workspace_id=ws.id, external_id="proj-huge", parent_ticket_id=ws.milestone_id
        )
        ticket.description = "x" * 159_960
        session.add(ticket)
        session.commit()

        orch = OrchestrationService(session)
        orch.update_ticket_manual(ticket, UpdateTicketRequest(description="y" * 160_100))
        session.refresh(ticket)
        assert len(ticket.description) == 160_100

        orch.update_ticket_manual(ticket, UpdateTicketRequest(description="z" * 20_000))
        session.refresh(ticket)
        assert len(ticket.description) == 20_000


def test_a_refused_criteria_write_leaves_the_description_unmutated(isolated_db, ws):
    """Guard-then-mutate. One request, a legal description and an illegal list.

    ``_apply_content_edits`` used to assign ``ticket.description`` before the
    criteria guard ran, so a refused write had already mutated the ORM object —
    whether that reached the database depended on who committed next.
    """
    with Session(isolated_db) as session:
        ticket = make_ticket(
            session, workspace_id=ws.id, external_id="proj-3", parent_ticket_id=ws.milestone_id
        )
        ticket.description = "the description that must survive"
        ticket.title = "the title that must survive"
        ticket.acceptance_criteria_json = serialize_criteria(_criteria(12))
        session.add(ticket)
        session.commit()

        orch = OrchestrationService(session)
        with pytest.raises(AcceptanceCriteriaCapError):
            orch.update_ticket_manual(
                ticket,
                UpdateTicketRequest(
                    title="a new title",
                    description="a legal description nobody asked to keep",
                    acceptance_criteria=_criteria(13),
                ),
            )
        # The in-memory object, before any refresh: a refusal must leave the
        # object untouched, not merely leave the row untouched.
        assert ticket.description == "the description that must survive"
        assert ticket.title == "the title that must survive"
        assert len(load_criteria(ticket.acceptance_criteria_json)) == 12


def test_a_refused_description_write_leaves_the_criteria_unmutated(isolated_db, ws):
    """The mirror of the test above: the same restructure, the other field first."""
    with Session(isolated_db) as session:
        ticket = make_ticket(
            session, workspace_id=ws.id, external_id="proj-4", parent_ticket_id=ws.milestone_id
        )
        ticket.acceptance_criteria_json = serialize_criteria(_criteria(3))
        session.add(ticket)
        session.commit()

        orch = OrchestrationService(session)
        with pytest.raises(DescriptionCapError):
            orch.update_ticket_manual(
                ticket,
                UpdateTicketRequest(
                    description="x" * (MAX_DESCRIPTION_CHARS + 1),
                    acceptance_criteria=_criteria(5),
                ),
            )
        assert load_criteria(ticket.acceptance_criteria_json) == _criteria(3)
        assert ticket.description == ""


# ---- AC3: a write that does not grow the ticket still succeeds -------


def _over_cap_ticket(client, isolated_db, *, title: str, count: int) -> str:
    """A ticket carrying `count` criteria, planted past the cap the guard defends.

    Planted through the ORM rather than through a writer, because every writer
    refuses this list — which is the point. The 78 live tickets in this state
    predate the guard.
    """
    milestone_id = next(
        t["id"]
        for t in client.get("/api/tickets?workspace=loregarden").json()
        if t["work_item_type"] == "milestone"
    )
    created = client.post(
        "/api/tickets",
        json={
            "workspace_slug": "loregarden",
            "title": title,
            "work_item_type": "bug",
            "parent_ticket_id": milestone_id,
        },
    )
    assert created.status_code == 201, created.text
    ticket_id = created.json()["id"]
    with Session(isolated_db) as session:
        ticket = session.get(Ticket, ticket_id)
        ticket.acceptance_criteria_json = serialize_criteria(_criteria(count))
        session.add(ticket)
        session.commit()
    return ticket_id


def test_an_append_that_dedupes_to_a_no_op_succeeds_on_an_over_cap_ticket(client, isolated_db):
    """AC3, end to end through the MCP tool, with `mode="append"`.

    A re-run stage re-sends criteria it already filed. ``merge_criteria`` dedupes
    them, so the merged list the guard sees is the stored list and the count has
    not moved. Driven through the tool rather than the service because the merge
    and the guard are on opposite sides of ``_collect_update_fields``, and it is
    their composition that makes a stage retry survivable.
    """
    ticket_id = _over_cap_ticket(client, isolated_db, title="Over-cap append target", count=20)
    response = call_mcp(
        client,
        "loregarden_update_ticket",
        {
            "ticket_id": ticket_id,
            "acceptance_criteria": _criteria(20)[:5],
            "mode": "append",
        },
    )
    assert "error" not in response, response
    detail = client.get(f"/api/tickets/{ticket_id}").json()
    assert detail["acceptance_criteria"] == _criteria(20)


def test_a_shrinking_replace_succeeds_on_an_over_cap_ticket(client, isolated_db):
    """AC3's other half: an over-cap ticket can always be made smaller."""
    ticket_id = _over_cap_ticket(client, isolated_db, title="Over-cap shrink target", count=20)
    response = call_mcp(
        client,
        "loregarden_update_ticket",
        {
            "ticket_id": ticket_id,
            "acceptance_criteria": _criteria(15),
            "mode": "replace",
        },
    )
    assert "error" not in response, response
    detail = client.get(f"/api/tickets/{ticket_id}").json()
    assert detail["acceptance_criteria"] == _criteria(15)


def test_an_append_that_crosses_the_cap_is_refused_through_the_mcp_tool(client, isolated_db):
    """The agent channel carries the agent wording: ``str(exc)``, naming depends_on."""
    ticket_id = _over_cap_ticket(client, isolated_db, title="At-cap append target", count=12)
    response = call_mcp(
        client,
        "loregarden_update_ticket",
        {
            "ticket_id": ticket_id,
            "acceptance_criteria": ["a thirteenth criterion"],
            "mode": "append",
        },
    )
    message = json.dumps(response)
    assert "13" in message
    assert "depends_on" in message
    detail = client.get(f"/api/tickets/{ticket_id}").json()
    assert detail["acceptance_criteria"] == _criteria(12)


def test_an_append_of_whitespace_variants_is_a_no_op_at_the_cap(client, isolated_db):
    """AC3's dedupe case at the exact boundary, with rows that only look new.

    A re-run stage re-sends its criteria from its own prose, so they come back
    with different surrounding whitespace. ``merge_criteria`` composes with
    ``normalize_criteria``, so the merged list is the stored list and the count
    has not moved — at 12, which is where 358 live tickets sit and where an
    off-by-one in the guard is the difference between a stage retry working and
    a stage retry being unable to proceed.
    """
    ticket_id = _over_cap_ticket(client, isolated_db, title="At-cap whitespace append", count=12)
    response = call_mcp(
        client,
        "loregarden_update_ticket",
        {
            "ticket_id": ticket_id,
            "acceptance_criteria": [f"  {line}\n" for line in _criteria(12)],
            "mode": "append",
        },
    )
    assert "error" not in response, response
    detail = client.get(f"/api/tickets/{ticket_id}").json()
    assert detail["acceptance_criteria"] == _criteria(12)


@pytest.mark.parametrize(
    "sent", [[], ["   "], ["\n", "\t  "]], ids=["empty", "one-blank", "only-blanks"]
)
def test_an_append_of_nothing_is_a_no_op_on_an_over_cap_ticket(client, isolated_db, sent):
    """Null and empty, against the writer that refuses growth.

    An append that carries no criteria is not growth, and an over-cap ticket is
    the case where "not growth" is the only thing keeping the write legal. A
    guard that reads `after > cap` without the `after > before` term refuses
    all three.
    """
    ticket_id = _over_cap_ticket(
        client, isolated_db, title=f"Over-cap empty append {len(sent)}", count=20
    )
    response = call_mcp(
        client,
        "loregarden_update_ticket",
        {"ticket_id": ticket_id, "acceptance_criteria": sent, "mode": "append"},
    )
    assert "error" not in response, response
    detail = client.get(f"/api/tickets/{ticket_id}").json()
    assert detail["acceptance_criteria"] == _criteria(20)


def test_a_replace_that_empties_an_over_cap_ticket_succeeds(client, isolated_db):
    """The far end of AC3's shrinking replace: all the way to zero."""
    ticket_id = _over_cap_ticket(client, isolated_db, title="Over-cap emptied", count=20)
    response = call_mcp(
        client,
        "loregarden_update_ticket",
        {"ticket_id": ticket_id, "acceptance_criteria": [], "mode": "replace"},
    )
    assert "error" not in response, response
    detail = client.get(f"/api/tickets/{ticket_id}").json()
    assert detail["acceptance_criteria"] == []


def test_a_replace_of_fourteen_rows_that_store_twelve_is_not_refused(isolated_db, ws):
    """The guard at the seam counts the stored list, not the request's length.

    Same rule as the unit test above, through ``update_ticket_manual``, because
    the normalize and the guard are two calls in one function and their order is
    what decides this. The ticket ends at 12 — proving the write happened rather
    than being quietly skipped.
    """
    with Session(isolated_db) as session:
        ticket = make_ticket(
            session, workspace_id=ws.id, external_id="proj-blank", parent_ticket_id=ws.milestone_id
        )
        orch = OrchestrationService(session)
        orch.update_ticket_manual(
            ticket,
            UpdateTicketRequest(acceptance_criteria=[*_criteria(12), "   ", "\n\t "]),
        )
        session.refresh(ticket)
        assert load_criteria(ticket.acceptance_criteria_json) == _criteria(12)


def test_a_refused_write_leaves_every_other_content_field_unmutated(isolated_db, ws):
    """Guard-then-mutate on the fields the two existing tests do not cover.

    ``_apply_content_edits`` writes four fields, and the restructure has to move
    the guards above all of them, not above the two a test happens to read.
    Tags are the one that would survive a half-done fix: a refused write that
    retags the ticket changes what the board shows, and nothing else in this
    module would notice.
    """
    with Session(isolated_db) as session:
        ticket = make_ticket(
            session, workspace_id=ws.id, external_id="proj-tags", parent_ticket_id=ws.milestone_id
        )
        ticket.acceptance_criteria_json = serialize_criteria(_criteria(12))
        session.add(ticket)
        session.commit()
        tags_before = ticket.tags_json
        revision_before = ticket.revision

        orch = OrchestrationService(session)
        with pytest.raises(AcceptanceCriteriaCapError):
            orch.update_ticket_manual(
                ticket,
                UpdateTicketRequest(
                    tags=["swept", "in", "by", "a", "refusal"],
                    acceptance_criteria=_criteria(13),
                ),
            )
        assert ticket.tags_json == tags_before
        # A refused write is not a write, so it does not earn a revision bump —
        # the number clients use to detect a conflicting edit.
        assert ticket.revision == revision_before


def test_a_write_that_names_no_description_never_refuses_an_over_cap_ticket(client, isolated_db):
    """34 live tickets are over the description cap and all of them stay editable.

    The field is optional on both bodies, so `before`/`after` on an absent
    description is the easy mistake: reading `body.description or ""` as the
    `after` turns every title edit on those 34 tickets into a refusal — or, just
    as bad, into a silent blanking of the description.
    """
    ticket_id = _over_cap_ticket(client, isolated_db, title="Over-cap description", count=1)
    with Session(isolated_db) as session:
        ticket = session.get(Ticket, ticket_id)
        ticket.description = "x" * (MAX_DESCRIPTION_CHARS + 500)
        session.add(ticket)
        session.commit()

    res = client.patch(f"/api/tickets/{ticket_id}", json={"title": "Retitled, nothing else"})
    assert res.status_code == 200, res.text
    detail = client.get(f"/api/tickets/{ticket_id}").json()
    assert detail["title"] == "Retitled, nothing else"
    assert len(detail["description"]) == MAX_DESCRIPTION_CHARS + 500

    # And the shrink an operator would actually make next, down to empty.
    res = client.patch(f"/api/tickets/{ticket_id}", json={"description": ""})
    assert res.status_code == 200, res.text
    assert client.get(f"/api/tickets/{ticket_id}").json()["description"] == ""


# ---- the HTTP surface ------------------------------------------------


def test_the_patch_endpoint_returns_a_structured_cap_detail(client, isolated_db):
    """A dict detail, the shape this same route already returns for its 409.

    Arm order is load-bearing: both cap errors are ``ValueError`` subclasses, so
    a specific arm placed after the generic one would flatten the payload to
    ``str(exc)`` and the client would get agent prose in an operator's slot.
    """
    ticket_id = _over_cap_ticket(client, isolated_db, title="PATCH cap target", count=12)
    res = client.patch(f"/api/tickets/{ticket_id}", json={"acceptance_criteria": _criteria(13)})
    assert res.status_code == 400, res.text
    detail = res.json()["detail"]
    assert detail["error"] == "acceptance_criteria_cap"
    assert detail["cap"] == MAX_ACCEPTANCE_CRITERIA
    assert detail["before"] == 12
    assert detail["after"] == 13
    assert "depends_on" not in detail["message"]

    res = client.patch(
        f"/api/tickets/{ticket_id}",
        json={"description": "x" * (MAX_DESCRIPTION_CHARS + 1)},
    )
    assert res.status_code == 400, res.text
    detail = res.json()["detail"]
    assert detail["error"] == "description_cap"
    assert detail["cap"] == MAX_DESCRIPTION_CHARS
    assert detail["after"] == MAX_DESCRIPTION_CHARS + 1


def test_a_patch_that_is_refused_for_another_reason_still_answers_prose(client, isolated_db):
    """The generic 400 arm is unchanged: only the two cap errors get a payload."""
    ticket_id = _over_cap_ticket(client, isolated_db, title="Generic 400 target", count=1)
    res = client.patch(f"/api/tickets/{ticket_id}", json={"title": "   "})
    assert res.status_code == 400, res.text
    assert isinstance(res.json()["detail"], str)


def test_the_create_endpoint_returns_a_structured_cap_detail(client):
    milestone_id = next(
        t["id"]
        for t in client.get("/api/tickets?workspace=loregarden").json()
        if t["work_item_type"] == "milestone"
    )
    res = client.post(
        "/api/tickets",
        json={
            "workspace_slug": "loregarden",
            "title": "Too long to create",
            "work_item_type": "bug",
            "parent_ticket_id": milestone_id,
            "description": "x" * (MAX_DESCRIPTION_CHARS + 1),
        },
    )
    assert res.status_code == 400, res.text
    detail = res.json()["detail"]
    assert detail["error"] == "description_cap"
    assert detail["cap"] == MAX_DESCRIPTION_CHARS
    assert detail["before"] == 0
    assert detail["after"] == MAX_DESCRIPTION_CHARS + 1

    res = client.post(
        "/api/tickets",
        json={
            "workspace_slug": "loregarden",
            "title": "Too many criteria to create",
            "work_item_type": "bug",
            "parent_ticket_id": milestone_id,
            "acceptance_criteria": _criteria(MAX_ACCEPTANCE_CRITERIA + 1),
        },
    )
    assert res.status_code == 400, res.text
    assert res.json()["detail"]["error"] == "acceptance_criteria_cap"


def test_a_survey_append_that_crosses_the_cap_is_refused_not_half_written(isolated_db, ws):
    """The reference-survey writer reaches the cap the way a stage agent does.

    ``_write_criteria`` merges in append mode and then reads the row back,
    raising ``TicketWriteMismatch`` if what it finds differs from what it sent —
    so the one thing it must not do is report success. The refusal has to come
    out as the cap error (which names ``depends_on``, the survey's actual next
    move) rather than as a read-back mismatch, which would tell the operator the
    database lied to them.

    The at-cap case next to it is the one that matters more often: a survey pass
    re-landing a finding it already filed must not be turned into a crash.
    """
    with Session(isolated_db) as session:
        ticket = make_ticket(
            session, workspace_id=ws.id, external_id="proj-survey", parent_ticket_id=ws.milestone_id
        )
        ticket.acceptance_criteria_json = serialize_criteria(_criteria(12))
        session.add(ticket)
        session.commit()

        # Nothing new: already filed, so the merge is a no-op and the write is
        # skipped entirely. A re-run survey pass has to survive this.
        assert ticket_writer._write_criteria(session, ticket, _criteria(12)[:3]) == []

        with pytest.raises(AcceptanceCriteriaCapError):
            ticket_writer._write_criteria(session, ticket, ["a thirteenth finding"])
        session.refresh(ticket)
        assert load_criteria(ticket.acceptance_criteria_json) == _criteria(12)


# ---- monitor_escalation ----------------------------------------------


def _finding(session, ws, index: int) -> monitor_escalation._CurrentFinding:
    ticket = make_ticket(
        session,
        workspace_id=ws.id,
        external_id=f"proj-finding-{index}",
        parent_ticket_id=ws.milestone_id,
    )
    return monitor_escalation._CurrentFinding(
        ticket=ticket,
        title="invariant_reentered:land_verification_ran",
        summary="a summary long enough to make the generated prose grow quickly " * 6,
        evidence={f"key_{i}": "v" * 80 for i in range(6)},
    )


def test_a_monitor_escalation_description_is_built_under_the_cap(isolated_db, ws):
    """The largest description in the whole database is a monitor escalation.

    159,960 characters, ``lg-monitor-escalations-1733`` — 10x the cap, so neither
    a waiver nor an allow-list entry would be honest. The prose is machine-built
    from monitor findings and rewritten every sweep, so it is nobody's only copy:
    truncating the source-ticket list is honest here in a way it would never be
    on an agent's own description, provided the output says what it dropped.
    """
    with Session(isolated_db) as session:
        findings = [_finding(session, ws, i) for i in range(300)]
        description = monitor_escalation._build_description(
            title="invariant_reentered:land_verification_ran", findings=findings
        )
    assert len(description) <= MAX_DESCRIPTION_CHARS
    # It must say what it left out rather than trailing off.
    last = description.rstrip().splitlines()[-1]
    omitted = len(findings) - description.count("- proj-finding-")
    assert omitted > 0
    assert str(omitted) in last

    with Session(isolated_db) as session:
        small = [_finding(session, ws, 1000 + i) for i in range(2)]
        short = monitor_escalation._build_description(title="t", findings=small)
    assert "- proj-finding-1000" in short
    assert "- proj-finding-1001" in short


def test_a_refused_monitor_refresh_warns_and_keeps_the_existing_description(
    isolated_db, ws, caplog
):
    """A background sweep is never killed by a raise, and never swallows one either.

    The truncation above is the real defence; this is the backstop, so it has to
    be visible at ``warning`` (``debug``/``info`` are below the default handler
    level, which is a failure reported nowhere) and must leave the stored
    description alone.
    """
    with Session(isolated_db) as session:
        bug = make_ticket(
            session, workspace_id=ws.id, external_id="proj-bug-1", parent_ticket_id=ws.milestone_id
        )
        bug.description = "the description the sweep must not replace"
        session.add(bug)
        session.commit()

        with caplog.at_level(logging.WARNING, logger=monitor_escalation.__name__):
            monitor_escalation._refresh_bug(
                session, bug, description="x" * (MAX_DESCRIPTION_CHARS + 1)
            )

        session.refresh(bug)
        assert bug.description == "the description the sweep must not replace"

    assert [r for r in caplog.records if r.levelno >= logging.WARNING], (
        "a refused refresh that logs nothing is a failure reported nowhere"
    )


# ---- the census: every writer of ticket content ----------------------

#: Every unguarded ticket-content writer, each with the reason it is one. An
#: entry here is a claim a reviewer checks, like a `silent-ok:` waiver.
UNGUARDED_WRITERS: dict[str, str] = {
    "loregarden/services/seed.py::_add_ticket": (
        "author-controlled bootstrap fixture rows, written before any agent can reach the "
        "database; the literals are in this repository and reviewed with it"
    ),
    "loregarden/services/seed.py::seed_database": (
        "the same bootstrap rows, passing their criteria down to _add_ticket"
    ),
    "loregarden/testing/factories.py::make_ticket": (
        "the test factory: suites plant the over-cap rows the guard exists to refuse, which is "
        "exactly what this module's own AC3 tests do"
    ),
}

#: Every writer that must call a growth guard before it writes.
GUARDED_WRITERS: frozenset[str] = frozenset(
    {
        "loregarden/services/ticket_manual_edit.py::_apply_content_edits",
        "loregarden/services/ticket_service.py::_create_ticket_impl",
        "loregarden/services/finalize_hierarchy.py::_build_ticket",
        "loregarden/services/monitor_escalation.py::_refresh_bug",
    }
)

#: The fields that are a ticket's content. `acceptance_criteria_json` names no
#: other model, so it is scanned everywhere; `description` is shared with
#: skills, workflows and reference sources, so it is scanned only in modules
#: that import the `Ticket` ORM model.
_CRITERIA_FIELD = "acceptance_criteria_json"
_DESCRIPTION_FIELD = "description"


def _is_ticket_aware(tree: ast.Module) -> bool:
    """Does this module handle the `Ticket` ORM model at all?

    Broader than "imports Ticket by name" on purpose: a module reaching it as
    ``domain.Ticket`` after ``from loregarden.models import domain`` imports no
    name called ``Ticket``, and an import-only rule is blind to every
    ``.description`` it writes. Any mention of the name makes the module
    ticket-aware, which over-reports rather than under-reports — and the
    over-report costs a named exception, while the under-report is a hole.
    Verified on the tree: the broader rule finds the same sites and no more.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and any(alias.name == "Ticket" for alias in node.names):
            return True
        if isinstance(node, ast.Name) and node.id == "Ticket":
            return True
        if isinstance(node, ast.Attribute) and node.attr == "Ticket":
            return True
    return False


def _function_owners(tree: ast.Module) -> dict[int, str]:
    """Map each node to the name of the function it sits in, outermost wins."""
    owners: dict[int, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.walk(node):
                owners.setdefault(id(child), node.name)
    return owners


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    return getattr(node.func, "attr", "")


#: Every statement form that can bind a value to an attribute. `Assign` alone
#: misses three: `ticket.description += extra`, `ticket.description: str = x`,
#: and `ticket.description, ok = text, True` — each a one-line way around a
#: scanner that only looks at `Assign` with a bare `Attribute` target.
_ASSIGNMENT_NODES = (ast.Assign, ast.AugAssign, ast.AnnAssign)


def _attribute_targets(node: ast.stmt) -> list[ast.Attribute]:
    """Every attribute this statement assigns to, however it is nested.

    Unpacking targets nest, so a tuple, list or starred target is walked rather
    than skipped: `ticket.description, _ = a, b` writes a ticket's description
    exactly as much as `ticket.description = a` does.
    """
    found: list[ast.Attribute] = []

    def walk(target: ast.expr) -> None:
        if isinstance(target, ast.Attribute):
            found.append(target)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                walk(element)
        elif isinstance(target, ast.Starred):
            walk(target.value)

    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    for target in targets:
        walk(target)
    return found


def _assignment_write(node: ast.stmt, *, ticket_aware: bool) -> str:
    """What this assignment writes to a ticket, or "" for anything else."""
    how = type(node).__name__
    for target in _attribute_targets(node):
        if target.attr == _CRITERIA_FIELD:
            return f"{how} to .{_CRITERIA_FIELD}"
        if target.attr == _DESCRIPTION_FIELD and ticket_aware:
            return f"{how} to .{_DESCRIPTION_FIELD}"
    return ""


def _setattr_write(node: ast.Call) -> str:
    """A content field reached by string name, which every other rule is blind to."""
    named = node.args[1] if len(node.args) > 1 else None
    if isinstance(named, ast.Constant) and named.value in {_CRITERIA_FIELD, _DESCRIPTION_FIELD}:
        return f"setattr(…, {named.value!r}, …)"
    return ""


def _call_write(node: ast.Call, *, ticket_aware: bool) -> str:
    """What this call writes to a ticket, or "" for anything else."""
    name = _call_name(node)
    if _CRITERIA_FIELD in {kw.arg for kw in node.keywords if kw.arg}:
        return f"passes {_CRITERIA_FIELD}= to {name or '<call>'}"
    if not ticket_aware:
        return ""
    if name == "Ticket" and any(kw.arg == _DESCRIPTION_FIELD for kw in node.keywords):
        return "constructs Ticket(description=…)"
    if name == "setattr":
        return _setattr_write(node)
    if name == "sqlmodel_update":
        return "sqlmodel_update on a Ticket-aware module"
    return ""


def _module_write_sites(path: pathlib.Path, rel: str) -> dict[str, list[str]]:
    """One module's ticket-content write sites, by `module::function`."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    ticket_aware = _is_ticket_aware(tree)
    owners = _function_owners(tree)
    sites: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, _ASSIGNMENT_NODES):
            what = _assignment_write(node, ticket_aware=ticket_aware)
        elif isinstance(node, ast.Call):
            what = _call_write(node, ticket_aware=ticket_aware)
        else:
            continue
        if what:
            key = f"{rel}::{owners.get(id(node), '<module>')}"
            sites.setdefault(key, []).append(f"{what} (line {node.lineno})")
    return sites


def _content_write_sites(
    root: pathlib.Path, base: pathlib.Path | None = None
) -> dict[str, list[str]]:
    """Every place in `root` that can put content on a ticket, by `module::function`.

    Derived from the capability — "this code can set a ticket's description or
    acceptance criteria" — and never from the list of sites a change happened to
    fix. A census built from the sites fixed cannot fail, which is the failure
    the lg-external-harness-1026 learning records.
    """
    sites: dict[str, list[str]] = {}
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(base or SERVER_ROOT).as_posix()
        for key, writes in _module_write_sites(path, rel).items():
            sites.setdefault(key, []).extend(writes)
    return sites


def _guard_calls(path: pathlib.Path, function: str) -> set[str]:
    """Which growth guards `function` calls, anywhere inside its own body."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function:
            return {
                _call_name(call)
                for call in ast.walk(node)
                if isinstance(call, ast.Call)
                and _call_name(call) in {"guard_criteria_growth", "guard_description_growth"}
            }
    return set()


def test_every_ticket_content_writer_is_a_guarded_seam_or_a_named_exception():
    """The census, and ``prevented_by`` for the invariant.

    Three ways this can fail, and each is the point:

    * a new writer appears and is in neither list — the suite reds until somebody
      decides whether it is a seam or an exception;
    * a guarded seam stops calling a guard — the suite reds;
    * an exception is listed that no longer writes anything — the suite reds, so
      the allow-list cannot accumulate stale entries nobody re-reads.

    Verified by deleting a guard call from ``_apply_content_edits`` and, separately,
    by adding a throwaway ``ticket.description = …`` in a scratch module: it reds
    on both. Enumerating the writers rather than exercising one is what the
    *Fixing a bad state* rule asks for.
    """
    sites = _content_write_sites(PACKAGE_ROOT)
    known = GUARDED_WRITERS | set(UNGUARDED_WRITERS)

    unaccounted = {key: why for key, why in sites.items() if key not in known}
    assert not unaccounted, (
        "these can write a ticket's description or acceptance criteria and are neither a "
        "guarded seam nor a named exception: " + json.dumps(unaccounted, indent=2)
    )

    assert GUARDED_WRITERS <= set(sites), (
        "a seam this test claims to guard no longer writes ticket content: "
        f"{sorted(GUARDED_WRITERS - set(sites))}"
    )
    assert set(UNGUARDED_WRITERS) <= set(sites), (
        "a named exception no longer writes ticket content, so its reason is stale: "
        f"{sorted(set(UNGUARDED_WRITERS) - set(sites))}"
    )

    for key in sorted(GUARDED_WRITERS):
        module, _, function = key.partition("::")
        guards = _guard_calls(SERVER_ROOT / module, function)
        assert guards, f"{key} writes ticket content and calls no growth guard"

    for why in UNGUARDED_WRITERS.values():
        assert len(why) > 40, "a named exception needs a reason, not a label"


def test_the_census_sees_a_writer_that_walks_around_the_seam(tmp_path):
    """The census's own control: a planted writer must be found.

    Without this, a scanner that silently matched nothing would report a clean
    census forever. `tmp_path` rather than the real tree, so the control cannot
    leave a scratch module behind.
    """
    module = tmp_path / "loregarden" / "services"
    module.mkdir(parents=True)
    (module / "sneaky.py").write_text(
        "from loregarden.models.domain import Ticket\n"
        "\n"
        "\n"
        "def grow(ticket: Ticket, text: str) -> None:\n"
        "    ticket.description = text\n"
        "    ticket.acceptance_criteria_json = text\n",
        encoding="utf-8",
    )
    sites = _content_write_sites(tmp_path, base=tmp_path)
    assert any(key.endswith("sneaky.py::grow") for key in sites), sites


#: Every one-line way a writer can bind ticket content without an `Assign`
#: statement whose target is a bare `Attribute`. Each entry is a module body and
#: the function name the census must key it under. A scanner that reads only
#: `Assign` finds the first and none of the rest.
_DODGES: dict[str, str] = {
    "augmented_assignment": (
        "def grow(ticket: Ticket, extra: str) -> None:\n    ticket.description += extra\n"
    ),
    "annotated_assignment": (
        "def grow(ticket: Ticket, text: str) -> None:\n    ticket.description: str = text\n"
    ),
    "tuple_unpacking": (
        "def grow(ticket: Ticket, text: str) -> None:\n"
        "    ticket.description, ok = text, True\n"
        "    return ok\n"
    ),
    "criteria_augmented": (
        "def grow(ticket: Ticket, text: str) -> None:\n"
        "    ticket.acceptance_criteria_json += text\n"
    ),
    "criteria_unpacked": (
        "def grow(ticket: Ticket, text: str) -> None:\n"
        "    [ticket.acceptance_criteria_json] = [text]\n"
    ),
    "setattr_by_name": (
        'def grow(ticket: Ticket, text: str) -> None:\n    setattr(ticket, "description", text)\n'
    ),
    "module_attribute_import": (
        "from loregarden.models import domain\n"
        "\n"
        "\n"
        "def grow(text: str) -> domain.Ticket:\n"
        "    row = domain.Ticket(description=text)\n"
        "    row.description = text\n"
        "    return row\n"
    ),
}


@pytest.mark.parametrize("dodge", sorted(_DODGES))
def test_the_census_sees_every_way_a_writer_can_dodge_the_scanner(tmp_path, dodge):
    """The scanner's controls, one per statement form that is not a plain ``Assign``.

    ``test_the_census_sees_a_writer_that_walks_around_the_seam`` plants the
    obvious writer. These plant the ones a scanner built around
    ``ast.Assign`` + a bare ``Attribute`` target silently misses: ``+=``
    (``AugAssign``), an annotated assignment (``AnnAssign``), a target nested in
    a tuple or a list, and a module that reaches the model as
    ``domain.Ticket`` so it imports no name called ``Ticket`` at all. Each is
    one line, each is reachable by an agent "fixing" a module, and each would
    otherwise leave the census reporting a clean tree.

    Parametrized rather than one test with seven plants so a regression names
    the form it lost, instead of one failure that could be any of them.
    """
    module = tmp_path / dodge / "loregarden" / "services"
    module.mkdir(parents=True)
    body = _DODGES[dodge]
    header = "" if body.startswith("from ") else "from loregarden.models.domain import Ticket\n\n\n"
    (module / "dodge.py").write_text(header + body, encoding="utf-8")

    sites = _content_write_sites(tmp_path / dodge, base=tmp_path / dodge)
    assert any(key.endswith("dodge.py::grow") for key in sites), (
        f"the {dodge} writer is invisible to the census: {sites}"
    )


def test_the_census_scanner_over_reports_nothing_on_the_tree_it_scans():
    """The other half of the control: hardening must not invent sites.

    A scanner loose enough to find every dodge is also loose enough to flag a
    ``.description`` on a skill, a workflow or a reference source. The two
    `description` false positives that rule exists to exclude
    (``mcp_registry``, ``skill_service``, ``studio_service``,
    ``reference_survey/describe``) must stay excluded, so the allow-list cannot
    be padded out by the scanner's own noise.
    """
    sites = _content_write_sites(PACKAGE_ROOT)
    modules = {key.partition("::")[0] for key in sites}
    for not_a_ticket in (
        "loregarden/services/mcp_registry.py",
        "loregarden/services/skill_service.py",
        "loregarden/services/studio_service.py",
        "loregarden/services/reference_survey/describe.py",
        "loregarden/services/reference_survey/upstream.py",
    ):
        assert not_a_ticket not in modules, (
            f"{not_a_ticket} writes a `.description` that is not a ticket's; the census "
            "would demand a named exception for a field this ticket does not cap"
        )


def test_the_reference_survey_ticket_writer_appends_only_through_the_guarded_seam():
    """The fifth writer that can grow a criteria list, pinned like the chat loop.

    ``reference_survey/ticket_writer._write_criteria`` merges in append mode and
    is absent from the census because it hands the merged list to
    ``update_ticket_manual``. It is the surface a survey pass lands findings
    through, so it reaches the cap the same way a stage agent does — and an
    agent "fixing" a refusal here by writing the column would add an unguarded
    writer. It also reads the row back and raises on a mismatch, so a silent
    half-write is not available to it either.
    """
    source = (PACKAGE_ROOT / "services" / "reference_survey" / "ticket_writer.py").read_text(
        encoding="utf-8"
    )
    assert "update_ticket_manual" in source
    assert f".{_CRITERIA_FIELD} =" not in source


def test_chat_feature_loop_writes_criteria_only_through_the_guarded_seam():
    """The chat loop appends criteria and is absent from the census on purpose.

    It is not an exception: it routes through ``update_ticket_manual``, so it
    inherits the guard. This pins that, because an agent "fixing" it by writing
    the column directly would add an unguarded writer the census would then
    demand an entry for.
    """
    source = (PACKAGE_ROOT / "services" / "chat_feature_loop.py").read_text(encoding="utf-8")
    assert "update_ticket_manual" in source
    assert f".{_CRITERIA_FIELD} =" not in source


# ---- the client mirrors the server -----------------------------------


def test_the_client_cap_constant_matches_the_servers():
    """One number, mirrored where the client already mirrors this seam.

    ``criterionDrafts.ts`` is the module whose ``draftCriteria`` already carries
    "mirrors the server's normalize_criteria", and the readout has to render
    before any request — so a ``/limits`` endpoint would add a loading state to a
    number that changes only when somebody edits a Python constant. A mirror
    needs a drift test, and this is it; precedent is ``test_ui_actions.py``'s
    "the client mirrors the server" block.
    """
    source = (CLIENT_ROOT / "src" / "lib" / "criterionDrafts.ts").read_text(encoding="utf-8")
    # A *declaration*, not any line mentioning the name: `draft.length >=
    # MAX_ACCEPTANCE_CRITERIA` contains an `=` too, so an `"=" in line` filter
    # reds the drift test the first time the constant is compared against —
    # which is the only thing it is for.
    declarations = [
        line
        for line in source.splitlines()
        if re.search(r"\bMAX_ACCEPTANCE_CRITERIA\b\s*(?::[^=]*)?(?<![<>=!])=(?!=)", line)
    ]
    assert len(declarations) == 1, (
        "the client must spell this number once; a second binding is a second "
        f"source of truth: {declarations}"
    )
    assert declarations[0].lstrip().startswith("export const"), declarations[0]
    assert re.search(rf"=\s*{MAX_ACCEPTANCE_CRITERIA}\b", declarations[0]), declarations[0]


# ---- the census as a count, and the invariant that re-runs it --------


def test_the_census_counts_every_ticket_over_a_write_cap(isolated_db, ws):
    """AC5's measurement, as a function rather than a throwaway query.

    The same detector answers "how many are over the line today" and "did one
    enter after the guard landed", so the number in the fix-coverage record and
    the number the reconciler watches cannot drift apart.
    """
    with Session(isolated_db) as session:
        at_criteria_cap = make_ticket(
            session, workspace_id=ws.id, external_id="proj-c-12", parent_ticket_id=ws.milestone_id
        )
        at_criteria_cap.acceptance_criteria_json = serialize_criteria(
            _criteria(MAX_ACCEPTANCE_CRITERIA)
        )
        over_criteria = make_ticket(
            session, workspace_id=ws.id, external_id="proj-c-13", parent_ticket_id=ws.milestone_id
        )
        over_criteria.acceptance_criteria_json = serialize_criteria(
            _criteria(MAX_ACCEPTANCE_CRITERIA + 1)
        )
        at_description_cap = make_ticket(
            session, workspace_id=ws.id, external_id="proj-d-at", parent_ticket_id=ws.milestone_id
        )
        at_description_cap.description = "x" * MAX_DESCRIPTION_CHARS
        over_description = make_ticket(
            session, workspace_id=ws.id, external_id="proj-d-over", parent_ticket_id=ws.milestone_id
        )
        over_description.description = "x" * (MAX_DESCRIPTION_CHARS + 1)
        session.add_all([at_criteria_cap, over_criteria, at_description_cap, over_description])
        session.commit()

        by_ticket = {v.ticket_id: v for v in tickets_over_a_write_cap(session)}

        assert over_criteria.id in by_ticket
        assert over_description.id in by_ticket
        # At the cap is not over it. 358 live tickets sit at exactly 12.
        assert at_criteria_cap.id not in by_ticket
        assert at_description_cap.id not in by_ticket

        criteria_detail = by_ticket[over_criteria.id].detail
        assert str(MAX_ACCEPTANCE_CRITERIA + 1) in criteria_detail
        assert str(MAX_ACCEPTANCE_CRITERIA) in criteria_detail
        description_detail = by_ticket[over_description.id].detail
        assert f"{MAX_DESCRIPTION_CHARS + 1:,}" in description_detail

        # `created_at`, not `updated_at`: see the invariant test below.
        assert by_ticket[over_criteria.id].entered_at == over_criteria.created_at


def test_a_ticket_over_both_caps_is_one_violation_naming_both(isolated_db, ws):
    """One row in the bad state is one violation, however many ways it is over."""
    with Session(isolated_db) as session:
        both = make_ticket(
            session, workspace_id=ws.id, external_id="proj-both", parent_ticket_id=ws.milestone_id
        )
        both.acceptance_criteria_json = serialize_criteria(_criteria(62))
        both.description = "x" * 159_960
        session.add(both)
        session.commit()
        violations = [v for v in tickets_over_a_write_cap(session) if v.ticket_id == both.id]

        assert len(violations) == 1
        detail = violations[0].detail
        assert "62" in detail
        assert f"{159_960:,}" in detail


def test_the_size_cap_invariant_is_registered_as_this_ticket_declares_it():
    """Report-only DEFECT, dated by `created_at`, pointing at the census test.

    Report-only because only a person decides what a too-large ticket splits
    into; an automatic repair would truncate a description or delete criteria,
    and repairing the existing population is lg-milestone-that-2251, this
    ticket's dependent.

    Dated by `created_at` because there is no ticket-content history table — the
    detector sees current size, never growth. `updated_at` moves on exactly the
    shrinking and no-op edits AC3 keeps legal, so most of the 78 legacy over-cap
    tickets would read as fresh re-entry and escalate a false guard-failure bug
    every reconcile pass. `created_at` under-reports an old ticket grown
    tomorrow, and detects correctly the case DEFECT + `since` exists for: a
    ticket created over a cap after the guard landed. The quiet error, chosen
    over the loud lie, and disclosed here rather than left to be rediscovered.
    """
    registered = [i for i in INVARIANTS if i.name == "tickets_are_created_under_the_size_caps"]
    assert len(registered) == 1, [i.name for i in INVARIANTS]
    invariant = registered[0]
    assert invariant.ticket == "lg-milestone-that-2250"
    assert invariant.detect is tickets_over_a_write_cap
    assert invariant.report_only
    assert invariant.prevented_by == (
        "server/tests/test_ticket_size_guard.py"
        "::test_every_ticket_content_writer_is_a_guarded_seam_or_a_named_exception"
    )
    # The route the refusals name, so the finding and the refusal agree.
    assert "depends_on" in invariant.unrepaired_action


# ---- the shape the editor is rendered over ---------------------------

#: The three criteria counts the editor has to survive, and the namespace whose
#: `prod_shape_id` fixes their ids so the jest fixtures can name the same rows.
#: 11 is under the cap, 12 is at it (358 live tickets, one click from a refusal),
#: 62 is the largest list in the database.
CRITERIA_CAP_SHAPE = (11, MAX_ACCEPTANCE_CRITERIA, 62)


def test_the_prod_shape_scenario_carries_a_ticket_at_each_cap_boundary(isolated_db):
    """11 / 12 / 62, at fixed ids, so the jest render is over real volume.

    Without the 62 the editor is only ever rendered a handful of rows and the
    "every row reachable, no clipped control" requirement is untested; without
    the 12 the disabled-Add case never renders; without the 11 the enabled case
    never does.
    """
    with Session(isolated_db) as session:
        build_prod_shape(session, graph_path=AgentMemoryService.from_settings().graph_path)
        found = {}
        for count in CRITERIA_CAP_SHAPE:
            ticket = session.get(Ticket, prod_shape_id("criteria-cap", count))
            assert ticket is not None, (
                f"the scenario has no ticket at {count} criteria; the editor's jest render "
                "needs one under the cap, one at it and one far over"
            )
            found[count] = len(load_criteria(ticket.acceptance_criteria_json))
    assert found == {count: count for count in CRITERIA_CAP_SHAPE}


def test_the_calibration_records_the_size_measurement_and_its_date():
    """A scenario that disagrees with production is worse than none, so the
    numbers it is built from are dated in one place and pinned here."""
    assert CALIBRATION.criteria_measured == "2026-11-05"
    assert CALIBRATION.tickets_at_the_criteria_cap == 358
    assert CALIBRATION.tickets_over_the_criteria_cap == 78
    assert CALIBRATION.largest_criteria_list == 62
    assert CALIBRATION.descriptions_over_the_cap == 34
    assert CALIBRATION.largest_description_chars == 159_960
