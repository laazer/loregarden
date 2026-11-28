"""Studio refuses an agent group the dispatcher could not run as one CLI turn.

Spec lg-run-economics-2636 §3. AC2: a non-consecutive group (by order, and on
the workflow's route), mixed models, a parallel step, exit actions,
and a classify route leaving the group from its middle are each refused, with a
message naming the group and the offending steps. AC9: a terminal step, or a
step with no agent, cannot join a group.

Each refusal is checked on create; the update, transitions-only update and
publish paths are each checked at least once, since each validates separately.
Step keys are distinct words so "names the step" cannot pass by substring.
"""

from __future__ import annotations

import json

import pytest
from loregarden.core.workflow_loader import get_template_stages
from loregarden.models.domain import (
    StudioAgent,
    StudioWorkflow,
    StudioWorkflowCreate,
    StudioWorkflowStage,
    StudioWorkflowUpdate,
    WorkflowTemplate,
)
from loregarden.services.studio_service import StudioService
from sqlmodel import Session, select

GROUP = "grp-x"
CLAUDE_ONE = "vgrp_claude_one"
CLAUDE_TWO = "vgrp_claude_two"
CURSOR = "vgrp_cursor"

SIGN_OFF = [
    {
        "key": "legacy-stage-sign-off",
        "label": "Approve completion",
        "requirement": {
            "kind": "operator_judgment",
            "decision_prompt": "Approve completion of stage.",
        },
    }
]


@pytest.fixture
def svc(db_session: Session) -> StudioService:
    """Agents whose default model the tests control (an agent declares no adapter)."""
    for slug in (CLAUDE_ONE, CLAUDE_TWO, CURSOR):
        db_session.add(StudioAgent(slug=slug, name=slug, default_model=""))
    db_session.commit()
    return StudioService(db_session)


def _stage(key: str, order: int, *, group: str = GROUP, **fields) -> StudioWorkflowStage:
    fields.setdefault("agent_id", CLAUDE_ONE)
    return StudioWorkflowStage(key=key, name=key.title(), order=order, agent_group=group, **fields)


def _done(order: int, **fields) -> StudioWorkflowStage:
    return StudioWorkflowStage(key="done", name="Done", order=order, terminal=True, **fields)


def _create(svc: StudioService, slug: str, stages, transitions=None):
    return svc.create_workflow(
        StudioWorkflowCreate(slug=slug, name=slug, stages=stages, transitions=transitions or [])
    )


def _assert_names(exc: pytest.ExceptionInfo, phrase: str, *keys: str) -> None:
    message = str(exc.value)
    assert phrase in message, message
    assert f"'{GROUP}'" in message, message
    for key in keys:
        assert key in message, f"{key!r} not named in: {message}"


def _valid_pair() -> list[StudioWorkflowStage]:
    return [
        _stage("alpha", 1),
        _stage("bravo", 2, agent_id=CLAUDE_TWO),
        _stage("charlie", 3, group=""),
        _done(4),
    ]


# ---- accepted -------------------------------------------------------------------------


def test_a_consecutive_group_on_one_adapter_and_model_is_accepted(svc):
    view = _create(svc, "ok-group", _valid_pair())
    groups = {stage.key: getattr(stage, "agent_group", None) for stage in view.stages}
    assert groups["alpha"] == GROUP and groups["bravo"] == GROUP


def test_a_single_member_group_with_every_field_set_is_accepted(svc, db_session):
    """D2: the shape test_studio_publish_fields builds, minus exit actions (D6)."""
    stage = _stage(
        "alpha",
        1,
        model="claude-opus-5",
        required_evidence=["real_surface"],
        checklist=["Run the thing"],
        stage_brief="Do the part the reviewer cannot do for you.",
        gate_commands=["true"],
        exit_actions_enabled=True,
    )
    _create(svc, "single-member", [stage, _done(2)])
    svc.publish_workflow("single-member")
    template = db_session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == "studio-single-member")
    ).one()
    alpha = next(s for s in get_template_stages(template) if s.key == "alpha")
    assert getattr(alpha, "agent_group", None) == GROUP


# ---- rule 5: consecutive by order --------------------------------------------------------


def test_a_group_interrupted_by_another_step_is_refused_naming_the_intruder(svc):
    stages = [
        _stage("alpha", 1),
        _stage("bravo", 2, group=""),
        _stage("charlie", 3),
        _done(4),
    ]
    with pytest.raises(ValueError) as exc:
        _create(svc, "gap", stages)
    _assert_names(exc, "not consecutive", "alpha", "charlie", "bravo")


