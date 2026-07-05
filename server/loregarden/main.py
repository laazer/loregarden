from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session

from loregarden.api import agents, cycles, events, export, inbox, mcp, orchestration, runs, studio, tickets, workflows, workspaces
from loregarden.config import settings
from loregarden.db.session import engine, init_db
from loregarden.services.run_service import fail_interrupted_runs
from loregarden.services.seed import seed_database


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    with Session(engine) as session:
        seed_database(session)
        fail_interrupted_runs(session)
    yield


app = FastAPI(title="Loregarden Control Plane", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(tickets.router, prefix="/api")
app.include_router(cycles.router, prefix="/api")
app.include_router(workspaces.router, prefix="/api")
app.include_router(inbox.router, prefix="/api")
app.include_router(events.router, prefix="/api")
app.include_router(runs.router, prefix="/api")
app.include_router(agents.router, prefix="/api")
app.include_router(workflows.router, prefix="/api")
app.include_router(orchestration.router, prefix="/api")
app.include_router(studio.router, prefix="/api")
app.include_router(export.router, prefix="/api")
app.include_router(mcp.router, prefix="/mcp")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "loregarden", "mcp": "/mcp"}
