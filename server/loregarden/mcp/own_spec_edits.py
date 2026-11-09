"""A run's own spec edits are not a spec changing underneath it (`spec_snapshot`)."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any

from sqlmodel import Session

from loregarden.mcp.tool_ids import McpTool
from loregarden.services.spec_snapshot import own_edit_baseline, rebaseline_after_own_edit

#: Tools a run uses to write its own ticket's spec.
SPEC_WRITING_TOOLS = frozenset({McpTool.UPDATE_TICKET.value, McpTool.WRITE_DOCUMENT.value})


def keeps_own_spec_edits(dispatch: Callable[..., str]) -> Callable[..., str]:
    """Wrap the tool dispatcher: a spec-writing call from a running run moves that
    run's spec baseline over what it wrote, so its completion does not report its
    own edit as a change (`spec_snapshot.rebaseline_after_own_edit`)."""

    @wraps(dispatch)
    def wrapper(
        session: Session,
        name: str,
        arguments: dict[str, Any] | Any,
        *,
        orchestrated: bool = False,
        run_id: str = "",
    ) -> str:
        baseline = own_edit_baseline(session, run_id) if name in SPEC_WRITING_TOOLS else None
        result = dispatch(session, name, arguments, orchestrated=orchestrated, run_id=run_id)
        if baseline is not None:
            rebaseline_after_own_edit(session, *baseline)
        return result

    return wrapper
