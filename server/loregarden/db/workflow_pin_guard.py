"""Refuse a workflow pin that freezes a ticket on a version with no exit.

A `WorkflowInstance` pins its ticket to one template version so an edit to the
template cannot mutate an in-flight ticket. That fidelity cuts both ways: a
version whose stage list has no terminal stage is reproduced just as faithfully,
and a ticket pinned to it reaches its last stage, passes, and has nowhere to
advance to — it re-loops instead of finishing. `studio-loregarden-tdd-v3`
version 9 was exactly that, and 103 live instances inherited it.

Template create/publish already require a terminal stage. Pinning did not, so
the failure surfaced a whole pipeline later, at a passing gate, as a routing
mystery rather than a rejected write. This closes that gap at the write.

The invariant is deliberately narrow: **a pin may not strip a terminal stage the
template has.** A template that genuinely has no terminal stage is a separate,
already-tolerated shape — `subtree_auto_run` finalizes aggregator parents on
exactly that path — and is create/publish's business, not the pin's. What is
never legitimate is a ticket frozen on an older snapshot whose exit the template
has since grown.

Deliberately *not* widened to skill names. Migration 0089 repins instances
frozen on a version naming an unregistered skill, and the symmetric guard was
considered here and rejected on three counts. The failure it would catch is
already loud and local — `SkillNotFoundError` names the skill and the searched
directories at dispatch — where a missing terminal stage is silent and surfaces
a pipeline later as a routing mystery. The property is not stable at write time
either: the skill registry is seeded lazily from `agent_context/skills`, so on a
cold database the guard would reject legitimate pins, and a skill added or
deleted after the pin flips the answer without any write to react to. A guard
whose verdict can go stale cannot replace the dispatch-time check, so it would
buy a second, less reliable copy of an error that already exists. The invariant
here stays exactly one thing: a pin may not strip an exit the template has.

Registered on the `WorkflowInstance` mapper rather than at the four call sites
that write a pin (`ticket_service`, `workflow_service` ×2, `orchestration`), so
a fifth writer added later inherits it and no caller can route around it.
"""

from __future__ import annotations

import json

from loregarden.core.workflow_terminal import find_terminal_stage, parse_stage_defs
from loregarden.models.domain import WorkflowInstance, WorkflowStageDef
from sqlalchemy import event, inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Mapper

#: Attributes whose change makes a row a *new* pin. Any other update to a
#: workflow instance — stage cursor, stage map — leaves the pin alone and must
#: not pay for two queries.
_PIN_ATTRS = ("template_id", "template_version")


class WorkflowPinWithoutTerminalStageError(ValueError):
    """A workflow instance was pinned to a version that cannot finish."""


def _stages_from_json(raw: str | None) -> list[WorkflowStageDef]:
    return parse_stage_defs(json.loads(raw or "[]"))


def _pinned_version_has_no_terminal(connection: Connection, template_id: str, version: int) -> bool:
    """Whether pinning `template_id` at `version` would strip its exit.

    False whenever the answer is not knowable *and* harmless: an unknown
    template, a template with no terminal stage of its own, a pin at head, or a
    missing snapshot — the last because `get_template_stages_at_version` falls
    back to the live template there, which we have already checked.
    """
    template = (
        connection.execute(
            text("SELECT version, stages_json FROM workflow_templates WHERE id=:id"),
            {"id": template_id},
        )
        .mappings()
        .fetchone()
    )
    if template is None:
        return False
    if find_terminal_stage(_stages_from_json(template["stages_json"])) is None:
        return False
    if version == template["version"]:
        return False

    snapshot = connection.execute(
        text(
            "SELECT snapshot_json FROM workflow_template_versions "
            "WHERE template_id=:id AND version=:v"
        ),
        {"id": template_id, "v": version},
    ).scalar()
    if snapshot is None:
        return False
    pinned = json.loads(snapshot or "{}").get("stages_json")
    return find_terminal_stage(_stages_from_json(pinned)) is None


def _check_pin(target: WorkflowInstance, connection: Connection) -> None:
    version = target.template_version
    if version is None:
        # Unpinned rows resolve against the live template, which create/publish
        # already guards. Nothing is frozen, so nothing can go stale.
        return
    if not _pinned_version_has_no_terminal(connection, target.template_id, version):
        return
    raise WorkflowPinWithoutTerminalStageError(
        f"Refusing to pin workflow instance for ticket {target.ticket_id} to template "
        f"{target.template_id} version {version}: that version has no terminal stage, so a "
        "passing final stage would have nowhere to advance to. The live template does have "
        "one — pin the ticket to a version that kept it, or leave template_version unset to "
        "follow the template."
    )


@event.listens_for(WorkflowInstance, "before_insert")
def _guard_pin_on_insert(_mapper: Mapper, connection: Connection, target: WorkflowInstance) -> None:
    _check_pin(target, connection)


@event.listens_for(WorkflowInstance, "before_update")
def _guard_pin_on_update(_mapper: Mapper, connection: Connection, target: WorkflowInstance) -> None:
    state = inspect(target)
    if not any(state.attrs[name].history.has_changes() for name in _PIN_ATTRS):
        return
    _check_pin(target, connection)
