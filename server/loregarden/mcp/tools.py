"""In-process MCP tool implementations — shared by HTTP mount and optional stdio proxy."""

from __future__ import annotations

import json
from typing import Any

from sqlmodel import Session

from loregarden.agents.executors.tool_auto_approve import (
    reject_orchestrated_argument_denial,
    reject_orchestrated_tool_denial,
)
from loregarden.mcp.artifact_tools import attach_evidence
from loregarden.mcp.asserted_not_proven_tools import ASSERTED_NOT_PROVEN_TOOL_DEFINITIONS
from loregarden.mcp.block_ticket_args import normalize_block_ticket
from loregarden.mcp.branch_moves_tool import TOOL_DEFINITION as BRANCH_MOVES_TOOL_DEFINITION
from loregarden.mcp.criterion_tools import CRITERION_TOOL_DEFINITIONS, settle_criterion_tool
from loregarden.mcp.deferral_tool import TOOL_DEFINITION as DEFER_TICKET_TOOL_DEFINITION
from loregarden.mcp.devdocs_tool import TOOL_DEFINITION as DEVDOCS_TOOL_DEFINITION
from loregarden.mcp.discovery_tools import DISCOVERY_TOOL_DEFINITIONS, decide_finding_tool
from loregarden.mcp.disk_usage_tool import TOOL_DEFINITION as DISK_USAGE_TOOL_DEFINITION
from loregarden.mcp.docker_capacity_tool import (
    TOOL_DEFINITIONS as DOCKER_CAPACITY_TOOL_DEFINITIONS,
)
from loregarden.mcp.doctor_tool import TOOL_DEFINITION as DOCTOR_TOOL_DEFINITION
from loregarden.mcp.document_tools import TOOL_DEFINITIONS as DOCUMENT_TOOL_DEFINITIONS
from loregarden.mcp.external_harness_tools import (
    EXTERNAL_HARNESS_TOOL_DEFINITIONS,
    normalize_external_harness_args,
)
from loregarden.mcp.feature_loop_tools import TOOL_DEFINITIONS as FEATURE_LOOP_TOOL_DEFINITIONS
from loregarden.mcp.fix_coverage_tools import FIX_COVERAGE_TOOL_DEFINITIONS
from loregarden.mcp.github_issue_tool import TOOL_DEFINITION as GITHUB_ISSUE_TOOL_DEFINITION
from loregarden.mcp.initiative_membership_tools import (
    TOOL_DEFINITIONS as INITIATIVE_MEMBERSHIP_TOOL_DEFINITIONS,
)
from loregarden.mcp.initiative_plan_tools import (
    TOOL_DEFINITIONS as INITIATIVE_PLAN_TOOL_DEFINITIONS,
)
from loregarden.mcp.local_instance_tools import TOOL_DEFINITIONS as LOCAL_INSTANCE_TOOL_DEFINITIONS
from loregarden.mcp.memory_tool_args import normalize_memory_tool_args
from loregarden.mcp.memory_tools import execute_memory_tool
from loregarden.mcp.merge_queue_tools import TOOL_DEFINITIONS as MERGE_QUEUE_TOOL_DEFINITIONS
from loregarden.mcp.operation_tools import (
    OPERATION_TOOLS,
    execute_operation_tool,
    operation_tool_schema,
)
from loregarden.mcp.operator_question_tools import OPERATOR_QUESTION_TOOL_DEFINITIONS
from loregarden.mcp.organization_tool import TOOL_DEFINITION as ORGANIZATION_TOOL_DEFINITION
from loregarden.mcp.own_spec_edits import keeps_own_spec_edits
from loregarden.mcp.reference_tool import TOOL_DEFINITION as REFERENCE_TOOL_DEFINITION
from loregarden.mcp.research_tools import RESEARCH_TOOL_DEFINITIONS
from loregarden.mcp.stage_ask_tool import STAGE_ASK_TOOL_DEFINITIONS
from loregarden.mcp.survey_tools import TOOL_DEFINITIONS as SURVEY_TOOL_DEFINITIONS
from loregarden.mcp.ticket_edit_tools import normalize_update_ticket_args
from loregarden.mcp.ticket_ops_tools import (
    TICKET_OPS_TOOL_DEFINITIONS,
    execute_ticket_ops_tool,
    normalize_ticket_ops_args,
)
from loregarden.mcp.tool_args import TABLE_NORMALIZERS as _TABLE_NORMALIZERS
from loregarden.mcp.tool_args import coerce_findings
from loregarden.mcp.tool_args import coerce_mapping as _coerce_mapping
from loregarden.mcp.tool_args import coerce_optional_bool as _coerce_optional_bool
from loregarden.mcp.tool_args import coerce_optional_int as _coerce_optional_int
from loregarden.mcp.tool_args import coerce_optional_string as _coerce_optional_string
from loregarden.mcp.tool_args import coerce_string as _coerce_string
from loregarden.mcp.tool_args import coerce_string_list as _coerce_string_list
from loregarden.mcp.tool_args import reject_truncated_call as _reject_truncated_call
from loregarden.mcp.tool_ids import McpTool
from loregarden.mcp.tool_registry import CHAT_WAIT_TOOL_DEFINITIONS, EXTENDED_TOOLS
from loregarden.mcp.tool_schemas import boolean_prop as _boolean_prop
from loregarden.mcp.tool_schemas import enum_string_prop as _enum_string_prop
from loregarden.mcp.tool_schemas import integer_prop as _integer_prop
from loregarden.mcp.tool_schemas import string_prop as _string_prop
from loregarden.mcp.tool_schemas import tool_schema as _tool_schema
from loregarden.mcp.ui_action_tools import TOOL_DEFINITIONS as UI_ACTION_TOOL_DEFINITIONS
from loregarden.mcp.ux_answers_tools import UX_ANSWERS_TOOL_DEFINITIONS
from loregarden.services.evidence import EVIDENCE_KINDS
from loregarden.services.operations import Caller, Transport
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.run_resolution import (
    TICKET_RUN_ID_DESCRIPTION,
    attributed_agent_run_id,
    resolve_run_ref,
)

