"""Every read writes nothing: each GET route, and each read-only MCP tool.

The operation layer's strict read guard (`LOREGARDEN_STRICT_READ_OPERATIONS`)
sees only reads routed through an operation, which was 21 of 178 GET routes.
The rest call services directly, and nothing stopped one from writing: the
ticket page reconciled its ticket, the docker board measured the host, the
queue board fitted the slot pool, and two lists seeded their tables when they
found them empty.

So this calls every GET route the app mounts, over the production-shaped
scenario with one of each entity it has none of, and records the SQL each one
issues. A write fails the test, naming the route and the statement. So does a
route that answers with an error: a read that never reaches its data proves
nothing about whether reading the data writes. The few that cannot reach it in
a test (GitHub, docker, a launched server) are listed with why, and must still
write nothing on the way to their error.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from loregarden.config import settings
from loregarden.db.session import get_session
from loregarden.main import app
from loregarden.mcp.tool_ids import READ_ONLY_MCP_TOOLS, McpTool
from loregarden.models.domain import (
    AgentRun,
    AgentSlot,
    Artifact,
    InitiativeMember,
    MergeQueueEntry,
    QueueOperation,
    Skill,
    StageFanoutAttempt,
    StageFanoutGroup,
    StudioWorkflow,
    SurveyResearchQuestion,
    SurveySource,
    Ticket,
    WorkflowTemplate,
    Workspace,
    Worktree,
)
from loregarden.models.domain.enums import utcnow
from loregarden.services.review_kit import captures_root
from loregarden.services.slot_pool import fit_slot_pool, fitted_slot_numbers
from loregarden.testing.prod_shape import prod_shape_id
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, select
from tests.mcp_helpers import call_mcp

_WRITES = ("INSERT", "UPDATE", "DELETE", "REPLACE")

#: Routes that cannot reach their data in a test, and why.
NOT_REACHABLE_IN_A_TEST: dict[str, str] = {
    "/api/workspaces/{slug}/pull-requests": "lists pull requests from GitHub; no gh under pytest",
    "/api/machine/disk/docker": "asks docker for its disk use; no docker under pytest",
    "/api/machine/disk/tree": "reads the last scan from process memory; a scan walks the disk",
    "/api/instances/{instance_id}": "an instance is a launched server process",
    "/api/instances/{instance_id}/health": "an instance is a launched server process",
    "/api/instances/{instance_id}/logs": "an instance is a launched server process",
    "/api/reference-research/questions/{question_id}/diff": (
        "compares two runs of a research question, and each run is an agent turn"
    ),
}

#: Routes that write on read, and why that is the design rather than a defect.
WRITES_BY_DESIGN: dict[str, str] = {
    "/api/living-map/{scope}/{ref}": (
        "files the records it reads onto the map, which keeps every item ever"
        " seen (services/living_map/store.py)"
    ),
}

#: Read-only MCP tools whose writes are their declared effect.
MCP_TOOLS_THAT_RECORD: dict[McpTool, str] = {
    McpTool.DOCKER_CAPACITY_STATUS: (
        "records the poll on a lease and may promote waiters; its operation is WRITE"
    ),
}

#: The MCP transport's audit row, written for every call whatever the tool does.
_MCP_AUDIT_TABLE = "mcp_tool_calls"


def _get_routes(routes=None, prefix: str = "") -> Iterator[tuple[str, APIRoute]]:
    for route in app.routes if routes is None else routes:
        inner = getattr(route, "original_router", None)
        if inner is not None:
            yield from _get_routes(inner.routes, prefix + route.include_context.prefix)
        elif type(route) is APIRoute and "GET" in route.methods:
            yield prefix + route.path, route


@contextmanager
def _writes(engine: Engine) -> Iterator[list[str]]:
    seen: list[str] = []

    def record(_conn, _cursor, statement, _params, _context, _many) -> None:
        if statement.lstrip().upper().startswith(_WRITES):
            seen.append(" ".join(statement.split())[:160])

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", record)


def _git(repo: str, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", repo, *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@dataclass
class World:
    """The ids each route's parameters name, and the query values it requires."""

    ids: dict[str, str]
    by_prefix: dict[str, dict[str, str]]
    query: dict[str, str]
    query_by_route: dict[str, dict[str, str]] = field(default_factory=dict)

    def url(self, path: str) -> str:
        values = dict(self.ids)
        for prefix, extra in self.by_prefix.items():
            if path.startswith(prefix):
                values.update(extra)
        return re.sub(r"{(\w+)(?::\w+)?}", lambda m: values[m.group(1)], path)

    def params(self, path: str, route: APIRoute) -> dict[str, str]:
        params = {
            q.name: self.query[q.name]
            for q in route.dependant.query_params
            if q.field_info.is_required() and q.name in self.query
        }
        return params | self.query_by_route.get(path, {})


