"""Every advertised MCP tool must be callable over JSON-RPC.

The tool list is the agent-facing API surface: prompts now route reports, checkpoints,
learnings, and memory through these names, so a tool that is advertised but errors on a
well-formed call is a silent dead end for every agent. This pins the whole surface at once —
tools/list matching the dispatcher, and each tool returning a non-error result.

Not a semantic test. It answers "is this wired", not "is the payload right"; per-tool
behaviour lives in the focused suites.
"""

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

MCP_TOOLS_SRC = Path(__file__).resolve().parents[1] / "loregarden" / "mcp" / "tools.py"

# Tools that do real work beyond bookkeeping and need care in a smoke test:
#   loregarden_start_orchestration -> BuiltinOrchestrator.execute() spawns CLI agents, so it is
#   exercised only through the external_mcp driver, which records a run and returns.
SPAWNS_AGENTS_WITH_BUILTIN_DRIVER = "loregarden_start_orchestration"


def _rpc(client: TestClient, method: str, params: dict | None = None, rpc_id: int = 1) -> dict:
    res = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params or {}},
    )
    assert res.status_code == 200, f"{method} -> HTTP {res.status_code}: {res.text[:300]}"
    return res.json()


def _call(client: TestClient, tool: str, args: dict) -> dict:
    return _rpc(client, "tools/call", {"name": tool, "arguments": args}, rpc_id=99)


def _advertised(client: TestClient) -> set[str]:
    return {t["name"] for t in _rpc(client, "tools/list")["result"]["tools"]}


def _dispatched() -> set[str]:
    """Tool names the dispatcher actually branches on."""
    return set(re.findall(r'name == "(loregarden_[a-z_]+)"', MCP_TOOLS_SRC.read_text()))


def test_advertised_tools_all_have_a_dispatch_branch(client: TestClient):
    """A tool in tools/list with no handler is a trap: agents call it and get an error."""
    missing = _advertised(client) - _dispatched()
    assert not missing, f"advertised but never dispatched: {sorted(missing)}"


def _seed_ticket(client: TestClient) -> tuple[str, str]:
    tickets = client.get("/api/tickets").json()
    assert tickets, "seed produced no tickets"
    return tickets[0]["id"], tickets[0]["external_id"]


def _args_for(
    tool: str, ticket_id: str, external_id: str, run_id: str, memory_id: str, stage_key: str
) -> dict | None:
    """Minimal well-formed arguments per tool, mirroring each schema's `required`."""
    ws = "loregarden"
    table: dict[str, dict] = {
        "loregarden_get_ticket": {"ticket_id": ticket_id},
        "loregarden_get_ticket_by_external": {"workspace_slug": ws, "external_id": external_id},
        "loregarden_list_tickets": {"workspace_slug": ws},
        "loregarden_memory_status": {"workspace_slug": ws},
        "loregarden_search_memory": {"query": "smoke", "workspace_slug": ws},
        "loregarden_upsert_memory": {
            "title": "smoke-memory",
            "workspace_slug": ws,
            "body": "written by the MCP smoke test",
        },
        "loregarden_create_memory_relation": {
            "source_id": memory_id,
            "target_id": memory_id,
            "workspace_slug": ws,
        },
        "loregarden_append_learning": {
            "ticket_id": ticket_id,
            "workspace_slug": ws,
            "content": "smoke learning",
        },
        "loregarden_append_checkpoint": {
            "ticket_id": ticket_id,
            "workspace_slug": ws,
            "run_id": run_id,
            "entry": "smoke checkpoint",
        },
        "loregarden_upsert_blog_post": {
            "ticket_id": ticket_id,
            "workspace_slug": ws,
            "title": "smoke-post",
            "body": "smoke body",
        },
        "loregarden_attach_artifact": {
            "run_id": run_id,
            "kind": "log",
            "title": "smoke artifact",
            "content_json": json.dumps({"lines": []}),
        },
        "loregarden_update_ticket": {"ticket_id": ticket_id, "state": "in_progress"},
        "loregarden_request_approval": {
            "run_id": run_id,
            "stage_key": stage_key,
            "title": "smoke approval",
        },
        "loregarden_write_handoff": {
            "ticket_id": ticket_id,
            "workspace_slug": ws,
            "from_agent": "ticket_scoper",
            "to_agent": "planner",
            "checklist": [{"item_key": "smoke_item", "item": "Smoke item", "status": "complete"}],
        },
        "loregarden_block_ticket": {"run_id": run_id, "message": "smoke block"},
        "loregarden_start_orchestration": {"ticket_id": ticket_id, "driver": "external_mcp"},
        "loregarden_complete_orchestration": {"run_id": run_id},
        "loregarden_start_stage": {"run_id": run_id, "stage_key": stage_key},
        "loregarden_complete_stage": {"run_id": run_id, "stage_key": stage_key},
        "loregarden_skip_stage": {"run_id": run_id, "stage_key": stage_key},
    }
    return table.get(tool)


