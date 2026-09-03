"""Shared prompt-block helpers.

One convention for every prompt this control plane builds. Stage runs and chat
rails both assemble a prompt from ordered blocks; keeping the block shape here
is what lets a chat surface render `## Agent Role` the same way a stage does,
rather than growing a second dialect of the same idea.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

#: Heading the agent's configured role body is rendered under, on every surface.
AGENT_ROLE_HEADING = "## Agent Role"

#: Ceiling on a role body pasted into a prompt. A role is identity, not a
#: document; past this the operator has put something in the wrong field and the
#: rest of the prompt is what gets squeezed out.
ROLE_BODY_CAP = 12000


@dataclass(frozen=True)
class PromptTruncation:
    """A prompt block that did not fit, and by how much.

    Collected rather than raised: a prompt that is slightly too long should
    still run. What must not happen is it running *silently*, which is what
    `body[:cap]` did — the gatekeeper agent once lost its entire approve/reject
    contract mid-word at "## Cor" and nothing anywhere said so
    (lg-workflow-integrity-91).
    """

    title: str
    cap: int
    original_length: int
    kept_length: int

    @property
    def dropped(self) -> int:
        return self.original_length - self.kept_length

    def describe(self) -> str:
        return (
            f"{self.title}: {self.original_length} chars exceeds its {self.cap} cap; "
            f"kept {self.kept_length}, dropped {self.dropped}"
        )


def _cut_at_section(body: str, cap: int) -> str:
    """`body` shortened to at most `cap`, at a section boundary where possible.

    A mid-word cut is how this defect stayed invisible: the prompt still looked
    like prose, so nothing read as broken. Cutting at the last markdown heading
    before the cap means the agent receives whole sections and the seam is
    legible — and a body whose first section already exceeds the cap falls back
    to a hard cut, because half a section is still better than none.
    """
    if len(body) <= cap:
        return body
    window = body[:cap]
    boundary = max(window.rfind("\n## "), window.rfind("\n# "))
    if boundary > 0:
        return window[:boundary]
    return window


def titled_block(
    title: str,
    body: str,
    *,
    cap: int = 0,
    truncations: list[PromptTruncation] | None = None,
) -> list[str]:
    """A titled prompt block, or nothing when the body is empty.

    The leading blank line lives here so callers only declare order.

    Pass `truncations` to be told when `cap` actually bites. Without it a cut is
    still made at a section boundary and still logged, but the caller has no way
    to report it — which is why the executor passes a list.
    """
    if not body:
        return []
    if not cap or len(body) <= cap:
        return ["", title, body]

    kept = _cut_at_section(body, cap)
    record = PromptTruncation(
        title=title, cap=cap, original_length=len(body), kept_length=len(kept)
    )
    logger.warning("Prompt block truncated — %s", record.describe())
    if truncations is not None:
        truncations.append(record)
    return ["", title, kept]


def raw_block(body: str) -> list[str]:
    """An untitled prompt block that supplies its own headings."""
    return ["", body] if body else []