@pytest.fixture(name="reader")
def reader_fixture(isolated_db):
    """The app over the test's database, without its lifespan.

    No `with`: the lifespan runs the startup sweep, whose writes are its own
    and not any route's.
    """

    def session_override():
        with Session(isolated_db) as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_session, None)


def _branch_with_a_commit(repo: str) -> tuple[str, str]:
    """The checked-out branch and a tracked file, after committing on a second branch."""
    branch = _git(repo, "branch", "--show-current")
    a_file = _git(repo, "ls-files").split()[0]
    _git(repo, "checkout", "-q", "-b", "reads-probe")
    (Path(repo) / "reads-probe.txt").write_text("x\n")
    _git(repo, "add", "reads-probe.txt")
    _git(repo, "commit", "-q", "-m", "reads probe")
    _git(repo, "checkout", "-q", branch)
    return branch, a_file


def _made_through_the_api(reader: TestClient, workspace: Workspace, run: AgentRun) -> dict:
    """The entities the scenario has none of that the app makes over its own API."""
    base = f"/api/workspaces/{workspace.slug}"
    chat = reader.post(f"{base}/baxter-chat/sessions", json={}).json()
    global_chat = reader.post("/api/baxter-chat/global/sessions", json={}).json()
    upload = {"file": ("note.txt", b"hello", "text/plain")}
    leaf = {"node": "leaf", "id": "n1", "size": 1.0, "container_id": "c1"}
    layout = {
        "kind": "flex_grid",
        "containers": {"c1": {"kind": "terminal", "settings": {}}},
        "root": leaf,
    }
    return {
        "chat": chat,
        "global_chat": global_chat,
        "chat_file": reader.post(
            f"{base}/baxter-chat/sessions/{chat['id']}/attachments", files=upload
        ).json(),
        "global_file": reader.post(
            f"/api/baxter-chat/global/sessions/{global_chat['id']}/attachments", files=upload
        ).json(),
        "view": reader.post(f"{base}/views", json={"title": "v", "layout": layout}).json(),
        "studio": reader.post(
            "/api/ticket-studio/sessions", json={"workspace_slug": workspace.slug, "title": "t"}
        ).json(),
        "review": reader.post(
            f"/api/parallel/workspace/{workspace.id}/runs/{run.id}/output-review",
            json={"output_type": "stdout", "output_content": "x"},
        ).json(),
        "graph": reader.get("/api/memory/graph", params={"workspace_slug": workspace.slug}).json(),
    }


