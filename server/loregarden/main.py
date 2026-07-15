import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session

from loregarden.api import (
    agents,
    analytics,
    branch_triage,
    bulk_queue_operations,
    ci,
    diff_review,
    editor,
    events,
    inbox,
    mcp,
    memory,
    orchestration,
    parallel,
    queue_management,
    queue_review,
    runs,
    studio,
    system,
    ticket_studio,
    tickets,
    usage,
    workflows,
    workspaces,
)
from loregarden.config import settings
from loregarden.core.auth import TokenAuthMiddleware
from loregarden.db.session import engine, init_db
from loregarden.services.run_service import (
    fail_interrupted_orchestration_runs,
    fail_interrupted_runs,
)
from loregarden.services.seed import seed_database

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
app.include_router(editor.router, prefix="/api")
app.include_router(branch_triage.router, prefix="/api")
app.include_router(system.router, prefix="/api")
app.include_router(inbox.router, prefix="/api")
app.include_router(events.router, prefix="/api")
app.include_router(runs.router, prefix="/api")
app.include_router(agents.router, prefix="/api")
app.include_router(workflows.router, prefix="/api")
app.include_router(orchestration.router, prefix="/api")
app.include_router(studio.router, prefix="/api")
app.include_router(ticket_studio.router, prefix="/api")
app.include_router(memory.router, prefix="/api")
app.include_router(usage.router, prefix="/api")
app.include_router(ci.router, prefix="/api")
app.include_router(parallel.router)
app.include_router(queue_management.router)
app.include_router(bulk_queue_operations.router)
app.include_router(queue_review.router)
app.include_router(analytics.router)
app.include_router(mcp.router, prefix="/mcp")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "loregarden", "mcp": "/mcp"}
