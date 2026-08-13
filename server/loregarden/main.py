import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session

from loregarden.api import (
    agents,
    analytics,
    baxter_chat,
    branch_triage,
    bulk_queue_operations,
    calendar_events,
    chat_turn_events,
    ci,
    composer_notes,
    diff_review,
    editor,
    events,
    inbox,
    mcp,
    mcp_servers,
    memory,
    orchestration,
    parallel,
    queue_events,
    queue_lanes,
    queue_management,
    queue_review,
    reference_repos,
    runs,
    stage_fanout,
    studio,
    system,
    terminal,
    ticket_studio,
    tickets,
    usage,
    workflows,
    workspaces,
)
from loregarden.config import settings
from loregarden.core.auth import TokenAuthMiddleware
from loregarden.db.session import engine, init_db
from loregarden.services.baxter_chat_run_service import fail_interrupted_baxter_chat_turns
from loregarden.services.branch_triage_run_service import fail_interrupted_branch_triage_turns
from loregarden.services.btw_run_service import fail_interrupted_asides
from loregarden.services.chat_thinking import clear_orphaned_chat_turn_thinking
from loregarden.services.orchestration_recovery import resume_interrupted_orchestrations
from loregarden.services.queue_lanes import QueueLaneService
from loregarden.services.run_service import (
    fail_interrupted_orchestration_runs,
    fail_interrupted_runs,
    settle_stranded_stages,
)
from loregarden.services.seed import seed_database
from loregarden.services.ticket_rollup import reconcile_all_parents
from loregarden.services.ticket_studio_run_service import fail_interrupted_studio_turns
from loregarden.services.triage_run_service import fail_interrupted_triage_turns
from loregarden.services.worktree_lifecycle import reconcile_worktrees

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not settings.api_token:
        logger.warning(
            "LOREGARDEN_API_TOKEN is not set — the API (which writes files and "
            "runs agents) is reachable by any local process. Set a token to "
            "require authentication on shared machines."
        )
    init_db()
    with Session(engine) as session:
        seed_database(session)
        fail_interrupted_runs(session)
        fail_interrupted_orchestration_runs(session)
        fail_interrupted_triage_turns(session)
        fail_interrupted_branch_triage_turns(session)
        fail_interrupted_baxter_chat_turns(session)
        fail_interrupted_studio_turns(session)
        # An aside has no run row of its own, so nothing above would ever reach it.
        fail_interrupted_asides(session)
        # The turns those four just settled are exactly the ones whose live
        # thinking rows outlived them; nothing is left watching for that text.
        clear_orphaned_chat_turn_thinking(session)
        # Last: the reaps above settle stages as they complete their runs, so this
        # only sees stages no run will ever account for.
        settle_stranded_stages(session)
        # After the reaps, so the runs they just failed count as finished and
        # the slots they were holding come back rather than staying claimed by
        # a run this process will never hear from again.
        QueueLaneService(session).reconcile_lanes()
        # The trees those dead runs were working in. After the reaps, so a
        # ticket the crash left mid-stage counts as unfinished and keeps its
        # worktree for the resume below.
        reconcile_worktrees(session)
        # After the reaps and stage settling, so every child ticket has reached
        # the state it will actually be in before its parents are summarised
        # from it. Catches whatever the push-on-change hooks missed.
        reconcile_all_parents(session)
        # Resume only after every orphan row and stranded stage has been made
        # durable. Recovery adds a fresh run; the failed rows remain the audit trail.
        resume_interrupted_orchestrations(session)
    yield


app = FastAPI(title="Loregarden Control Plane", version="0.1.0", lifespan=lifespan)

# Order matters: the last-added middleware is outermost. CORS is added last so it
# wraps auth — it answers preflight and attaches CORS headers even to 401s. Auth
# also exempts OPTIONS directly. When no token is configured auth is a pass-through.
app.add_middleware(TokenAuthMiddleware, token=settings.api_token)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(tickets.router, prefix="/api")
app.include_router(diff_review.router, prefix="/api")
app.include_router(workspaces.router, prefix="/api")
app.include_router(calendar_events.router, prefix="/api")
app.include_router(editor.router, prefix="/api")
app.include_router(branch_triage.router, prefix="/api")
app.include_router(baxter_chat.router, prefix="/api")
app.include_router(composer_notes.router, prefix="/api")
app.include_router(system.router, prefix="/api")
app.include_router(inbox.router, prefix="/api")
app.include_router(events.router, prefix="/api")
app.include_router(runs.router, prefix="/api")
app.include_router(agents.router, prefix="/api")
app.include_router(mcp_servers.router, prefix="/api")
app.include_router(workflows.router, prefix="/api")
app.include_router(orchestration.router, prefix="/api")
app.include_router(stage_fanout.router)
app.include_router(studio.router, prefix="/api")
app.include_router(ticket_studio.router, prefix="/api")
app.include_router(reference_repos.router, prefix="/api")
app.include_router(memory.router, prefix="/api")
app.include_router(usage.router, prefix="/api")
app.include_router(ci.router, prefix="/api")
app.include_router(parallel.router)
app.include_router(queue_lanes.router)
app.include_router(queue_management.router)
app.include_router(bulk_queue_operations.router)
app.include_router(queue_review.router)
app.include_router(analytics.router)
app.include_router(terminal.router)
app.include_router(queue_events.router)
app.include_router(chat_turn_events.router)
app.include_router(mcp.router, prefix="/mcp")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "loregarden", "mcp": "/mcp"}
