"""Transition `when` spellings, including the two legacy ones.

A template may say `when: pass`, or the YAML 1.1 spelling `on: pass` — which
that parser turns into a boolean True key — or the quoted `"on": "pass"` that
survives a JSON round-trip. The last was accepted in name only: the guard was
`isinstance(legacy, str)` and `.get(True, "")` returns "" when the boolean key
is absent, so the function always returned before reaching it, and such a
transition was silently ignored in favour of linear stage order.
"""

from loregarden.core.state_machine import StateMachine


def _stages():
    from loregarden.models.domain import WorkflowStageDef

    return [
        WorkflowStageDef(key="implement", name="Implement", order=1),
        WorkflowStageDef(key="review", name="Review", order=2),
        WorkflowStageDef(key="rework", name="Rework", order=3),
    ]


def test_when_is_honoured():
    transitions = [{"from": "implement", "when": "pass", "to": "review"}]
    assert StateMachine.resolve_transition_target(transitions, "implement") == ("review", "")


def test_the_yaml_boolean_on_key_is_honoured():
    """`on: pass` unquoted — YAML 1.1 hands us True as the key."""
    transitions = [{"from": "implement", True: "pass", "to": "review"}]
    assert StateMachine.resolve_transition_target(transitions, "implement") == ("review", "")


def test_a_quoted_on_key_is_honoured():
    """Was dead: the function returned on the boolean branch every time."""
    transitions = [{"from": "implement", "on": "pass", "to": "review"}]
    assert StateMachine.resolve_transition_target(transitions, "implement") == ("review", "")


def test_a_quoted_on_key_routes_a_rejection():
    """The case the dead branch actually cost: a reject route ignored entirely.

    With `on` unread the transition looked unconditional, so a rejection took
    the pass route instead of the rework one.
    """
    transitions = [
        {"from": "review", "on": "pass", "to": "rework"},
        {"from": "review", "on": "reject", "to": "implement"},
    ]
    assert StateMachine.resolve_transition_target(transitions, "review", "reject") == (
        "implement",
        "",
    )


def test_when_wins_over_a_legacy_key():
    transitions = [{"from": "implement", "when": "pass", "on": "reject", "to": "review"}]
    assert StateMachine._transition_when(transitions[0]) == "pass"
