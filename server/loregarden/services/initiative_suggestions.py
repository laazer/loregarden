"""Suggest initiatives from open work, and create the ones the operator keeps.

Two kinds of suggestion, both drawn from work no initiative owns yet:

* **Themes** — open milestones whose titles share a distinctive word. Crude by
  design: it is instant and needs no model, and the agent regroup
  (`initiative_suggestion_agent`) is there when the words are not enough.
* **Sprint** — open features and bugs sized to what each workspace has
  actually been finishing (`initiative_forecast.measure_paces`), work already
  in progress first. It is offered whenever there is such work, and it is the
  only suggestion when nothing shares a theme.

Nothing is stored until `apply_suggestions`, which validates the whole batch
before writing any of it, so a stale page fails with nothing half-created.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from itertools import pairwise

from loregarden.models.domain import (
    CreatedInitiative,
    ForecastBasis,
    InitiativeDraft,
    InitiativePlanUpdate,
    InitiativeSuggestion,
    InitiativeSuggestionSet,
    ScheduleTargetInput,
    SprintSizing,
    SuggestedItem,
    SuggestionApply,
    SuggestionApplyResult,
    SuggestionKind,
    SuggestionSource,
    Ticket,
    TicketState,
    WorkItemType,
)
from loregarden.services.hierarchy_service import (
    descendants_by_root,
    reparent_ticket,
    validate_parent_child,
)
from loregarden.services.initiative_forecast import measure_paces
from loregarden.services.initiative_plan_service import update_plan
from loregarden.services.initiative_service import workspace_slugs
from loregarden.services.ticket_rollup import RESOLVED_STATES, reconcile_lineage
from loregarden.services.ticket_service import TicketService
from sqlmodel import Session, col, select

#: Who an initiative created from a suggestion is attributed to.
_OPERATOR = "human"

#: States a sprint cannot move without a person first.
_STALLED = frozenset({TicketState.BLOCKED, TicketState.PARKED})

#: Types that count as a work item in a sprint's budget (the forecast's unit).
_CONTAINERS = frozenset({WorkItemType.INITIATIVE, WorkItemType.MILESTONE})

#: Budget for a workspace whose pace cannot be measured: a handful a week, not
#: zero, so a new workspace still gets a sprint — and the sizing says it was assumed.
UNMEASURED_ITEMS_PER_WEEK = 3

#: A theme word shared by more than this share of milestones names nothing.
_MAX_THEME_SHARE = 0.4
_MAX_THEMES = 8

_WORD_RE = re.compile(r"[a-z][a-z0-9]+")
_MIN_WORD = 4
#: Words too common in milestone titles to name a goal.
_STOP_WORDS = frozenset(
    "about after again also before being between both class does done each every "
    "first from give have into just like make makes made more most need only onto "
    "over real same should some stop than that their them then they this through "
    "time under until what when where which while with without work follow".split()
)


class SuggestionConflictError(ValueError):
    """The work moved since the suggestions were drawn; refresh and choose again."""


@dataclass
class SuggestionPool:
    """Everything a suggestion may claim, and how big a sprint can be."""

    milestones: list[SuggestedItem]
    #: Sprint candidates, best first.
    sprint_pool: list[SuggestedItem]
    #: workspace slug -> work items a sprint may plan there.
    budgets: dict[str, int]
    sizing: SprintSizing
    days: int
    now: datetime
    milestone_descriptions: dict[str, str] = field(default_factory=dict)
    #: milestone external id -> ids of every open feature and bug under it, for
    #: milestones that also have resolved children. A sprint that takes all the
    #: open ones leaves only resolved children, so the milestone rolls up as done.
    #: (One left with no children at all keeps its state.)
    open_children: dict[str, set[str]] = field(default_factory=dict)
    #: Workspace names: every milestone in a workspace shares them, so they name no goal.
    workspace_words: frozenset[str] = frozenset()


def clock() -> datetime:
    """Now. One seam, so a recorded fixture can pin the sprint's dates."""
    return datetime.now(timezone.utc)


def _is_open(ticket: Ticket) -> bool:
    return ticket.state not in RESOLVED_STATES


