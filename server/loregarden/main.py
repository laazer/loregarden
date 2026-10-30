import asyncio
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlmodel import Session

from loregarden.agents.mcp_context import unverified_api_base_url
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
    docker_capacity,
    editor,
    events,
    gate_presets,
    github_issues,
    inbox,
    initiatives,
    instance_templates,
    living_map,
    local_instances,
    machine_disk,
    mcp,
    mcp_servers,
    memory,
    orchestration,
    parallel,
    queue_events,
    queue_lanes,
    queue_management,
    queue_review,
    reference_library,
    reference_repos,
    runs,
    stage_fanout,
    studio,
    system,
    terminal,
    ticket_autopilot,
    ticket_handoff,
    ticket_pull_request,
    ticket_studio,
    tickets,
    ui_action_events,
    usage,
    views,
    workflow_monitor,
    workflows,
    workspace_dashboard,
    workspace_integration,
    workspaces,
)
from loregarden.config import settings
from loregarden.console_logging import configure_console_logging
from loregarden.core.auth import TokenAuthMiddleware
from loregarden.db.session import engine, init_db
from loregarden.services.baxter_chat_run_service import fail_interrupted_baxter_chat_turns
from loregarden.services.branch_triage_run_service import fail_interrupted_branch_triage_turns
from loregarden.services.btw_run_service import fail_interrupted_asides
from loregarden.services.chat_branch_sweep import sweep_all_chat_branches
from loregarden.services.chat_thinking import clear_orphaned_chat_turn_thinking
from loregarden.services.chat_wait_watch import resume_chat_waits
from loregarden.services.ci_poll_scheduler import start_ci_poll_loop
from loregarden.services.drain import begin_drain, end_drain, wait_for_quiescence
from loregarden.services.github_push_on_edit import start_push_worker
from loregarden.services.github_sync_scheduler import start_github_sync_loop
from loregarden.services.initiative_autopilot import start_autopilot_loop
from loregarden.services.initiative_planner_service import fail_interrupted_planner_turns
from loregarden.services.local_instances import register_main
from loregarden.services.orchestration_recovery import resume_interrupted_orchestrations
from loregarden.services.reconcile_timer import start_reconcile_loop
from loregarden.services.reconciliation import reconcile_once
from loregarden.services.reference_survey.feeder import start_survey_loop
from loregarden.services.run_reattach import reattach_surviving_runs
from loregarden.services.run_service import (
    fail_interrupted_orchestration_runs,
    fail_interrupted_runs,
    settle_stranded_stages,
)
from loregarden.services.sandbox_endpoint import (
    INSTANCE_ID,
    SandboxMcpUrlError,
    reset_verification,
    verify_this_instance,
)
from loregarden.services.seed import seed_database
from loregarden.services.ticket_studio_run_service import fail_interrupted_studio_turns
from loregarden.services.triage_run_service import fail_interrupted_triage_turns
from loregarden.services.worktree_lifecycle import reconcile_worktrees

logger = logging.getLogger(__name__)


def _recover_previous_boot(session: Session) -> None:
    """Settle what the last process left running, and resume what it was doing."""
    # Before the reapers, and before anything reads a run's status: since
    # 317 detaches the agent, a run at RUNNING here may have a live process
    # behind it. Adopting it keeps its row and starts renewing its lease in
    # this process; the reapers below skip exactly what this adopted (470).
    reattach_surviving_runs(session)
    fail_interrupted_runs(session)
    fail_interrupted_orchestration_runs(session)
    fail_interrupted_triage_turns(session)
    fail_interrupted_branch_triage_turns(session)
    fail_interrupted_baxter_chat_turns(session)
    fail_interrupted_studio_turns(session)
    fail_interrupted_planner_turns(session)
    # An aside has no run row of its own, so nothing above would ever reach it.
    fail_interrupted_asides(session)
    # After the chat reapers: origin turns that were RUNNING are now settled, so
    # a GONE child can continue without double-inflight. UNKNOWN at boot writes
    # the Boot UNKNOWN system message; ALIVE keeps watching.
    resume_chat_waits(session)
    # The turns those four just settled are exactly the ones whose live
    # thinking rows outlived them; nothing is left watching for that text.
    clear_orphaned_chat_turn_thinking(session)
    # Last: the reaps above settle stages as they complete their runs, so this
    # only sees stages no run will ever account for.
    settle_stranded_stages(session)
    # The trees those dead runs were working in. After the reaps, so a
    # ticket the crash left mid-stage counts as unfinished and keeps its
    # worktree for the resume below. Startup only: it is the one sweep that
    # deletes, and boot is the only moment nothing is in flight.
    reconcile_worktrees(session)
    # The chat rail's equivalent, and startup-only for the same reason. It
    # walks the trees `reconcile_worktrees` cannot judge — a chat thread has
    # no ticket whose state could say it is finished — and removes only the
    # branches whose content is already in base, where the ref was keeping
    # nothing reachable. Opt-in per workspace; see `chat_branch_sweep`.
    sweep_all_chat_branches(session)
    # Lanes and parents, from the same pass the timer runs. After the reaps,
    # so the runs they just failed count as finished and the slots they held
    # come back rather than staying claimed by a run this process will never
    # hear from again — and so every child has reached the state it will
    # actually be in before its parents are summarised from it.
    reconcile_once(session)
    # Resume only after every orphan row and stranded stage has been made
    # durable. Recovery adds a fresh run; the failed rows remain the audit trail.
    resume_interrupted_orchestrations(session)


