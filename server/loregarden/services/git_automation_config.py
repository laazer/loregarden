"""What git automation applies to one ticket.

Two layers: the workspace's orchestration profile sets the policy, and a
ticket may override individual keys. The override stores only the keys that
differ, so a ticket that opted out of auto-merge last week still picks up a
workspace that has since turned on PRs.

The distinction that makes this worth a module: an empty override means
"inherit", which is not the same as an override where every flag is false.
Storing the full config per ticket would freeze that ticket's policy at the
moment someone touched one switch.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from loregarden.models.domain import Ticket, Workspace
from loregarden.services.orchestration_profile import (
    GitAutomationConfig,
    resolve_orchestration_profile,
)

logger = logging.getLogger(__name__)

#: Keys a ticket is allowed to override. Anything else in the stored JSON is
#: ignored rather than trusted — the column is written by the API and by
#: agents, and an unknown key means a stale client, not a new feature.
OVERRIDABLE = frozenset(GitAutomationConfig.model_fields)


def parse_override(raw: str) -> dict[str, Any]:
    """The ticket's override, as a dict of known keys only."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Ignoring unparseable ticket git override: %r", raw[:120])
        return {}
    if not isinstance(parsed, dict):
        logger.warning("Ignoring non-object ticket git override: %r", raw[:120])
        return {}
    return {key: value for key, value in parsed.items() if key in OVERRIDABLE}


def serialize_override(override: dict[str, Any]) -> str:
    """Store only known keys, and store nothing at all for an empty override."""
    cleaned = {key: value for key, value in override.items() if key in OVERRIDABLE}
    return json.dumps(cleaned, sort_keys=True) if cleaned else ""


def resolve_git_automation(
    workspace: Workspace, ticket: Ticket | None = None
) -> GitAutomationConfig:
    """The effective policy for this ticket in this workspace."""
    base = resolve_orchestration_profile(workspace).git
    if ticket is None:
        return base

    override = parse_override(ticket.git_automation_json)
    if not override:
        return base

    return GitAutomationConfig.model_validate({**base.model_dump(mode="json"), **override})


def enabled_steps(config: GitAutomationConfig) -> list[str]:
    """The pipeline steps that will actually run, in order.

    Each step depends on the one before it: pushing an uncommitted tree does
    nothing, a PR needs a pushed branch, and auto-merge needs a PR. So the
    sequence stops at the first switch that is off rather than skipping it —
    a config with commit off and open_pr on runs nothing, which is what the
    user asked for even if it is not what they meant.
    """
    steps: list[str] = []
    for name, on in (
        ("commit", config.commit),
        ("push", config.push),
        ("open_pr", config.open_pr),
        ("auto_merge", config.auto_merge),
    ):
        if not on:
            break
        steps.append(name)
    return steps