_ALIAS_MAP: dict[str, tuple[str, ...]] = {
    "ticket_id": ("ticketId", "id"),
    "workspace_slug": ("workspaceSlug", "workspace"),
    "external_id": ("externalId", "slug"),
    "run_id": ("runId",),
    "stage_key": ("stageKey", "stage"),
    "agent_id": ("agentId",),
    "skill_name": ("skillName",),
    "content_json": ("contentJson", "content"),
    "next_agent": ("nextAgent",),
    "next_stage_key": ("nextStageKey", "route_to_stage"),
    "blocking_issues": ("blockingIssues",),
    "outcome": ("routeOutcome",),
}


def _declared_properties(name: str) -> frozenset[str]:
    """Argument names `name`'s own schema declares."""
    for tool in TOOL_DEFINITIONS:
        if tool["name"] == name:
            return frozenset(tool.get("inputSchema", {}).get("properties", {}))
    return frozenset()


def _apply_aliases(name: str, args: dict[str, Any]) -> None:
    """Rewrite bridge aliases to canonical names, in place.

    The alias map is global but the tools do not share a vocabulary: one tool's alias is
    another's real argument. `content` is an alias for `content_json` on attach_artifact and
    is also append_learning's own required field, so aliasing it blindly popped `content`
    away and left append_learning reporting it missing on every correct call. Never rewrite
    an argument the target tool declares itself.
    """
    declared = _declared_properties(name)
    for canonical, aliases in _ALIAS_MAP.items():
        if canonical in args:
            continue
        for alias in aliases:
            if alias in args and alias not in declared:
                args[canonical] = args.pop(alias)
                break


_STAGE_SCOPED_TOOLS = frozenset(
    {
        "loregarden_start_stage",
        "loregarden_complete_stage",
        "loregarden_skip_stage",
        "loregarden_request_approval",
    }
)


def _normalize_get_ticket(args: dict[str, Any]) -> dict[str, Any]:
    """Either identifier will do, but one of them must be present."""
    payload: dict[str, Any] = {}
    if args.get("ticket_id") is not None:
        payload["ticket_id"] = _coerce_string(args.get("ticket_id"), field="ticket_id")
    if args.get("external_id") is not None:
        payload["external_id"] = _coerce_string(args.get("external_id"), field="external_id")
    if args.get("workspace_slug") is not None:
        payload["workspace_slug"] = _coerce_string(
            args.get("workspace_slug"), field="workspace_slug"
        )
    if not payload.get("ticket_id") and not payload.get("external_id"):
        raise ValueError("ticket_id or external_id is required")
    return payload