def _start_sandbox_endpoint_check() -> asyncio.Task[None]:
    """Prove the MCP URL this sandbox hands its agents reaches this process.

    Runs once the server is serving (the task waits for it); until it passes,
    sandbox agent turns refuse to start — see `services.sandbox_endpoint`.
    """
    reset_verification()

    async def check() -> None:
        try:
            base = unverified_api_base_url()
        except SandboxMcpUrlError:
            # The resolver itself refused (no URL and no port): agent turns raise
            # the same error, with the fix in it, when they ask.
            logger.exception("sandbox: no MCP endpoint of its own; agent turns are refused")
            return
        async with httpx.AsyncClient() as client:
            await verify_this_instance(base, client=client)

    return asyncio.create_task(check(), name="sandbox-endpoint-check")


def _start_background_loops() -> list[asyncio.Task]:
    """The clocks this server runs on. None of them in a sandbox.

    A sandbox runs on a snapshot of main's database: reconciling it would act on
    main's runs, syncing it would push its stale tickets to GitHub over real
    edits, the autopilot would queue real agent runs for its tickets, and the CI
    poll would reopen its done tickets for fixes against work that has moved on.
    """
    if settings.sandbox:
        return []
    # Repair on a clock from here, not only at the next boot and not only while
    # someone has the dashboard open.
    started = [
        start_reconcile_loop(),
        start_github_sync_loop(),
        start_push_worker(),
        start_autopilot_loop(),
        start_ci_poll_loop(),
        start_survey_loop(),
    ]
    return [task for task in started if task is not None]


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_console_logging()
    if not settings.api_token:
        logger.warning(
            "LOREGARDEN_API_TOKEN is not set — the API (which writes files and "
            "runs agents) is reachable by any local process. Set a token to "
            "require authentication on shared machines."
        )
    # A fresh app is never draining. Matters because a test process builds and
    # tears down many apps in one interpreter, and the flag below outlives the
    # app that set it.
    end_drain()
    init_db()
    with Session(engine) as session:
        seed_database(session)
        if settings.sandbox:
            # Everything the recovery pass acts on — run processes, worktrees,
            # orchestrations — is main's, described by a snapshot of main's
            # database. See `Settings.sandbox`.
            logger.warning(
                "sandbox instance: startup recovery and the reconcile timer are off; "
                "this database is a snapshot and its runs belong to another server"
            )
        else:
            _recover_previous_boot(session)

    background_loops = _start_background_loops()
    # After boot, so nothing finds this server before it can answer.
    advertised = register_main()
    endpoint_check = _start_sandbox_endpoint_check() if settings.sandbox else None
    try:
        yield
    finally:
        if endpoint_check is not None:
            endpoint_check.cancel()
        if advertised is not None:
            advertised.release()
        # Draining before anything else is torn down: the point is to stop
        # starting work and give what is running a bounded chance to land.
        # Whatever misses the window is settled by the interruption path on the
        # next boot, exactly as it is today — drain improves the good case and
        # must not change the bad one.
        begin_drain()
        if settings.drain_timeout_seconds > 0:
            await asyncio.to_thread(
                wait_for_quiescence, timeout_seconds=settings.drain_timeout_seconds
            )
        for task in background_loops:
            task.cancel()
        # The drain is over, so stop advertising it. A real process exits here
        # and nobody reads the flag again; a test process keeps going, and a
        # flag left set refuses work in every test that follows — which is
        # exactly what it did.
        end_drain()


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


