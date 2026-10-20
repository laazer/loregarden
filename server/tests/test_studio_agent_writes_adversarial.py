"""The corners of the two Studio agent write surfaces.

`test_studio_agent_writes.py` pins the contract. This file attacks it: the
inputs that reach the reference scan from rows nobody validated, the blast
radius of a delete that finally works, the boundaries of AC8's truncation, and
the mutations of the PATCH body that a `extra="forbid"` arm alone does not
answer.

Every assertion here traces to a criterion on lg-milestone-that-1239 rather
than to a preference, and the comment on each test names it. Three of them pin
a reading the spec leaves open; those say so, and the reading is recorded as a
checkpoint on the ticket.

The implementations these are written against are the ones that pass the
contract suite and are still wrong:

* a scan that `json.loads` every `stages_json` with no handler, so one row
  written by an older build turns a delete into a 500 — or worse, a scan whose
  handler returns "no references" and deletes an agent a template still names
  (AC1, AC4);
* a scan that compares the slug against every value in the stage dict, so a
  `skill_name` that happens to equal the slug refuses a delete nothing can
  clear (AC16);
* a scan that matches with `in` or `startswith`, so `delete-probe-2` blocks
  `delete-probe` (AC16);
* a delete that drops the agent row and leaves its version rows orphaned, or
  reaches another agent's rows (AC1);
* a rejection that answers 422 and has already bumped the version (AC2 — the
  re-read must show *every* field unchanged);
* a truncation applied to the sentence but not to `references`, or a count
  reporting the truncated length rather than the true total (AC8).
"""

import json
import logging

import pytest
from fastapi.testclient import TestClient
from loregarden.models.domain import McpToolCall, TicketState
from loregarden.testing.factories import make_agent_run, make_ticket, make_workspace
from sqlmodel import Session
from tests.studio_agent_helpers import (
    AGENTS_PATH,
    add_raw_draft,
    add_raw_template,
    add_template,
    classes,
    create_agent,
    orphan_version_rows,
    stage,
    version_rows,
    versions_status,
)


@pytest.fixture
def session(client: TestClient):
    """A session on the suite's isolated engine, alongside the TestClient.

    `client` is requested first on purpose: it is what rebinds the engine the
    app and this session both open.
    """
    from loregarden.db.session import engine

    with Session(engine) as session:
        yield session


@pytest.fixture
def workspace(session):
    return make_workspace(session, slug="agent-refs-adv-ws")


def warnings_in(caplog) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.levelno >= logging.WARNING]


# --- AC1 + AC4: a row the scan cannot read is not a silent "no references" ---

#: `stages_json` shapes the scan cannot read as a stage list. Two of these are
#: not hypothetical: the column has no schema, and the stage model has gained
#: fields over its life.
UNREADABLE_STAGES_JSON = [
    pytest.param("{not json at all", id="corrupt-json"),
    pytest.param("", id="empty-string"),
    pytest.param('["work", "review"]', id="list-of-strings"),
    pytest.param("[1, 2, 3]", id="list-of-numbers"),
]


@pytest.mark.parametrize("stages_json", UNREADABLE_STAGES_JSON)
def test_an_unreadable_stages_json_is_neither_a_500_nor_a_silent_pass(
    client: TestClient, session, caplog, stages_json
):
    """AC1 + AC4. AC1 says the delete never answers 500 for an agent that
    exists; AC4 says nothing on this path is swallowed. A bare `json.loads`
    breaks the first, and `except Exception: return no_references()` breaks the
    second — it deletes an agent whose references were never actually read.

    Both permitted answers are accepted here: refuse, or delete and say on the
    log that a row could not be read. What is not accepted is a 500, or a 200
    with nothing reported anywhere.
    """
    agent = create_agent(client)
    add_raw_template(session, "tpl-unreadable", stages_json)
    caplog.clear()

    with caplog.at_level(logging.WARNING):
        res = client.delete(f"{AGENTS_PATH}/{agent['slug']}")

    assert res.status_code != 500, res.text
    assert res.status_code in (200, 409), res.text
    if res.status_code == 200:
        assert warnings_in(caplog), (
            "the scan could not read tpl-unreadable and deleted the agent anyway, "
            "with nothing on the log, the response or anywhere else (AC4)"
        )