def _made_as_rows(
    engine: Engine, run: AgentRun, ticket: Ticket, *, repo: str, branch: str, chat_id: str
) -> dict[str, str]:
    """The entities only an agent run makes, written as the rows that run would leave."""
    with Session(engine) as writer:
        template = writer.exec(
            select(WorkflowTemplate).where(WorkflowTemplate.slug == "loregarden-tdd")
        ).one()
        tree = Worktree(
            workspace_id=ticket.workspace_id,
            agent_run_id=run.id,
            worktree_path=repo,
            branch=branch,
        )
        group = StageFanoutGroup(
            workspace_id=ticket.workspace_id, ticket_id=ticket.id, stage_key="implement"
        )
        writer.add_all([tree, group])
        writer.flush()
        attempt = StageFanoutAttempt(group_id=group.id, attempt_index=0, worktree_id=tree.id)
        operation = QueueOperation(
            workspace_id=ticket.workspace_id, before_state_json="{}", after_state_json="{}"
        )
        question = SurveyResearchQuestion(
            workspace_id=ticket.workspace_id, thread_id=chat_id, question="why?"
        )
        flow = StudioWorkflow(slug="reads-flow", name="r", published_template_id=template.id)
        writer.add_all([attempt, operation, question, flow])
        writer.commit()
        return {
            "group_id": group.id,
            "attempt_id": attempt.id,
            "operation_id": operation.id,
            "question_id": question.id,
            "template_slug": template.slug,
            "flow_slug": flow.slug,
        }


@pytest.fixture(name="world")
def world_fixture(prod_shape_session, isolated_db, reader, monkeypatch) -> World:
    """The scenario, plus one of each entity it has none of, made as the app makes them."""
    session = prod_shape_session
    run = session.get(AgentRun, prod_shape_id("worked-run", 0))
    assert run is not None
    ticket = session.get(Ticket, run.ticket_id)
    assert ticket is not None
    workspace = session.get(Workspace, ticket.workspace_id)
    assert workspace is not None
    repo = session.exec(select(Workspace.repo_path).where(Workspace.slug == "loregarden")).one()
    monkeypatch.setattr(settings, "browse_root", str(Path(repo).parent))
    branch, a_file = _branch_with_a_commit(repo)
    api = _made_through_the_api(reader, workspace, run)
    capture_id = "0" * 32 + ".png"
    (captures_root() / ticket.id).mkdir(parents=True, exist_ok=True)
    (captures_root() / ticket.id / capture_id).write_bytes(b"png")
    made = _made_as_rows(
        isolated_db, run, ticket, repo=repo, branch=branch, chat_id=api["chat"]["id"]
    )
    template_slug, flow_slug = made.pop("template_slug"), made.pop("flow_slug")
    with Session(isolated_db) as reading:
        initiative_id = reading.exec(select(InitiativeMember.initiative_id)).first()
        entry_id = reading.exec(select(MergeQueueEntry.id)).first()
        source = reading.exec(select(SurveySource)).first()
        skill = reading.exec(select(Skill.slug)).first()
        artifact_id = reading.exec(
            select(Artifact.id).where(Artifact.ticket_id == ticket.id)
        ).first()
    assert initiative_id and entry_id and source and skill and artifact_id, (
        "the scenario lost a row this test needs"
    )

    return World(
        ids={
            "ticket_id": ticket.id,
            "run_id": run.id,
            "external_id": ticket.external_id,
            "workspace_slug": workspace.slug,
            "slug": workspace.slug,
            "workspace_id": workspace.id,
            "initiative_id": initiative_id,
            "entry_id": entry_id,
            "artifact_id": artifact_id,
            "agent_id": "planner",
            "version": "1",
            "scope": "ticket",
            "ref": ticket.id,
            "view_id": api["view"]["id"],
            "review_id": api["review"]["review_id"],
            "node_id": api["graph"]["nodes"][0]["id"],
            "capture_id": capture_id,
            "source_id": source.id,
            "instance_id": "no-such-instance",
            # A turn's thinking lives only while the turn runs, and answers empty after.
            "turn_id": "no-turn-running",
            **made,
        },
        by_prefix={
            "/api/studio/agents/": {"slug": "planner"},
            "/api/studio/skills/": {"slug": skill},
            "/api/studio/workflows/": {"slug": flow_slug},
            "/api/workspaces/{slug}/baxter-chat/": {
                "session_id": api["chat"]["id"],
                "attachment_id": api["chat_file"]["id"],
            },
            "/api/baxter-chat/global/": {
                "session_id": api["global_chat"]["id"],
                "attachment_id": api["global_file"]["id"],
            },
            "/api/ticket-studio/": {"session_id": api["studio"]["id"]},
        },
        query={
            "template_slug": template_slug,
            "search": "ab",
            "path": repo,
            "since": (utcnow() - timedelta(days=30)).isoformat(),
            "branch": branch,
            "sha": _git(repo, "rev-parse", "HEAD"),
            "ticket_id": ticket.id,
            "workspace": workspace.slug,
            "workspace_slug": workspace.slug,
            "url": source.url,
            "capability": "x",
        },
        query_by_route={
            "/api/workspaces/{slug}/editor/file": {"path": a_file},
            "/api/tickets/{ticket_id}/fanout/{group_id}/attempts/{attempt_id}/file": {
                "path": a_file
            },
            "/api/workspaces/{slug}/branch-triage/diff": {"branch": "reads-probe"},
            "/api/system/browse": {"path": repo},
            "/api/system/browse-import": {"path": repo},
            "/api/review/preview/{workspace_slug}": {"ref": branch},
            "/api/memory/nodes/{node_id}": {"workspace_slug": workspace.slug},
            "/api/memory/nodes/{node_id}/lineage": {"workspace_slug": workspace.slug},
        },
    )