def _item(
    ticket: Ticket, slugs: dict[str, str], *, from_milestone: str, cost: int
) -> SuggestedItem:
    return SuggestedItem(
        id=ticket.id,
        external_id=ticket.external_id,
        title=ticket.title,
        state=ticket.state,
        work_item_type=ticket.work_item_type,
        workspace_slug=slugs.get(ticket.workspace_id or "", ""),
        from_milestone=from_milestone,
        cost=cost,
    )


def _open_cost(tickets: list[Ticket]) -> int:
    return sum(1 for t in tickets if _is_open(t) and t.work_item_type not in _CONTAINERS)


def _sprint_order(ticket: Ticket, milestone: Ticket) -> tuple:
    """Work in progress first, then work under a milestone already moving."""
    return (
        ticket.state != TicketState.IN_PROGRESS,
        milestone.state != TicketState.IN_PROGRESS,
        ticket.priority,
        ticket.created_at,
    )


def build_pool(session: Session, *, days: int, now: datetime | None = None) -> SuggestionPool:
    now = now or clock()
    milestones = [
        m
        for m in session.exec(
            select(Ticket)
            .where(
                Ticket.work_item_type == WorkItemType.MILESTONE,
                col(Ticket.parent_ticket_id).is_(None),
            )
            .order_by(Ticket.priority, Ticket.created_at)
        ).all()
        if _is_open(m)
    ]
    by_id = {m.id: m for m in milestones}
    open_children = _open_work_under(session, list(by_id))
    children = [
        t
        for t in open_children
        # An integration review depends on its siblings, so it stays with them.
        if t.state not in _STALLED and not t.is_integration_review
    ]
    committed = _open_work_under(
        session,
        list(
            session.exec(
                select(Ticket.id).where(Ticket.work_item_type == WorkItemType.INITIATIVE)
            ).all()
        ),
    )
    trees = descendants_by_root(session, [t.id for t in [*children, *committed]])
    milestone_trees = descendants_by_root(session, list(by_id))
    slugs = workspace_slugs(session, [*milestones, *children, *committed])
    committed_cost: Counter[str] = Counter()
    for ticket in committed:
        committed_cost[slugs.get(ticket.workspace_id or "", "")] += 1 + _open_cost(trees[ticket.id])

    milestone_items = [
        _item(m, slugs, from_milestone="", cost=max(1, _open_cost(milestone_trees[m.id])))
        for m in milestones
    ]
    children.sort(key=lambda t: _sprint_order(t, by_id[t.parent_ticket_id or ""]))
    sprint_pool = [
        _item(
            t,
            slugs,
            from_milestone=by_id[t.parent_ticket_id or ""].external_id,
            cost=1 + _open_cost(trees[t.id]),
        )
        for t in children
    ]

    budgets, sizing = _size_sprint(
        session, children, slugs, committed=committed_cost, days=days, now=now
    )
    resolved_parents = set(
        session.exec(
            select(Ticket.parent_ticket_id).where(
                col(Ticket.parent_ticket_id).in_(list(by_id)),
                col(Ticket.state).in_(list(RESOLVED_STATES)),
            )
        ).all()
    )
    by_milestone: dict[str, set[str]] = {}
    for ticket in open_children:
        if ticket.parent_ticket_id in resolved_parents:
            by_milestone.setdefault(by_id[ticket.parent_ticket_id or ""].external_id, set()).add(
                ticket.id
            )
    return SuggestionPool(
        milestones=milestone_items,
        sprint_pool=sprint_pool,
        budgets=budgets,
        sizing=sizing,
        days=days,
        now=now,
        milestone_descriptions={m.id: m.description for m in milestones},
        open_children=by_milestone,
        workspace_words=frozenset(
            stem for slug in slugs.values() for part in slug.split("-") for stem in _words(part)
        ),
    )


def _open_work_under(session: Session, parent_ids: list[str]) -> list[Ticket]:
    """Open features and bugs directly under any of `parent_ids`."""
    if not parent_ids:
        return []
    rows = session.exec(
        select(Ticket)
        .where(
            col(Ticket.parent_ticket_id).in_(parent_ids),
            col(Ticket.work_item_type).in_([WorkItemType.FEATURE, WorkItemType.BUG]),
        )
        .order_by(Ticket.created_at)
    ).all()
    return [t for t in rows if _is_open(t)]