def _normalize_stage_scoped(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Coerce the run+stage tools, which share a run_id/stage_key core."""
    payload = {
        "run_id": _coerce_string(args.get("run_id"), field="run_id"),
        "stage_key": _coerce_string(args.get("stage_key"), field="stage_key"),
    }
    if name == "loregarden_start_stage":
        payload["agent_id"] = _coerce_optional_string(args.get("agent_id"))
        payload["force"] = _coerce_optional_bool(args.get("force"))
        payload["ignore_prerequisites"] = _coerce_optional_bool(args.get("ignore_prerequisites"))
    if name == "loregarden_complete_stage":
        payload["next_agent"] = _coerce_optional_string(args.get("next_agent"))
        payload["next_stage_key"] = _coerce_optional_string(args.get("next_stage_key"))
        payload["outcome"] = _coerce_optional_string(args.get("outcome")) or "pass"
        payload["blocking_issues"] = _coerce_optional_string(args.get("blocking_issues"))
        payload["findings"] = coerce_findings(args.get("findings"))
    if name == "loregarden_skip_stage":
        payload["reason"] = _coerce_optional_string(args.get("reason"))
    if name == "loregarden_request_approval":
        payload["title"] = _coerce_optional_string(args.get("title"))
        payload["impact"] = _coerce_optional_string(args.get("impact"))
    return payload


def _normalize_attach_evidence(args: dict[str, Any]) -> dict[str, Any]:
    content_json = args.get("content_json")
    if content_json is not None and not isinstance(content_json, str):
        content_json = json.dumps(content_json)
    return {
        "run_id": _coerce_string(args.get("run_id"), field="run_id"),
        "evidence_kind": _coerce_string(args.get("evidence_kind"), field="evidence_kind"),
        "title": _coerce_string(args.get("title"), field="title"),
        "content_json": _coerce_optional_string(content_json),
    }


def _normalize_ticket_ops_args(name: str, args: dict[str, Any]) -> dict[str, Any] | None:
    """`normalize_ticket_ops_args` with this module's coercers already bound."""
    return normalize_ticket_ops_args(
        name,
        args,
        coerce_string=_coerce_string,
        coerce_optional_string=_coerce_optional_string,
        coerce_string_list=_coerce_string_list,
    )


def _normalize_start_orchestration(args: dict[str, Any]) -> dict[str, Any]:
    """Only ticket_id is required; the rest narrow how (and by whom) it runs."""
    payload = {"ticket_id": _coerce_string(args.get("ticket_id"), field="ticket_id")}
    for field in ("driver", "external_harness", "idempotency_key"):
        if args.get(field) is not None:
            payload[field] = _coerce_string(args.get(field), field=field)
    max_stages = _coerce_optional_int(args.get("max_stages"))
    if max_stages is not None:
        payload["max_stages"] = max_stages
    return payload


def normalize_tool_arguments(name: str, arguments: Any) -> dict[str, Any]:
    """Coerce Claude MCP bridge quirks (aliases, stringified JSON, camelCase)."""
    args = _coerce_mapping(arguments)
    _apply_aliases(name, args)

    table = _TABLE_NORMALIZERS.get(name)
    if table is not None:
        return table(args)

    if name == "loregarden_get_ticket":
        return _normalize_get_ticket(args)

    if name == "loregarden_list_tickets":
        # Optional: listing initiatives is global; ticket_search owns the error.
        payload = {"workspace_slug": _coerce_optional_string(args.get("workspace_slug")) or ""}
        for field in (
            "state",
            "work_item_type",
            "search",
            "parent_ticket_id",
            "parent_external_id",
        ):
            if args.get(field) is not None:
                payload[field] = _coerce_string(args.get(field), field=field)
        if args.get("roots_only") is not None:
            payload["roots_only"] = _coerce_optional_bool(args.get("roots_only"))
        if args.get("limit") is not None:
            payload["limit"] = _coerce_optional_int(args.get("limit")) or 50
        return payload

    if name == "loregarden_get_ticket_by_external":
        return {
            "workspace_slug": _coerce_string(args.get("workspace_slug"), field="workspace_slug"),
            "external_id": _coerce_string(args.get("external_id"), field="external_id"),
        }

    if name == "loregarden_start_orchestration":
        return _normalize_start_orchestration(args)

    external_harness_args = normalize_external_harness_args(
        name,
        args,
        coerce_string=_coerce_string,
        coerce_optional_string=_coerce_optional_string,
    )
    if external_harness_args is not None:
        return external_harness_args

    if name in _STAGE_SCOPED_TOOLS:
        return _normalize_stage_scoped(name, args)

    if name == "loregarden_block_ticket":
        return normalize_block_ticket(
            args,
            coerce_string=_coerce_string,
            coerce_optional_string=_coerce_optional_string,
            coerce_string_list=_coerce_string_list,
        )

    if name == "loregarden_attach_evidence":
        return _normalize_attach_evidence(args)

    if name == "loregarden_attach_artifact":
        content_json = args.get("content_json")
        if isinstance(content_json, dict):
            content_json = json.dumps(content_json)
        elif content_json is not None and not isinstance(content_json, str):
            content_json = json.dumps(content_json)
        return {
            "run_id": _coerce_string(args.get("run_id"), field="run_id"),
            "kind": _coerce_string(args.get("kind"), field="kind"),
            "title": _coerce_string(args.get("title"), field="title"),
            "content_json": _coerce_optional_string(content_json),
        }

    if name == "loregarden_complete_orchestration":
        payload = {"run_id": _coerce_string(args.get("run_id"), field="run_id")}
        if args.get("status") is not None:
            payload["status"] = _coerce_string(args.get("status"), field="status")
        payload["message"] = _coerce_optional_string(args.get("message"))
        return payload

    if name == "loregarden_update_ticket":
        return normalize_update_ticket_args(
            args,
            coerce_string=_coerce_string,
            coerce_string_list=_coerce_string_list,
            coerce_int=_coerce_optional_int,
            coerce_bool=_coerce_optional_bool,
        )

    if name == "loregarden_create_ticket":
        # Deliberately loose here: title/workspace_slug are passed through
        # (stripped, not rejected) so TicketService.create_ticket owns every
        # validation message — duplicating "Title is required" here with
        # different casing would silently diverge from the service's own text.
        payload = {
            "workspace_slug": _coerce_optional_string(args.get("workspace_slug")),
            "title": _coerce_optional_string(args.get("title")),
            "work_item_type": _coerce_optional_string(args.get("work_item_type")) or "task",
            "description": _reject_truncated_call(
                _coerce_optional_string(args.get("description")), field="description"
            ),
            "acceptance_criteria": _coerce_string_list(
                args.get("acceptance_criteria") or [], field="acceptance_criteria"
            ),
            "priority": _coerce_optional_int(args.get("priority"), field="priority"),
            "external_id": _coerce_optional_string(args.get("external_id")),
            "parent": _coerce_optional_string(args.get("parent")),
            "depends_on": _coerce_string_list(args.get("depends_on") or [], field="depends_on"),
        }
        if payload["priority"] is None:
            payload["priority"] = 3
        return payload

    if name == "loregarden_write_handoff":
        checklist = args.get("checklist")
        if isinstance(checklist, str):
            stripped = checklist.strip()
            if stripped:
                try:
                    checklist = json.loads(stripped)
                except json.JSONDecodeError:
                    pass  # leave as string; the service reports the parse error
        return {
            "ticket_id": _coerce_string(args.get("ticket_id"), field="ticket_id"),
            "workspace_slug": _coerce_string(args.get("workspace_slug"), field="workspace_slug"),
            "from_agent": _coerce_string(
                args.get("from_agent")
                if args.get("from_agent") is not None
                else args.get("fromAgent"),
                field="from_agent",
            ),
            "to_agent": _coerce_string(
                args.get("to_agent") if args.get("to_agent") is not None else args.get("toAgent"),
                field="to_agent",
            ),
            "checklist": checklist,
        }

    # Whatever the branches above did not claim goes to the modules that own
    # their own tools. Each returns None for a name it does not own, and neither
    # ever returns an empty payload — every branch it takes builds a dict with at
    # least the tool's required field — so `or` reads the same as an
    # `is not None` chain here, and `args` remains the fallthrough it always was.
    return normalize_memory_tool_args(name, args) or _normalize_ticket_ops_args(name, args) or args


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": McpTool.GET_TICKET,
        "description": (
            "Read what a ticket asks for — title, description, acceptance_criteria — "
            "along with its workflow state, stage map, hierarchy neighbors, and active "
            "orchestration run. Read this before inferring a requirement from the code."
        ),
    },
    {
        "name": McpTool.LIST_TICKETS,
        "description": (
            "Search and list tickets in a workspace (flat results for discovery). "
            "With work_item_type=initiative, lists initiatives across all workspaces."
        ),
    },
    {
        "name": McpTool.GET_TICKET_BY_EXTERNAL,
        "description": (
            "Read a ticket's requirement and workflow state by workspace slug and "
            "external_id. Same payload as loregarden_get_ticket."
        ),
    },
    {
        "name": McpTool.START_ORCHESTRATION,
        "description": "Start a top-level orchestration run for a ticket.",
    },
    {
        "name": McpTool.START_STAGE,
        "description": "Mark a workflow stage as running before invoking a sub-agent.",
    },
    {
        "name": McpTool.COMPLETE_STAGE,
        "description": (
            "Mark a stage complete and advance the workflow cursor. "
            "Use outcome=reject with next_stage_key to route back to an upstream agent."
        ),
    },
    {
        "name": McpTool.SKIP_STAGE,
        "description": (
            "Mark a stage this ticket does not need as won't do, so the workflow never runs "
            "it. Only stages the workflow template marks optional can be skipped, and only "
            "before they run — the error names the ones this workflow offers."
        ),
    },
    {
        "name": McpTool.BLOCK_TICKET,
        "description": (
            "Block the ticket and fail the orchestration run. If a person has to finish "
            "this, do not stop at describing it: work down the tier ladder first and say "
            "what you got to. Try it yourself (agent_attempted); if you cannot run it, "
            "commit a script that can and name it (one_click); only claim `manual` for the "
            "part that genuinely needs a person present. A block that hands over a "
            "multi-step procedure with nothing prepared is reported back to you as such."
        ),
    },
    {
        "name": McpTool.ATTACH_EVIDENCE,
        "description": (
            "Attach proof that the work behaves as claimed. The commit it proves is "
            "stamped server-side, so evidence captured before your last edit is "
            "distinguishable from proof of the current code."
        ),
        "inputSchema": _tool_schema(
            properties={
                "run_id": _string_prop(TICKET_RUN_ID_DESCRIPTION),
                "evidence_kind": _enum_string_prop(
                    "What this proves: a red-to-green test, output captured from the "
                    "real surface a user touches, a verifier's verdict, or the full "
                    "regression suite passing green at this commit.",
                    list(EVIDENCE_KINDS),
                ),
                "title": _string_prop("Short description of what was captured."),
                "content_json": _string_prop(
                    "Captured output as a JSON string. To hand a human reviewer what to "
                    "look at, add a `review` object: `steps` [{action, expect, wrong_if, judges, "
                    "why_person}] (only judgments no test or capture can make; a step that "
                    "states a fact is refused) or `nothing_to_judge` (what proved it "
                    "instead), `scene` (the scene file to run), "
                    "`screenshots` [{path, caption, scene}] "
                    "(PNG/JPEG/GIF/WebP files on this machine, copied in at attach "
                    "time), `links` [{path or url, label}] to open, `requests` "
                    "[{name, method, path, body, expect_status}] the reviewer can send "
                    "from the review page against a local instance, and "
                    "`replaces_earlier` to hide older kits. Use real_surface."
                ),
            },
            required=["run_id", "evidence_kind", "title"],
        ),
    },
    {
        "name": McpTool.ATTACH_ARTIFACT,
        "description": "Attach an artifact (log, diff, test output) to a ticket.",
    },
    {
        "name": McpTool.CLAIM_PATHS,
        "description": (
            "Declare files you are about to change, before you change them, when the plan "
            "did not name them: a signature change, a moved module, a new shared helper. "
            "Open tickets in the same landing tree see the claim in their next stage prompt, "
            "so they can keep clear. The reply names any sibling already claiming those "
            "files. Your own ticket only; at most 25 paths (claim a directory with a "
            "trailing /)."
        ),
    },
    {
        "name": McpTool.REQUEST_APPROVAL,
        "description": (
            "Open a human approval inbox item for a stage. Only for an agentless human gate "
            "(no agent — a person is the stage), or a sign-off after this stage's own agent "
            "run succeeded. Never a substitute for running a stage (start_stage / "
            "begin_external_stage) or waiving one (skip_stage with a reason): a stage with "
            "agents and no succeeded run is refused. Not a way to authorize an action: "
            "for a person's go-ahead on a force-push, a publish or a pull request, block "
            "with blocked_kind 'decision' and the exact command as an option."
        ),
    },
    {
        "name": McpTool.COMPLETE_ORCHESTRATION,
        "description": "Finish the top-level orchestration run.",
    },
    {
        "name": McpTool.UPDATE_TICKET,
        "description": (
            "Update ticket state or content (state, title, description, priority, "
            "acceptance criteria, tags, parent). Supply at least one field besides ticket_id. "
            "Acceptance criteria belong here — never append them to the description. "
            "Naming a state pins it; pass auto_state to hand it back to the workflow."
        ),
    },
    {
        "name": McpTool.LINK_DEPENDENCY,
        "description": (
            "Link one ticket to wait for another: ticket_id depends on (runs after) "
            "depends_on. Best-effort ordering within a parent's subtree — it does not "
            "hard-block a standalone run. Idempotent; rejects self-links, ancestor "
            "links (a parent is done only when its children are), and cycles. "
            "Both ids accept a UUID or external id."
        ),
    },
    {
        "name": McpTool.UNLINK_DEPENDENCY,
        "description": (
            "Remove a dependency edge: ticket_id no longer waits for depends_on. "
            "No-op if the edge does not exist. Both ids accept a UUID or external_id."
        ),
    },
    {
        "name": McpTool.LINK_RELATION,
        "description": (
            "Relate two tickets for context: symmetric and non-blocking, so neither "
            "waits for the other and subtree run order is unchanged. Use link_dependency "
            "instead when one must run after the other. Idempotent in both directions; "
            "rejects self-links. Both ids accept a UUID or external id."
        ),
    },
    {
        "name": McpTool.UNLINK_RELATION,
        "description": (
            "Remove a relation between two tickets. No-op if they are not related. "
            "Both ids accept a UUID or external id."
        ),
    },
    {
        "name": McpTool.CREATE_TICKET,
        "description": (
            "Create a new ticket. Mirrors the TicketCreate schema — validation "
            "(hierarchy rules: initiatives are parentless and own milestones) is owned "
            "by TicketService.create_ticket, not reimplemented here. Returns the "
            "created ticket's id, external_id, and title. A pipeline agent may file "
            "up to three per run; each is related to the ticket the run is working on."
        ),
    },
    {
        "name": McpTool.MEMORY_STATUS,
        "description": (
            "Report configured Obsidian/iCloud memory backends and workspace-scoped resolved paths."
        ),
    },
    {
        "name": McpTool.APPEND_LEARNING,
        "description": "Persist ticket learnings to Obsidian notes and/or the memory graph SQLite.",
        "inputSchema": _tool_schema(
            properties={
                "ticket_id": _string_prop("Ticket external id or UUID."),
                "workspace_slug": _string_prop("Workspace slug."),
                "content": _string_prop("Learning body (markdown)."),
                "title": _string_prop(
                    "What the learning says, as a canonical name (e.g. 'Use DELETE journal "
                    "on iCloud SQLite'). Omitted: taken from the content's first line."
                ),
                "aliases": _string_prop("Other names for it: comma-separated or JSON array."),
                "tags": _string_prop("Optional comma-separated tags or JSON array."),
                "anchors": _string_prop(
                    "Optional symbol anchors: qualified names from this checkout's symbol "
                    "map (e.g. pkg.module.function), comma-separated or a JSON array. Each "
                    "is resolved against this run's own checkout BEFORE the write and "
                    "stamped with the fingerprint the code has right now, so a later reader "
                    "is told whether the claim still describes it. A name the map does not "
                    "hold refuses the WHOLE write rather than recording it ungrounded."
                ),
            },
            required=["ticket_id", "workspace_slug", "content"],
        ),
    },
    {
        "name": McpTool.UPSERT_MEMORY,
        "description": (
            "Upsert a durable memory node under the workspace-scoped Obsidian dir and graph SQLite."
        ),
        "inputSchema": _tool_schema(
            properties={
                "node_id": _string_prop("Optional stable node id for updates."),
                "title": _string_prop("Memory title."),
                "body": _string_prop("Memory body (markdown)."),
                "tags": _string_prop("Optional comma-separated tags or JSON array."),
                "ticket_id": _string_prop("Optional related ticket id."),
                "workspace_slug": _string_prop(
                    "Workspace slug (required — scopes note and graph DB)."
                ),
                "discredited": _boolean_prop(
                    "Omit it. A change to the flag needs a reason recorded in the node's "
                    "history, which this tool does not carry, so a value that would change "
                    "it is refused, naming where an operator makes the change."
                ),
                "anchors": _string_prop(
                    "Optional symbol anchors: qualified names from this checkout's symbol "
                    "map (e.g. pkg.module.function), comma-separated or a JSON array. Each "
                    "is resolved against this run's own checkout BEFORE the write and "
                    "stamped with the fingerprint the code has right now, so a later reader "
                    "is told whether the claim still describes it. A name the map does not "
                    "hold refuses the WHOLE write rather than recording it ungrounded."
                ),
            },
            required=["title", "workspace_slug"],
        ),
    },
    {
        "name": McpTool.UPSERT_BLOG_POST,
        "description": (
            "Persist a human-readable blog post markdown note under the workspace-scoped BlogPosts Obsidian dir."
        ),
        "inputSchema": _tool_schema(
            properties={
                "ticket_id": _string_prop("Ticket external id or UUID."),
                "workspace_slug": _string_prop("Workspace slug (required — scopes blog post dir)."),
                "title": _string_prop("Blog post title."),
                "body": _string_prop("Blog post body (markdown)."),
                "note_id": _string_prop("Optional stable note id for updates."),
                "tags": _string_prop("Optional comma-separated tags or JSON array."),
            },
            required=["ticket_id", "workspace_slug", "title", "body"],
        ),
    },
    {
        "name": McpTool.APPEND_CHECKPOINT,
        "description": (
            "Append a checkpoint entry for a ticket+run to the workspace-scoped Checkpoints "
            "Obsidian dir — same vault as memory/learnings, not the workspace repo. Only for "
            "an assumption that needs nobody's answer: a question you would have asked the "
            "operator goes to loregarden_ask_operator, which the inbox and the gate read. "
            "Multiple entries accumulate in one ticket+run file."
        ),
        "inputSchema": _tool_schema(
            properties={
                "ticket_id": _string_prop("Ticket external id or UUID."),
                "workspace_slug": _string_prop(
                    "Workspace slug (required — scopes checkpoint dir)."
                ),
                "run_id": _string_prop("Run id for this ticket+run's checkpoint log."),
                "entry": _string_prop(
                    "Checkpoint entry markdown block "
                    "(### [TICKET_ID] Stage — label / Assumption made / Confidence)."
                ),
                "blocker": {
                    "type": "boolean",
                    "description": (
                        "Set true when this entry records something you could NOT resolve "
                        "and cannot proceed past. The ticket is blocked and the entry is "
                        "surfaced to the operator. Leave false (the default) for an "
                        "assumption you made and moved on from — that is what a checkpoint "
                        "normally is. Recording a blocker is not the same as documenting "
                        "one you worked around."
                    ),
                },
            },
            required=["ticket_id", "workspace_slug", "run_id", "entry"],
        ),
    },
    {
        "name": McpTool.WRITE_HANDOFF,
        "description": (
            "Record a ticket's handoff from a STRUCTURED checklist, then validate it against "
            "the workspace's own handoff gate and return any violations. The handoff is stored "
            "as an artifact row; the YAML the gate reads is exported to a gitignored scratch "
            "tree, not written into the repository's tracked checkpoints. Use this instead of "
            "hand-writing the YAML: it renders canonical schema and auto-computes the "
            "required/met counters. On validation FAIL nothing is stored — the write is rolled "
            "back and the violations returned, so you can fix and retry in the same turn. "
            "Where the workspace has no handoff gate, or its gate cannot run, the handoff is "
            "stored with status `stored_unvalidated` and a reason: nothing checked it, which "
            "is not the same as it passing, and an error artifact says so on the ticket. Use "
            "the exact item_key/item labels from the frozen catalog for the "
            "(from_agent → to_agent) pair (see mandatory_workflow_gates_v1.md)."
        ),
        "inputSchema": _tool_schema(
            properties={
                "ticket_id": _string_prop("Ticket external id slug or UUID."),
                "workspace_slug": _string_prop("Workspace slug (scopes the repo + gate)."),
                "from_agent": _string_prop("Finishing (upstream) agent, e.g. test_designer."),
                "to_agent": _string_prop("Next (downstream) agent, e.g. test_breaker."),
                "checklist": {
                    "type": "array",
                    "description": (
                        "Checklist items for the pair. Counters are computed for you; do not send "
                        "required_items_met/total_required_items."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "item_key": _string_prop(
                                "Frozen catalog key, e.g. test_suite_complete."
                            ),
                            "item": _string_prop(
                                "Catalog label text (must match the key's catalog text)."
                            ),
                            "status": _enum_string_prop(
                                "Item status.",
                                ["complete", "incomplete", "deferred", "blocked"],
                            ),
                            "evidence": _string_prop("Evidence (path or attestation text)."),
                            "evidence_type": _string_prop(
                                "Optional 'path' or 'attestation'; defaults to the catalog's type."
                            ),
                            "certainty": _enum_string_prop(
                                "How much this claim is worth, and the only field that "
                                "counts an item as met. 'verified' means an evidence "
                                "artifact on this ticket backs it, and requires "
                                "evidence_artifact_id. 'user_confirmed' means a human "
                                "approved it. 'inferred' means you believe it and have no "
                                "artifact — the default, and honest. Prose in `evidence` "
                                "is not proof; if you ran something, attach it with "
                                "loregarden_attach_evidence and claim verified.",
                                ["verified", "user_confirmed", "inferred"],
                            ),
                            "evidence_artifact_id": _string_prop(
                                "Id of an evidence artifact on this ticket, from "
                                "loregarden_attach_evidence. Required when "
                                "certainty=verified. Evidence captured before your last "
                                "edit reads as stale and stops counting."
                            ),
                            "required": {
                                "type": "boolean",
                                "description": "Optional; defaults true. Match the catalog default.",
                            },
                        },
                        "required": ["item_key", "item", "status"],
                        "additionalProperties": False,
                    },
                },
            },
            required=["ticket_id", "workspace_slug", "from_agent", "to_agent", "checklist"],
        ),
    },
    {
        "name": McpTool.SEARCH_PRIOR_WORK,
        "description": (
            "Find finished tickets like this one and what they hit on the way. Use "
            "before starting work to avoid repeating an approach that already failed."
        ),
        "inputSchema": _tool_schema(
            properties={
                "query": _string_prop("What you are about to work on."),
                "workspace_slug": _string_prop("Workspace slug to search within."),
                "ticket_id": _string_prop("Optional current ticket, excluded from results."),
            },
            required=["query"],
        ),
    },
    {
        "name": McpTool.SEARCH_MEMORY,
        "description": "Search Obsidian notes and memory graph nodes, optionally scoped to a workspace.",
        "inputSchema": _tool_schema(
            properties={
                "query": _string_prop("Search text."),
                "workspace_slug": _string_prop("Optional workspace slug to scope results."),
                "limit": _integer_prop("Max results per backend (default 20)."),
            },
            required=["query"],
        ),
    },
    {
        "name": McpTool.CREATE_MEMORY_RELATION,
        "description": "Link two memory graph nodes in the workspace-scoped SQLite memory store.",
    },
]

