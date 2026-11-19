"""Every failed agent run says why it failed, written at one point.

`AgentRun.failure_kind` arrived (migration `20261013_agent_run_failure_kind`)
with one writer, the integration merge turn, and nothing making the other
thirteen places that fail a run fill it in. Two thirds of failed runs then
carried no kind, which hid how often runs die of the harness (a usage limit, a
checkout that could not take its target, a reaped parent) rather than of the
work. `set_run_status` is the one place a run's status is set to FAILED, and it
always sets the kind with it; `test_run_failure_kind` fails any other writer,
and the `run_failure_kind` invariant repairs and reports a row that slips past.

The vocabulary is `BlockKind`, classified by `block_classification` plus the
few messages only a run's failure carries (a checkout before the agent
started, a CLI that could not reach its provider). Imports nothing from the
orchestration modules, so those can import it.
"""

from __future__ import annotations

import re

from loregarden.models.domain import AgentRun, BlockKind, RunStatus
from loregarden.services.block_classification import classify_block_message, is_checkout_conflict

#: How a stage's run reports the checkout that failed before the agent started
#: (`agents/executors/cli.py`, `services/parallel_stage.py`). The prefix is
#: what tells a harness failure from the agent's own.
CHECKOUT_FAILED_PREFIX = "Failed to checkout branch: "

#: An integration branch refreshed from its target over conflicts
#: (`'integration/x' cannot take 'main': merge conflicts in …`). The ticket
#: branch's own wording is `is_checkout_conflict`; this one names no "before
#: starting", so block classification does not read it, and runs must.
_TARGET_CONFLICT = re.compile(r"cannot take '[^']+': merge conflicts", re.IGNORECASE)

#: A CLI that could not authenticate. A person's hands: a login to refresh.
_CREDENTIAL_SIGNATURES = (
    "saved login in the macos keychain",
    "keychain item already exists",
)

#: Failures of the CLI or its provider, before or instead of the work: the
#: run never got to try. Lowercase; matched as substrings.
_RUN_HARNESS_SIGNATURES = (
    "api error: 5",  # a provider 5xx: "API Error: 529 Overloaded"
    "retriableerror",
    "interrupted by a server restart",
    "invalid mcp configuration",
    "unknown agent:",
    "no prompt provided",
    "bun could not find a file",
    "killed as a presumed hang",
)


def classify_run_failure(text: str) -> BlockKind:
    """The kind a failed run's own failure text supports."""
    message = text or ""
    lowered = message.lower()
    if is_checkout_conflict(message) or _TARGET_CONFLICT.search(message):
        return BlockKind.CONFLICT
    if lowered.startswith(CHECKOUT_FAILED_PREFIX.lower()):
        return BlockKind.HARNESS
    if any(sig in lowered for sig in _CREDENTIAL_SIGNATURES):
        return BlockKind.HUMAN_ACTION
    if any(sig in lowered for sig in _RUN_HARNESS_SIGNATURES):
        return BlockKind.HARNESS
    return classify_block_message(message)


def set_run_status(
    run: AgentRun,
    status: RunStatus,
    *,
    failure_text: str = "",
    failure_kind: BlockKind | None = None,
) -> None:
    """Set `run.status`, and with a failure, the kind it failed of.

    `failure_kind` is for a writer that knows better than the text (the merge
    turn's resolution names its own); otherwise `failure_text`, the reason the
    run will carry in `stderr`, decides. Any other status clears the kind, so a
    row never reads as both succeeded and failed of something.

    The only writer of a FAILED status: `test_run_failure_kind` enumerates the
    rest of the tree and fails one that assigns it directly.
    """
    run.status = status
    if status is RunStatus.FAILED:
        run.failure_kind = failure_kind or classify_run_failure(failure_text)
    else:
        run.failure_kind = None