def _size_sprint(
    session: Session,
    candidates: list[Ticket],
    slugs: dict[str, str],
    *,
    committed: Counter[str],
    days: int,
    now: datetime,
) -> tuple[dict[str, int], SprintSizing]:
    """Per-workspace budgets: the pace's worth of work, less what a sprint already holds.

    Work already sitting directly under an initiative is a sprint in all but
    name, so it is charged first — suggesting twice does not plan two sprints.
    """
    workspace_ids = {t.workspace_id for t in candidates if t.workspace_id}
    paces = measure_paces(session, {wid: [] for wid in workspace_ids}, now)
    budgets: dict[str, int] = {}
    measured = 0
    for workspace_id, pace in paces.items():
        slug = slugs.get(workspace_id, "")
        if pace.per_day is None:
            gross = max(1, round(UNMEASURED_ITEMS_PER_WEEK * days / 7))
        else:
            gross = max(1, round(pace.per_day * days))
            measured += gross
        budgets[slug] = max(0, gross - committed[slug])
    any_measured = any(p.per_day is not None for p in paces.values())
    sizing = SprintSizing(
        days=days,
        capacity=measured if any_measured else None,
        committed=sum(committed[slug] for slug in budgets),
        planned=0,
        basis=ForecastBasis.WORKSPACE_THROUGHPUT if any_measured else ForecastBasis.NONE,
    )
    return budgets, sizing


def plan_sprint(pool: SuggestionPool) -> list[SuggestedItem]:
    """Work in progress always; then the best-ordered work that fits each budget."""
    spent: Counter[str] = Counter()
    chosen: list[SuggestedItem] = []
    for item in pool.sprint_pool:
        budget = pool.budgets[item.workspace_slug]
        in_flight = item.state == TicketState.IN_PROGRESS
        if not in_flight and spent[item.workspace_slug] + item.cost > budget:
            continue
        spent[item.workspace_slug] += item.cost
        chosen.append(item)
    return chosen


def sprint_suggestion(
    pool: SuggestionPool, items: list[SuggestedItem], *, rationale: str = ""
) -> InitiativeSuggestion:
    start = pool.now.date()
    end = start + timedelta(days=pool.days)
    capacity = pool.sizing.capacity
    sized = (
        f"about {capacity} work items at the pace each workspace finished over the last three weeks"
        if capacity is not None
        else "an assumed handful per workspace — nothing finished recently enough to measure a pace"
    )
    return InitiativeSuggestion(
        key="sprint",
        kind=SuggestionKind.SPRINT,
        title=f"Sprint {start:%b %-d} – {end:%b %-d}",
        description=f"A {pool.days}-day sprint, {start:%Y-%m-%d} to {end:%Y-%m-%d}.",
        rationale=rationale or f"Work already in progress, then the next work that fits {sized}.",
        items=items,
        empties=_emptied_milestones(pool, items),
        target_date=end,
    )


def _emptied_milestones(pool: SuggestionPool, items: list[SuggestedItem]) -> list[str]:
    """Milestones this sprint would take every open feature and bug out of."""
    taken = {i.id for i in items}
    return sorted(m for m, ids in pool.open_children.items() if ids and ids <= taken)


def _stem(word: str) -> str:
    stem = word[:-3] + "y" if word.endswith("ies") else word
    return stem[:-1] if stem.endswith("s") and not stem.endswith("ss") else stem


def _words(title: str) -> dict[str, str]:
    """Stemmed word -> the spelling it took in this title."""
    out: dict[str, str] = {}
    for word in _WORD_RE.findall(title.lower()):
        if len(word) < _MIN_WORD or word in _STOP_WORDS:
            continue
        out.setdefault(_stem(word), word)
    return out


