"""The parts of a chat prompt that every operator rail shares.

Home chat and ticket triage are the same agent (``triage``, "Baxter") answering
in two places, but each rail used to carry its own copy of the identity and
posture prose. Two copies drift, and only one of them was ever edited.

What lives here is the shared frame: the agent's configured role, the
interactive-vs-advisory posture, and the chat UI wire protocol. What stays in
each service is that rail's own data — the ticket, the snapshot, the history.

Deliberately *not* configurable: the posture blocks and the UI primitives
contract. Posture is derived per turn from adapter capabilities, so static text
cannot express it; the primitives block is a wire protocol the frontend parses,
and a wire protocol behind an editable textarea is a card that silently stops
rendering. The agent's identity is the part an operator should be able to edit,
and that is exactly what ``role_body`` supplies.
"""

from __future__ import annotations

import logging

from loregarden.agents.prompt_blocks import AGENT_ROLE_HEADING, ROLE_BODY_CAP, titled_block
from loregarden.models.domain.enums import ChatSurface

logger = logging.getLogger(__name__)

# Posted by the chat UI Run button on an agent execution plan. Must stay in sync
# with ``agentPlanExecuteMessage`` in the client TodoListPrimitive. It lives here
# rather than in a chat service because the primitives block below interpolates
# it, and both chat services read that block.
AGENT_PLAN_EXECUTE_PREFIX = "Execute this agent execution plan now."


def chat_role_blocks(agent: dict, *, surface: ChatSurface) -> list[str]:
    """The agent's configured role body, under the same heading a stage uses.

    An empty role body renders nothing — correct, but never silently: a chat
    turn running without the identity an operator configured is the failure
    that motivated this module, so it leaves a trace.
    """
    role_body = (agent.get("role_body") or "").strip()
    if not role_body:
        logger.warning(
            "chat prompt built with an empty role body (agent=%r surface=%s); "
            "the rail is running without its configured identity",
            agent.get("slug") or agent.get("id") or "?",
            surface.value,
        )
        return []
    return titled_block(AGENT_ROLE_HEADING, role_body, cap=ROLE_BODY_CAP)


def chat_advisory_blocks(advisory_reason: str = "") -> list[str]:
    """What to tell a rail that has no tools this turn.

    A one-shot advisory turn ends with its reply: there is no later message in
    which announced work happens. An answer opening "I'll check X, then I'll do
    Y" therefore reads to the operator as a promise that was silently dropped —
    which is what made this rail feel broken rather than merely limited.
    """
    sections = [
        "You are advisory only in this channel — you have no tools. Do not claim to have "
        "executed tools or changed the repo.",
        "Do not announce work you are about to do — no 'I'll check…', 'I'll inspect…', "
        "'let me look at…'. This reply is the whole turn; nothing runs after it. "
        "Answer from what you already have.",
        "When the operator asks for an action you cannot take, say so in one line and "
        "name who or what can take it — do not narrate an attempt.",
        # The workspace checkout is not the control plane. An agent that goes
        # looking for Loregarden's records on this filesystem finds nothing and
        # invents a path that sounds right, which is what happened here.
        "Loregarden's tickets, runs, and approvals live in the control plane's own "
        "database, not in this workspace's files. Never grep, `rg`, `find`, or guess "
        "at file paths to locate them — no such files exist here.",
        "If you need a lookup, call the Loregarden MCP tools, which stay available on "
        "this read-only turn. If one is genuinely unavailable to you, say so plainly "
        "rather than inventing a way to do it.",
    ]
    if advisory_reason:
        # Naming the cause turns "I can't do that" into something the operator
        # can act on, and stops the model inventing a reason of its own. The
        # same sentence is on the snapshot behind the mode indicator, so asking
        # Baxter and reading the badge give one answer, not two.
        sections.extend(
            [
                f"Why this channel is advisory: {advisory_reason}",
                "If the operator asks why you cannot act, or how to let you, answer with "
                "exactly that reason and that remedy. Do not speculate past it, and do not "
                "offer a workaround it does not name.",
            ]
        )
    return sections


