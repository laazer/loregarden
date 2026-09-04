"""Compare a Studio draft to the template it publishes to, before the publish.

`studio_workflows` holds the editable draft; `workflow_templates` holds what the
orchestrator actually runs. `publish_workflow` overwrites the second from the
first, and nothing has kept them in step. The `loregarden-tdd-v3` draft was once
9 stages against a live 12-stage template — pressing publish would have dropped
`plan-synthesis`, `verify` and the terminal stage that migration 0045 exists to
guarantee. It was repaired by hand; nothing prevented a recurrence.

Two things this module is deliberate about.

**Not every drift is a count difference.** Migration 0108 grouped v2's two
implementation stages in the template; had it not also written the draft, the
drift would have been equal stage counts, equal keys, equal transitions, and one
differing field *inside* a stage — invisible to a comparison that only counts.
So stages are compared field by field.

**The join is the FK, not the slug.** `publish_workflow` names the template
`f"studio-{draft.slug}"`, so the two slugs are never equal and joining on
equality reports every draft as never-published. `published_template_id` is
authoritative, survives a rename, and distinguishes "never published" (NULL)
from "published and drifted".
"""

from __future__ import annotations

import json

from loregarden.models.domain import (
    StudioWorkflow,
    StudioWorkflowStage,
    Ticket,
    TicketState,
    WorkflowInstance,
    WorkflowTemplate,
)
from loregarden.models.domain.studio_drift import (
    StageFieldDrift,
    StrandedInstances,
    WorkflowDrift,
)
from sqlmodel import Session, col, select

#: Fields excluded from the per-stage comparison. `order` is derived from list
#: position on publish, so a difference here is noise rather than drift.
_UNCOMPARED_STAGE_FIELDS = frozenset({"order"})


class StageRemovalNeedsConfirmation(ValueError):
    """A publish would strand live tickets on a stage it removes.

    Distinct from the other publish rejections, which are author errors with no
    valid override. This one is a legitimate edit the caller has to acknowledge,
    so the API answers 409 rather than 400 and the message names the cost.
    """


def _draft_stages(workflow: StudioWorkflow) -> dict[str, dict]:
    raw = json.loads(workflow.stages_json or "[]")
    return {stage.get("key", ""): stage for stage in raw}


def _template_stages(template: WorkflowTemplate) -> dict[str, dict]:
    raw = json.loads(template.stages_json or "[]")
    return {stage.get("key", ""): stage for stage in raw}


def _normalised(stage: dict) -> dict:
    """A stage dict with every model field present, so a stored row written
    before a field existed compares equal to one written after it.

    Without this, adding a field to the model reports every unpublished draft as
    drifted on the day of the migration — a false positive that would train
    people to ignore the check.
    """
    parsed = StudioWorkflowStage.model_validate(stage)
    return {
        name: value
        for name, value in parsed.model_dump().items()
        if name not in _UNCOMPARED_STAGE_FIELDS
    }


def _field_drift(draft: dict, template: dict) -> list[StageFieldDrift]:
    left, right = _normalised(draft), _normalised(template)
    # The UNION of keys, not the draft's. Iterating one side only makes a field
    # the other side has and this one lacks invisible — and since `_normalised`
    # is what guarantees both sides are complete, that would have silently made
    # the comparison depend on it rather than merely tidied by it.
    return [
        StageFieldDrift(field=name, draft=repr(left.get(name)), template=repr(right.get(name)))
        for name in sorted(set(left) | set(right))
        if left.get(name) != right.get(name)
    ]


def stranded_instances(
    session: Session, template: WorkflowTemplate, removed_keys: list[str]
) -> StrandedInstances:
    """Live workflow instances that would lose the stage they are sitting on.

    Only instances that read the LIVE template are at risk. An instance pinned to
    a template version resolves through its snapshot and is unaffected — but
    `get_template_stages_at_version` falls back to the live template when
    `template_version` is NULL, and 152 of 790 rows were NULL when this was
    written (75 of them on an open ticket). So "instances are version-pinned" is
    not a defence, and this counts the unpinned ones specifically.
    """
    if not removed_keys:
        return StrandedInstances(count=0, stage_keys=[], ticket_ids=[])

    rows = session.exec(
        select(WorkflowInstance, Ticket)
        .join(Ticket, col(Ticket.id) == col(WorkflowInstance.ticket_id))
        .where(
            col(WorkflowInstance.template_id) == template.id,
            col(WorkflowInstance.template_version).is_(None),
            col(Ticket.state).not_in([TicketState.DONE, TicketState.WONT_DO]),
            col(WorkflowInstance.current_stage_key).in_(removed_keys),
        )
    ).all()
    return StrandedInstances(
        count=len(rows),
        stage_keys=sorted({instance.current_stage_key for instance, _ in rows}),
        ticket_ids=sorted(ticket.external_id for _, ticket in rows),
    )


def detect_drift(session: Session, workflow: StudioWorkflow) -> WorkflowDrift:
    """Compare one draft to its published template."""
    template = (
        session.get(WorkflowTemplate, workflow.published_template_id)
        if workflow.published_template_id
        else None
    )
    if template is None:
        return WorkflowDrift(
            slug=workflow.slug,
            published_template_slug="",
            published=False,
            drifted=False,
        )

    draft_stages = _draft_stages(workflow)
    template_stages = _template_stages(template)
    added = sorted(set(draft_stages) - set(template_stages))
    removed = sorted(set(template_stages) - set(draft_stages))
    changed = {
        key: drift
        for key in sorted(set(draft_stages) & set(template_stages))
        if (drift := _field_drift(draft_stages[key], template_stages[key]))
    }
    draft_transitions = len(json.loads(workflow.transitions_json or "[]"))
    template_transitions = len(json.loads(template.transitions_json or "[]"))

    return WorkflowDrift(
        slug=workflow.slug,
        published_template_slug=template.slug,
        published=True,
        drifted=bool(added or removed or changed or draft_transitions != template_transitions),
        stages_added=added,
        stages_removed=removed,
        stages_changed=changed,
        draft_transition_count=draft_transitions,
        template_transition_count=template_transitions,
        template_version=template.version,
        stranded=stranded_instances(session, template, removed),
    )


def detect_all_drift(session: Session) -> list[WorkflowDrift]:
    """Every draft, drifted or not, in slug order."""
    drafts = session.exec(select(StudioWorkflow).order_by(col(StudioWorkflow.slug))).all()
    return [detect_drift(session, draft) for draft in drafts]