# Tools that live in their own module rather than in this file's chain.
TOOL_DEFINITIONS.append(ORGANIZATION_TOOL_DEFINITION)
TOOL_DEFINITIONS.append(DOCTOR_TOOL_DEFINITION)
TOOL_DEFINITIONS.append(DISK_USAGE_TOOL_DEFINITION)
TOOL_DEFINITIONS.append(BRANCH_MOVES_TOOL_DEFINITION)
TOOL_DEFINITIONS.append(DEFER_TICKET_TOOL_DEFINITION)
TOOL_DEFINITIONS.append(GITHUB_ISSUE_TOOL_DEFINITION)
TOOL_DEFINITIONS.append(REFERENCE_TOOL_DEFINITION)
TOOL_DEFINITIONS.append(DEVDOCS_TOOL_DEFINITION)
TOOL_DEFINITIONS.extend(TICKET_OPS_TOOL_DEFINITIONS)
TOOL_DEFINITIONS.extend(UX_ANSWERS_TOOL_DEFINITIONS)
TOOL_DEFINITIONS.extend(RESEARCH_TOOL_DEFINITIONS)
TOOL_DEFINITIONS.extend(FIX_COVERAGE_TOOL_DEFINITIONS)
TOOL_DEFINITIONS.extend(OPERATOR_QUESTION_TOOL_DEFINITIONS)
TOOL_DEFINITIONS.extend(ASSERTED_NOT_PROVEN_TOOL_DEFINITIONS)
TOOL_DEFINITIONS.extend(STAGE_ASK_TOOL_DEFINITIONS)
TOOL_DEFINITIONS.extend(DISCOVERY_TOOL_DEFINITIONS)
TOOL_DEFINITIONS.extend(CRITERION_TOOL_DEFINITIONS)
TOOL_DEFINITIONS.extend(MERGE_QUEUE_TOOL_DEFINITIONS)
TOOL_DEFINITIONS.extend(EXTERNAL_HARNESS_TOOL_DEFINITIONS)
TOOL_DEFINITIONS.extend(DOCKER_CAPACITY_TOOL_DEFINITIONS)
TOOL_DEFINITIONS.extend(UI_ACTION_TOOL_DEFINITIONS)
TOOL_DEFINITIONS.extend(LOCAL_INSTANCE_TOOL_DEFINITIONS)
TOOL_DEFINITIONS.extend(INITIATIVE_PLAN_TOOL_DEFINITIONS)
TOOL_DEFINITIONS.extend(INITIATIVE_MEMBERSHIP_TOOL_DEFINITIONS)
TOOL_DEFINITIONS.extend(FEATURE_LOOP_TOOL_DEFINITIONS + CHAT_WAIT_TOOL_DEFINITIONS)
TOOL_DEFINITIONS.extend(DOCUMENT_TOOL_DEFINITIONS)
TOOL_DEFINITIONS.extend(SURVEY_TOOL_DEFINITIONS)