def chat_posture_blocks(
    *,
    surface: ChatSurface,
    interactive: bool,
    approval_bridge: bool = False,
    advisory_reason: str = "",
) -> list[str]:
    """Whether this turn can act, and what that means for how it answers.

    Derived per turn from adapter capabilities — the same inputs on either rail
    produce the same text, which is the point: a posture that differs between
    Home and triage is drift, not design.
    """
    if not interactive:
        return chat_advisory_blocks(advisory_reason)

    sections = [
        "You have real tool access in this workspace — file read/write, git, shell, and "
        "the Loregarden MCP tools where the runtime supports them.",
        "Investigate before answering: read code, run tests, reproduce failures.",
        "When you find an actionable fix, make it directly rather than only describing it.",
    ]
    if surface is ChatSurface.HOME:
        sections.extend(
            [
                # Home is where an operator asks for branch work directly, so
                # git is named in scope rather than left to inference. Scoped to
                # this rail: the ticket rail's git happens through its stages.
                "Git is in scope when the operator asks — status, diff, add, commit, and push "
                "of the current branch. Destructive git (force-push, reset --hard, deleting a "
                "branch) still goes through the approval inbox.",
                "This channel is not scoped to a work item, so no ticket is implied — name the "
                "ticket explicitly on any MCP call that needs one.",
            ]
        )
    if approval_bridge:
        sections.append(
            "Destructive or high-risk actions route through Loregarden's approval prompt "
            "automatically — request them when needed rather than avoiding the work."
        )
    else:
        sections.append(
            "This turn runs on the operator's selected CLI (not a Claude-only bridge). "
            "Workspace writes are enabled; stay inside the repo and prefer reversible changes."
        )
    return sections


def chat_ui_primitives_blocks() -> list[str]:
    """The `loregarden` fenced-JSON card contract the chat frontend parses.

    Kept in code on purpose — see the module docstring. It interpolates
    ``AGENT_PLAN_EXECUTE_PREFIX``, which the client also hardcodes, so the two
    ends of the protocol move together or not at all.
    """
    return [
        "",
        "## Chat UI primitives",
        "When a live card helps more than prose, emit a fenced `loregarden` JSON",
        "block with a `primitive` field. Prefer thin refs (ticket_id, agent_id).",
        "Kinds: thinking, ticket, ticket_workflow, parent_ticket, ticket_list,",
        "status_column, kanban, filterable_kanban, agent, workflow, gate,",
        "terminal, edit, calendar, calendar_event, workspace, todo_list,",
        "branch_history, commit, qa, giphy.",
        "Rules:",
        "- Never invent ticket/agent ids. Only reference ids from the live",
        "  snapshot or resolved references above, or ids returned by MCP after",
        "  you create/look them up.",
        "- `ticket` / `ticket_list` / `kanban` cards are for existing tickets only.",
        '- Agent execution plan (`todo_list`, owner "agent"): only when you are',
        "  about to do multi-step work in this workspace and the operator has",
        "  not started it yet. Never for a question, an explanation, a status",
        "  answer, a single step, or work already finished — answer those in",
        "  prose. Do not fake a ticket card for unfiled work either.",
        "- One plan per thread. Give it a stable `plan_id` and reuse that exact",
        "  id every time you re-emit it; the UI replaces the old card in place,",
        "  so a new id (or a missing one) leaves a duplicate stale card behind.",
        '  Example: ```loregarden\\n{"primitive":"todo_list","owner":"agent",'
        '"plan_id":"history-api","title":"Agent execution plan",'
        '"items":[{"id":"api","text":"Add history API","checked":false}]}\\n```',
        "- The UI shows Run on that card. When the operator sends",
        f'  "{AGENT_PLAN_EXECUTE_PREFIX}…", do the unchecked steps',
        "  with tools on whatever CLI they selected — do not only restate",
        "  the plan, and do not claim you need Claude.",
        "  Re-emit the plan (same `plan_id`) only when step state actually",
        "  changed. When the final item completes, emit the matching plan",
        "  once with every item checked, then report the outcome in prose.",
        "  Do not end an execution turn while actionable items remain.",
        "  Stop only after all items are checked, or after emitting a `qa`",
        "  card for a concrete blocker, required approval, or operator input.",
        "- To ask the operator before proceeding, emit `qa`.",
        "- After creating a ticket via MCP, emit `ticket` with the real returned id.",
    ]