@pytest.mark.parametrize("stages_json", UNREADABLE_STAGES_JSON)
def test_an_unreadable_draft_does_not_hide_a_template_that_does_name_the_agent(
    client: TestClient, session, stages_json
):
    """AC16 + AC4. The scan reads several sources. One unreadable row must not
    end the scan: a `json.loads` outside the loop, or a handler that returns
    early, hides the template that genuinely blocks and the agent is deleted
    out from under it.
    """
    agent = create_agent(client)
    add_raw_draft(session, "draft-unreadable", stages_json)
    add_template(session, "tpl-really-blocks", [stage(agent_id=agent["slug"])])

    res = client.delete(f"{AGENTS_PATH}/{agent['slug']}")

    assert res.status_code == 409, res.text
    blocking = classes(res.json(), "blocking")
    assert "tpl-really-blocks" in blocking["workflow_template"]["ids"]
    assert versions_status(client, agent["slug"]) == 200


#: Readable JSON that names no agent. These must not refuse: a refusal nobody
#: can clear is the failure mode this ticket exists to stop (AC16, A7).
READABLE_WITHOUT_THE_SLUG = [
    pytest.param("[]", id="no-stages"),
    pytest.param("null", id="json-null"),
    pytest.param("{}", id="json-object"),
    pytest.param('[{"key": "work"}]', id="stage-with-no-agent-field"),
    pytest.param('[{"agent_id": null}]', id="agent-id-null"),
    pytest.param('[{"agent_id": ""}]', id="agent-id-empty"),
    pytest.param('[{"agent_id": "planner"}]', id="another-agent"),
    pytest.param('[{"classify_routes": null}]', id="classify-routes-null"),
    pytest.param('[{"classify_routes": [{"agent_id": "planner"}]}]', id="classify-another-agent"),
    pytest.param('[{"parallel_agents": [{}]}]', id="parallel-agent-with-no-id"),
    pytest.param('[{"agent_id": "planner", "unknown_future_field": {"a": 1}}]', id="newer-stage"),
]


@pytest.mark.parametrize("stages_json", READABLE_WITHOUT_THE_SLUG)
def test_a_stage_list_that_does_not_name_the_agent_never_refuses(
    client: TestClient, session, stages_json
):
    """AC16. The scan reads the three modelled positions and matches this slug.
    Nothing here names it, so the delete is the 2xx arm — and it must not be a
    500 either, which an unguarded `stage["agent_id"]` or a `None`-unsafe
    comparison would make it."""
    agent = create_agent(client)
    add_raw_template(session, "tpl-readable", stages_json)

    res = client.delete(f"{AGENTS_PATH}/{agent['slug']}")

    assert res.status_code == 200, res.text
    assert versions_status(client, agent["slug"]) == 404


#: Values that are *near* the slug, in the right field. A scan matching with
#: `in`, `startswith` or a normalising comparison refuses on these.
NEAR_MISS_AGENT_IDS = [
    "delete-probe-2",
    "pre-delete-probe",
    "delete-prob",
    "delete_probe",
    "Delete-Probe",
]


@pytest.mark.parametrize("agent_id", NEAR_MISS_AGENT_IDS)
def test_an_agent_id_that_is_merely_close_does_not_block(client: TestClient, session, agent_id):
    """AC16. `delete-probe-2` is a different agent. A substring or prefix match
    refuses a delete the operator cannot clear by editing anything, because no
    stage names the agent being deleted.

    `Delete-Probe` and `delete_probe` are the same argument from the other
    side, and the bound is set by the orchestrator rather than by taste:
    `stage_offers_agent` (services/studio_routing.py:666) matches with
    `(stage.agent_id or "").strip() == agent_id`. No case folding and no
    slugify, so neither of those strings routes anywhere, and a scan that
    normalises before comparing claims a reference the orchestrator does not
    have. The one normalisation the orchestrator *does* do is pinned by
    `test_a_padded_agent_id_still_blocks` below.
    """
    agent = create_agent(client)
    add_template(session, "tpl-near-miss", [stage(agent_id=agent_id)])

    res = client.delete(f"{AGENTS_PATH}/{agent['slug']}")

    assert res.status_code == 200, res.text
    assert versions_status(client, agent["slug"]) == 404


