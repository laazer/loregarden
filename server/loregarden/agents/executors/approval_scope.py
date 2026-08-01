"""Where permission approvals hang: a ticket, or a workspace side channel."""

from __future__ import annotations

from dataclasses import dataclass

from loregarden.models.domain import Ticket, Workspace

#: Stage key recorded on approvals raised by a workspace-scoped chat turn. Not a
#: workflow stage — approvals need one, and "triage" would misfile them under a
#: ticket's side channel.
HOME_CHAT_STAGE_KEY = "home-chat"
BRANCH_TRIAGE_STAGE_KEY = "branch-triage"


@dataclass(frozen=True)
class ApprovalScope:
    """What an approval, and its telemetry, hangs off.

    Usually a ticket. Home Baxter chat is workspace-scoped and has no work item,
    so every ticket-only step — workflow stage tracking, scope reroute, gate
    checklists — is skipped rather than faked against a placeholder.
    """

    workspace_id: str
    ticket: Ticket | None = None
    side_channel_stage_key: str = "triage"

    @classmethod
    def for_ticket(cls, ticket: Ticket) -> ApprovalScope:
        return cls(workspace_id=ticket.workspace_id, ticket=ticket)

    @classmethod
    def for_workspace(
        cls, workspace: Workspace, *, stage_key: str = HOME_CHAT_STAGE_KEY
    ) -> ApprovalScope:
        return cls(workspace_id=workspace.id, side_channel_stage_key=stage_key)

    @property
    def ticket_id(self) -> str | None:
        return self.ticket.id if self.ticket else None

    def approval_stage_key(self, track_workflow_stage: bool) -> str:
        if track_workflow_stage and self.ticket:
            return self.ticket.workflow_stage_key
        return self.side_channel_stage_key
