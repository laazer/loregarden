"""Shared prompt-block helpers.

One convention for every prompt this control plane builds. Stage runs and chat
rails both assemble a prompt from ordered blocks; keeping the block shape here
is what lets a chat surface render `## Agent Role` the same way a stage does,
rather than growing a second dialect of the same idea.
"""

from __future__ import annotations

#: Heading the agent's configured role body is rendered under, on every surface.
AGENT_ROLE_HEADING = "## Agent Role"

#: Ceiling on a role body pasted into a prompt. A role is identity, not a
#: document; past this the operator has put something in the wrong field and the
#: rest of the prompt is what gets squeezed out.
ROLE_BODY_CAP = 12000


def titled_block(title: str, body: str, *, cap: int = 0) -> list[str]:
    """A titled prompt block, or nothing when the body is empty.

    The leading blank line lives here so callers only declare order.
    """
    if not body:
        return []
    return ["", title, body[:cap] if cap else body]


def raw_block(body: str) -> list[str]:
    """An untitled prompt block that supplies its own headings."""
    return ["", body] if body else []
