"""Map Loregarden workflow stage keys to agent-facing run context."""

from __future__ import annotations

from loregarden.agents.registry import REPAIR_AGENT_ID
from loregarden.models.domain import AgentRun, StageReportStatus, Ticket, WorkflowStageDef
from loregarden.services.compatibility_posture import ResolvedPosture
from loregarden.services.gate_prep import gate_prep_target
from loregarden.services.person_judgment import PersonJudgment
from loregarden.services.recheck_round import recheck_context_lines
from loregarden.services.repair_brief import gate_fix_brief_for, repair_brief_for
from loregarden.services.rework_feedback import render_rework_feedback
from loregarden.services.stage_verdict import (
    StageVerdict,
    collect_signals,
    derive_verdict,
    handoff_expected_for,
    unresolved_section_lines,
)
from loregarden.services.studio_routing import is_prunable_stage
from loregarden.services.ticket_deferral import deferral_context_lines
from loregarden.services.unresolved_ledger import LedgerActor, visible_unresolved
from sqlmodel import Session


def build_orchestration_context(
    *,
    ticket: Ticket,
    run: AgentRun,
    stage_def: WorkflowStageDef | None,
    stages: list[WorkflowStageDef] | None = None,
    posture: ResolvedPosture | None = None,
    session: Session | None = None,
    assigned_exit_actions: list[dict] | list[object] | None = None,
) -> str:
    stage_key = run.stage_key or ticket.workflow_stage_key
    display_name = stage_def.name if stage_def else stage_key
    skill = run.skill_name or (stage_def.skill_name if stage_def else "")

    lines = [
        "## Loregarden run context (authoritative for this run)",
        "This stage was started by the Loregarden control plane. The values below are the truth",
        "for this run — they override any other stage or agent you infer from elsewhere.",
        "",
        f"- Loregarden stage key: `{stage_key}`",
        f"- Display name: {display_name}",
        f"- Assigned agent: {run.agent_id}",
        f"- Skill: {skill or '—'}",
        "",
        "This ticket has no markdown file. Ticket data — description, acceptance criteria, stage",
        "cursor — lives in Loregarden's database and is reachable only via the MCP tools. Do not",
        "search the repo for a ticket file, and do not write ticket content to one; `project_board/`",
        "holds checkpoint and handoff artifacts only. Complete this stage, then record changes",
        "through MCP.",
    ]

    # Without the real key list, `reroute_to_stage` is a guess — and a plausible
    # invented key (e.g. "implementation" where this workflow says "implement")
    # gets dropped, sending rework to the wrong stage.
    upstream = [stage.key for stage in sorted(stages or [], key=lambda s: s.order)]
    if stage_key in upstream:
        upstream = upstream[: upstream.index(stage_key)]
    if upstream:
        lines += [
            "",
            "### Valid `reroute_to_stage` values for this workflow",
            "If your stage report rejects this work, `reroute_to_stage` MUST be one of these exact",
            "keys (upstream of your own stage) — anything else is discarded and the rework is routed",
            "to the immediately preceding stage instead. Use `null` if none applies.",
            "",
            ", ".join(f"`{key}`" for key in upstream),
        ]

    lines += _reroute_agent_lines(stages or [], stage_key)

    # An affordance nothing mentions is one no agent uses: skip_stage exists,
    # but without the key list a caller has to guess which stages it may prune,
    # and every guess at a required stage is refused.
    ordered = sorted(stages or [], key=lambda s: s.order)
    keys = [stage.key for stage in ordered]
    downstream = ordered[keys.index(stage_key) + 1 :] if stage_key in keys else []
    prunable = [stage for stage in downstream if is_prunable_stage(stage)]
    if prunable:
        lines += [
            "",
            "### Stages you may declare won't-do",
            "These later stages are optional for this workflow. If this ticket plainly does not "
            "need one — a frontend review on a change that touches no client code — call "
            "`loregarden_skip_stage` with the stage key and a reason, and it will not run. Skip "
            "only what the work does not need; if you are unsure, leave it and let it run. Every "
            "other stage is required and the call will be refused.",
            "",
            ", ".join(f"`{stage.key}` ({stage.name})" for stage in prunable),
        ]

    brief = (stage_def.stage_brief if stage_def else "").strip()
    if brief:
        lines += [
            "",
            "### What this workflow wants from this stage",
            "Set by the workflow template, on top of your role. It is here because the work is "
            "this stage's to do — not a later reviewer's, and not a human's at a sign-off gate.",
            "",
            brief,
        ]

    if assigned_exit_actions:
        lines += _assigned_exit_action_lines(assigned_exit_actions)

    gate = gate_prep_target(stages, stage_key)
    if gate is not None:
        lines += [
            "",
            f"### You are the last stage before the `{gate.key}` human gate",
            f"When this work passes review it parks at '{gate.name}', where a person runs it by "
            "hand. That gate is a sign-off, not a build step — nothing gets authored there. Any "
            "scene, level, harness, fixture, or entry point needed to exercise this change must "
            "exist and be committed when you finish, and your stage report must name the files "
            "the operator should open and run.",
            *_review_kit_lines(),
        ]

    if posture is not None:
        lines += [
            "",
            "### Compatibility posture — how freely you may change existing code",
            f"**{posture.posture.value}** (source: {posture.source})",
            "",
            "This is the authoritative answer to 'am I allowed to break this?' for this work item.",
            "It overrides any general instinct — or any role/module text — telling you to preserve",
            "existing behaviour by default.",
            "",
            posture.contract,
        ]

    lines += turn_context_lines(session, ticket, run, stage_def)

    return "\n".join(lines)