def test_a_padded_agent_id_still_blocks(client: TestClient, session):
    """AC16. The scan's match must be the orchestrator's match, and the
    orchestrator strips: `stage_offers_agent` compares
    `(stage.agent_id or "").strip()`. So a stage whose `agent_id` carries
    whitespace *does* dispatch this agent, and deleting it leaves that stage
    pointing at nothing — the stale-slot defect this ticket is about.

    A scan written as a bare `==` against the stored string passes every other
    test in both files and misses this reference.
    """
    agent = create_agent(client)
    add_raw_template(session, "tpl-padded", json.dumps([stage(agent_id=f" {agent['slug']} ")]))

    res = client.delete(f"{AGENTS_PATH}/{agent['slug']}")

    assert res.status_code == 409, res.text
    assert "tpl-padded" in classes(res.json(), "blocking")["workflow_template"]["ids"]


@pytest.mark.parametrize("field", ["key", "name", "skill_name", "stage_type"])
def test_the_slug_in_a_field_that_is_not_an_agent_position_does_not_block(
    client: TestClient, session, field
):
    """AC16. The criterion names three positions: `stage.agent_id`,
    `classify_routes[].agent_id`, `parallel_agents[].agent_id`. A scan that
    walks the stage dict's values instead refuses here — and `skill_name`
    equal to the slug is the realistic case, because skills and agents are
    named from the same vocabulary.

    The contract suite's substring test uses `rev-pass-review`, which an
    equality comparison over all values survives. This one does not.
    """
    agent = create_agent(client)
    add_template(session, "tpl-wrong-position", [stage(**{field: agent["slug"]})])

    res = client.delete(f"{AGENTS_PATH}/{agent['slug']}")

    assert res.status_code == 200, res.text
    assert versions_status(client, agent["slug"]) == 404


def test_a_historical_column_holding_the_slug_does_not_block_through_the_blocking_half(
    client: TestClient, session, workspace
):
    """AC16 + A7. `agent_runs.agent_id` and `tickets.next_agent` are the same
    string in two tables, and only one of them blocks. A scan that collects
    every row mentioning the slug and then splits it by table is correct; one
    that splits it by "did we find anything" is not."""
    agent = create_agent(client)
    ticket = make_ticket(
        session,
        workspace_id=workspace.id,
        external_id="hist-live-ticket",
        state=TicketState.IN_PROGRESS,
    )
    make_agent_run(
        session,
        workspace_id=workspace.id,
        ticket_id=ticket.id,
        run_code="RUN-ADV-HIST",
        agent_id=agent["slug"],
    )
    session.commit()

    res = client.delete(f"{AGENTS_PATH}/{agent['slug']}")

    assert res.status_code == 200, res.text
    assert versions_status(client, agent["slug"]) == 404


# --- AC1: what a working delete is allowed to touch -------------------------


def test_the_delete_takes_every_version_row_and_leaves_no_orphan(client: TestClient, session):
    """AC1(a). "Deleted" is the agent row *and* its history. A delete that
    takes the agent and leaves the versions behind satisfies a 200 and a
    `/versions` 404 (that route looks the agent up first) while leaving rows
    nothing can ever reach — and the next agent created with the same id
    inherits them."""
    agent = create_agent(client)
    for note in ("second", "third"):
        assert (
            client.patch(
                f"{AGENTS_PATH}/{agent['slug']}", json={"description": note, "change_note": note}
            ).status_code
            == 200
        )
    assert len(version_rows(session, agent["slug"])) >= 3

    res = client.delete(f"{AGENTS_PATH}/{agent['slug']}")

    assert res.status_code == 200, res.text
    assert orphan_version_rows(session) == []


def test_deleting_one_agent_leaves_another_agents_history_alone(client: TestClient, session):
    """AC1. The blast radius: a delete whose `where` clause went missing, or a
    scan-then-delete that deleted by slug prefix, passes every single-agent
    test in the contract suite."""
    doomed = create_agent(client, slug="delete-probe")
    keeper = create_agent(client, slug="delete-probe-keeper")
    assert (
        client.patch(f"{AGENTS_PATH}/{keeper['slug']}", json={"description": "v2"}).status_code
        == 200
    )
    kept_before = len(version_rows(session, keeper["slug"]))
    assert kept_before >= 2

    assert client.delete(f"{AGENTS_PATH}/{doomed['slug']}").status_code == 200

    session.expire_all()
    assert len(version_rows(session, keeper["slug"])) == kept_before
    assert client.get(f"{AGENTS_PATH}/{keeper['slug']}").status_code == 200
    assert versions_status(client, keeper["slug"]) == 200


