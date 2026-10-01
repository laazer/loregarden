"""Re-export of `loregarden.testing.factories`, where the row builders now live.

They moved into the package so `loregarden sandbox seed` builds its
production-shaped database from the same factories the tests use. This module
keeps every existing `from tests.factories import …` working unchanged.
"""

from loregarden.testing.factories import (
    NO_REPO,
    make_agent_run,
    make_orchestration_run,
    make_ticket,
    make_workspace,
    make_workspace_ticket,
    operator_judgment_gate_payload,
    queued_run,
)
from sqlmodel import select

__all__ = [
    "NO_REPO",
    "make_agent_run",
    "make_orchestration_run",
    "make_ticket",
    "make_workspace",
    "make_workspace_ticket",
    "operator_judgment_gate_payload",
    "queued_run",
    "select",
]
