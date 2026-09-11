"""`extended-tdd` had a review stage and nowhere to send a rejection.

`lg-workflow-integrity-677`, found by the integration review of feature -92.
lg-workflow-integrity-94 gave the studio templates reject transitions so a typed
reject verdict (-95) had somewhere to route; extended-tdd was outside its scope
and kept transitions carrying only from/to.

WHY IT IS NOT MERELY COSMETIC, which the ticket did not say. On a rejection
`StateMachine.resolve_transition_target` matches `when == "reject"` and nothing
else — an unconditioned edge is never a fallback for it. So the reject fell
through to `apply_stage_route`'s previous-stage fallback and landed on
`static_qa`, the stage before review. That re-runs static analysis and flows
straight back to review with the code unchanged: a loop in which the implementer
is never asked to fix anything.

Latent rather than live: extended-tdd has 0 workflow instances.
"""

from __future__ import annotations

from loregarden.core.state_machine import StateMachine
from loregarden.core.workflow_loader import get_template_stages, sync_workflow_templates
from loregarden.models.domain import WorkflowTemplate
from loregarden.services.workflow_routing import normalize_transitions_for_api
from sqlmodel import Session, select

SLUG = "extended-tdd"
REVIEW = "review"


def _template(session: Session) -> WorkflowTemplate:
    sync_workflow_templates(session)
    template = session.exec(select(WorkflowTemplate).where(WorkflowTemplate.slug == SLUG)).first()
    assert template is not None, f"{SLUG} template missing"
    return template


def _transitions(session: Session) -> list[dict]:
    """Exactly what the orchestrator resolves — `_resolve_transitions` calls this,
    so the tests route through the same parse production does."""
    return StateMachine.parse_transitions(_template(session).transitions_json)


def test_a_rejected_review_routes_to_implement(db_session: Session):
    """The fix, asserted through the resolver rather than by reading rows.

    A transition list can look right and still not be what the state machine
    picks, so this asks the code that actually routes.
    """
    routed = StateMachine.resolve_transition_target(
        _transitions(db_session), REVIEW, outcome="reject"
    )
    assert routed is not None, "a rejected review still has nowhere to go"
    assert routed[0] == "implement"


def test_a_passing_review_still_advances(db_session: Session):
    """The other half. Making the forward edge explicitly `pass` must not break
    the pass path it already served."""
    routed = StateMachine.resolve_transition_target(
        _transitions(db_session), REVIEW, outcome="pass"
    )
    assert routed is not None
    assert routed[0] == "approval"


def test_the_reject_no_longer_falls_back_to_the_previous_stage(db_session: Session):
    """The defect, named as the behaviour it produced.

    `static_qa` sits immediately before `review`, so the previous-stage fallback
    sent rework there — re-running static analysis and returning to review with
    the code untouched.
    """
    routed = StateMachine.resolve_transition_target(
        _transitions(db_session), REVIEW, outcome="reject"
    )
    assert routed[0] != "static_qa", (
        "rework is going back to static analysis, which cannot fix what review rejected"
    )


def test_it_matches_the_shape_loregarden_tdd_already_uses(db_session: Session):
    """Deliberately the same arrangement as the template that already worked,
    rather than a third way of expressing the same decision."""
    sync_workflow_templates(db_session)
    reference = db_session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == "loregarden-tdd")
    ).first()

    def verdicts(template: WorkflowTemplate) -> set[str]:
        # The API normalizer, because it is what turns an absent `when` into a
        # named one — comparing raw rows would call "missing" and "pass" equal.
        return {
            t["when"]
            for t in normalize_transitions_for_api(template.transitions_json)
            if t.get("from") == REVIEW
        }

    assert verdicts(_template(db_session)) == verdicts(reference) == {"pass", "reject"}


def test_every_stage_that_can_reject_has_a_reject_route(db_session: Session):
    """The gap that produced this, closed for the whole template.

    Only a stage that can return a verdict needs one — `spike` has no review or
    gate stage and correctly carries no conditional transitions, so a blanket
    rule would report it falsely.
    """
    template = _template(db_session)
    transitions = StateMachine.parse_transitions(template.transitions_json)
    verdict_types = {"parallel", "gate", "verify"}

    for stage in get_template_stages(template):
        if stage.stage_type not in verdict_types:
            continue
        routed = StateMachine.resolve_transition_target(transitions, stage.key, outcome="reject")
        assert routed is not None, (
            f"stage '{stage.key}' ({stage.stage_type}) can return a reject verdict "
            f"and {SLUG} gives it nowhere to route"
        )