def _theme_title(stem: str, spelling: str, titles: list[str]) -> str:
    """The two-word phrase around the shared word when titles agree on one.

    "Agent prompt contract" and "…localization for agent prompts" share "agent
    prompt", which names a goal; "agent" alone names a third of the backlog.
    Without a phrase most of them share, the word is a topic, so it reads as one.
    """
    phrases: Counter[tuple[str, str]] = Counter()
    spelled: dict[tuple[str, str], str] = {}
    for title in titles:
        words = _WORD_RE.findall(title.lower())
        seen: set[tuple[str, str]] = set()
        for left, right in pairwise(words):
            pair = (_stem(left), _stem(right))
            if stem not in pair or len(left) < 3 or len(right) < 3 or pair in seen:
                continue
            if left in _STOP_WORDS or right in _STOP_WORDS:
                continue
            seen.add(pair)
            phrases[pair] += 1
            spelled.setdefault(pair, f"{left} {right}")
    if phrases:
        pair, count = phrases.most_common(1)[0]
        # A phrase only some members share would name a subset as the whole.
        if count >= 2 and count * 2 > len(titles):
            return spelled[pair].capitalize()
    return f"{spelling.capitalize()} work"


def theme_suggestions(
    milestones: list[SuggestedItem], *, ignore: frozenset[str] = frozenset()
) -> list[InitiativeSuggestion]:
    """Greedy cover: the word shared by the most unclaimed milestones, repeatedly."""
    words = {
        m.id: {stem: w for stem, w in _words(m.title).items() if stem not in ignore}
        for m in milestones
    }
    by_id = {m.id: m for m in milestones}
    min_size = 3 if len(milestones) >= 12 else 2
    max_size = max(min_size, int(len(milestones) * _MAX_THEME_SHARE))
    unclaimed = [m.id for m in milestones]
    themes: list[InitiativeSuggestion] = []
    while len(themes) < _MAX_THEMES:
        holders: dict[str, list[str]] = {}
        for mid in unclaimed:
            for stem in words[mid]:
                holders.setdefault(stem, []).append(mid)
        fits = [
            (len(ids), stem) for stem, ids in holders.items() if min_size <= len(ids) <= max_size
        ]
        if not fits:
            break
        _, stem = max(fits)
        members = holders[stem]
        spelling = Counter(words[mid][stem] for mid in members).most_common(1)[0][0]
        themes.append(
            InitiativeSuggestion(
                key=f"theme:{stem}",
                kind=SuggestionKind.THEME,
                title=_theme_title(stem, spelling, [by_id[mid].title for mid in members]),
                description="",
                rationale=(
                    f"{len(members)} open milestones mention “{spelling}”. A keyword match — "
                    "check they serve one goal, or ask the agent to regroup."
                ),
                items=[by_id[mid] for mid in members],
                empties=[],
                target_date=None,
            )
        )
        unclaimed = [mid for mid in unclaimed if mid not in set(members)]
    return themes


def assemble(
    pool: SuggestionPool,
    suggestions: list[InitiativeSuggestion],
    *,
    source: SuggestionSource,
    warnings: list[str],
) -> InitiativeSuggestionSet:
    claimed = {i.id for s in suggestions if s.kind == SuggestionKind.THEME for i in s.items}
    planned = sum(i.cost for s in suggestions if s.kind == SuggestionKind.SPRINT for i in s.items)
    return InitiativeSuggestionSet(
        source=source,
        suggestions=suggestions,
        ungrouped=[m for m in pool.milestones if m.id not in claimed],
        sprint=pool.sizing.model_copy(update={"planned": planned}),
        warnings=warnings,
        generated_at=pool.now,
    )


def suggest_initiatives(
    session: Session, *, days: int, now: datetime | None = None
) -> InitiativeSuggestionSet:
    pool = build_pool(session, days=days, now=now)
    suggestions = theme_suggestions(pool.milestones, ignore=pool.workspace_words)
    sprint_items = plan_sprint(pool)
    if sprint_items:
        suggestions.append(sprint_suggestion(pool, sprint_items))
    return assemble(pool, suggestions, source=SuggestionSource.HEURISTIC, warnings=[])


#: Most rows the "add to sprint" search returns.
ADDABLE_LIMIT = 15