# An operation's tool declares no schema of its own: it is generated from the
# operation's args model, which validates the call (`mcp/operation_schemas.py`).
for _tool in TOOL_DEFINITIONS:
    if _tool["name"] in OPERATION_TOOLS:
        if "inputSchema" in _tool:
            # Written beside the model, it would be overwritten unread and drift unseen.
            raise RuntimeError(f"{_tool['name']} is an operation's tool; drop its inputSchema")
        _tool["inputSchema"] = operation_tool_schema(_tool["name"])


def _decide_finding(
    session: Session, _svc, arguments: dict[str, Any], *, orchestrated: bool, run_id: str
) -> str:
    return decide_finding_tool(session, arguments, orchestrated=orchestrated, run_id=run_id)


def _settle_criterion(
    session: Session, _svc, arguments: dict[str, Any], *, orchestrated: bool, run_id: str
) -> str:
    return settle_criterion_tool(session, arguments, orchestrated=orchestrated, run_id=run_id)


#: Tools whose answer depends on which run is calling: whether the caller is the
#: stage that may decide a finding or settle a criterion.
_RUN_AWARE_HANDLERS = {
    McpTool.DECIDE_FINDING.value: _decide_finding,
    McpTool.SETTLE_CRITERION.value: _settle_criterion,
}


