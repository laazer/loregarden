"""MCP handlers for a harness running a ticket from outside this control plane.

The pair wraps ``start_stage``/``complete_stage`` rather than sitting beside
them: an outside harness has no agent run of its own here, so checking a stage
out has to *open* one — with the harness stamped on it — and handing it back has
to settle it. Calling the raw stage tools instead advances the workflow with no
run behind it, which is exactly the state the stale-stage reaper exists to clean
up after.

See ``services.external_harness`` for the run lifecycle and why these runs are
outside the queue.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from sqlmodel import Session

from loregarden.mcp.tool_ids import McpTool
from loregarden.mcp.tool_schemas import (
    boolean_prop,
    integer_prop,
    string_list_prop,
    string_prop,
    tool_schema,
)
from loregarden.models.domain import AgentRun, OrchestrationRun, RunUsage
from loregarden.services.external_harness import begin_external_stage, finish_external_stage

#: Fields of ``RunUsage`` a harness may report on finish_external_stage. Flat
#: on the tool rather than nested, matching the arguments already there.
_USAGE_KEYS: tuple[str, ...] = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "model",
    "effort",
)

EXTERNAL_HARNESS_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": McpTool.BEGIN_EXTERNAL_STAGE,
        "description": (
            "Check the ticket's next stage out to the external harness driving this "
            "orchestration run. Returns a `runs` list — one entry per agent the stage "
            "needs, so a parallel stage returns several — each with the prompt "
            "Loregarden's own agent would receive and the agent_run_id to hand back "
            "with loregarden_finish_external_stage. Every entry shares one repo_path. "
            "An empty `runs` list means the stage runs no agent — read `message` and stop."
        ),
        "inputSchema": tool_schema(
            properties={
                "run_id": string_prop(
                    "Orchestration run UUID from loregarden_start_orchestration "
                    "(started with external_harness set)."
                ),
                "stage_key": string_prop(
                    "Optional stage to run. Defaults to the ticket's current stage; pass "
                    "one only to re-run a specific stage."
                ),
            },
            required=["run_id"],
        ),
    },
    {
        "name": McpTool.FINISH_EXTERNAL_STAGE,
        "description": (
            "Settle one run checked out with loregarden_begin_external_stage and route the "
            "workflow from its stage report. Call it once per entry in that call's `runs` "
            "list; a parallel stage stays RUNNING until its last member is back "
            "(`stage_finalized`, `outstanding_members`). Returns the run's duration, where "
            "the workflow went next, and whether it is finished."
        ),
        "inputSchema": tool_schema(
            properties={
                "agent_run_id": string_prop(
                    "Agent run UUID returned by loregarden_begin_external_stage."
                ),
                "transcript": string_prop(
                    "Your stage output, containing the <<<LOREGARDEN_STAGE_REPORT>>> block "
                    "verbatim. Loregarden parses it to advance, reroute or block."
                ),
                "failed": boolean_prop(
                    "True if the stage could not run at all (crash, unusable environment). "
                    "A stage that ran and rejected the work is not a failure — say so in "
                    "the stage report instead."
                ),
                "input_tokens": integer_prop(
                    "Tokens this stage was billed at the full input rate, excluding cache "
                    "reads and cache writes. Omit it if you cannot read your own usage — "
                    "an omitted figure is recorded as unmeasured, while a zero is recorded "
                    "as a real zero and averaged into cost reports."
                ),
                "output_tokens": integer_prop(
                    "Tokens this stage generated, reasoning tokens included."
                ),
                "cache_read_tokens": integer_prop(
                    "Input tokens served from the prompt cache, if your harness reports them "
                    "separately."
                ),
                "cache_write_tokens": integer_prop(
                    "Input tokens written to the prompt cache, if your harness reports them "
                    "separately."
                ),
                "model": string_prop(
                    "The model that actually ran this stage. Without it the token counts "
                    "cannot be priced."
                ),
                "effort": string_prop(
                    "The reasoning effort actually applied (e.g. `high`), not the one your "
                    "config asks for."
                ),
                "changed_paths": string_list_prop(
                    "Repo-relative paths this stage left modified. Loregarden diffs the tree "
                    "itself for runs it supervises; for yours it has no before-and-after, so "
                    "this is the only way the run's work can be attributed to it."
                ),
            },
            required=["agent_run_id", "transcript"],
        ),
    },
]


def normalize_external_harness_args(
    name: str,
    args: dict[str, Any],
    *,
    coerce_string: Callable[..., str],
    coerce_optional_string: Callable[[Any], str],
) -> dict[str, Any] | None:
    """Coerce this module's arguments, or None for a tool it does not own."""
    if name == McpTool.BEGIN_EXTERNAL_STAGE.value:
        return {
            "run_id": coerce_string(args.get("run_id"), field="run_id"),
            "stage_key": coerce_optional_string(args.get("stage_key")) or None,
        }
    if name == McpTool.FINISH_EXTERNAL_STAGE.value:
        return {
            "agent_run_id": coerce_string(args.get("agent_run_id"), field="agent_run_id"),
            "transcript": coerce_string(args.get("transcript"), field="transcript"),
            "failed": bool(args.get("failed") or False),
            # Passed through untouched, one key per declared argument. An absent
            # figure has to stay absent: coercing a missing count to 0 here is
            # exactly the mistake the nullable columns exist to prevent, and
            # folding the six into one object would drop them from the
            # normalized payload the schema promises.
            **{key: args.get(key) for key in _USAGE_KEYS},
            "changed_paths": [
                coerce_string(path, field="changed_paths")
                for path in (args.get("changed_paths") or [])
            ],
        }
    return None


def begin_external_stage_tool(session: Session, arguments: dict[str, Any]) -> str:
    orch_run = session.get(OrchestrationRun, arguments["run_id"])
    if not orch_run:
        raise ValueError(f"Orchestration run not found: {arguments['run_id']}")
    view = begin_external_stage(session, orch_run, stage_key=arguments.get("stage_key"))
    return json.dumps(view.model_dump(mode="json"), indent=2)


def finish_external_stage_tool(session: Session, arguments: dict[str, Any]) -> str:
    run = session.get(AgentRun, arguments["agent_run_id"])
    if not run:
        raise ValueError(f"Agent run not found: {arguments['agent_run_id']}")
    reported = {key: arguments[key] for key in _USAGE_KEYS if arguments.get(key) is not None}
    view = finish_external_stage(
        session,
        run,
        transcript=arguments["transcript"],
        failed=arguments.get("failed", False),
        usage=RunUsage.model_validate(reported) if reported else None,
        changed_paths=arguments["changed_paths"],
    )
    return json.dumps(view.model_dump(mode="json"), indent=2)
