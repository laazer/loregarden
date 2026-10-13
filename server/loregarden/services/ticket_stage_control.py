"""The moves a requeue (and a decision answer) needs from the orchestration service, as a Protocol.

`services.requeue` is imported by the orchestration module (a decision block
resolves into a requeue), so it cannot import `OrchestrationService` back.
The protocol names what it actually uses; `OrchestrationService` satisfies it.
"""

from __future__ import annotations

from typing import Protocol

from loregarden.models.domain import Ticket, UpdateTicketRequest


class StageControl(Protocol):
    def update_ticket_manual(self, ticket: Ticket, body: UpdateTicketRequest) -> Ticket: ...

    def refresh_stage_retry_budget(self, ticket: Ticket, stage_key: str) -> None: ...

    def route_workflow_stage(
        self, ticket: Ticket, *, from_stage_key: str, outcome: str = "reject"
    ) -> Ticket: ...
