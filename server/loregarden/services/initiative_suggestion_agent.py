"""Ask an agent to regroup open work into initiatives.

The keyword grouping in `initiative_suggestions` is instant and crude; this is
the same candidates, read by one agent turn that can see what titles do not
say. The agent only proposes: its reply is validated against the pool — an id
it invented, a milestone it put in two themes, a sprint item that is not a
candidate — and everything it proposed that could not be used is returned as
a warning, never silently dropped. Nothing is written until the operator
applies a suggestion through the same path the heuristic's take.

It runs under the initiative planner's agent definition (model, runtime,
timeout) with a prompt of its own and no tools: everything it needs is in the
prompt, and it has nothing to write.
"""

from __future__ import annotations

from loregarden.models.domain import (
    InitiativeSuggestion,
    InitiativeSuggestionSet,
    SuggestedItem,
    SuggestionKind,
    SuggestionSource,
    Workspace,
)
from loregarden.services.cli_agent_runner import (
    CliAgentProfile,
    run_cli_agent_turn,
    stub_response,
)
from loregarden.services.initiative_planner_service import INITIATIVE_PLANNER_AGENT_ID
from loregarden.services.initiative_suggestions import (
    SuggestionPool,
    assemble,
    build_pool,
    plan_sprint,
    sprint_suggestion,
    theme_suggestions,
)
from loregarden.services.ticket_studio_service import extract_json_block
from loregarden.services.workspace_paths import resolve_workspace_root
from pydantic import BaseModel, ValidationError
from sqlmodel import Session, col, select

GROUPER_CLI_PROFILE = CliAgentProfile(
    agent_id=INITIATIVE_PLANNER_AGENT_ID,
    assistant_label="Initiative grouper",
    cli_label="Initiative grouper",
    stub_env="LOREGARDEN_INITIATIVE_GROUPER_STUB_RESPONSE",
    timeout_env="LOREGARDEN_INITIATIVE_GROUPER_TIMEOUT",
    tmp_prefix="loregarden-initiative-grouper-",
    reply_cap=40_000,
)

#: Sprint candidates listed in the prompt, best first; the rest are summarised.
MAX_PROMPT_SPRINT_ITEMS = 150
_DESCRIPTION_CHARS = 160


class GrouperReplyError(RuntimeError):
    """The agent answered, but not with suggestions this page can use."""


class GrouperRunError(RuntimeError):
    """The agent turn itself failed: the CLI exited, timed out, or could not sign in."""


class _AgentSuggestion(BaseModel):
    kind: SuggestionKind
    title: str
    description: str = ""
    rationale: str = ""
    item_ids: list[str]


class _AgentReply(BaseModel):
    suggestions: list[_AgentSuggestion]


def _line(item: SuggestedItem, extra: str = "") -> str:
    parts = [item.id, item.external_id, item.workspace_slug, item.state.value, f"cost {item.cost}"]
    if item.from_milestone:
        parts.append(f"in {item.from_milestone}")
    return " | ".join(parts) + f" | {item.title}" + (f" — {extra}" if extra else "")


def build_grouper_prompt(pool: SuggestionPool, draft: list[InitiativeSuggestion]) -> str:
    budgets = ", ".join(f"{slug}: {n}" for slug, n in sorted(pool.budgets.items())) or "(none)"
    milestones = [
        _line(m, " ".join(pool.milestone_descriptions.get(m.id, "").split())[:_DESCRIPTION_CHARS])
        for m in pool.milestones
    ]
    sprint = [_line(i) for i in pool.sprint_pool[:MAX_PROMPT_SPRINT_ITEMS]]
    omitted = len(pool.sprint_pool) - len(sprint)
    draft_lines = [
        f"- {s.kind.value}: {s.title} — {', '.join(i.external_id for i in s.items)}" for s in draft
    ]
    return "\n".join(
        [
            "You group a software team's open work into initiatives for them to review.",
            "",
            "## Rules",
            "- A `theme` initiative groups open milestones that serve one goal. Name the goal",
            "  in a short title an operator would recognise; say in `rationale` why they belong",
            "  together. A theme needs at least two milestones. Milestones may come from",
            "  different workspaces. Each milestone goes in at most one theme. Leave a",
            "  milestone out rather than forcing it into a weak theme.",
            "- At most one `sprint` initiative: open features and bugs to finish in the next",
            f"  {pool.days} days. Keep work already in progress. Respect each workspace's",
            "  budget, counted in `cost` (work items): " + budgets + ".",
            "- If no milestones share a real goal, propose only the sprint.",
            "- Use only ids listed below, exactly as written.",
            "",
            "## Open milestones without an initiative",
            "id | external id | workspace | state | cost | title — description",
            *(milestones or ["(none)"]),
            "",
            "## Sprint candidates (open features and bugs, best first)",
            "id | external id | workspace | state | cost | current milestone | title",
            *(sprint or ["(none)"]),
            *([f"({omitted} lower-ranked candidates omitted)"] if omitted else []),
            "",
            "## A keyword draft to improve on",
            *(draft_lines or ["(nothing)"]),
            "",
            "## Reply",
            "Reply with one JSON object in a ```json fence and nothing else:",
            '{"suggestions": [{"kind": "theme" | "sprint", "title": "...", "description": "...",'
            ' "rationale": "...", "item_ids": ["<id>", ...]}]}',
        ]
    )