def test_update_refuses_a_non_consecutive_group(svc):
    _create(svc, "gap-update", _valid_pair())
    stages = [
        _stage("alpha", 1),
        _stage("bravo", 2, group=""),
        _stage("charlie", 3),
        _done(4),
    ]
    with pytest.raises(ValueError) as exc:
        svc.update_workflow("gap-update", StudioWorkflowUpdate(stages=stages))
    _assert_names(exc, "not consecutive", "alpha", "charlie", "bravo")


def test_publish_refuses_a_stored_draft_with_a_non_consecutive_group(svc, db_session):
    """A draft saved before groups were validated is refused when it is published."""
    _create(svc, "gap-publish", _valid_pair())
    draft = db_session.exec(
        select(StudioWorkflow).where(StudioWorkflow.slug == "gap-publish")
    ).one()
    stored = json.loads(draft.stages_json)
    for item in stored:
        item["agent_group"] = {"alpha": GROUP, "bravo": "", "charlie": GROUP}.get(item["key"], "")
    draft.stages_json = json.dumps(stored)
    db_session.add(draft)
    db_session.commit()

    with pytest.raises(ValueError) as exc:
        svc.publish_workflow("gap-publish")
    _assert_names(exc, "not consecutive", "alpha", "charlie", "bravo")


# ---- rule 6: consecutive on the route ---------------------------------------------------


def _skipping_route() -> list[dict[str, str]]:
    """A pass from alpha jumps to charlie, past bravo, its group's next step."""
    return [
        {"from": "alpha", "to": "charlie", "when": "pass"},
        {"from": "bravo", "to": "charlie", "when": "pass"},
        {"from": "charlie", "to": "done", "when": "pass"},
    ]


def test_a_group_the_route_does_not_walk_in_order_is_refused(svc):
    with pytest.raises(ValueError) as exc:
        _create(svc, "route-skip", _valid_pair(), transitions=_skipping_route())
    _assert_names(exc, "not consecutive on the workflow's route", "alpha", "charlie", "bravo")


def test_a_transitions_only_update_is_checked_against_the_stored_groups(svc):
    """R8: the route decides consecutiveness, so changing only the route must validate."""
    _create(svc, "route-update", _valid_pair())
    with pytest.raises(ValueError) as exc:
        svc.update_workflow("route-update", StudioWorkflowUpdate(transitions=_skipping_route()))
    _assert_names(exc, "not consecutive on the workflow's route", "alpha", "charlie", "bravo")


# ---- rule 8: one model ---------------------------------------------------------------------


def test_a_group_mixing_models_is_refused_naming_both_steps(svc):
    stages = [
        _stage("alpha", 1, model="claude-opus-5"),
        _stage("bravo", 2, model="claude-sonnet-5"),
        _done(3),
    ]
    with pytest.raises(ValueError) as exc:
        _create(svc, "mixed-model", stages)
    _assert_names(exc, "mixes models", "alpha", "bravo")


def test_an_agents_default_model_counts_as_the_steps_model(svc, db_session):
    """D17: the effective model, not the stage field alone."""
    agent = db_session.exec(select(StudioAgent).where(StudioAgent.slug == CLAUDE_TWO)).one()
    agent.default_model = "claude-haiku-5"
    db_session.add(agent)
    db_session.commit()
    stages = [
        _stage("alpha", 1, agent_id=CLAUDE_ONE, model="claude-opus-5"),
        _stage("bravo", 2, agent_id=CLAUDE_TWO),
        _done(3),
    ]
    with pytest.raises(ValueError) as exc:
        _create(svc, "default-model", stages)
    _assert_names(exc, "mixes models", "alpha", "bravo")


def test_publish_refuses_a_stored_draft_mixing_models(svc, db_session):
    _create(svc, "mixed-model-publish", _valid_pair())
    draft = db_session.exec(
        select(StudioWorkflow).where(StudioWorkflow.slug == "mixed-model-publish")
    ).one()
    stored = json.loads(draft.stages_json)
    for item in stored:
        item["model"] = {"alpha": "claude-opus-5", "bravo": "claude-sonnet-5"}.get(item["key"], "")
    draft.stages_json = json.dumps(stored)
    db_session.add(draft)
    db_session.commit()

    with pytest.raises(ValueError) as exc:
        svc.publish_workflow("mixed-model-publish")
    _assert_names(exc, "mixes models", "alpha", "bravo")