def _review_kit_lines() -> list[str]:
    """How the gate's reviewer gets what this stage saw, not just a file list.

    The Review page shows a kit beside each gate: screenshots, links, and
    endpoint checks the reviewer can send. Nothing filled it until the stage
    that made the change was told the tool and the shape.
    """
    return [
        "",
        "Hand the reviewer a review kit, so they can check the change instead of hunting for it. "
        'Call `loregarden_attach_evidence` with `evidence_kind: "real_surface"` and a '
        "`content_json` carrying a `review` block. Always write `steps` or `nothing_to_judge` (this "
        "stage does not pass without one); include the other parts that apply:",
        "",
        "```json",
        '{"review": {',
        '  "scene": "scenes/levels/sandbox/latch_3d.tscn",',
        '  "steps": [{"action": "Walk to the enemy\'s left side and press C",',
        '             "expect": "You notice the rejection without reading any text",',
        '             "wrong_if": "It passes for a hit, or you missed it",',
        '             "judges": "clarity",',
        '             "why_person": "Tests prove the flash plays; only a player can say it reads"}],',
        '  "screenshots": [{"path": "/abs/path/shot.png", "caption": "What it shows"}],',
        '  "links": [{"path": "/the-page", "label": "Where to look"}],',
        '  "requests": [{"name": "List items", "method": "GET", "path": "/api/items",',
        '                "expect_status": 200}]',
        "}}",
        "```",
        "",
        "- **steps**: the judgments only a person can make, and nothing else (usually 1 to 3; 12 at "
        "most). Does it feel right, look right, sound right, make sense to someone new to it? "
        "`judges` is one of " + ", ".join(f"`{j.value}`" for j in PersonJudgment) + ", and "
        "`why_person` says why no test or capture can answer it; the reviewer sees both. A fact is "
        "your work, not a step: what latched, what a counter or label reads, how long or how much, "
        "what a key does, whether anything errored. Prove it with a test or a scripted run before "
        "you pass. A step stating a fact is refused.",
        "- A person must be able to answer it by looking, listening or playing, at normal speed, "
        "in one sitting: no counting frames or seconds, no comparing numbers, no second player, no "
        "audience, no log. `action` gets them there in plain words, naming the key, control and "
        "place; no ids, class names or constants. Each must be able to fail.",
        "- **nothing_to_judge**: instead of steps, when every claim this change makes is a fact: "
        "what you checked and how. Do not invent a step to fill the kit.",
        "- **scene**: the scene file the steps and screenshots happen in, when the workspace has "
        "scenes. It becomes the reviewer's launch button. Leave it out when the steps start from "
        "the build's own start screen.",
        "- **screenshots**: PNG/JPEG/GIF files you captured of the changed screen or scene "
        "(10 MB each, 12 at most), at the window size the reviewer's launch opens, never scaled. "
        "Show the change happening; a picture of the start view says nothing about it. They are "
        "copied in when you attach, so a temp path is fine. Set `scene` on one taken elsewhere.",
        "- **links**: pages where the change can be seen, as a `path` on the app (`/work-items`). "
        "The review page opens it on whichever instance of this branch is running, and can "
        "capture it; use `url` only for a page outside the app.",
        "- **requests**: endpoints this change added or altered, as a path (never a full URL); "
        "the reviewer sends each against their local server and sees whether the status matches.",
        "",
        'Attaching again after rework? Set `"replaces_earlier": true` so the reviewer does not '
        "get the old pictures and steps beside the new ones.",
        "",
        "A malformed kit is refused whole and the error names the field; fix it and attach again.",
    ]


def turn_context_lines(
    session: Session | None,
    ticket: Ticket,
    run: AgentRun,
    stage_def: WorkflowStageDef | None,
) -> list[str]:
    """The sections that change from one run of a stage to the next.

    A resumed rework round keeps the system prompt its session started under
    (changing it re-caches the session's whole history), so these travel in the
    resumed turn's message instead (`rework_resume.follow_up_prompt`).
    """
    stage_key = run.stage_key or ticket.workflow_stage_key
    return [
        *_why_youre_here_lines(session, ticket, run, stage_key),
        *_unresolved_context_lines(session, ticket, run, stage_def),
        *recheck_context_lines(session, ticket, run),
        *deferral_context_lines(session, ticket),
    ]


