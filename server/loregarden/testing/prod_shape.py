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
from sqlmodel import Session, select

from loregarden.models.domain import (
    AgentRun,
    Artifact,
    ArtifactKind,
    InitiativeMember,
    MonitorArtifactKind,
    RunStatus,
    StageBudgetArtifactKind,
    Ticket,
    TicketState,
    WorkItemType,
)
from loregarden.services.artifact_records import (
    RUN_CONTEXT_ARTIFACT_TITLE,
    run_log_artifact_title,
)
from loregarden.services.memory_store import MemoryGraphStore
from loregarden.services.ticket_ids import assign_initiative_external_id
from loregarden.services.ticket_rollup import reconcile_parent
from loregarden.testing.factories import (
    NO_REPO,
    make_agent_run,
    make_artifact,
    make_ticket,
    make_workspace,
)


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
    #: One worked ticket at the live p90, measured 2026-10-08 over the 128 tickets
    #: with runs: runs (median 15, p90 33, max 76), artifact rows (median 73, p90
    #: 172, max 397), and the share of rows that are platform bookkeeping (40%).
    worked_runs: int
    worked_artifacts: int
    worked_system_share: float


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
    worked_runs=33,
    worked_artifacts=172,
    worked_system_share=0.40,
)

#: Live had no initiatives at calibration, with every milestone unclaimed. The
#: scenario carries one anyway, so the Initiatives page's populated state is seen
#: at real volume: it claims the milestones of one theme that spans three
#: workspaces, and leaves the other 65 unclaimed — still the shape that dominates.
INITIATIVE_THEME = "gate outcomes on stage transitions"
INITIATIVE_TITLE = "Trustworthy gate outcomes on every stage transition"

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
    #: Milestones the scenario's one initiative claims.
    initiative_milestones: int
    findings: int
    findings_on_finished: int
    long_documents: int
    memory_records: dict[str, int]
    worked_runs: int
    worked_artifacts: int


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
    themed: list[str] = []
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
            if INITIATIVE_THEME in ms.title.lower():
                themed.append(ms.id)
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

    _add_initiative(session, themed, all_tickets)
    findings = _add_findings(session, all_tickets, rng)
    documents = _add_documents(session, all_tickets, rng)
    # Its own stream, so adding the history did not reshuffle every row drawn after it.
    worked_runs, worked_artifacts = _add_worked_history(
        session, all_tickets, random.Random(_RNG_SEED + 1)
    )
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
        initiative_milestones=len(themed),
        findings=findings,
        findings_on_finished=CALIBRATION.findings_on_finished,
        long_documents=documents,
        memory_records=memory,
        worked_runs=worked_runs,
        worked_artifacts=worked_artifacts,
    )


def _add_initiative(session: Session, milestone_ids: list[str], tickets: list) -> None:
    """One initiative over `milestone_ids`, spelled and rolled up by the real services.

    It also tracks one open feature by membership, from a milestone it does not
    own — the case membership exists for: the feature keeps its milestone.
    """
    initiative_id = prod_shape_id("initiative", 0)
    is_new = session.get(Ticket, initiative_id) is None
    initiative = make_ticket(
        session,
        workspace_id=None,
        ticket_id=initiative_id,
        title=INITIATIVE_TITLE,
        work_item_type=WorkItemType.INITIATIVE,
        description=(
            "Every workspace's stage transitions report the gate outcome they actually got, "
            "so a ticket never advances on a check that did not run."
        ),
    )
    if is_new:
        assign_initiative_external_id(session, initiative)
    for milestone_id in milestone_ids:
        milestone = session.get(Ticket, milestone_id)
        milestone.parent_ticket_id = initiative.id
        session.add(milestone)
    owned = set(milestone_ids)
    for _slug, ticket_id, _ext, state, parent in tickets:
        ticket = session.get(Ticket, ticket_id)
        if (
            parent not in owned
            and ticket.work_item_type == WorkItemType.FEATURE
            and state not in (TicketState.DONE, TicketState.WONT_DO)
        ):
            if session.get(InitiativeMember, (initiative.id, ticket_id)) is None:
                session.add(
                    InitiativeMember(
                        initiative_id=initiative.id, ticket_id=ticket_id, added_by="prod-shape"
                    )
                )
            break
    session.flush()
    reconcile_parent(session, initiative)
    session.add(initiative)
    session.commit()


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