@app.exception_handler(RequestValidationError)
async def validation_error_response(request: Request, exc: RequestValidationError) -> JSONResponse:
    """FastAPI's 422, minus the case where building it is itself a 500.

    Each reported error echoes the ``input`` that failed, and ``json.loads``
    accepts the ``Infinity``/``NaN`` literals that ``json.dumps`` refuses to
    write back: send one to any field that rejects it — a bounded float, or an
    ``int`` — and the 422 body cannot be rendered, so the request that should
    have been a 4xx becomes a 500.

    The default body is built first and returned untouched whenever it encodes,
    which is every request that does not carry a non-finite number, so no
    existing endpoint's response changes by so much as a byte. Only the payload
    that cannot be written back loses its ``input`` echo — the field path,
    message and type, which are what a client acts on, all survive.
    """
    errors = jsonable_encoder(exc.errors())
    try:
        return JSONResponse(status_code=422, content={"detail": errors})
    except ValueError:
        logger.warning(
            "422 body held a non-encodable value; dropping the `input` echo",
            exc_info=True,
        )
        stripped = [
            {key: value for key, value in error.items() if key != "input"} for error in errors
        ]
        return JSONResponse(status_code=422, content={"detail": stripped})


app.include_router(tickets.router, prefix="/api")
app.include_router(ticket_pull_request.router, prefix="/api")
app.include_router(ticket_autopilot.router, prefix="/api")
app.include_router(ticket_handoff.router, prefix="/api")
app.include_router(initiatives.router, prefix="/api")
app.include_router(living_map.router, prefix="/api")
app.include_router(diff_review.router, prefix="/api")
app.include_router(workspaces.router, prefix="/api")
app.include_router(workspace_dashboard.router, prefix="/api")
app.include_router(calendar_events.router, prefix="/api")
app.include_router(editor.router, prefix="/api")
app.include_router(branch_triage.router, prefix="/api")
app.include_router(baxter_chat.router, prefix="/api")
app.include_router(baxter_chat.global_router, prefix="/api")
app.include_router(composer_notes.router, prefix="/api")
app.include_router(views.router, prefix="/api")
app.include_router(system.router, prefix="/api")
app.include_router(inbox.router, prefix="/api")
app.include_router(events.router, prefix="/api")
app.include_router(docker_capacity.router, prefix="/api")
app.include_router(machine_disk.router, prefix="/api")
app.include_router(runs.router, prefix="/api")
app.include_router(agents.router, prefix="/api")
app.include_router(mcp_servers.router, prefix="/api")
app.include_router(local_instances.router, prefix="/api/instances")
app.include_router(instance_templates.router, prefix="/api")
app.include_router(workspace_integration.router, prefix="/api")
app.include_router(gate_presets.router, prefix="/api")
app.include_router(workflows.router, prefix="/api")
app.include_router(orchestration.router, prefix="/api")
app.include_router(workflow_monitor.router, prefix="/api")
app.include_router(stage_fanout.router)
app.include_router(studio.router, prefix="/api")
app.include_router(ticket_studio.router, prefix="/api")
app.include_router(reference_repos.router, prefix="/api")
app.include_router(memory.router, prefix="/api")
app.include_router(reference_library.router, prefix="/api")
app.include_router(usage.router, prefix="/api")
app.include_router(ci.router, prefix="/api")
app.include_router(github_issues.router, prefix="/api")
app.include_router(parallel.router)
app.include_router(queue_lanes.router)
app.include_router(queue_management.router)
app.include_router(bulk_queue_operations.router)
app.include_router(queue_review.router)
app.include_router(analytics.router)
app.include_router(terminal.router)
app.include_router(queue_events.router)
app.include_router(chat_turn_events.router)
app.include_router(ui_action_events.router)
app.include_router(mcp.router, prefix="/mcp")


@app.get("/health")
def health() -> dict:
    # `instance_id` lets a sandbox prove the URL it hands its agents is itself.
    return {"status": "ok", "service": "loregarden", "mcp": "/mcp", "instance_id": INSTANCE_ID}