def test_a_second_delete_of_the_same_agent_is_404_not_500(client: TestClient):
    """AC1. Order dependency: the operator's second click, or a retry after a
    dropped response. The row is gone, so this is the missing-agent arm."""
    agent = create_agent(client)
    assert client.delete(f"{AGENTS_PATH}/{agent['slug']}").status_code == 200

    res = client.delete(f"{AGENTS_PATH}/{agent['slug']}")

    assert res.status_code == 404, res.text


def test_a_refusal_leaves_the_version_history_byte_for_byte(client: TestClient, session):
    """AC1(b). "Nothing is deleted" includes the history. A scan that runs
    after the version deletes but before the agent delete refuses with the
    right status and has already destroyed the history."""
    agent = create_agent(client)
    for note in ("second", "third"):
        client.patch(f"{AGENTS_PATH}/{agent['slug']}", json={"change_note": note})
    before = client.get(f"{AGENTS_PATH}/{agent['slug']}/versions").json()
    assert len(before) >= 3
    add_template(session, "tpl-refuses", [stage(agent_id=agent["slug"])])

    assert client.delete(f"{AGENTS_PATH}/{agent['slug']}").status_code == 409

    assert client.get(f"{AGENTS_PATH}/{agent['slug']}/versions").json() == before


def test_a_refusal_leaves_the_agent_writable_afterwards(client: TestClient, session):
    """AC1(b) + AC4. R2 says the session is left unmodified. A refusal raised
    with pending deletes in the session, or after a partial flush, poisons the
    next write on the same request-scoped session instead of failing here."""
    agent = create_agent(client)
    add_template(session, "tpl-refuses-then-write", [stage(agent_id=agent["slug"])])
    assert client.delete(f"{AGENTS_PATH}/{agent['slug']}").status_code == 409

    res = client.patch(f"{AGENTS_PATH}/{agent['slug']}", json={"description": "Still editable."})

    assert res.status_code == 200, res.text
    assert client.get(f"{AGENTS_PATH}/{agent['slug']}").json()["description"] == "Still editable."


def test_two_identical_refusals_answer_identically(client: TestClient, session):
    """AC8. Determinism. The truncation picks 10 of a larger set; if the pick
    rides on dict or set iteration order, the operator's notice reshuffles on
    every click and two screenshots of one refusal disagree."""
    agent = create_agent(client)
    for index in range(14):
        add_template(session, f"tpl-det-{index:02d}", [stage(agent_id=agent["slug"])])

    first = client.delete(f"{AGENTS_PATH}/{agent['slug']}").json()
    second = client.delete(f"{AGENTS_PATH}/{agent['slug']}").json()

    assert first == second


def test_several_blocking_classes_are_all_reported(client: TestClient, session, workspace):
    """AC7 + AC16. The combinatorial case, and the one the live install
    produces: `godot-ddd-migrator` is named by a template *and* a draft. A
    scan that returns at the first blocking class it finds tells the operator
    to go fix one of two things and refuses again when they have."""
    agent = create_agent(client)
    add_template(session, "tpl-multi", [stage(agent_id=agent["slug"])])
    add_raw_draft(session, "draft-multi", json.dumps([stage(agent_id=agent["slug"])]))
    ticket = make_ticket(
        session,
        workspace_id=workspace.id,
        external_id="pin-multi",
        state=TicketState.IN_PROGRESS,
    )
    ticket.next_agent = agent["slug"]
    session.add(ticket)
    session.commit()

    body = client.delete(f"{AGENTS_PATH}/{agent['slug']}").json()

    blocking = classes(body, "blocking")
    assert {"workflow_template", "studio_workflow", "ticket_next_agent"} <= set(blocking)
    assert "tpl-multi" in blocking["workflow_template"]["ids"]
    assert "draft-multi" in blocking["studio_workflow"]["ids"]
    assert "pin-multi" in blocking["ticket_next_agent"]["ids"]


