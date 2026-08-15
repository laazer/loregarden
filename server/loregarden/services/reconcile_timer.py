"""The clock behind the reconciliation pass.

Separate from `reconciliation` so the pass itself stays a plain synchronous
function anything can call — a test, a CLI, the startup lifespan — with no
asyncio in its way. This module owns only the cadence.

Two things it must never do: block the event loop, and die. The sweeps are
synchronous SQLite, so they run on a worker thread; and every iteration is
wrapped, because a loop that exits on the first bad row silently returns the
system to the cadence this ticket removed — repair only when someone restarts.
"""

from __future__ import annotations

import asyncio
import logging

from loregarden.config import settings
from loregarden.db.session import engine
from loregarden.services.reconciliation import reconcile_once
from sqlmodel import Session

logger = logging.getLogger(__name__)


def _sweep() -> list[str]:
    """One pass, in its own session, on a worker thread."""
    with Session(engine) as session:
        return reconcile_once(session)


async def run_reconcile_loop(interval_seconds: float) -> None:
    """Sweep every `interval_seconds` until cancelled.

    Sleeps first: startup has just run the same pass, and repeating it
    immediately would only cost a query per boot.
    """
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            failed = await asyncio.to_thread(_sweep)
            if failed:
                logger.warning("Reconciliation pass completed with failures: %s", ", ".join(failed))
        except asyncio.CancelledError:
            # Shutdown. Re-raised so the task actually ends rather than being
            # swallowed by the guard below and looping forever.
            raise
        except Exception:  # noqa: BLE001 — the loop outliving a bad pass is the point
            logger.exception("Reconciliation loop iteration failed; continuing")


def start_reconcile_loop(interval_seconds: float | None = None) -> asyncio.Task | None:
    """Start the loop, or return None when the interval disables it.

    A non-positive interval is the off switch — useful for a test process, or an
    operator who wants the old startup-only behaviour back without a code change.
    """
    if interval_seconds is None:
        interval_seconds = settings.reconcile_interval_seconds
    if interval_seconds <= 0:
        logger.info("Reconciliation timer disabled (interval=%s)", interval_seconds)
        return None
    logger.info("Reconciliation timer running every %.0fs", interval_seconds)
    return asyncio.create_task(run_reconcile_loop(interval_seconds))