@keeps_own_spec_edits
def execute_tool(
    session: Session,
    name: str,
    arguments: dict[str, Any] | Any,
    *,
    orchestrated: bool = False,
    run_id: str = "",
    transport: Transport = Transport.MCP,
) -> str:
    """Dispatch a tool call. `run_id` names the orchestrated run, when known.

    `transport` is MCP unless the CLI is running the tool in-process; an
    operation offered on one and not the other refuses accordingly.

    `orchestrated=True` marks a call made by an agent CLI subprocess Loregarden itself
    supervises (any run built via `agents.cli_adapters.resolve_cli_invocation` — builtin
    autopilot or a manually-started single stage alike; see `ORCHESTRATED_DENIED_MCP_TOOLS`).
    It is threaded down from the MCP transport: the HTTP endpoint sets it from the
    `X-Loregarden-Orchestrated` header, which only Loregarden's own CLI invocation
    builders attach to a run's `--mcp-config`/stdio env. A caller that never sets that
    header — a human's own terminal `claude` session, Ticket Studio chat, a direct
    operator `curl` against `/mcp`, or an `external_mcp`-driven orchestrator calling
    tools/call directly — is NOT covered by this check; `orchestrated` defaults to False
    for exactly that reason. That gap is recorded debt (a9-create-ticket-mcp-tool),
    pending a2-per-agent-server-policy's real per-agent x per-server policy table.
    """
    reject_orchestrated_tool_denial(name, orchestrated=orchestrated)

    svc = OrchestrationCallbackService(session)
    arguments = normalize_tool_arguments(name, arguments)
    reject_orchestrated_argument_denial(name, arguments, orchestrated=orchestrated)

    table_handler = EXTENDED_TOOLS.get(name)
    if table_handler is not None:
        return table_handler(session, arguments)

    caller = Caller(transport=transport, orchestrated=orchestrated, run_id=run_id)
    operation_reply = execute_operation_tool(session, name, arguments, caller)
    if operation_reply is not None:
        return operation_reply

    # The module that owns its own ticket tools: None for a name it does not own.
    delegated = execute_ticket_ops_tool(name, session, svc, arguments)
    if delegated is not None:
        return delegated

    run_aware = _RUN_AWARE_HANDLERS.get(name)
    if run_aware is not None:
        return run_aware(session, svc, arguments, orchestrated=orchestrated, run_id=run_id)

    if name == "loregarden_write_handoff":
        from loregarden.services.handoff_writer import write_handoff

        result = write_handoff(
            session,
            ticket_id=arguments["ticket_id"],
            workspace_slug=arguments["workspace_slug"],
            from_agent=arguments["from_agent"],
            to_agent=arguments["to_agent"],
            checklist=arguments["checklist"],
        )
        return json.dumps(result, indent=2)

    memory_result = execute_memory_tool(
        session, name, arguments, orchestrated=orchestrated, run_id=run_id
    )
    if memory_result is not None:
        return memory_result

    named_run = arguments.get("run_id")
    if not named_run:
        raise ValueError("run_id is required")

    run_ref = resolve_run_ref(session, named_run)
    ticket = svc.resolve_ticket(ticket_id=run_ref.ticket_id)
    source_run_id = attributed_agent_run_id(session, run_ref, transport_run_id=run_id)

    # Ticket-scoped: a standalone stage run, with no orchestration, may use it.
    if name == McpTool.ATTACH_EVIDENCE:
        return attach_evidence(session, svc, ticket, arguments, run_id=source_run_id)

    raise ValueError(f"Unknown tool: {name}")


def tool_names() -> list[str]:
    return [t["name"] for t in TOOL_DEFINITIONS]