def test_one_template_naming_the_agent_twice_counts_it_once(client: TestClient, session):
    """AC8. The count is the operator's workload: "8 templates still name it"
    is a number of places to go. A per-occurrence count turns one template with
    a classify route and a parallel stage into three, and the sentence is then
    false."""
    agent = create_agent(client)
    add_template(
        session,
        "tpl-twice",
        [
            stage(agent_id=agent["slug"]),
            stage(key="again", order=2, agent_id=agent["slug"]),
            stage(
                key="classify",
                order=3,
                stage_type="classify",
                classify_routes=[{"agent_id": agent["slug"]}],
            ),
        ],
    )

    body = client.delete(f"{AGENTS_PATH}/{agent['slug']}").json()

    entry = classes(body, "blocking")["workflow_template"]
    assert entry["ids"] == ["tpl-twice"]
    assert entry["count"] == 1


# --- AC8: the boundaries of the truncation ----------------------------------


@pytest.mark.parametrize("total", [1, 9, 10, 11])
def test_the_truncation_boundary(client: TestClient, session, total):
    """AC8. "At most 10 ids alongside the true total" has exactly one off-by-one
    each side of 10, and both ship as a wrong number in front of the operator:
    truncating at 10 but reporting `len(ids)` says "10 templates" when there
    are 11, and truncating at 11 puts the eleventh in the toast."""
    agent = create_agent(client)
    slugs = [f"tpl-bound-{index:02d}" for index in range(total)]
    for template_slug in slugs:
        add_template(session, template_slug, [stage(agent_id=agent["slug"])])

    body = client.delete(f"{AGENTS_PATH}/{agent['slug']}").json()

    entry = classes(body, "blocking")["workflow_template"]
    assert entry["count"] == total
    assert len(entry["ids"]) == min(total, 10)
    assert set(entry["ids"]) <= set(slugs)
    assert len([name for name in slugs if name in body["detail"]]) <= 10


def test_the_historical_half_is_bounded_and_counted_the_same_way(
    client: TestClient, session, workspace
):
    """AC8. The criterion says "per reference class", not "per blocking class",
    and the historical half is where the big numbers are: `planner` carries 491
    runs and 379 tool calls. A bound applied only to the blocking half puts 491
    identifiers on the response."""
    agent = create_agent(client)
    ticket = make_ticket(session, workspace_id=workspace.id, external_id="hist-bound")
    for index in range(13):
        make_agent_run(
            session,
            workspace_id=workspace.id,
            ticket_id=ticket.id,
            run_code=f"RUN-BOUND-{index:02d}",
            agent_id=agent["slug"],
        )
        session.add(
            McpToolCall(
                run_id=f"run-bound-{index:02d}",
                ticket_id=ticket.id,
                agent_id=agent["slug"],
                tool_name="Bash",
            )
        )
    session.commit()
    add_template(session, "tpl-hist-bound", [stage(agent_id=agent["slug"])])

    body = client.delete(f"{AGENTS_PATH}/{agent['slug']}").json()

    historical = classes(body, "historical")
    for kind in ("agent_run", "mcp_tool_call"):
        assert historical[kind]["count"] == 13, historical
        assert len(historical[kind]["ids"]) <= 10
    for entry in body["references"]["historical"]:
        assert set(entry) == {"kind", "ids", "count"}


def test_the_detail_stays_a_sentence_at_the_largest_realistic_volume(
    client: TestClient, session, workspace
):
    """AC8. Measured bound: `planner` carries 8 templates, 3 drafts and 65 live
    tickets. Unbounded that is ~76 identifiers in a nine-second toast, which is
    the failure this criterion exists to prevent — so the assertion is on the
    sentence's own size, not only on the id lists."""
    agent = create_agent(client)
    for index in range(40):
        add_template(session, f"tpl-vol-{index:02d}", [stage(agent_id=agent["slug"])])
    for index in range(12):
        add_raw_draft(
            session, f"draft-vol-{index:02d}", json.dumps([stage(agent_id=agent["slug"])])
        )
    for index in range(20):
        ticket = make_ticket(
            session,
            workspace_id=workspace.id,
            external_id=f"pin-vol-{index:02d}",
            state=TicketState.IN_PROGRESS,
        )
        ticket.next_agent = agent["slug"]
        session.add(ticket)
    session.commit()

    body = client.delete(f"{AGENTS_PATH}/{agent['slug']}").json()

    detail = body["detail"]
    assert isinstance(detail, str)
    assert len(detail) < 1200, f"a {len(detail)}-character toast is the unbounded detail (AC8)"
    for entry in body["references"]["blocking"]:
        assert len(entry["ids"]) <= 10, entry
    assert classes(body, "blocking")["workflow_template"]["count"] == 40
    assert classes(body, "blocking")["ticket_next_agent"]["count"] == 20


