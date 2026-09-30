"""The shapes a workflow stage can take.

`WorkflowStageDef.stage_type` is stored as a string (`agent | classify | gate |
parallel | verify`) and was compared bare at five sites before this enum
existed — which is what the organization gate is for. Its own module rather
than `enums.py`, which sits at the size cap.
"""

from __future__ import annotations

from enum import StrEnum


class StageType(StrEnum):
    """What runs at a stage: one agent, a classifier's pick, a gate's checks,
    a fan-out of agents, or a verifier."""

    AGENT = "agent"
    CLASSIFY = "classify"
    GATE = "gate"
    PARALLEL = "parallel"
    VERIFY = "verify"