def _runnable_workspace(session: Session, slugs: list[str]) -> Workspace:
    """The CLI needs a directory to run in; any checked-out candidate workspace will do."""
    workspaces = session.exec(select(Workspace).where(col(Workspace.slug).in_(slugs))).all()
    for workspace in sorted(workspaces, key=lambda w: slugs.index(w.slug)):
        if resolve_workspace_root(workspace).is_dir():
            return workspace
    raise ValueError(
        "None of the workspaces with open work is checked out on this machine "
        f"({', '.join(slugs) or 'none'}); the agent needs one to run in."
    )


def _resolve(
    raw: list[str], index: dict[str, SuggestedItem]
) -> tuple[list[SuggestedItem], list[str]]:
    items: list[SuggestedItem] = []
    unknown: list[str] = []
    for ref in raw:
        item = index.get(ref.strip())
        if item is None:
            unknown.append(ref)
        elif item not in items:
            items.append(item)
    return items, unknown


def validate_reply(
    pool: SuggestionPool, reply: str
) -> tuple[list[InitiativeSuggestion], list[str]]:
    payload = extract_json_block(reply)
    if payload is None:
        raise GrouperReplyError("The agent did not reply with a JSON object of suggestions.")
    try:
        parsed = _AgentReply.model_validate(payload)
    except ValidationError as exc:
        raise GrouperReplyError(f"The agent's suggestions did not match the format: {exc}") from exc

    by_kind = {
        SuggestionKind.THEME: pool.milestones,
        SuggestionKind.SPRINT: pool.sprint_pool,
    }
    indexes = {
        kind: {key: i for i in items for key in (i.id, i.external_id)}
        for kind, items in by_kind.items()
    }
    warnings: list[str] = []
    claimed: set[str] = set()
    suggestions: list[InitiativeSuggestion] = []
    sprint_seen = False
    for n, proposed in enumerate(parsed.suggestions):
        label = proposed.title.strip() or f"suggestion {n + 1}"
        if proposed.kind == SuggestionKind.SPRINT and sprint_seen:
            warnings.append(f"Dropped “{label}”: only one sprint is suggested at a time.")
            continue
        items, unknown = _resolve(proposed.item_ids, indexes[proposed.kind])
        if unknown:
            warnings.append(
                f"“{label}” named {len(unknown)} id(s) that are not open {proposed.kind.value} "
                f"candidates, left out: {', '.join(unknown[:5])}"
            )
        repeated = [i for i in items if i.id in claimed]
        if repeated:
            warnings.append(
                f"“{label}” repeated {', '.join(i.external_id for i in repeated)} from an "
                "earlier suggestion; kept in the first."
            )
        items = [i for i in items if i.id not in claimed]
        if not items or not proposed.title.strip():
            warnings.append(f"Dropped “{label}”: nothing usable left in it.")
            continue
        claimed.update(i.id for i in items)
        if proposed.kind == SuggestionKind.SPRINT:
            sprint_seen = True
            base = sprint_suggestion(pool, items, rationale=proposed.rationale.strip())
            suggestions.append(base.model_copy(update={"title": proposed.title.strip()}))
            continue
        suggestions.append(
            InitiativeSuggestion(
                key=f"agent:{n}",
                kind=SuggestionKind.THEME,
                title=proposed.title.strip(),
                description=proposed.description.strip(),
                rationale=proposed.rationale.strip(),
                items=items,
                empties=[],
                target_date=None,
            )
        )
    return suggestions, warnings


def regroup_with_agent(session: Session, *, days: int) -> InitiativeSuggestionSet:
    """One synchronous agent turn over the pool. Raises on any failure to answer."""
    pool = build_pool(session, days=days)
    reply = stub_response(GROUPER_CLI_PROFILE)
    if reply is None:
        draft = theme_suggestions(pool.milestones, ignore=pool.workspace_words)
        sprint_items = plan_sprint(pool)
        if sprint_items:
            draft.append(sprint_suggestion(pool, sprint_items))
        slugs = list(dict.fromkeys(i.workspace_slug for i in [*pool.milestones, *pool.sprint_pool]))
        workspace = _runnable_workspace(session, [s for s in slugs if s])
        try:
            reply = run_cli_agent_turn(
                GROUPER_CLI_PROFILE,
                workspace=workspace,
                prompt=build_grouper_prompt(pool, draft),
                workspace_slug=workspace.slug or "",
                granted_tools=[],
                read_only=True,
            )
        except (RuntimeError, TimeoutError) as exc:
            # The CLI's own words say what broke; which runtime it ran under is
            # what the operator needs to go and fix it.
            runtime = workspace.cli_adapter or "its default"
            raise GrouperRunError(
                f"The agent ran under {workspace.slug}'s runtime ({runtime}) and failed: {exc} "
                f"— check {workspace.slug}'s agent runtime settings, then ask again."
            ) from exc
    suggestions, warnings = validate_reply(pool, reply)
    return assemble(pool, suggestions, source=SuggestionSource.AGENT, warnings=warnings)
