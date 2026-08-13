import json
from dataclasses import dataclass
from typing import cast

from loregarden.models.domain import StageStatus, TicketState, WorkflowStageDef


@dataclass(frozen=True)
class StageRoutePlan:
    """Resolved workflow cursor move after a stage completes or rejects."""

    from_key: str
    to_key: str
    outcome: str
    upstream: bool
    transition_agent_id: str = ""


class StateMachine:
    """Workflow stage routing. Ticket *state* is `services.ticket_state_service`.

    This class once carried a ticket transition table too. It described moves
    the system does not make and forbade several it does, and it was consulted
    at one of the eight places that wrote `tickets.state` — so it constrained
    nothing. What replaced it splits a chosen move from a state recomputed out
    of stages or children, which is the distinction the table could not express.
    """

    TERMINAL_TICKET_STATES = frozenset({TicketState.DONE, TicketState.WONT_DO})

    @staticmethod
    def parse_stages(stages_json: str) -> list[WorkflowStageDef]:
        raw = json.loads(stages_json or "[]")
        return [WorkflowStageDef.model_validate(item) for item in raw]

    @staticmethod
    def parse_transitions(transitions_json: str) -> list[dict[str, str]]:
        raw = json.loads(transitions_json or "[]")
        return [item for item in raw if isinstance(item, dict)]

    @staticmethod
    def _ordered_keys(stages: list[WorkflowStageDef]) -> list[str]:
        return [stage.key for stage in sorted(stages, key=lambda s: s.order)]

    @staticmethod
    def _stage_index(stages: list[WorkflowStageDef], stage_key: str) -> int | None:
        keys = StateMachine._ordered_keys(stages)
        try:
            return keys.index(stage_key)
        except ValueError:
            return None

    @staticmethod
    def is_upstream_route(
        stages: list[WorkflowStageDef],
        from_key: str,
        to_key: str,
    ) -> bool:
        from_idx = StateMachine._stage_index(stages, from_key)
        to_idx = StateMachine._stage_index(stages, to_key)
        if from_idx is None or to_idx is None:
            return False
        return to_idx < from_idx

    @staticmethod
    def _transition_when(item: dict[str, str]) -> str:
        when = item.get("when", "")
        if when:
            return when
        # YAML 1.1 parses bare `on` as boolean True, so a transition written
        # `on: pass` reaches us keyed by True rather than "on". The annotation
        # says str keys and the runtime disagrees; casting states that rather
        # than silencing the checker.
        #
        # Both spellings are accepted. The quoted one used to be dead code: the
        # guard was `isinstance(legacy, str)` and `.get(True, "")` yields "" when
        # the boolean key is absent, which satisfies it — so the function always
        # returned on the True branch and a template written `"on": "pass"` was
        # silently ignored, falling through to linear stage order.
        loose = cast("dict[object, str]", item)
        return loose.get(True, "") or loose.get("on", "")

    @staticmethod
    def resolve_transition_target(
        transitions: list[dict[str, str]],
        from_key: str,
        outcome: str = "pass",
    ) -> tuple[str, str] | None:
        """Return (to_stage_key, transition_agent_id) for a workflow transition."""
        matches = [item for item in transitions if item.get("from") == from_key]
        if not matches:
            return None

        def pick(when_values: tuple[str, ...]) -> tuple[str, str] | None:
            for when in when_values:
                for item in matches:
                    item_when = StateMachine._transition_when(item)
                    if item_when == when:
                        return item.get("to", ""), item.get("agent_id", "")
            return None

        if outcome == "reject":
            routed = pick(("reject",))
            return routed if routed and routed[0] else None

        routed = pick(("pass", "default", ""))
        if routed and routed[0]:
            return routed

        legacy = [item for item in matches if not StateMachine._transition_when(item)]
        if len(legacy) == 1 and legacy[0].get("to"):
            item = legacy[0]
            return item["to"], item.get("agent_id", "")
        if len(matches) == 1 and matches[0].get("to"):
            item = matches[0]
            return item["to"], item.get("agent_id", "")
        return None

    @staticmethod
    def resolve_next_stage_key(
        stages: list[WorkflowStageDef],
        transitions: list[dict[str, str]],
        current_key: str,
        *,
        outcome: str = "pass",
        explicit_to: str = "",
    ) -> StageRoutePlan | None:
        if explicit_to:
            # An unknown target used to be honored verbatim, parking the cursor
            # on a phantom stage.
            if explicit_to not in {stage.key for stage in stages}:
                raise ValueError(
                    f"Unknown target stage '{explicit_to}' for route from '{current_key}'"
                )
            return StageRoutePlan(
                from_key=current_key,
                to_key=explicit_to,
                outcome=outcome,
                upstream=StateMachine.is_upstream_route(stages, current_key, explicit_to),
            )

        routed = StateMachine.resolve_transition_target(transitions, current_key, outcome)
        if routed:
            to_key, agent_id = routed
            return StageRoutePlan(
                from_key=current_key,
                to_key=to_key,
                outcome=outcome,
                upstream=StateMachine.is_upstream_route(stages, current_key, to_key),
                transition_agent_id=agent_id,
            )

        if outcome == "reject":
            return None

        linear = StateMachine.next_stage_key(stages, current_key)
        if not linear:
            return None
        return StageRoutePlan(
            from_key=current_key,
            to_key=linear,
            outcome=outcome,
            upstream=False,
        )

    @staticmethod
    def reset_upstream_stages(
        stage_map: dict[str, StageStatus],
        stages: list[WorkflowStageDef],
        *,
        from_key: str,
        to_key: str,
    ) -> dict[str, StageStatus]:
        ordered = sorted(stages, key=lambda s: s.order)
        from_idx = StateMachine._stage_index(stages, from_key)
        to_idx = StateMachine._stage_index(stages, to_key)
        if from_idx is None or to_idx is None or to_idx > from_idx:
            return stage_map

        updated = dict(stage_map)
        for index in range(to_idx, from_idx + 1):
            updated[ordered[index].key] = StageStatus.PENDING
        return updated

    @staticmethod
    def skip_intermediate_stages(
        stage_map: dict[str, StageStatus],
        stages: list[WorkflowStageDef],
        *,
        from_key: str,
        to_key: str,
    ) -> dict[str, StageStatus]:
        """Mark stages a forward branch jumped over as WONT_DO.

        Left PENDING they never resolve, so _derive_ticket_state could never
        reach DONE and the ticket would hang after finishing its branch.
        """
        ordered = sorted(stages, key=lambda s: s.order)
        from_idx = StateMachine._stage_index(stages, from_key)
        to_idx = StateMachine._stage_index(stages, to_key)
        if from_idx is None or to_idx is None or to_idx <= from_idx + 1:
            return stage_map

        updated = dict(stage_map)
        for index in range(from_idx + 1, to_idx):
            key = ordered[index].key
            if updated.get(key, StageStatus.PENDING) == StageStatus.PENDING:
                updated[key] = StageStatus.WONT_DO
        return updated

    @staticmethod
    def next_stage_key(stages: list[WorkflowStageDef], current_key: str) -> str | None:
        ordered = sorted(stages, key=lambda s: s.order)
        keys = [s.key for s in ordered]
        if not current_key:
            return keys[0] if keys else None
        try:
            idx = keys.index(current_key)
        except ValueError:
            # Was keys[0], which silently rewound the cursor to stage one.
            return None
        if idx + 1 < len(keys):
            return keys[idx + 1]
        return None
