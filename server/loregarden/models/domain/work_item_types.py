"""Work-item hierarchy vocabulary.

Its own module rather than ``enums.py``, which sits at the size cap — same
reason ``stage_types`` and ``block_kinds`` left.
"""

from __future__ import annotations

from enum import Enum


class WorkItemType(str, Enum):
    """Hierarchy types — matches lllm-charge convention."""

    INITIATIVE = "initiative"
    MILESTONE = "milestone"
    FEATURE = "feature"
    CAPABILITY = "capability"
    TASK = "task"
    BUG = "bug"


VALID_HIERARCHY: dict[WorkItemType, list[WorkItemType]] = {
    WorkItemType.INITIATIVE: [WorkItemType.MILESTONE],
    WorkItemType.MILESTONE: [WorkItemType.FEATURE, WorkItemType.BUG],
    WorkItemType.FEATURE: [WorkItemType.CAPABILITY, WorkItemType.BUG],
    WorkItemType.CAPABILITY: [WorkItemType.TASK, WorkItemType.BUG],
    WorkItemType.TASK: [],
    WorkItemType.BUG: [],
}

# Explicit five-type set — never frozenset(WorkItemType), which would auto-include
# INITIATIVE and let non-executable roots enter workflow/orchestration paths.
WORKFLOW_WORK_ITEM_TYPES = frozenset(
    {
        WorkItemType.MILESTONE,
        WorkItemType.FEATURE,
        WorkItemType.CAPABILITY,
        WorkItemType.TASK,
        WorkItemType.BUG,
    }
)
