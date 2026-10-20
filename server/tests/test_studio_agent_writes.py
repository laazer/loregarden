"""The two Studio agent write surfaces must succeed, or refuse in words.

`DELETE /api/studio/agents/{slug}` answers 500 today for every db-backed agent
(the version rows and the agent row go out in one flush, and SQLite's FK check
fires on the agent row first), and `PATCH` accepts a `slug` change with 200
without applying it — a write that reports success and changes nothing, which
is the *No silent failures* shape CLAUDE.md forbids.

Covers lg-milestone-that-1239 AC1, AC2, AC3, AC4, AC8, AC9 and AC16.

Two deliberate choices in here, because the tests are the contract:

* The post-delete re-read is at **row level** (`/versions` → 404, and absence
  from the list among the `read_only: false` entries). `GET /agents/{slug}`
  falls back to the registry view when no row exists, so it answers 200 for a
  built-in slug whose row is gone — a broken delete would read as working (AC1,
  K3).
* The refusal body is asserted as `{"detail": "<str>", "references": {...}}`.
  `http.ts` lifts `detail` into the thrown `ApiError` only when it is a `str`,
  and `HTTPException` can carry only one of the two (AC8).
"""

import pytest
from fastapi.testclient import TestClient
from loregarden.models.domain import (
    McpToolCall,
    StageFanoutGroup,
    StageFanoutGroupStatus,
    StudioAgent,
    TicketState,
)
from loregarden.models.domain.schemas import StudioAgentUpdate
from loregarden.services.studio_service import _AGENT_UPDATE_COLUMNS
from loregarden.testing.factories import make_agent_run, make_ticket, make_workspace
from sqlmodel import Session, select
from tests.studio_agent_helpers import (
    AGENT_SLUG,
    AGENTS_PATH,
    add_draft,
    add_template,
    classes,
    create_agent,
    pin_ticket,
    stage,
    versions_status,
    writable_slugs,
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
    return make_workspace(session, slug="agent-refs-ws")


# --- AC1: the delete succeeds or refuses, never 500s ------------------------


def test_deleting_an_agent_nothing_references_succeeds(client: TestClient):
    """AC1(a). Every db-backed agent has >=1 version row, so this is the case
    that 500s today — there is no passing case to regress."""
    agent = create_agent(client)
    assert versions_status(client, agent["slug"]) == 200

    res = client.delete(f"{AGENTS_PATH}/{agent['slug']}")

    assert res.status_code == 200, res.text
    assert res.json() == {"ok": True}
    # Row level, not `GET /agents/{slug}`: that falls back to the registry view.
    assert versions_status(client, agent["slug"]) == 404
    assert agent["slug"] not in writable_slugs(client)


def test_the_delete_never_answers_500(client: TestClient, session, workspace):
    """AC1. The headline: whichever arm it takes, it is not a server error."""
    agent = create_agent(client)
    add_template(session, "tpl-blocks", [stage(agent_id=agent["slug"])])

    refused = client.delete(f"{AGENTS_PATH}/{agent['slug']}")
    assert refused.status_code < 500, refused.text
    assert refused.status_code == 409

    other = create_agent(client, slug="delete-probe-free")
    assert client.delete(f"{AGENTS_PATH}/{other['slug']}").status_code < 500


def test_a_referenced_agent_is_refused_and_nothing_is_deleted(
    client: TestClient, session, workspace
):
    """AC1(b). The refusal is 409 and the agent plus its history survive it."""
    agent = create_agent(client)
    add_template(session, "studio-blobert-ddd", [stage(agent_id=agent["slug"])])
    before = client.get(f"{AGENTS_PATH}/{agent['slug']}").json()

    res = client.delete(f"{AGENTS_PATH}/{agent['slug']}")

    assert res.status_code == 409, res.text
    assert versions_status(client, agent["slug"]) == 200
    assert agent["slug"] in writable_slugs(client)
    assert client.get(f"{AGENTS_PATH}/{agent['slug']}").json() == before


def test_deleting_an_absent_agent_is_still_404(client: TestClient):
    """AC2/AC1. The reference scan must not turn a missing agent into a 409."""
    res = client.delete(f"{AGENTS_PATH}/no-such-agent-anywhere")
    assert res.status_code == 404


# --- AC16: the refusal is decided by a reference scan, and its scope is pinned


def ref_template_stage_agent(session, workspace, slug):
    add_template(session, "tpl-stage-agent", [stage(agent_id=slug)])
    return "workflow_template", "tpl-stage-agent"


def ref_template_classify_route(session, workspace, slug):
    add_template(
        session,
        "tpl-classify-route",
        [
            stage(
                stage_type="classify",
                classify_routes=[
                    {"agent_id": slug, "languages": [], "specialties": [], "default": True}
                ],
            )
        ],
    )
    return "workflow_template", "tpl-classify-route"


def ref_template_parallel_agent(session, workspace, slug):
    add_template(
        session,
        "tpl-parallel-agent",
        [stage(stage_type="parallel", parallel_agents=[{"agent_id": slug, "skill_name": ""}])],
    )
    return "workflow_template", "tpl-parallel-agent"


def ref_draft_stage_agent(session, workspace, slug):
    add_draft(session, "blobert-ddd", [stage(agent_id=slug)])
    return "studio_workflow", "blobert-ddd"


def ref_draft_classify_route(session, workspace, slug):
    add_draft(
        session,
        "draft-classify-route",
        [stage(stage_type="classify", classify_routes=[{"agent_id": slug}])],
    )
    return "studio_workflow", "draft-classify-route"


def ref_draft_parallel_agent(session, workspace, slug):
    add_draft(
        session,
        "draft-parallel-agent",
        [stage(stage_type="parallel", parallel_agents=[{"agent_id": slug}])],
    )
    return "studio_workflow", "draft-parallel-agent"


def ref_live_ticket_next_agent(session, workspace, slug):
    pin_ticket(
        session,
        workspace,
        "pin-next-agent",
        column="next_agent",
        agent=slug,
        state=TicketState.IN_PROGRESS,
    )
    return "ticket_next_agent", "pin-next-agent"


def ref_live_ticket_scope_reroute(session, workspace, slug):
    pin_ticket(
        session,
        workspace,
        "pin-scope-reroute",
        column="scope_reroute_agent",
        agent=slug,
        state=TicketState.BACKLOG,
    )
    return "ticket_scope_reroute_agent", "pin-scope-reroute"


BLOCKING_CLASSES = [
    ref_template_stage_agent,
    ref_template_classify_route,
    ref_template_parallel_agent,
    ref_draft_stage_agent,
    ref_draft_classify_route,
    ref_draft_parallel_agent,
    ref_live_ticket_next_agent,
    ref_live_ticket_scope_reroute,
]


@pytest.mark.parametrize("reference", BLOCKING_CLASSES, ids=lambda fn: fn.__name__)
def test_each_blocking_reference_class_refuses_on_its_own(
    client: TestClient, session, workspace, reference
):
    """AC16. One reference of one class is enough, and the payload names it."""
    agent = create_agent(client)
    kind, identifier = reference(session, workspace, agent["slug"])

    res = client.delete(f"{AGENTS_PATH}/{agent['slug']}")

    assert res.status_code == 409, res.text
    body = res.json()
    blocking = classes(body, "blocking")
    assert kind in blocking, f"{kind} missing from {body['references']}"
    assert identifier in blocking[kind]["ids"]
    assert blocking[kind]["count"] >= 1
    assert versions_status(client, agent["slug"]) == 200


@pytest.mark.parametrize("state", [TicketState.DONE, TicketState.WONT_DO])
@pytest.mark.parametrize("column", ["next_agent", "scope_reroute_agent"])
def test_a_terminal_tickets_agent_pin_does_not_block(
    client: TestClient, session, workspace, column, state
):
    """AC16. A closed ticket's pin routes nothing, so it must not refuse."""
    agent = create_agent(client)
    pin_ticket(
        session,
        workspace,
        f"terminal-{column}-{state.value}",
        column=column,
        agent=agent["slug"],
        state=state,
    )

    res = client.delete(f"{AGENTS_PATH}/{agent['slug']}")

    assert res.status_code == 200, res.text
    assert versions_status(client, agent["slug"]) == 404


def test_historical_references_alone_never_block(client: TestClient, session, workspace):
    """AC16/A7. A ledger row is append-only: blocking on one is a refusal with
    no exit, because nothing the operator does can clear it."""
    agent = create_agent(client)
    ticket = make_ticket(session, workspace_id=workspace.id, external_id="hist-only")
    make_agent_run(session, workspace_id=workspace.id, ticket_id=ticket.id, agent_id=agent["slug"])
    session.add(
        McpToolCall(
            run_id="run-hist-only",
            ticket_id=ticket.id,
            agent_id=agent["slug"],
            tool_name="Bash",
        )
    )
    session.add(
        StageFanoutGroup(
            workspace_id=workspace.id,
            ticket_id=ticket.id,
            stage_key="implement",
            pre_fanout_next_agent=agent["slug"],
            status=StageFanoutGroupStatus.SETTLED,
        )
    )
    session.commit()

    res = client.delete(f"{AGENTS_PATH}/{agent['slug']}")

    assert res.status_code == 200, res.text
    assert res.json() == {"ok": True}
    assert versions_status(client, agent["slug"]) == 404


def test_historical_references_are_reported_on_a_refusal(client: TestClient, session, workspace):
    """AC7/AC16. Counted and reported — but only once something else refuses."""
    agent = create_agent(client)
    ticket = make_ticket(session, workspace_id=workspace.id, external_id="hist-and-block")
    make_agent_run(
        session,
        workspace_id=workspace.id,
        ticket_id=ticket.id,
        run_code="RUN-HIST",
        agent_id=agent["slug"],
    )
    session.commit()
    add_template(session, "tpl-with-history", [stage(agent_id=agent["slug"])])

    body = client.delete(f"{AGENTS_PATH}/{agent['slug']}").json()

    historical = classes(body, "historical")
    assert "agent_run" in historical
    assert historical["agent_run"]["count"] >= 1


def test_a_stage_key_or_skill_that_merely_contains_the_slug_does_not_block(
    client: TestClient, session, workspace
):
    """AC16/K4. A raw substring match over `stages_json` would refuse here, and
    the operator could never clear it — nothing names the agent."""
    create_agent(client, slug="rev-pass")
    add_template(
        session,
        "tpl-substring-only",
        [
            stage(
                key="rev-pass-check",
                name="rev-pass check",
                agent_id="planner",
                skill_name="rev-pass-review",
            )
        ],
    )

    res = client.delete(f"{AGENTS_PATH}/rev-pass")

    assert res.status_code == 200, res.text
    assert versions_status(client, "rev-pass") == 404


def test_a_stage_naming_another_agent_does_not_block(client: TestClient, session, workspace):
    """AC16. The scan is scoped to this slug, not to "any agent_id"."""
    agent = create_agent(client)
    add_template(session, "tpl-other-agent", [stage(agent_id="planner")])

    assert client.delete(f"{AGENTS_PATH}/{agent['slug']}").status_code == 200


# --- AC4: the 409 is not derived from the integrity error -------------------


def test_the_delete_path_does_not_decide_the_refusal_from_an_integrity_error(client: TestClient):
    """AC4/D4. The only FK to `studio_agents` is the agent's own version
    history, so a refusal derived from `IntegrityError` would name
    `studio_agent_versions` as what still references the agent — a refusal the
    operator can never clear, which is the failure mode this ticket exists to
    stop. Asserted on the source because the wrong implementation passes every
    behavioural test above."""
    import inspect

    from loregarden.services import studio_agent_references, studio_service

    delete_source = inspect.getsource(studio_service.StudioService.delete_agent)
    assert "IntegrityError" not in delete_source
    assert "IntegrityError" not in inspect.getsource(studio_agent_references)
    # The ordering fix, not a swallow: the version deletes reach the database
    # before the agent row does.
    assert "flush" in delete_source


def test_the_scan_module_exposes_the_two_halves(client: TestClient, session, workspace):
    """AC16. The scan is a pure read with a blocking half and a historical one,
    so demoting a class stays a one-line change."""
    from loregarden.services.studio_agent_references import (
        AgentDeleteRefused,
        scan_agent_references,
    )

    assert issubclass(AgentDeleteRefused, ValueError)
    agent = create_agent(client)
    add_template(session, "tpl-for-scan", [stage(agent_id=agent["slug"])])

    refs = scan_agent_references(session, agent["slug"])

    assert [entry.kind for entry in refs.blocking] == ["workflow_template"]
    assert refs.historical == []
    # A pure read: the scan is run before any delete and leaves the row alone.
    assert versions_status(client, agent["slug"]) == 200


# --- AC8: the refusal detail is one bounded string --------------------------


def test_the_refusal_detail_is_a_bounded_string_and_the_counts_are_true(
    client: TestClient, session, workspace
):
    """AC8. `planner` carries ~76 identifiers on the live database; unbounded,
    that is the whole list in a 9-second toast."""
    agent = create_agent(client)
    slugs = [f"tpl-many-{index:02d}" for index in range(12)]
    for template_slug in slugs:
        add_template(session, template_slug, [stage(agent_id=agent["slug"])])

    body = client.delete(f"{AGENTS_PATH}/{agent['slug']}").json()

    detail = body["detail"]
    assert isinstance(detail, str)
    assert len([name for name in slugs if name in detail]) <= 10
    assert "12" in detail, "the class's true total belongs in the sentence"
    entry = classes(body, "blocking")["workflow_template"]
    assert entry["count"] == 12
    assert len(entry["ids"]) == 10
    assert set(entry["ids"]) <= set(slugs)


def test_the_refusal_carries_the_structured_references(client: TestClient, session, workspace):
    """AC7/AC8. The notice reads its links off this payload rather than a second
    fetch (AC12) or the sentence's prose."""
    agent = create_agent(client)
    add_template(session, "tpl-structured", [stage(agent_id=agent["slug"])])

    body = client.delete(f"{AGENTS_PATH}/{agent['slug']}").json()

    assert set(body) == {"detail", "references"}
    assert set(body["references"]) == {"blocking", "historical"}
    for entry in body["references"]["blocking"]:
        assert set(entry) == {"kind", "ids", "count"}
        assert isinstance(entry["kind"], str)
        assert isinstance(entry["count"], int)
        assert len(entry["ids"]) <= 10


# --- AC2 / AC9: the PATCH rejects a slug change in words --------------------


def test_patch_rejects_a_differing_slug_and_writes_nothing(client: TestClient):
    """AC2/AC9/D1. Today this answers 200 and silently drops the slug."""
    agent = create_agent(client)
    before = client.get(f"{AGENTS_PATH}/{agent['slug']}").json()

    res = client.patch(
        f"{AGENTS_PATH}/{agent['slug']}",
        json={"slug": "renamed-probe", "name": "Renamed Probe"},
    )

    assert res.status_code == 422, res.text
    detail = res.json()["detail"]
    # Not pydantic's [{"type": "extra_forbidden", ...}]: `http.ts` lifts `detail`
    # into the toast only when it is a str, and the operator needs a sentence.
    assert isinstance(detail, str)
    assert "slug" in detail
    assert client.get(f"{AGENTS_PATH}/renamed-probe").status_code == 404
    assert client.get(f"{AGENTS_PATH}/{agent['slug']}").json() == before
    assert "renamed-probe" not in writable_slugs(client)


@pytest.mark.parametrize("echo", [AGENT_SLUG, "Delete Probe", "  delete-probe  ", "DELETE-PROBE"])
def test_patch_treats_a_slug_equal_after_slugify_as_a_no_op(client: TestClient, echo):
    """AC2/D3/A5. Nothing is discarded when no change was requested, and an echo
    differing only in case or spacing must not read as a rename."""
    agent = create_agent(client)

    res = client.patch(
        f"{AGENTS_PATH}/{agent['slug']}", json={"slug": echo, "description": "Still here."}
    )

    assert res.status_code == 200, res.text
    assert res.json()["slug"] == agent["slug"]
    assert res.json()["description"] == "Still here."


def test_patch_treats_a_builtin_underscore_slug_echo_as_a_no_op(client: TestClient, session):
    """AC2/A5 on the slug shape 29 of this install's 31 agents actually have.

    Built-in agents are seeded with the registry id verbatim (`ac_gatekeeper`),
    and `slugify` maps `_` to `-`, so a check that only compares the slugified
    echo against the raw stored slug refuses an agent's own slug echoed back
    unchanged. `AGENT_SLUG` is slugify-stable, so the fixture above cannot see
    it.
    """
    stored = session.exec(select(StudioAgent).where(StudioAgent.slug == "ac_gatekeeper")).first()
    assert stored is not None and stored.built_in, "the suite's seed no longer has this built-in"

    res = client.patch(
        f"{AGENTS_PATH}/ac_gatekeeper",
        json={"slug": "ac_gatekeeper", "description": "Still here."},
    )

    assert res.status_code == 200, res.text
    assert res.json()["slug"] == "ac_gatekeeper"
    assert res.json()["description"] == "Still here."


def test_patch_rejects_an_undeclared_field(client: TestClient):
    """AC2. `extra="ignore"` is pydantic v2's default, so this is a silent 200
    today — and so is any other key nobody declared."""
    agent = create_agent(client)

    res = client.patch(f"{AGENTS_PATH}/{agent['slug']}", json={"bogus_field": 1})

    assert res.status_code == 422, res.text


def test_patch_rejects_a_misspelled_field_rather_than_discarding_it(client: TestClient):
    """AC2/AC3. `role_bdy` is the realistic shape of the same bug."""
    agent = create_agent(client)
    before = client.get(f"{AGENTS_PATH}/{agent['slug']}").json()["role_body"]

    res = client.patch(
        f"{AGENTS_PATH}/{agent['slug']}", json={"role_bdy": "You do something else."}
    )

    assert res.status_code == 422, res.text
    assert client.get(f"{AGENTS_PATH}/{agent['slug']}").json()["role_body"] == before


def test_patch_rejects_a_view_only_field_the_client_type_used_to_permit(client: TestClient):
    """AC6. `updateStudioAgent`'s body was `Partial<StudioAgent>` — the view
    type — so `read_only`, `built_in`, `version` and friends type-checked."""
    agent = create_agent(client)

    res = client.patch(f"{AGENTS_PATH}/{agent['slug']}", json={"read_only": True, "version": 99})

    assert res.status_code == 422, res.text


# --- AC3: no field is accepted-and-discarded --------------------------------


#: A value distinct from what `create_agent` stores, per writable field. Keyed
#: by field so the parity assertion below fails when a new field is declared on
#: `StudioAgentUpdate` and nobody gave it a value here.
DISTINCT_VALUES: dict[str, object] = {
    "name": "Renamed Probe",
    "description": "A different one-line description.",
    "role_body": "You now do an entirely different thing.",
    "adapter": "cursor",
    "default_model": "opus",
    "timeout": 1234,
    # Replaced per-run with a skill this database actually has: the skills
    # registry reads the `skills` table, and `validate_default_skill` rejects a
    # name that is not in it.
    "default_skill": None,
    "mcp_enabled": False,
    # `loregarden_doctor` is in neither STAGE_DEFAULT_MCP_TOOLS nor
    # MEMORY_DEFAULT_MCP_TOOLS, so its arrival cannot be a default's doing.
    "mcp_tools": ["loregarden_get_ticket", "loregarden_doctor"],
    "gate_checks": [
        {"kind": "ac_review", "title": "Distinct gate check", "impact": "Blocks the merge"}
    ],
    "handoff_checks": [{"kind": "custom", "prompt": "A distinct handoff prompt."}],
    "tool_grants": {
        "posture": "allowlist",
        "allowed_tools": ["Read", "Grep"],
        "disallowed_tools": [],
        "mcp_servers": [],
    },
}


def assert_arrived(field: str, sent: object, stored: object) -> None:
    if field == "role_body":
        # `ensure_studio_role_preamble` prepends the Studio preamble on the way in.
        assert isinstance(stored, str) and str(sent) in stored
        return
    if field == "mcp_tools":
        # The view merges MEMORY_DEFAULT_MCP_TOOLS back in on read.
        assert set(sent) <= set(stored)  # type: ignore[arg-type]
        return
    assert stored == sent


def test_every_writable_field_on_the_patch_arrives(client: TestClient):
    """AC3. Enumerated from the schema, not from a hand-written list, so the
    next silently-ignored field fails here rather than shipping."""
    writable = set(StudioAgentUpdate.model_fields) - {"change_note", "slug"}
    assert writable == set(DISTINCT_VALUES), (
        "a writable field on StudioAgentUpdate has no distinct value in this test"
    )

    skill = client.post(
        "/api/studio/skills",
        json={
            "slug": "agent-write-probe-skill",
            "name": "Agent Write Probe Skill",
            "body": "A skill that exists, so `default_skill` has a valid value.",
        },
    )
    assert skill.status_code == 200, skill.text
    values = {**DISTINCT_VALUES, "default_skill": skill.json()["slug"]}

    agent = create_agent(client, default_skill="", default_model="")
    # `mcp_enabled: False` goes last: the view resolves `mcp_tools` to [] while
    # MCP is off, so disabling it first would hide the mcp_tools write rather
    # than test it.
    for field in sorted(writable, key=lambda name: (name == "mcp_enabled", name)):
        sent = values[field]
        res = client.patch(f"{AGENTS_PATH}/{agent['slug']}", json={field: sent})
        assert res.status_code == 200, f"{field}: {res.text}"
        stored = client.get(f"{AGENTS_PATH}/{agent['slug']}").json()[field]
        assert_arrived(field, sent, stored)


def test_the_update_column_table_matches_the_schema(client: TestClient):
    """AC3. The other half of the same bug: a field declared on the schema and
    forgotten in `_AGENT_UPDATE_COLUMNS` is accepted and discarded with no
    error, and `extra="forbid"` cannot see it."""
    mapped = {field for field, _column in _AGENT_UPDATE_COLUMNS}

    assert mapped == set(StudioAgentUpdate.model_fields) - {"change_note", "slug"}
    assert "slug" not in mapped, "the slug is never written to the agent row (D1)"