def _why_youre_here_lines(
    session: Session | None, ticket: Ticket, run: AgentRun, stage_key: str
) -> list[str]:
    """The reason this run exists: a repair brief, a gate-fix brief, prior feedback."""
    lines: list[str] = []
    # Prefer the full rework-feedback ledger over ticket.blocking_issues: the
    # latter is capped for the workflow pane (a 200-char pointer for anything
    # longer), so on a reroute it can carry no actionable detail at all. The
    # ledger holds every prior round's feedback in full — the union across
    # rounds, not just the latest framing. Fall back to blocking_issues when no
    # ledger exists (older tickets, or non-reroute blocks).
    feedback = render_rework_feedback(session, ticket, stage_key) if session else ""
    feedback = feedback or ticket.blocking_issues
    brief = _repair_brief(session, ticket, run, stage_key)
    if brief:
        lines += ["", "## Why you're here — your repair brief", brief]
    gate_brief = gate_fix_brief_for(session, ticket, run=run) if session else ""
    if gate_brief:
        lines += ["", "## Why you're here — the transition gate refused this stage", gate_brief]
    if feedback and feedback not in (brief, gate_brief):
        lines += [
            "",
            "## Why you're here — prior stage feedback",
            "This ticket was routed back to this stage. Address every point below before "
            "reporting `pass` — each is from a prior reviewer or verifier that rejected this "
            "work. Later rounds do not supersede earlier ones; all must be resolved:",
            "",
            feedback,
        ]
    return lines


def _unresolved_context_lines(
    session: Session | None,
    ticket: Ticket,
    run: AgentRun,
    stage_def: WorkflowStageDef | None,
) -> list[str]:
    """Visible unresolved names (persisted ∪ live), or empty when there are none."""
    if session is None or stage_def is None:
        return []
    live = derive_verdict(
        collect_signals(
            session,
            ticket,
            report=None,
            tool_outcome=None,
            stage_def=stage_def,
            handoff_expected=handoff_expected_for(stage_def),
            agent_id=run.agent_id,
        )
    ).unresolved
    remaining = visible_unresolved(
        session,
        ticket,
        live,
        current_stage_key=stage_def.key,
        actor=LedgerActor(agent_id=run.agent_id, stage_key=run.stage_key),
    )
    status = StageReportStatus.NEEDS_REWORK if remaining else StageReportStatus.PASS
    return unresolved_section_lines(StageVerdict(status=status, unresolved=remaining))


def _repair_brief(session: Session | None, ticket: Ticket, run: AgentRun, stage_key: str) -> str:
    """A repair turn's brief, read from where dispatch cannot have blanked it (929)."""
    if session is None or run.agent_id != REPAIR_AGENT_ID or run.created_at is None:
        return ""
    return repair_brief_for(session, ticket, stage_key=stage_key, before=run.created_at)


def _assigned_exit_action_lines(assigned_exit_actions: list[dict] | list[object]) -> list[str]:
    """Prompt lines naming the exit actions this run must execute."""
    labels: list[str] = []
    for action in assigned_exit_actions:
        if isinstance(action, dict):  # py-org: allow-isinstance
            label = str(action.get("action_label") or action.get("label") or "")
        else:
            label = str(action.action_label or "")
        if label:
            labels.append(label)
    if not labels:
        return []
    return [
        "",
        "### Exit actions assigned to this run",
        "These requirements are satisfied for your runtime. Execute each one before you "
        "finish. A passing stage report attests that the assigned actions completed; a "
        "failed or reworked report completes none of them.",
        "",
        *(f"- {label}" for label in labels),
    ]


def _reroute_agent_lines(stages: list[WorkflowStageDef], stage_key: str) -> list[str]:
    """The specialists a reject from ``stage_key`` may name in ``reroute_to_agent``.

    Only stages that choose between specialists: elsewhere the agent is fixed
    and naming one changes nothing, so listing it would only spend prompt.
    """
    reached = sorted(stages, key=lambda s: s.order)
    keys_reached = [stage.key for stage in reached]
    if stage_key in keys_reached:
        reached = reached[: keys_reached.index(stage_key) + 1]
    choosable = [
        (stage.key, sorted({route.agent_id for route in stage.classify_routes if route.agent_id}))
        for stage in reached
    ]
    choosable = [(key, agents) for key, agents in choosable if len(agents) > 1]
    if not choosable:
        return []
    return [
        "",
        "### Valid `reroute_to_agent` values for this workflow",
        "Name one only when rejecting and you know which specialist should redo the work:",
        "",
        *(
            f"- `{key}`: " + ", ".join(f"`{agent}`" for agent in agents)
            for key, agents in choosable
        ),
    ]