def test_every_advertised_tool_is_callable(client: TestClient):
    """Call each tool once with well-formed args; none may return a JSON-RPC error.

    Ordering matters: an orchestration run must exist before stage tools, and a run_id must
    exist before attach_artifact, so the flow is start -> stage tools -> complete.
    """
    ticket_id, external_id = _seed_ticket(client)

    started = _call(
        client, "loregarden_start_orchestration", {"ticket_id": ticket_id, "driver": "external_mcp"}
    )
    assert "error" not in started, f"start_orchestration: {started.get('error')}"
    run = json.loads(started["result"]["content"][0]["text"])
    run_id = run.get("id", "")
    assert run_id, f"start_orchestration returned no run id: {run}"
    stage_key = run.get("current_stage_key") or "triage"

    # create_memory_relation needs real node ids; make one to point at.
    mem = _call(
        client,
        "loregarden_upsert_memory",
        {"title": "smoke-anchor", "workspace_slug": "loregarden", "body": "anchor"},
    )
    # upsert_memory returns {"obsidian": {"id": ...}, "graph": {"id": ...}} — no top-level id.
    memory_id = ""
    if "error" not in mem and not mem.get("result", {}).get("isError"):
        try:
            payload = json.loads(mem["result"]["content"][0]["text"])
            for backend in ("graph", "obsidian"):
                node = payload.get(backend) or {}
                if isinstance(node, dict) and node.get("id"):
                    memory_id = node["id"]
                    break
        except (ValueError, KeyError, IndexError):
            memory_id = ""
    assert memory_id, f"could not resolve a memory node id from upsert_memory: {mem}"

    ordered = [
        "loregarden_get_ticket",
        "loregarden_get_ticket_by_external",
        "loregarden_list_tickets",
        "loregarden_memory_status",
        "loregarden_search_memory",
        "loregarden_upsert_memory",
        "loregarden_create_memory_relation",
        "loregarden_append_learning",
        "loregarden_append_checkpoint",
        "loregarden_upsert_blog_post",
        "loregarden_attach_artifact",
        "loregarden_update_ticket",
        "loregarden_request_approval",
        "loregarden_write_handoff",
        "loregarden_start_stage",
        "loregarden_complete_stage",
        "loregarden_skip_stage",
        "loregarden_block_ticket",
        "loregarden_complete_orchestration",
    ]
    advertised = _advertised(client)
    assert set(ordered) | {SPAWNS_AGENTS_WITH_BUILTIN_DRIVER} >= advertised, (
        "tool advertised but not covered here: "
        f"{sorted(advertised - (set(ordered) | {SPAWNS_AGENTS_WITH_BUILTIN_DRIVER}))}"
    )

    failures: list[str] = []
    for tool in ordered:
        if tool not in advertised:
            continue
        args = _args_for(tool, ticket_id, external_id, run_id, memory_id, stage_key)
        if args is None:
            failures.append(f"{tool}: no args defined in the smoke table")
            continue
        body = _call(client, tool, args)
        if "error" in body:
            failures.append(f"{tool}: {json.dumps(body['error'])[:200]}")
            continue
        result = body.get("result", {})
        if result.get("isError"):
            text = (result.get("content") or [{}])[0].get("text", "")
            failures.append(f"{tool}: isError -> {text[:200]}")

    assert not failures, "MCP tools failed:\n  " + "\n  ".join(failures)


@pytest.mark.parametrize("tool", ["loregarden_get_ticket", "loregarden_complete_stage"])
def test_unknown_ticket_is_a_clean_error_not_a_crash(client: TestClient, tool: str):
    """Agents pass bad ids. That must surface as an error, not a 500."""
    body = _call(client, tool, {"ticket_id": "does-not-exist", "outcome": "proceed"})
    assert "error" in body or body["result"].get("isError"), (
        f"{tool} silently accepted an unknown ticket_id"
    )
