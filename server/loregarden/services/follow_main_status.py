"""What `follow_main` did on its last tick, recorded for the doctor.

Split from `follow_main` so the doctor can read it without importing the run
machinery that module reaches (`self_improve_restart` → `run_service` →
`doctor`, a cycle).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from loregarden.dev_supervisor import STATE_DIR
from loregarden.models.domain import FollowMainState

FOLLOW_FILE = "follow-main.json"
#: Outcomes where the server is not keeping up and a person has to act.
STUCK_STATES = frozenset(
    {
        FollowMainState.OFF_BASE,
        FollowMainState.DIVERGED,
        FollowMainState.REFUSED,
        FollowMainState.GIT_FAILED,
    }
)


@dataclass(frozen=True)
class FollowStatus:
    state: FollowMainState
    detail: str
    #: The checkout's head after this tick.
    head: str = ""
    at: str = ""


def read_follow_status(repo_root: Path) -> FollowStatus | None:
    """The last tick's outcome, or None when this checkout never followed main.

    Raises `ValueError` for a file that exists but does not parse.
    """
    path = repo_root / STATE_DIR / FOLLOW_FILE
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:  # silent-ok: no file means following never ran here; None says so
        return None
    try:
        data = json.loads(raw)
        return FollowStatus(
            state=FollowMainState(data["state"]),
            detail=data["detail"],
            head=data.get("head", ""),
            at=data.get("at", ""),
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{path} is not a follow-main status: {exc}") from exc