#: The worked ticket's pipeline: (stage, lanes as (agent, skill), statuses per
#: attempt). One lane with several statuses retries; several lanes fan out. The
#: same stage after another is a revisit — implement and review both come back,
#: as they do live when verify or a reviewer sends work back.
_WORKED_PIPELINE: list[tuple[str, list[tuple[str, str]], list[RunStatus]]] = [
    ("triage", [("ticket_scoper", "")], [RunStatus.SUCCEEDED]),
    (
        "plan",
        [("planner", f"plan-{lens}") for lens in ("simplest", "risk", "seams", "user", "ops")],
        [RunStatus.SUCCEEDED] * 5,
    ),
    ("ui-design", [("ui_design_decision", "")], [RunStatus.SUCCEEDED]),
    ("plan-synthesis", [("planner", "plan-synthesis")], [RunStatus.SUCCEEDED]),
    ("spec", [("spec", "")], [RunStatus.FAILED, RunStatus.SUCCEEDED]),
    ("test-design", [("test_designer", "")], [RunStatus.SUCCEEDED]),
    ("test-break", [("test_breaker", "")], [RunStatus.FAILED, RunStatus.SUCCEEDED]),
    (
        "implement",
        [("backend_implementer", "")],
        [RunStatus.FAILED, RunStatus.FAILED, RunStatus.SUCCEEDED],
    ),
    ("verify", [("verifier", "")], [RunStatus.SUCCEEDED]),
    ("implement", [("backend_implementer", "")], [RunStatus.FAILED, RunStatus.SUCCEEDED]),
    ("verify", [("verifier", "")], [RunStatus.FAILED, RunStatus.SUCCEEDED]),
    (
        "review",
        [
            (agent, "")
            for agent in ("static_qa", "architecture_reviewer", "security_reviewer", "visual_qa")
        ],
        [RunStatus.SUCCEEDED, RunStatus.SUCCEEDED, RunStatus.SUCCEEDED, RunStatus.FAILED],
    ),
    ("implement", [("backend_implementer", "")], [RunStatus.SUCCEEDED]),
    (
        "review",
        [
            (agent, "")
            for agent in ("static_qa", "architecture_reviewer", "security_reviewer", "visual_qa")
        ],
        [RunStatus.SUCCEEDED] * 4,
    ),
    ("gate", [("gatekeeper", "")], [RunStatus.FAILED, RunStatus.FAILED, RunStatus.SUCCEEDED]),
]

#: Work-output kinds the filler draws from, weighted toward the live mix.
_WORKED_FILLER = ["evidence", "test", "handoff", "review", "spec", "source_analysis", "test_spec"]


