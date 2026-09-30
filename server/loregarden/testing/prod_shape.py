"""A production-shaped dataset, built from the integration-test factories.

UI that is only ever seen on an empty database or a three-row fixture ships
unusable. `task sandbox` fixes that where the live database exists; this is the
same data *shape* everywhere else — CI, cloud sessions, another workspace — with
no access to anyone's data. It is deterministic (fixed seed, fixed ids), so a
recording made from it (`client/src/test/fixtures/prod-shape`) is reproducible.

What matters is not the words but the proportions a surface has to survive,
each calibrated from the live database. Re-measure when they drift and update
`CALIBRATION` with the date — a stale shape is a fixture again.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel
from sqlmodel import Session

from loregarden.models.domain import MonitorArtifactKind, TicketState, WorkItemType
from loregarden.services.memory_store import MemoryGraphStore
from loregarden.testing.factories import NO_REPO, make_artifact, make_ticket, make_workspace


class WorkspaceShape(BaseModel):
    prefix: str
    tickets: int
    #: Every milestone is one an initiative could claim: none exist live.
    milestones: int


class Calibration(BaseModel):
    """The proportions the scenario reproduces. Re-measure, and re-date, when
    production drifts — a stale shape is a fixture again."""

    measured: str
    workspaces: dict[str, WorkspaceShape]
    #: Non-milestone mix by type, and each type's state split (live counts).
    types: dict[WorkItemType, dict[TicketState, int]]
    milestone_states: dict[TicketState, int]
    findings_total: int
    findings_on_finished: int
    #: Records per workspace graph. Live: 0 recorded relations anywhere, and no
    #: near-duplicate proposals (25 "titled by its ticket" ones on loregarden).
    memory: dict[str, int]
    #: Plan + review pairs; plans average ~8KB live, reviews ~1.6KB.
    long_documents: int


#: Measured from the live database, 2026-09-28 (read-only).
CALIBRATION = Calibration(
    measured="2026-09-28",
    workspaces={
        "loregarden": WorkspaceShape(prefix="lg", tickets=812, milestones=54),
        "blobert": WorkspaceShape(prefix="blob", tickets=115, milestones=11),
        "lore-eden": WorkspaceShape(prefix="lor", tickets=56, milestones=4),
        "loremaker": WorkspaceShape(prefix="lmkr", tickets=33, milestones=6),
    },
    types={
        WorkItemType.FEATURE: {
            TicketState.BACKLOG: 383,
            TicketState.DONE: 107,
            TicketState.IN_PROGRESS: 14,
            TicketState.BLOCKED: 1,
            TicketState.WONT_DO: 1,
        },
        WorkItemType.BUG: {
            TicketState.BACKLOG: 100,
            TicketState.DONE: 100,
            TicketState.BLOCKED: 1,
            TicketState.WONT_DO: 1,
        },
        WorkItemType.CAPABILITY: {
            TicketState.DONE: 107,
            TicketState.BACKLOG: 60,
            TicketState.IN_PROGRESS: 2,
            TicketState.BLOCKED: 1,
        },
        WorkItemType.TASK: {
            TicketState.DONE: 41,
            TicketState.BACKLOG: 20,
            TicketState.BLOCKED: 1,
            TicketState.WONT_DO: 1,
        },
    },
    milestone_states={
        TicketState.BACKLOG: 42,
        TicketState.IN_PROGRESS: 24,
        TicketState.DONE: 7,
        TicketState.BLOCKED: 2,
    },
    findings_total=94,
    findings_on_finished=85,
    memory={"loregarden": 34, "blobert": 106},
    long_documents=30,
)

_RNG_SEED = 20260928
_EPOCH = datetime(2026, 9, 1, tzinfo=timezone.utc)

_VERBS = ["Fix", "Add", "Make", "Stop", "Show", "Split", "Route", "Retry", "Cache", "Surface"]
_OBJECTS = [
    "the queue board's stalled lane",
    "gate outcomes on stage transitions",
    "memory recall ranking",
    "branch cleanup after a squash merge",
    "the approval inbox for parked stages",
    "worktree isolation for chat turns",
    "the rework ledger's loop cap",
    "docker capacity leases",
    "GitHub issue two-way sync",
    "the run log's live tail",
    "initiative progress rollup",
    "stage timeout budgets",
]
_STAGES = ["review", "implement", "spec", "plan", "script_review", "test-design"]
#: Enough distinct words that two sampled records rarely share many of them.
_LEXICON = sorted(
    set(
        """
        retry budget ledger lease worktree branch squash merge gate verdict stage
        report sentinel handoff checkpoint rework reroute pin cursor template
        version snapshot migration shard vault graph relation alias discredit
        confidence ladder outcome briefing recall ranker digest proposal curation
        queue lane slot promotion capacity docker daemon probe timeout floor
        percentile sample baseline thrash stall orphan zombie adapter token
        session keychain oauth prompt skill role asset contract schema enum
        payload validator coercion fixture factory scenario sandbox recorder
        monitor finding escalation telemetry sweep reconcile drain boot recovery
        websocket event hub toast dialog focus keyboard escape skeleton spinner
        tree parent child milestone initiative rollup dependency integration
        commit push hook lefthook ruff pylint oxlint jest pytest xdist flake
        clock cache stale fresh idempotent atomic transaction pragma journal wal
        backup restore sqlite icloud obsidian markdown frontmatter export import
        github issue sync label webhook review lane visual static security
        """.split()
    )
)

_TAGS = [
    "frontend",
    "retry-budget",
    "testing",
    "orchestration",
    "workflow",
    "triage",
    "gates",
    "sqlite",
]


class ProdShapeSummary(BaseModel):
    tickets: int
    milestones: int
    findings: int
    findings_on_finished: int
    long_documents: int
    memory_records: dict[str, int]


def prod_shape_id(namespace: str, n: int | str) -> str:
    """The fixed id of the `n`th row in `namespace`, so tests and recordings can name rows."""
    return str(uuid5(NAMESPACE_URL, f"loregarden-prod-shape/{namespace}/{n}"))


def _states(split: dict[TicketState, int], count: int, rng: random.Random) -> list[TicketState]:
    """`count` states in the proportions of `split`, shuffled deterministically."""
    total = sum(split.values())
    states: list[TicketState] = []
    for state, n in split.items():
        states += [state] * round(count * n / total)
    states = (states + [TicketState.BACKLOG] * count)[:count]
    rng.shuffle(states)
    return states


def _title(rng: random.Random) -> str:
    return f"{rng.choice(_VERBS)} {rng.choice(_OBJECTS)}"


def long_markdown(rng: random.Random, title: str, sections: int) -> str:
    """Agent-shaped prose: headings, paragraphs, lists, a code block and a table."""
    parts = [f"# {title}", ""]
    for i in range(sections):
        parts += [
            f"## {rng.choice(['Approach', 'Why this shape', 'Risks', 'Evidence', 'Left out'])} {i + 1}",
            "",
            " ".join(
                f"The {rng.choice(_OBJECTS)} path reads `services/{rng.choice(_STAGES)}.py` "
                f"before it writes, so a retry after a crash sees the committed row."
                for _ in range(4)
            ),
            "",
            *[
                f"- {_title(rng)} — `{rng.choice(_STAGES)}` stage, {rng.randint(2, 40)} runs"
                for _ in range(5)
            ],
            "",
            "```python",
            "def settle(session, ticket):",
            "    return reconcile_parent(session, ticket.parent_ticket_id)",
            "```",
            "",
            "| stage | runs | failed |",
            "|---|---|---|",
            *[f"| {s} | {rng.randint(1, 60)} | {rng.randint(0, 9)} |" for s in _STAGES[:4]],
            "",
        ]
    return "\n".join(parts)


def _finding_payload(rng: random.Random, condition: str, stage: str, n: int) -> dict:
    attempts = rng.randint(4, 12)
    first = _EPOCH + timedelta(hours=n * 5)
    summaries = {
        "stage_thrash": f"Stage '{stage}' ran {attempts} times in one orchestration run "
        "(baseline 1.58 attempts per stage).",
        "stalled_run": f"Run has been RUNNING for {attempts / 10:.1f}h, past the 0.3h bound for this stage.",
        "unbudgeted_repeat": f"Stage '{stage}' was attempted {attempts} times with no orchestration run.",
        "unsettled_stage": f"Stage '{stage}' is blocked, but its last run recorded success.",
    }
    return {
        "condition": condition,
        "stage_key": stage,
        "summary": summaries[condition],
        "evidence": {"attempts": str(attempts), "threshold": "4"},
        # The live value is a sweep-tick count in the thousands; see monitorFindings.ts.
        "occurrences": 4000 + n,
        "first_seen": first.isoformat(),
        "last_seen": (first + timedelta(days=20)).isoformat(),
    }


def _memory(
    graph: MemoryGraphStore,
    slug: str,
    count: int,
    tickets: list[tuple[str, str]],
    rng: random.Random,
) -> int:
    """`count` records, no recorded relations — the live graphs have none.

    Learnings are titled by their ticket, as agents write them, so the lead of
    the body is the only readable name. Tickets repeat, and tags recur, which is
    the structure the Memory page's inferred groups exist to show.
    """
    for n in range(count):
        ticket_id, _milestone = tickets[(n * 7) % len(tickets)] if n % 5 else tickets[n % 4]
        learning = n % 4 != 3
        tags = [slug] + rng.sample(_TAGS, k=1 + (n % 2))
        # Distinct per record, as real learnings are: a shared sentence template
        # made every pair clear the curation's 0.6 term-overlap bar, so the
        # scenario claimed 20 near-duplicates where production has none.
        words = rng.sample(_LEXICON, k=18)
        lead = f"{words[0].capitalize()} {words[1]} {words[2]} when {words[3]} {words[4]}"
        body = (
            f"**{lead}.** {words[5].capitalize()} {words[6]} {words[7]} {words[8]}; "
            f"{words[9]} {words[10]} {words[11]} {words[12]} — {words[13]} {words[14]} "
            f"{words[15]} {words[16]} {words[17]} ({ticket_id})."
        )
        graph.upsert_node(
            node_id=prod_shape_id(f"memory-{slug}", n),
            title=f"Learning — {ticket_id}" if learning else lead,
            body=body,
            tags=["learning", *tags] if learning else tags,
            ticket_id=ticket_id if learning or n % 2 else "",
            workspace_slug=slug,
            node_type="learning" if learning else "memory",
        )
    # The graph stamps records from the clock at one-second resolution and lists
    # them newest first with no tie-break, so a seed that crossed a second
    # boundary at a different record came out in a different order (seen as a
    # 1-in-5 flake of the recorded fixture). Spread them over fixed times.
    with graph.connection() as conn:
        for n in range(count):
            stamp = (_EPOCH + timedelta(hours=n)).isoformat()
            conn.execute(
                "UPDATE memory_nodes SET created_at = ?, updated_at = ? WHERE id = ?",
                (stamp, stamp, prod_shape_id(f"memory-{slug}", n)),
            )
    return count


def build_prod_shape(
    session: Session, *, graph_path: Callable[[str], Path | None] | None = None
) -> ProdShapeSummary:
    """Populate `session`'s database (and memory graphs, when `graph_path` names
    where they go) with the calibrated shape. Idempotent by id."""
    rng = random.Random(_RNG_SEED)
    all_tickets = []
    milestones = 0
    for slug, ws in CALIBRATION.workspaces.items():
        workspace = make_workspace(session, slug=slug, repo_path=NO_REPO)
        prefix = ws.prefix
        ms_states = _states(CALIBRATION.milestone_states, ws.milestones, rng)
        ms_ids = []
        for n, state in enumerate(ms_states):
            ms = make_ticket(
                session,
                workspace_id=workspace.id,
                ticket_id=prod_shape_id(f"{slug}/milestone", n),
                external_id=f"{prefix}-m{n + 1}-{n + 1}",
                title=f"M{n + 1:02d} — {_title(rng).capitalize()}",
                work_item_type=WorkItemType.MILESTONE,
                state=state,
            )
            ms_ids.append(ms.id)
        milestones += len(ms_ids)

        rest = ws.tickets - ws.milestones
        weights = {t: sum(split.values()) for t, split in CALIBRATION.types.items()}
        total_weight = sum(weights.values())
        number = ws.milestones
        for kind, split in CALIBRATION.types.items():
            share = round(rest * weights[kind] / total_weight)
            for state in _states(split, share, rng):
                number += 1
                parent = ms_ids[number % len(ms_ids)]
                ticket = make_ticket(
                    session,
                    workspace_id=workspace.id,
                    ticket_id=prod_shape_id(f"{slug}/ticket", number),
                    external_id=f"{prefix}-m{(number % len(ms_ids)) + 1}-{number}",
                    title=_title(rng),
                    work_item_type=kind,
                    state=state,
                    parent_ticket_id=parent,
                )
                all_tickets.append((slug, ticket.id, ticket.external_id, state, parent))

    findings = _add_findings(session, all_tickets, rng)
    documents = _add_documents(session, all_tickets, rng)
    memory = {}
    if graph_path is not None:
        for slug, count in CALIBRATION.memory.items():
            path = graph_path(slug)
            if path is None:
                continue
            refs = [(ext, parent) for s, _id_, ext, _st, parent in all_tickets if s == slug]
            memory[slug] = _memory(MemoryGraphStore(path), slug, count, refs, rng)
    return ProdShapeSummary(
        tickets=len(all_tickets) + milestones,
        milestones=milestones,
        findings=findings,
        findings_on_finished=CALIBRATION.findings_on_finished,
        long_documents=documents,
        memory_records=memory,
    )


def _add_findings(session: Session, tickets: list, rng: random.Random) -> int:
    finished = [t for t in tickets if t[3] in (TicketState.DONE, TicketState.WONT_DO)]
    live = [t for t in tickets if t[3] in (TicketState.IN_PROGRESS, TicketState.BLOCKED)]
    # Several findings per ticket, one per (condition, stage) — the upsert key.
    targets = [finished[i % 34] for i in range(CALIBRATION.findings_on_finished)] + [
        live[i % 5] for i in range(CALIBRATION.findings_total - CALIBRATION.findings_on_finished)
    ]
    conditions = (
        ["stage_thrash"] * 6 + ["stalled_run"] * 2 + ["unbudgeted_repeat", "unsettled_stage"]
    )
    for n, (_slug, ticket_id, _ext, _state, _parent) in enumerate(targets):
        condition = conditions[n % len(conditions)]
        stage = _STAGES[n % len(_STAGES)]
        make_artifact(
            session,
            artifact_id=prod_shape_id("finding", n),
            ticket_id=ticket_id,
            kind=MonitorArtifactKind.FINDING.value,
            title=f"{condition}:{stage}:{n}",
            content=_finding_payload(rng, condition, stage, n),
        )
    return len(targets)


def _add_documents(session: Session, tickets: list, rng: random.Random) -> int:
    count = CALIBRATION.long_documents
    for n in range(count):
        _slug, ticket_id, ext, _state, _parent = tickets[(n * 13) % len(tickets)]
        title = f"Plan — {_title(rng)}"
        make_artifact(
            session,
            artifact_id=prod_shape_id("plan", n),
            ticket_id=ticket_id,
            kind="plan",
            title=title,
            content={
                "ticket": ext,
                "document": long_markdown(rng, title, 6),
                "task_count": rng.randint(3, 14),
                "steps": [_title(rng) for _ in range(6)],
                "left_out": [_title(rng) for _ in range(3)],
            },
        )
        make_artifact(
            session,
            artifact_id=prod_shape_id("review", n),
            ticket_id=ticket_id,
            kind="review",
            title=f"Review — {ext}",
            content={
                "verdict": rng.choice(["pass", "needs_rework"]),
                "summary": long_markdown(rng, "Summary", 1),
                "findings": [
                    {"title": _title(rng), "detail": long_markdown(rng, "Detail", 1)}
                    for _ in range(3)
                ],
            },
        )
    return count * 2