# --- AC2 / AC3: the PATCH body's corners ------------------------------------


def test_an_omitted_slug_is_not_a_rename(client: TestClient):
    """AC2 + A5. `slug: null` is `StudioAgentUpdate`'s default once the field is
    declared, so an implementation checking `"slug" in body` or truthiness
    rather than `body.slug is not None` rejects every save the client makes."""
    agent = create_agent(client)

    res = client.patch(
        f"{AGENTS_PATH}/{agent['slug']}", json={"slug": None, "description": "Unchanged id."}
    )

    assert res.status_code == 200, res.text
    assert res.json()["slug"] == agent["slug"]
    assert res.json()["description"] == "Unchanged id."


def test_an_empty_patch_body_is_a_no_op_and_not_a_422(client: TestClient):
    """AC2. `extra="forbid"` must reject undeclared keys, not an absent body.
    An empty object declares nothing and asks for nothing."""
    agent = create_agent(client)
    before = client.get(f"{AGENTS_PATH}/{agent['slug']}").json()

    res = client.patch(f"{AGENTS_PATH}/{agent['slug']}", json={})

    assert res.status_code == 200, res.text
    assert {k: v for k, v in res.json().items() if k not in {"version", "updated_at"}} == {
        k: v for k, v in before.items() if k not in {"version", "updated_at"}
    }


def test_change_note_is_accepted_and_is_not_written_to_the_agent(client: TestClient):
    """AC3. `change_note` is the 13th field and belongs to the version row —
    which is why the parity assertion excludes it. `extra="forbid"` must not
    reject it, and it must not arrive on the agent."""
    agent = create_agent(client)

    res = client.patch(
        f"{AGENTS_PATH}/{agent['slug']}",
        json={"description": "With a note.", "change_note": "why I changed it"},
    )

    assert res.status_code == 200, res.text
    assert "change_note" not in res.json()
    notes = [
        row["change_note"] for row in client.get(f"{AGENTS_PATH}/{agent['slug']}/versions").json()
    ]
    assert "why I changed it" in notes


def test_a_rejected_slug_change_does_not_bump_the_version(client: TestClient):
    """AC2. "A re-read shows the old slug AND every other field unchanged" —
    `version` is one of those fields, and `updated_at` another. A rejection
    raised after `agent.version += 1` and the version-row write answers 422
    and has already recorded a revision that changed nothing, which is the
    silent-write shape from the other direction."""
    agent = create_agent(client)
    before = client.get(f"{AGENTS_PATH}/{agent['slug']}").json()
    versions_before = client.get(f"{AGENTS_PATH}/{agent['slug']}/versions").json()

    res = client.patch(f"{AGENTS_PATH}/{agent['slug']}", json={"slug": "renamed-probe"})

    assert res.status_code == 422, res.text
    after = client.get(f"{AGENTS_PATH}/{agent['slug']}").json()
    assert after == before
    assert client.get(f"{AGENTS_PATH}/{agent['slug']}/versions").json() == versions_before


def test_a_rejected_undeclared_field_does_not_bump_the_version(client: TestClient):
    """AC2. Same argument for the `extra="forbid"` arm: FastAPI rejects before
    the handler runs, so this is a regression guard on the ordering rather than
    on new code — and it fails loudly if the forbid arm is ever implemented as
    a post-write check instead."""
    agent = create_agent(client)
    before = client.get(f"{AGENTS_PATH}/{agent['slug']}").json()

    assert (
        client.patch(f"{AGENTS_PATH}/{agent['slug']}", json={"bogus_field": 1}).status_code == 422
    )

    assert client.get(f"{AGENTS_PATH}/{agent['slug']}").json() == before


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"slug": 123}, id="slug-not-a-string"),
        pytest.param({"slug": ["renamed"]}, id="slug-a-list"),
        pytest.param({"slug": {"value": "renamed"}}, id="slug-an-object"),
        pytest.param({"timeout": "soon"}, id="timeout-not-an-int"),
        pytest.param({"mcp_tools": "Read"}, id="mcp-tools-not-a-list"),
        pytest.param({"gate_checks": "none"}, id="gate-checks-not-a-list"),
        pytest.param({"tool_grants": "inherit"}, id="tool-grants-not-an-object"),
    ],
)
def test_a_wrongly_typed_value_is_refused_rather_than_dropped(client: TestClient, body):
    """AC2. "Never accepts a field it discards" covers the type as well as the
    name: a `slug` that is not a string must not slip past a comparison that
    only looks at strings, and `timeout: "soon"` must not be coerced to
    something nobody asked for."""
    agent = create_agent(client)
    before = client.get(f"{AGENTS_PATH}/{agent['slug']}").json()

    res = client.patch(f"{AGENTS_PATH}/{agent['slug']}", json=body)

    assert res.status_code == 422, res.text
    assert client.get(f"{AGENTS_PATH}/{agent['slug']}").json() == before


