"""What an operation is: one thing the control plane does, declared once.

Every transport (MCP, the CLI, REST) reaches an operation through
`operation_call`, never its handler directly, so an operation behaves the same
on each and turning one transport off is a line here, with its reason, rather
than a route nobody remembers to delete.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel
from sqlmodel import Session


class Transport(StrEnum):
    """How a caller reached the control plane."""

    MCP = "mcp"
    #: `loregarden-cli.sh mcp call`: MCP's tool names, run in-process.
    CLI = "cli"
    REST = "rest"


class Effect(StrEnum):
    READ = "read"
    WRITE = "write"


class OperationId(StrEnum):
    #: What an agent reads before acting on a ticket: position, requirement,
    #: blockers, documents.
    TICKET_BRIEF = "ticket_brief"
    #: Where a ticket sits in its workflow, with its edges and active run.
    TICKET_STATE = "ticket_state"
    #: The ticket page's view: the summary plus artifacts, transitions, runtime
    #: and delivery.
    TICKET_DETAIL = "ticket_detail"
    #: An agent's ticket lookup: short rows to choose from.
    TICKET_SEARCH = "ticket_search"
    #: The board's ticket list: full summaries, filtered and optionally paged.
    TICKET_SUMMARIES = "ticket_summaries"
    #: Which documents describe a ticket, up its ancestors, without bodies.
    ARTIFACT_INDEX = "artifact_index"
    #: Every artifact on one ticket, newest first, for the Artifacts tab.
    ARTIFACT_FEED = "artifact_feed"
    #: Start a ticket's orchestration on the profile's driver, admitted to a lane.
    ORCHESTRATION_START = "orchestration_start"
    #: The calls a run makes back as it works (`run_callbacks.py`).
    STAGE_START = "stage_start"
    STAGE_COMPLETE = "stage_complete"
    STAGE_SKIP = "stage_skip"
    TICKET_BLOCK = "ticket_block"
    ARTIFACT_ATTACH = "artifact_attach"
    APPROVAL_REQUEST = "approval_request"
    ORCHESTRATION_COMPLETE = "orchestration_complete"
    #: A run declares the files it is about to change (`path_claim_operations.py`).
    PATHS_CLAIM = "paths_claim"
    TICKET_SIBLING_CLAIMS = "ticket_sibling_claims"
    HANDOFF_FEED = "handoff_feed"
    #: The edges between tickets (`ticket_links.py`).
    TICKET_DEPENDENCY_ADD = "ticket_dependency_add"
    TICKET_DEPENDENCY_REMOVE = "ticket_dependency_remove"
    TICKET_RELATION_ADD = "ticket_relation_add"
    TICKET_RELATION_REMOVE = "ticket_relation_remove"
    #: The merge queue (`merge_queue_ops.py`): read it, put a PR in it, and
    #: withdraw or retry an entry.
    MERGE_QUEUE_READ = "merge_queue_read"
    MERGE_QUEUE_ENQUEUE = "merge_queue_enqueue"
    MERGE_QUEUE_ENTRY_ACTION = "merge_queue_entry_action"
    #: An initiative's membership and plan (`initiatives.py`).
    INITIATIVE_MEMBER_ADD = "initiative_member_add"
    INITIATIVE_MEMBER_REMOVE = "initiative_member_remove"
    INITIATIVE_PLAN = "initiative_plan"
    #: Any plan root's autopilot: an initiative, a milestone or a parent ticket.
    PLAN_AUTOPILOT_SET = "plan_autopilot_set"
    #: The Initiatives page's switch, which takes an initiative and nothing else.
    INITIATIVE_AUTOPILOT_SET = "initiative_autopilot_set"
    INITIATIVE_WORK_START = "initiative_work_start"
    INITIATIVE_NEEDS_PERSON = "initiative_needs_person"
    #: Near-twins unified to the stricter rule (`ticket_requeue.py`,
    #: `memory_discredit.py`, `survey_queue.py`, `research_record.py`).
    TICKET_REQUEUE = "ticket_requeue"
    MEMORY_DISCREDIT = "memory_discredit"
    SURVEY_QUEUE = "survey_queue"
    RESEARCH_RECORD = "research_record"
    #: Memory, docker capacity, disk, GitHub issues and workspace integration
    #: (`memory_operations.py`, `docker_capacity_operations.py`,
    #: `disk_operations.py`, `github_issue_operations.py`,
    #: `organization_operations.py`).
    MEMORY_STATUS = "memory_status"
    MEMORY_RELATION_CREATE = "memory_relation_create"
    DOCKER_CAPACITY_STATUS = "docker_capacity_status"
    DOCKER_LEASE_FORCE_RELEASE = "docker_lease_force_release"
    #: An agent's one-call answer about disk use; may start a scan.
    DISK_USAGE = "disk_usage"
    DISK_OVERVIEW = "disk_overview"
    DISK_SCAN = "disk_scan"
    DISK_TREE = "disk_tree"
    #: The `loregarden_sync_github_issues` tool, dispatching on its action.
    GITHUB_ISSUES = "github_issues"
    GITHUB_ISSUE_LINK = "github_issue_link"
    GITHUB_ISSUE_PUBLISH = "github_issue_publish"
    GITHUB_ISSUE_SYNC = "github_issue_sync"
    GITHUB_ISSUES_SYNC_WORKSPACE = "github_issues_sync_workspace"
    ORGANIZATION_CHECK = "organization_check"
    WORKSPACE_INTEGRATION = "workspace_integration"
    WORKSPACE_INTEGRATION_INSTALL = "workspace_integration_install"
    #: Filing a ticket and editing one (`ticket_writes.py`), and merging its PR
    #: (`pull_request_merges.py`).
    TICKET_CREATE = "ticket_create"
    TICKET_UPDATE = "ticket_update"
    TICKET_WORKFLOW_SET = "ticket_workflow_set"
    TICKET_PULL_REQUEST_MERGE = "ticket_pull_request_merge"
    #: A workflow agent's question, carried on past with a default
    #: (`operator_questions.py`), and the list a ticket shows of them.
    OPERATOR_QUESTION_ASK = "operator_question_ask"
    OPERATOR_QUESTION_LIST = "operator_question_list"
    #: A stage run's question to an earlier stage on its ticket (`stage_asks.py`).
    STAGE_ASK = "stage_ask"
    #: The Review page: the queue, a capture, and the build launcher and try-it
    #: request a person drives (`review_operations.py`).
    REVIEW_QUEUE = "review_queue"
    REVIEW_CAPTURE = "review_capture"
    REVIEW_LAUNCH_STATUS = "review_launch_status"
    REVIEW_LAUNCH = "review_launch"
    REVIEW_LAUNCH_STOP = "review_launch_stop"
    REVIEW_TRY = "review_try"


@dataclass(frozen=True)
class Caller:
    transport: Transport
    #: A run Loregarden itself supervises (MCP's `X-Loregarden-Orchestrated`).
    orchestrated: bool = False
    #: The supervised agent run whose MCP config made the call, when known.
    run_id: str = ""


@dataclass(frozen=True)
class Operation:
    id: OperationId
    effect: Effect
    #: Validates the caller's arguments; every transport passes the same model.
    args: type[BaseModel]
    #: Called with the session, the validated args and the caller.
    handler: Callable[[Session, Any, Caller], Any]
    #: Transports this operation is not offered on, each with the reason. An
    #: absent transport is offered, and must be bound (`test_operations.py`).
    unavailable_on: Mapping[Transport, str] = field(default_factory=dict)
    #: How a transport words the handler's result, where its callers have always
    #: been answered in their own shape (an agent with the state payload, the
    #: ticket page with its detail). The effect is one; the reply is the
    #: transport's. A transport absent here gets the result as it is.
    replies: Mapping[Transport, Callable[[Session, Any], Any]] = field(default_factory=dict)


class OperationUnavailable(Exception):  # noqa: N818 — reads as the condition, like StopIteration
    """The operation is not offered on the caller's transport."""

    def __init__(self, operation: OperationId, transport: Transport, reason: str) -> None:
        super().__init__(f"{operation} is not available over {transport}: {reason}")
        self.operation = operation
        self.transport = transport
        self.reason = reason


class ReadOperationWrote(RuntimeError):  # noqa: N818 — reads as the condition
    """A read operation tried to write. Raised where reads are strict (tests)."""