def test_every_get_route_reaches_its_data_and_writes_nothing(world, reader, isolated_db) -> None:
    wrote: list[str] = []
    unreached: list[str] = []
    stale: list[str] = []
    for path, route in _get_routes():
        with _writes(isolated_db) as seen:
            response = reader.get(world.url(path), params=world.params(path, route))
        if seen and path not in WRITES_BY_DESIGN:
            wrote.append(f"{path}: {seen[:3]}")
        if path in WRITES_BY_DESIGN and not seen:
            stale.append(f"{path} no longer writes; drop it from WRITES_BY_DESIGN")
        reached = response.status_code < 400
        if not reached and path not in NOT_REACHABLE_IN_A_TEST:
            unreached.append(f"{path}: {response.status_code} {response.text[:160]}")
        if reached and path in NOT_REACHABLE_IN_A_TEST:
            stale.append(f"{path} now answers; drop it from NOT_REACHABLE_IN_A_TEST")

    assert not wrote, "GET routes that write:\n" + "\n".join(wrote)
    assert not unreached, (
        "GET routes this test never got past an error; give it the ids or entities"
        " they need, or say why they cannot reach their data in a test:\n" + "\n".join(unreached)
    )
    assert not stale, "\n".join(stale)


def test_every_listed_route_is_mounted() -> None:
    mounted = {path for path, _ in _get_routes()}
    assert set(NOT_REACHABLE_IN_A_TEST) <= mounted
    assert set(WRITES_BY_DESIGN) <= mounted


def test_no_get_route_is_shadowed_by_an_earlier_one() -> None:
    """`/studio/workflows/drift` sat behind `/studio/workflows/{slug}` and was never reached."""
    routes = [path for path, _ in _get_routes()]
    shadowed = []
    for index, path in enumerate(routes):
        if "{" in path.rsplit("/", 1)[-1]:
            continue
        url = re.sub(r"{[^}]+}", "x", path)
        for earlier in routes[:index]:
            pattern = re.sub(
                r"{[^}:]+(:path)?}", lambda m: ".+" if m.group(1) else "[^/]+", earlier
            )
            if earlier != path and re.fullmatch(pattern, url):
                shadowed.append(f"{path} is matched first by {earlier}")
    assert not shadowed, "\n".join(shadowed)