# ---- rule 3: parallel ------------------------------------------------------------------------


def test_a_parallel_step_in_a_group_is_refused(svc):
    stages = [
        _stage("alpha", 1),
        _stage(
            "reviews",
            2,
            stage_type="parallel",
            agent_id="",
            parallel_agents=[{"agent_id": CLAUDE_ONE}, {"agent_id": CLAUDE_TWO}],
        ),
        _done(3),
    ]
    with pytest.raises(ValueError) as exc:
        _create(svc, "parallel-member", stages)
    _assert_names(exc, "parallel step", "reviews")


# ---- rule 4: exit actions (D6) -----------------------------------------------------------------


@pytest.mark.parametrize("member", ["alpha", "bravo"], ids=["first", "last"])
def test_a_grouped_step_declaring_exit_actions_is_refused(svc, member):
    sign_off = {"exit_actions_enabled": True, "exit_actions": SIGN_OFF}
    stages = [
        _stage("alpha", 1, **(sign_off if member == "alpha" else {})),
        _stage("bravo", 2, agent_id=CLAUDE_TWO, **(sign_off if member == "bravo" else {})),
        _stage("charlie", 3, group=""),
        _done(4),
    ]
    with pytest.raises(ValueError) as exc:
        _create(svc, f"exit-{member}", stages)
    _assert_names(exc, "exit actions", member)


# ---- rule 9: classify leaving from the middle ------------------------------------------------


def _classify(key: str, order: int, to_stage: str, *, group: str = GROUP) -> StudioWorkflowStage:
    return _stage(
        key,
        order,
        group=group,
        stage_type="classify",
        agent_id="",
        classify_routes=[{"agent_id": CLAUDE_ONE, "default": True, "to_stage": to_stage}],
    )


def _classify_workflow(
    first: StudioWorkflowStage, *, last_member: StudioWorkflowStage | None = None
):
    """first (member), middle (member), last (member), next (after the group), far, done."""
    return [
        first,
        _stage("middle", 2),
        last_member or _stage("closing", 3),
        _stage("following", 4, group=""),
        _stage("faraway", 5, group=""),
        _done(6),
    ]


def test_a_classify_step_mid_group_routing_past_the_group_is_refused(svc):
    stages = _classify_workflow(_classify("sorter", 1, "faraway"))
    with pytest.raises(ValueError) as exc:
        _create(svc, "classify-leaves", stages)
    _assert_names(exc, "leaves the group from its middle", "sorter", "faraway")


def test_a_classify_step_mid_group_may_route_to_the_step_right_after_the_group(svc):
    _create(svc, "classify-next", _classify_workflow(_classify("sorter", 1, "following")))


def test_a_classify_step_mid_group_may_route_to_a_later_member(svc):
    _create(svc, "classify-later", _classify_workflow(_classify("sorter", 1, "closing")))


def test_a_classify_step_last_in_its_group_may_route_anywhere(svc):
    stages = _classify_workflow(_stage("opening", 1), last_member=_classify("sorter", 3, "faraway"))
    _create(svc, "classify-last", stages)


# ---- AC9: terminal and agentless steps -------------------------------------------------------


def test_a_group_containing_the_terminal_step_is_refused(svc):
    stages = [_stage("alpha", 1), _done(2, agent_group=GROUP)]
    with pytest.raises(ValueError) as exc:
        _create(svc, "terminal-member", stages)
    _assert_names(exc, "terminal step", "done")


def test_a_group_containing_a_step_keyed_done_without_the_flag_is_refused(svc):
    stages = [
        _stage("alpha", 1),
        StudioWorkflowStage(key="done", name="Done", order=2, agent_group=GROUP),
    ]
    with pytest.raises(ValueError) as exc:
        _create(svc, "done-key-member", stages)
    _assert_names(exc, "terminal step", "done")


def test_a_group_containing_a_step_with_no_agent_is_refused(svc):
    stages = [_stage("alpha", 1), _stage("hollow", 2, agent_id=""), _done(3)]
    with pytest.raises(ValueError) as exc:
        _create(svc, "agentless-member", stages)
    _assert_names(exc, "with no agent", "hollow")


# ---- test-break: what a lazy implementation would get past --------------------------------


