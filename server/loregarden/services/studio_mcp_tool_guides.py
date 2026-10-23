"""The per-tool guide text rendered into every MCP-enabled agent's prompt.

Catalog data, read by `studio_agent_prompt.build_studio_prompt_sections` (which
renders it) and by `StudioService.mcp_tool_guides` (which serves it to the agent
editor). It lives in its own module so neither of those has to import the other.
"""

from __future__ import annotations

from loregarden.models.domain import StudioMcpToolGuide

MCP_TOOL_GUIDES: list[StudioMcpToolGuide] = [
    StudioMcpToolGuide(
        name="loregarden_get_ticket",
        description="Read ticket workflow state, stage map, hierarchy neighbors, and active orchestration run.",
        when_to_use="At stage start and before any workflow decision — never trust stale project_board WORKFLOW STATE alone.",
        example='tools/call loregarden_get_ticket {"ticket_id": "<uuid or external id>", "workspace_slug": "loregarden"}',
        stage_agent=True,
    ),
    StudioMcpToolGuide(
        name="loregarden_list_tickets",
        description="Search and list tickets in a workspace for discovery.",
        when_to_use="When you need sibling tasks, child work items, or to find a ticket by title/slug.",
        example='tools/call loregarden_list_tickets {"workspace_slug": "loregarden", "search": "cli runner"}',
        stage_agent=True,
    ),
    StudioMcpToolGuide(
        name="loregarden_get_ticket_by_external",
        description="Read ticket state by workspace slug and external_id.",
        when_to_use="When you know the ticket id (e.g. lor-mcp-gateway-142) but not the UUID.",
        example='tools/call loregarden_get_ticket_by_external {"workspace_slug": "loregarden", "external_id": "lor-mcp-gateway-142"}',
        stage_agent=True,
    ),
    StudioMcpToolGuide(
        name="loregarden_start_orchestration",
        description="Start a top-level orchestration run for a ticket.",
        when_to_use="Autopilot / external orchestrator only — not typical stage agents.",
        example='tools/call loregarden_start_orchestration {"ticket_id": "<uuid>", "driver": "external_mcp"}',
        orchestrator_only=True,
        stage_agent=False,
    ),
    StudioMcpToolGuide(
        name="loregarden_start_stage",
        description="Mark a workflow stage as running before invoking a sub-agent.",
        when_to_use="Orchestrator only — before delegating to a sub-agent.",
        example='tools/call loregarden_start_stage {"run_id": "<orchestration_run_id>", "stage_key": "implement", "agent_id": "backend_implementer"}',
        orchestrator_only=True,
        stage_agent=False,
    ),
    StudioMcpToolGuide(
        name="loregarden_complete_stage",
        description="Mark a stage done and advance the workflow cursor.",
        when_to_use="Orchestrator after a sub-agent succeeds and gates pass. Stage runs from the IDE usually auto-complete.",
        example='tools/call loregarden_complete_stage {"run_id": "<orchestration_run_id>", "stage_key": "testing"}',
        orchestrator_only=True,
        stage_agent=False,
    ),
    StudioMcpToolGuide(
        name="loregarden_skip_stage",
        description="Mark an optional stage as won't do.",
        when_to_use=(
            "When a stage the template marks optional does not apply to this ticket — a "
            "frontend review on a backend-only change. Required stages are refused."
        ),
        example='tools/call loregarden_skip_stage {"run_id": "<orchestration_run_id>", "stage_key": "frontend_review", "reason": "No client/ files touched"}',
        orchestrator_only=True,
        stage_agent=False,
    ),
    StudioMcpToolGuide(
        name="loregarden_block_ticket",
        description="Block the ticket and fail the orchestration run.",
        when_to_use="Unrecoverable failure — document message clearly for operators.",
        example='tools/call loregarden_block_ticket {"run_id": "<orchestration_run_id>", "message": "Tests failed after 3 attempts"}',
        orchestrator_only=True,
        stage_agent=False,
    ),
    StudioMcpToolGuide(
        name="loregarden_attach_evidence",
        description="Attach proof that the work behaves as claimed, stamped with the commit it proves.",
        when_to_use=(
            "When you can show the change working, not just that tests pass: a red-to-green "
            "test, output from the real surface (HTTP response, screenshot, DB row), or a "
            "verifier's verdict."
        ),
        example=(
            'tools/call loregarden_attach_evidence {"run_id": "<agent_run>", '
            '"evidence_kind": "real_surface", "title": "POST /api/tickets returns 201", '
            '"content_json": "{"status": 201}"}'
        ),
        stage_agent=True,
    ),
    StudioMcpToolGuide(
        name="loregarden_attach_artifact",
        description="Attach log, diff, test output, or other artifact to the ticket.",
        when_to_use="After producing logs, diffs, or structured output the operator should see in the IDE.",
        example='tools/call loregarden_attach_artifact {"run_id": "<agent_run>", "kind": "log", "title": "Test summary", "content_json": "{\\"lines\\":[]}"}',
        stage_agent=True,
    ),
    StudioMcpToolGuide(
        name="loregarden_request_approval",
        description="Raise the workflow gate for a stage's outstanding exit actions.",
        when_to_use=(
            "A stage whose exit actions still need a person: an agentless gate stage, or an "
            "agent stage whose run left actions only a person can resolve. Refused when "
            "nothing is outstanding; a sign-off is an operator_judgment exit action authored "
            "on the stage. Not to run a stage (start_stage / begin_external_stage) or waive "
            "one (skip_stage)."
        ),
        example='tools/call loregarden_request_approval {"run_id": "<agent_run>", "stage_key": "playtest"}',
        stage_agent=True,
    ),
    StudioMcpToolGuide(
        name="loregarden_complete_orchestration",
        description="Finish an orchestration run.",
        when_to_use="Orchestrator when all stages are done or the run should terminate.",
        example='tools/call loregarden_complete_orchestration {"run_id": "<orchestration_run_id>", "status": "succeeded"}',
        orchestrator_only=True,
        stage_agent=False,
    ),
    StudioMcpToolGuide(
        name="loregarden_memory_status",
        description="Discover workspace-scoped Obsidian dirs (memory, learnings, blog posts) and SQLite graph path.",
        when_to_use="Before writing or searching agent memory artifacts — always pass workspace_slug from the run prompt.",
        example='tools/call loregarden_memory_status {"workspace_slug": "loregarden"}',
        stage_agent=True,
    ),
    StudioMcpToolGuide(
        name="loregarden_append_learning",
        description="Persist ticket learnings to obsidian_learnings_dir and optional graph SQLite.",
        when_to_use="Learning Agent after Gatekeeper — ticket-scoped insights.",
        example='tools/call loregarden_append_learning {"ticket_id": "03-wire-cli", "workspace_slug": "loregarden", "content": "…"}',
        stage_agent=True,
    ),
    StudioMcpToolGuide(
        name="loregarden_upsert_memory",
        description="Upsert durable memory nodes under obsidian_memory_dir and graph SQLite.",
        when_to_use="Patterns, anti-patterns, and reusable knowledge — never write vault files directly.",
        example='tools/call loregarden_upsert_memory {"title": "MCP workflow state", "body": "…", "workspace_slug": "loregarden"}',
        stage_agent=True,
    ),
    StudioMcpToolGuide(
        name="loregarden_upsert_blog_post",
        description="Persist human-readable blog post markdown under obsidian_blogposts_dir.",
        when_to_use="Blog Post Agent after learning — retrospective for operators.",
        example='tools/call loregarden_upsert_blog_post {"ticket_id": "03-wire-cli", "workspace_slug": "loregarden", "title": "…", "body": "…"}',
        stage_agent=True,
    ),
    StudioMcpToolGuide(
        name="loregarden_create_memory_relation",
        description="Link two memory graph nodes in the workspace SQLite DB (memory_relations table).",
        when_to_use="Learning Agent — use graph.id values from upsert/append responses as source_id and target_id.",
        example='tools/call loregarden_create_memory_relation {"source_id": "<uuid>", "target_id": "<uuid>", "workspace_slug": "loregarden", "relation_type": "supports"}',
        stage_agent=True,
    ),
    StudioMcpToolGuide(
        name="loregarden_search_memory",
        description="Search memory, learnings, and blog post notes plus SQLite graph nodes in a workspace.",
        when_to_use="Before acting when prior workspace context may exist (planner, spec, implementers).",
        example='tools/call loregarden_search_memory {"query": "permission bridge", "workspace_slug": "loregarden"}',
        stage_agent=True,
    ),
    StudioMcpToolGuide(
        name="loregarden_search_reference",
        description="Search DevDocs for a documentation page and get back ranked entries with their URLs.",
        when_to_use="Before fetching framework docs — search for the exact page instead of guessing a URL, then pass the returned url to loregarden_fetch_reference. Omit docset to get suggestions.",
        example='tools/call loregarden_search_reference {"query": "useEffect", "docset": "react"}',
        stage_agent=True,
    ),
    StudioMcpToolGuide(
        name="loregarden_fetch_reference",
        description="Fetch a documentation page through loregarden's cache, extracted to markdown.",
        when_to_use="Instead of WebFetch for framework or library docs — the raw HTML is fetched and extracted once per URL, and repeat reads are served from the cache.",
        example='tools/call loregarden_fetch_reference {"url": "https://docs.pydantic.dev/latest/concepts/models/"}',
        stage_agent=True,
    ),
]
