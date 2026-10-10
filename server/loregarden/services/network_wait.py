"""Park a stage while the provider is unreachable, instead of spending retries.

A run that dies because the model API cannot be reached says nothing about the
work, so it earns a transient retry (`stage_transient_retry`). But those retries
are immediate. On 2026-10-09 the provider was unreachable by 02:17, and three
tickets spent all their retries and their repair turns by 03:41 — 19 runs, each
failing `ENOTFOUND` in about three and a half minutes. Then they sat blocked
until a person requeued them at 12:22.

So before re-dispatching, the failure is checked against the network itself.
If the provider host is unreachable from here too, the stage is parked behind a
`NETWORK_WAIT` marker rather than retried — no budget spent, no repair turn —
and the reconcile pass resumes it once the host answers
(`network_wait_resume`). If the host answers but the CLI still could not reach
it, the ordinary retries run as before: a probe that passes cannot also park
the ticket, so the two cannot loop.
"""

from __future__ import annotations

import json
import logging
import socket

from loregarden.config import settings
from loregarden.models.domain import Artifact, StageBudgetArtifactKind
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

_KIND = StageBudgetArtifactKind.NETWORK_WAIT

#: How the CLIs say the provider could not be reached, lowercased. Name
#: resolution and connection failures only: a reachable API answering with an
#: error is a different failure, and not one the network coming back fixes.
_UNREACHABLE_SIGNATURES = (
    "enotfound",
    "eai_again",
    "econnrefused",
    "enetunreach",
    "ehostunreach",
    "can't reach the api server",
    "network is unreachable",
    "temporary failure in name resolution",
    "could not resolve host",
    "getaddrinfo",
)

PROBE_TIMEOUT_SECONDS = 3.0


def is_network_unreachable(stdout: str, stderr: str) -> bool:
    blob = f"{stdout}\n{stderr}".lower()
    return any(sig in blob for sig in _UNREACHABLE_SIGNATURES)


def network_waits_enabled() -> bool:
    return bool(settings.network_probe_host)


def provider_reachable() -> bool:
    """Whether the probe host resolves and accepts a TCP connection on 443."""
    try:
        with socket.create_connection(
            (settings.network_probe_host, 443), timeout=PROBE_TIMEOUT_SECONDS
        ):
            return True
    except OSError as exc:  # silent-ok: unreachable is this probe's answer, logged here
        logger.warning("Network probe to %s failed: %s", settings.network_probe_host, exc)
        return False


def network_wait_title(stage_key: str) -> str:
    return f"stage-network-wait:{stage_key}"


def network_wait_message(stage_key: str, detail: str) -> str:
    return (
        f"Stage '{stage_key}' could not reach the model provider, and neither can this "
        f"machine ({settings.network_probe_host}). It resumes automatically when the "
        f"network is back; nothing about the work was rejected. Cause: {detail[:500]}"
    )


def record_network_wait(session: Session, ticket_id: str, stage_key: str, *, run_id: str) -> None:
    session.add(
        Artifact(
            ticket_id=ticket_id,
            kind=_KIND,
            title=network_wait_title(stage_key),
            content_json=json.dumps({"stage_key": stage_key, "run_id": run_id}),
        )
    )
    session.commit()


def pending_network_waits(session: Session) -> list[Artifact]:
    return list(session.exec(select(Artifact).where(Artifact.kind == _KIND)).all())


def stage_of(marker: Artifact) -> str:
    return marker.title.removeprefix(network_wait_title(""))