def test_a_middle_member_on_another_model_is_refused_and_every_member_listed(svc):
    """First and last agree; only the middle differs. Comparing the ends is not enough."""
    stages = [
        _stage("alpha", 1, agent_id=CLAUDE_ONE, model="claude-opus-5"),
        _stage("bravo", 2, agent_id=CURSOR, model="claude-sonnet-5"),
        _stage("charlie", 3, agent_id=CLAUDE_TWO, model="claude-opus-5"),
        _done(4),
    ]
    with pytest.raises(ValueError) as exc:
        _create(svc, "mixed-model-every-member", stages)
    _assert_names(exc, "mixes models", "alpha", "bravo", "charlie")


def test_a_middle_member_on_another_model_is_refused(svc):
    stages = [
        _stage("alpha", 1, model="claude-opus-5"),
        _stage("bravo", 2, model="claude-sonnet-5"),
        _stage("charlie", 3, model="claude-opus-5"),
        _done(4),
    ]
    with pytest.raises(ValueError) as exc:
        _create(svc, "mixed-model-middle", stages)
    _assert_names(exc, "mixes models", "bravo")


def test_a_classify_members_route_agents_count_toward_the_groups_model(svc, db_session):
    """A classify step has no agent_id; its models are its routes' agents' models."""
    agent = db_session.exec(select(StudioAgent).where(StudioAgent.slug == CURSOR)).one()
    agent.default_model = "claude-haiku-5"
    db_session.add(agent)
    db_session.commit()
    sorter = _stage(
        "sorter",
        1,
        stage_type="classify",
        agent_id="",
        classify_routes=[{"agent_id": CURSOR, "default": True, "to_stage": "closing"}],
    )
    stages = [sorter, _stage("closing", 2), _stage("following", 3, group=""), _done(4)]
    with pytest.raises(ValueError) as exc:
        _create(svc, "classify-model", stages)
    _assert_names(exc, "mixes models", "sorter", "closing")
    assert "claude-haiku-5" in str(exc.value)


def test_a_mid_group_classify_routing_back_before_the_group_is_refused(svc):
    stages = [
        _stage("before", 1, group=""),
        _stage("opening", 2),
        _classify("sorter", 3, "before"),
        _stage("closing", 4),
        _stage("following", 5, group=""),
        _done(6),
    ]
    with pytest.raises(ValueError) as exc:
        _create(svc, "classify-back", stages)
    _assert_names(exc, "leaves the group from its middle", "sorter", "before")


def test_a_mid_group_classify_routing_to_an_earlier_member_is_refused(svc):
    """'A later step in the group': an earlier member is not one."""
    stages = [
        _stage("opening", 1),
        _classify("sorter", 2, "opening"),
        _stage("closing", 3),
        _stage("following", 4, group=""),
        _done(5),
    ]
    with pytest.raises(ValueError) as exc:
        _create(svc, "classify-earlier", stages)
    _assert_names(exc, "leaves the group from its middle", "sorter", "opening")


def test_every_group_is_validated_not_only_the_first(svc):
    """A valid group sorting first must not hide an invalid second group."""
    stages = [
        _stage("alpha", 1, group="grp-a"),
        _stage("bravo", 2, group="grp-a", agent_id=CLAUDE_TWO),
        _stage("charlie", 3, group="grp-z", agent_id=CLAUDE_ONE, model="claude-opus-5"),
        _stage("delta", 4, group="grp-z", agent_id=CURSOR, model="claude-sonnet-5"),
        _done(5),
    ]
    with pytest.raises(ValueError) as exc:
        _create(svc, "two-groups", stages)
    message = str(exc.value)
    assert "mixes models" in message and "'grp-z'" in message, message
    assert "charlie" in message and "delta" in message, message


def test_two_valid_groups_side_by_side_are_accepted(svc):
    """Adjacent groups are each consecutive; one group ending where another begins is legal."""
    stages = [
        _stage("alpha", 1, group="grp-a"),
        _stage("bravo", 2, group="grp-a", agent_id=CLAUDE_TWO),
        _stage("charlie", 3, group="grp-z", agent_id=CURSOR),
        _stage("delta", 4, group="grp-z", agent_id=CURSOR),
        _done(5),
    ]
    view = _create(svc, "two-valid-groups", stages)
    groups = {stage.key: getattr(stage, "agent_group", None) for stage in view.stages}
    assert groups == {
        "alpha": "grp-a",
        "bravo": "grp-a",
        "charlie": "grp-z",
        "delta": "grp-z",
        "done": "",
    }
