"""Asking a workspace-scoped chat turn's agent to stop.

Home chat and branch triage both mint a ticket-less ``AgentRun`` against the
workspace checkout, and both had to stop one the same way: find it by stage key,
ask it to cancel, and treat a refusal as nothing worse than a log line — the
pending message row is what actually unlocks the composer, so the run flag is
best-effort by design.

That was fifteen identical lines in two services. It lives here now because a
third surface asking the same question should not have to copy it a second time.

Deliberately *not* generalised past that: settling the pending row is each
surface's own, because the row is in each surface's own table, and Ticket Studio
has no run to cancel at all — its turn is an in-process model call. A helper
that pretended those were the same shape would take callbacks for every part
that differs and share only its name.
"""

from __future__ import annotations

import logging

from loregarden.models.domain import AgentRun
from loregarden.services.run_cancellation import request_cancel
from loregarden.services.run_concurrency import find_active_workspace_chat_run
from sqlmodel import Session

logger = logging.getLogger(__name__)


def request_chat_run_cancel(
    session: Session, workspace_id: str, *, stage_key: str
) -> AgentRun | None:
    """Ask the workspace's in-flight chat run to stop. Returns it, or None.

    Never raises: a run that will not take a cancel request is already past the
    point where one would help, and the caller's next move — settling the
    pending row — is what the operator is waiting on either way.
    """
    run = find_active_workspace_chat_run(session, workspace_id, stage_key=stage_key)
    if not run:
        return None
    try:
        return request_cancel(session, run)
    except ValueError:
        logger.warning("Chat run %s (stage %s) would not take a cancel request", run.id, stage_key)
        return run