def _add_worked_history(session: Session, tickets: list, rng: random.Random) -> tuple[int, int]:
    """One in-progress ticket worked to the live p90: runs grouped into stage
    visits with retries, fan-outs and revisits, and an artifact feed of the
    same size and system share as production.

    Every row is stamped inside its run's window, so the Timeline places it as
    it would a live row. About half the work outputs carry their run, as live
    rows written before attach_artifact recorded one do not.
    """
    # Idempotent like the rest of the scenario: a rebuild reuses the ticket the
    # first build chose, rather than picking another now that this one has rows.
    first_run = session.get(AgentRun, prod_shape_id("worked-run", 0))
    if first_run is not None:
        ticket_id = first_run.ticket_id
        ext = session.get(Ticket, ticket_id).external_id
    else:
        with_artifacts = {row.ticket_id for row in session.exec(select(Artifact)).all()}
        _slug, ticket_id, ext, _state, _parent = next(
            row
            for row in tickets
            if row[0] == "loregarden"
            and row[3] == TicketState.IN_PROGRESS
            and row[1] not in with_artifacts
        )
    workspace_id = session.get(Ticket, ticket_id).workspace_id
    clock = _EPOCH + timedelta(days=10)
    run_n = 0
    rows: list[dict] = []
    for stage, lanes, statuses in _WORKED_PIPELINE:
        rows.append(
            {
                "kind": StageBudgetArtifactKind.DISPATCH.value,
                "title": f"stage-dispatch:{stage}",
                "at": clock - timedelta(seconds=5),
                "system": True,
            }
        )
        parallel = len(lanes) > 1
        visit_start = clock
        for attempt, status in enumerate(statuses):
            agent, skill = lanes[attempt % len(lanes)]
            start = visit_start if parallel else clock
            finish = start + timedelta(minutes=rng.randint(2, 25))
            run = make_agent_run(
                session,
                workspace_id=workspace_id,
                ticket_id=ticket_id,
                run_id=prod_shape_id("worked-run", run_n),
                run_code=f"run_w{run_n:05d}",
                agent_id=agent,
                skill_name=skill,
                stage_key=stage,
                status=status,
                command="",
                stdout="",
                stderr="Traceback (most recent call last):\n  ...\nAssertionError: gate failed"
                if status == RunStatus.FAILED
                else "",
                created_at=start,
                started_at=start,
                finished_at=finish,
            )
            run_n += 1
            mid = start + (finish - start) / 2
            rows.append(
                {
                    "kind": ArtifactKind.CONTEXT.value,
                    "title": RUN_CONTEXT_ARTIFACT_TITLE,
                    "run": run.id,
                    "at": start,
                    "system": True,
                }
            )
            rows.append(
                {
                    "kind": ArtifactKind.LOG.value,
                    "title": run_log_artifact_title(run.run_code),
                    "run": run.id,
                    "at": finish,
                    "system": True,
                }
            )
            verdict = "fail" if status == RunStatus.FAILED else "pass"
            rows.append(
                {
                    "kind": ArtifactKind.CONTEXT.value,
                    "title": f"Stage report — {stage} ({agent})",
                    "run": run.id if run_n % 2 else None,
                    "at": finish,
                    "content": {
                        "stage_key": stage,
                        "status": verdict,
                        "confidence": round(rng.uniform(0.7, 0.95), 2),
                    },
                }
            )
            if status == RunStatus.FAILED:
                rows.append(
                    {
                        "kind": "error",
                        "title": f"{stage} failed — {agent}",
                        "at": finish,
                        "content": {
                            "stage_key": stage,
                            "message": long_markdown(rng, "Failure", 1),
                        },
                    }
                )
            if parallel and skill.startswith("plan-"):  # each planning lens writes its plan
                rows.append(
                    {
                        "kind": ArtifactKind.PLAN.value,
                        "title": f"Plan ({skill}) — {ext}",
                        "run": run.id,
                        "at": mid,
                        "content": {
                            "verdict": _title(rng),
                            "steps": [_title(rng) for _ in range(5)],
                        },
                    }
                )
            clock = max(clock, finish + timedelta(minutes=1))
        rows.append(
            {
                "kind": "handoff",
                "title": f"handoff {stage} → next",
                "at": clock - timedelta(seconds=30),
                "content": {"from_stage": stage, "checklist": [_title(rng) for _ in range(3)]},
            }
        )

    # Trim bookkeeping to the live share — run-log pointers go first, as live
    # runs that never streamed a line have none — then fill work outputs to the
    # live size, each stamped inside some run's window.
    system_budget = round(CALIBRATION.worked_artifacts * CALIBRATION.worked_system_share)
    system = [row for row in rows if row.get("system")]
    work = [row for row in rows if not row.get("system")]
    surplus = max(0, len(system) - system_budget)
    pointers = [row for row in system if row["kind"] == ArtifactKind.LOG.value]
    dropped = {id(row) for row in pointers[::2][:surplus]}
    system = [row for row in system if id(row) not in dropped]
    runs = session.exec(select(AgentRun).where(AgentRun.ticket_id == ticket_id)).all()
    n = 0
    while len(system) + len(work) < CALIBRATION.worked_artifacts:
        run = runs[n % len(runs)]
        kind = _WORKED_FILLER[n % len(_WORKED_FILLER)]
        work.append(
            {
                "kind": kind,
                "title": f"{kind.replace('_', ' ').capitalize()} — {_title(rng)}",
                "at": run.started_at + (run.finished_at - run.started_at) / 3,
                "content": {"summary": long_markdown(rng, "Summary", 1)},
            }
        )
        n += 1

    for i, row in enumerate(system + work):
        make_artifact(
            session,
            artifact_id=prod_shape_id("worked-artifact", i),
            ticket_id=ticket_id,
            run_id=row.get("run"),
            kind=row["kind"],
            title=row["title"],
            content=row.get("content", {}),
            created_at=row["at"],
        )
    return run_n, len(system) + len(work)