@pytest.mark.parametrize(
    "echo", ["delete_probe", "Delete_Probe!", "delete--probe", "-delete-probe-"]
)
def test_more_echoes_that_slugify_to_the_same_id_are_no_ops(client: TestClient, echo):
    """AC2 + A5. Equality is compared after `slugify`, whose rule is "collapse
    every non-alphanumeric run to one dash and strip the ends". Each of these
    resolves to the stored slug, so each is a no-op rather than a rename, and a
    strict string comparison 422s an operator who typed the display name."""
    agent = create_agent(client)

    res = client.patch(f"{AGENTS_PATH}/{agent['slug']}", json={"slug": echo})

    assert res.status_code == 200, res.text
    assert res.json()["slug"] == agent["slug"]


def test_a_slug_change_on_an_agent_that_does_not_exist_is_404(client: TestClient):
    """AC2 + AC9. Which refusal wins when both apply. The sentence AC9 requires
    — "duplicate the agent under a new slug instead" — is false for an agent
    that does not exist, and the delete path already answers 404 first for the
    same reason. So the lookup precedes the slug check.

    The spec does not pin this ordering; it is recorded as a checkpoint on the
    ticket rather than left for two stages to decide differently.
    """
    res = client.patch(f"{AGENTS_PATH}/no-such-agent-anywhere", json={"slug": "renamed"})

    assert res.status_code == 404, res.text


def test_every_writable_field_arrives_when_they_are_sent_together(client: TestClient):
    """AC3. The contract suite sends one field per request, which an updater
    applying only the first non-None value survives. The operator's Save sends
    the whole draft."""
    agent = create_agent(client, default_model="")
    payload = {
        "name": "All At Once",
        "description": "Every field in one request.",
        "role_body": "You do all of it at once.",
        "adapter": "cursor",
        "default_model": "opus",
        "timeout": 4321,
        "mcp_enabled": True,
        "mcp_tools": ["loregarden_get_ticket", "loregarden_doctor"],
        "gate_checks": [{"kind": "ac_review", "title": "Together", "impact": "Blocks the merge"}],
        "handoff_checks": [{"kind": "custom", "prompt": "Together."}],
        "tool_grants": {
            "posture": "allowlist",
            "allowed_tools": ["Read"],
            "disallowed_tools": [],
            "mcp_servers": [],
        },
    }

    res = client.patch(f"{AGENTS_PATH}/{agent['slug']}", json=payload)

    assert res.status_code == 200, res.text
    stored = client.get(f"{AGENTS_PATH}/{agent['slug']}").json()
    assert stored["name"] == payload["name"]
    assert stored["description"] == payload["description"]
    assert payload["role_body"] in stored["role_body"]
    assert stored["adapter"] == "cursor"
    assert stored["default_model"] == "opus"
    assert stored["timeout"] == 4321
    assert set(payload["mcp_tools"]) <= set(stored["mcp_tools"])
    assert stored["gate_checks"][0]["title"] == "Together"
    assert stored["handoff_checks"][0]["prompt"] == "Together."
    assert stored["tool_grants"]["allowed_tools"] == ["Read"]


def test_the_create_surface_still_takes_a_slug(client: TestClient):
    """AC2 + AC6. The slug is fixed *after* creation. `StudioAgentCreate` is a
    different schema, and a forbid-and-reject arm written on the shared one
    would close the only way to choose a slug at all."""
    created = create_agent(client, slug="create-still-works")

    assert created["slug"] == "create-still-works"
    assert client.get(f"{AGENTS_PATH}/create-still-works").status_code == 200