def addable_work(session: Session, initiative_id: str, search: str) -> list[SuggestedItem]:
    """Open features and bugs matching `search` that could join this initiative.

    Leaves out what a sprint must not take — integration reviews, which run
    after their siblings — and what is already directly under an initiative.
    """
    needle = f"%{search.strip()}%"
    rows = session.exec(
        select(Ticket)
        .where(
            col(Ticket.work_item_type).in_([WorkItemType.FEATURE, WorkItemType.BUG]),
            col(Ticket.state).not_in(list(RESOLVED_STATES)),
            col(Ticket.is_integration_review).is_(False),
            col(Ticket.parent_ticket_id) != initiative_id,
            col(Ticket.title).ilike(needle) | col(Ticket.external_id).ilike(needle),
        )
        .order_by(Ticket.priority, Ticket.created_at)
    ).all()
    parents = {
        p.id: p
        for p in session.exec(
            select(Ticket).where(col(Ticket.id).in_({t.parent_ticket_id for t in rows}))
        ).all()
    }
    movable = [
        t
        for t in rows
        if t.parent_ticket_id in parents
        and parents[t.parent_ticket_id].work_item_type != WorkItemType.INITIATIVE
    ][:ADDABLE_LIMIT]
    trees = descendants_by_root(session, [t.id for t in movable])
    slugs = workspace_slugs(session, movable)
    return [
        _item(
            t,
            slugs,
            from_milestone=parents[t.parent_ticket_id or ""].external_id,
            cost=1 + _open_cost(trees[t.id]),
        )
        for t in movable
    ]


def _validate_batch(session: Session, drafts: list[InitiativeDraft]) -> dict[str, Ticket]:
    """Load every item and refuse the whole batch if any one cannot move."""
    seen: set[str] = set()
    tickets: dict[str, Ticket] = {}
    for draft in drafts:
        if not draft.title.strip():
            raise ValueError("Every initiative needs a title")
        for item_id in draft.item_ids:
            if item_id in seen:
                raise ValueError(f"{item_id} is in more than one initiative")
            seen.add(item_id)
            ticket = session.get(Ticket, item_id)
            if ticket is None:
                raise SuggestionConflictError(f"Work item {item_id} no longer exists")
            validate_parent_child(WorkItemType.INITIATIVE, ticket.work_item_type)
            if ticket.is_integration_review:
                raise ValueError(
                    f"{ticket.external_id} is an integration review; it stays with its siblings"
                )
            parent = (
                session.get(Ticket, ticket.parent_ticket_id) if ticket.parent_ticket_id else None
            )
            if parent is not None and parent.work_item_type == WorkItemType.INITIATIVE:
                raise SuggestionConflictError(
                    f"{ticket.external_id} already belongs to {parent.external_id}"
                )
            tickets[item_id] = ticket
    return tickets


def apply_suggestions(session: Session, body: SuggestionApply) -> SuggestionApplyResult:
    """Create each kept initiative and move its items under it.

    Every item is checked before anything is written. Each initiative then
    lands with its items in one commit, and every parent that lost or gained a
    child re-derives its state, as a manual reparent does.
    """
    tickets = _validate_batch(session, body.initiatives)
    created: list[CreatedInitiative] = []
    service = TicketService(session)
    for draft in body.initiatives:
        initiative: Ticket | None = None
        try:
            initiative = service.create_ticket(
                title=draft.title,
                work_item_type=WorkItemType.INITIATIVE,
                description=draft.description,
            )
            old_parents = {tickets[i].parent_ticket_id for i in draft.item_ids}
            for item_id in draft.item_ids:
                reparent_ticket(session, tickets[item_id], initiative.id)
            session.commit()
        except ValueError as exc:
            session.rollback()
            done = [c.external_id for c in created]
            if initiative is not None:
                done.append(f"{initiative.external_id} (empty)")
            raise ValueError(
                f"Stopped at “{draft.title}”: {exc}. Already created: {', '.join(done) or 'none'}."
            ) from exc
        for parent_id in old_parents:
            reconcile_lineage(session, parent_id)
        reconcile_lineage(session, initiative.id)
        if draft.target_date is not None:
            update_plan(
                session,
                initiative.id,
                InitiativePlanUpdate(
                    targets=[
                        ScheduleTargetInput(ticket_id=initiative.id, target_date=draft.target_date)
                    ]
                ),
                actor=_OPERATOR,
            )
        created.append(
            CreatedInitiative(
                id=initiative.id,
                external_id=initiative.external_id,
                title=initiative.title,
                attached=len(draft.item_ids),
            )
        )
    return SuggestionApplyResult(created=created)
