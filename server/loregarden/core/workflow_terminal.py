"""What it means for a workflow stage to end the workflow.

Lifted out of `services.studio_routing` so the pin guard in `db.workflow_pin_guard`
can ask the same question without importing the services layer (which pulls in the
agent registry, and would close an import cycle back through `db.session`).
`studio_routing` re-exports these names, so every existing import site is unchanged.
"""

from __future__ import annotations

from loregarden.models.domain import WorkflowStageDef

TERMINAL_STAGE_KEY = "done"


def is_terminal_stage(stage: WorkflowStageDef) -> bool:
    """Whether reaching this stage ends the workflow.

    The `terminal` flag is authoritative; `key == "done"` remains a fallback so
    templates authored before the flag — including version-pinned instances —
    keep terminating.
    """
    return bool(stage.terminal) or stage.key == TERMINAL_STAGE_KEY


def find_terminal_stage(stages: list[WorkflowStageDef]) -> WorkflowStageDef | None:
    """First stage by order that ends the workflow, or None."""
    for stage in sorted(stages, key=lambda s: s.order):
        if is_terminal_stage(stage):
            return stage
    return None


def parse_stage_defs(raw: list[dict]) -> list[WorkflowStageDef]:
    """Validate a decoded `stages_json` payload into stage definitions.

    Snapshots and templates both store stages as JSON; modelling them before
    asking about `terminal` keeps the question typed rather than a dict probe.
    """
    return [WorkflowStageDef.model_validate(item) for item in raw]