def _mcp_args(ids: dict[str, str]) -> dict[McpTool, dict[str, str]]:
    slug = ids["workspace_slug"]
    return {
        McpTool.BRANCH_MOVES: {"workspace_slug": slug},
        McpTool.DISK_USAGE: {},
        McpTool.DOCKER_CAPACITY_STATUS: {},
        McpTool.DOCTOR: {"workspace_slug": slug},
        McpTool.GET_INITIATIVE_PLAN: {"initiative_id": ids["initiative_id"]},
        McpTool.GET_TICKET: {"ticket_id": ids["ticket_id"]},
        McpTool.GET_TICKET_BY_EXTERNAL: {
            "external_id": ids["external_id"],
            "workspace_slug": slug,
        },
        McpTool.INSTANCE_STATUS: {"instance_id": ids["instance_id"]},
        McpTool.LIST_ARTIFACTS: {"ticket_id": ids["ticket_id"]},
        McpTool.LIST_INSTANCES: {},
        McpTool.LIST_OPERATOR_QUESTIONS: {"workspace_slug": slug},
        McpTool.LIST_TICKETS: {"workspace_slug": slug},
        McpTool.LIST_UI_ACTIONS: {},
        McpTool.MEMORY_STATUS: {"workspace_slug": slug},
        McpTool.MERGE_QUEUE_STATUS: {"workspace_slug": slug},
        McpTool.READ_ARTIFACT: {"artifact_id": ids["artifact_id"]},
        McpTool.SEARCH_MEMORY: {"query": "test", "workspace_slug": slug},
    }


def test_every_read_only_mcp_tool_writes_nothing(world, reader, isolated_db) -> None:
    args = _mcp_args(world.ids)
    assert set(args) == set(READ_ONLY_MCP_TOOLS), "a read-only tool has no call here"
    wrote = []
    refused = []
    for tool, arguments in sorted(args.items(), key=lambda item: item[0].value):
        with _writes(isolated_db) as seen:
            reply = call_mcp(reader, tool.value, arguments)
        writes = [statement for statement in seen if _MCP_AUDIT_TABLE not in statement]
        if writes and tool not in MCP_TOOLS_THAT_RECORD:
            wrote.append(f"{tool.value}: {writes[:3]}")
        result = reply.get("result", {})
        if "error" in reply or result.get("isError"):
            detail = reply.get("error") or result["content"][0]["text"][:160]
            refused.append(f"{tool.value}: {detail}")
    assert not wrote, "read-only MCP tools that write:\n" + "\n".join(wrote)
    assert not refused, "read-only MCP tools that refused a well-formed call:\n" + "\n".join(
        refused
    )


# ---- reads that wrote only on a database nobody had seeded ------------------
#
# Every entry point seeds skills and workflow templates when it opens the
# database, so the scenario above never shows these. Without `client`, a test's
# database is the bare schema: what these reads found when they seeded on read.


@pytest.mark.parametrize(
    "path", ["/api/agents/skills", "/api/workflows/templates", "/api/parallel/status"]
)
def test_a_read_of_an_unseeded_table_writes_nothing(reader, isolated_db, path) -> None:
    with _writes(isolated_db) as seen:
        response = reader.get(path)
    assert response.status_code == 200, response.text
    assert seen == []


@pytest.mark.parametrize(
    ("size", "slots"),
    [
        (3, []),
        (3, [(1, True), (2, True), (3, True)]),
        # Lowered: one idle slot past the limit, which a fit retires, and one busy one it keeps.
        (2, [(1, True), (2, True), (3, True), (4, False)]),
        (4, [(2, False)]),
    ],
)
def test_the_board_draws_the_lanes_a_fit_would_leave(isolated_db, size, slots) -> None:
    """`fitted_slot_numbers` is the read of `fit_slot_pool`: the same lanes, unwritten."""
    with Session(isolated_db) as session:
        session.add_all(
            AgentSlot(slot_number=number, is_available=available) for number, available in slots
        )
        session.commit()
        with _writes(isolated_db) as seen:
            drawn = fitted_slot_numbers(session, size)
        assert seen == []
        fit_slot_pool(session, size)
        assert drawn == sorted(session.exec(select(AgentSlot.slot_number)).all())
