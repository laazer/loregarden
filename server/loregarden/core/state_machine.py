import json
from dataclasses import dataclass

from loregarden.models.domain import StageStatus, TicketState, WorkflowStageDef


@dataclass(frozen=True)
class TransitionResult:
    ok: bool
    message: str = ""


class StateMachine:
    TICKET_TRANSITIONS: dict[TicketState, set[TicketState]] = {
        TicketState.BACKLOG: {TicketState.IN_PROGRESS, TicketState.WONT_DO},
        TicketState.IN_PROGRESS: {
            TicketState.BLOCKED,
            TicketState.DONE,
            TicketState.BACKLOG,
            TicketState.WONT_DO,
        },
        TicketState.BLOCKED: {TicketState.IN_PROGRESS, TicketState.BACKLOG, TicketState.WONT_DO},
        TicketState.DONE: {TicketState.BACKLOG, TicketState.WONT_DO},
        TicketState.WONT_DO: {TicketState.BACKLOG, TicketState.IN_PROGRESS},
    }

    TERMINAL_TICKET_STATES = frozenset({TicketState.DONE, TicketState.WONT_DO})

    @classmethod
    def can_transition_ticket(cls, current: TicketState, target: TicketState) -> TransitionResult:
        allowed = cls.TICKET_TRANSITIONS.get(current, set())
        if target in allowed:
            return TransitionResult(ok=True)
        return TransitionResult(
            ok=False,
            message=f"Invalid ticket transition {current.value} -> {target.value}",
        )

    @staticmethod
    def parse_stages(stages_json: str) -> list[WorkflowStageDef]:
        raw = json.loads(stages_json or "[]")
        return [WorkflowStageDef.model_validate(item) for item in raw]

    @staticmethod
    def next_stage_key(stages: list[WorkflowStageDef], current_key: str) -> str | None:
        ordered = sorted(stages, key=lambda s: s.order)
        keys = [s.key for s in ordered]
        if not current_key:
            return keys[0] if keys else None
        try:
            idx = keys.index(current_key)
        except ValueError:
            return keys[0] if keys else None
        if idx + 1 < len(keys):
            return keys[idx + 1]
        return None

    @staticmethod
    def stage_status_for_ticket_state(state: TicketState) -> StageStatus:
        if state == TicketState.BLOCKED:
            return StageStatus.BLOCKED
        if state in (TicketState.DONE, TicketState.WONT_DO):
            return StageStatus.WONT_DO if state == TicketState.WONT_DO else StageStatus.DONE
        return StageStatus.PENDING
