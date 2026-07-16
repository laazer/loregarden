"""Bridge external CLI permission prompts into Loregarden approvals."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlmodel import Session

if TYPE_CHECKING:
    from loregarden.services.run_log_stream import RunLogStreamer

from loregarden.agents.cli_adapters import CliInvocation
from loregarden.agents.registry import get_agent
from loregarden.config import settings
from loregarden.db.session import engine
from loregarden.models.domain import (
    AgentRun,
    Approval,
    ApprovalKind,
    ApprovalStatus,
    RunStatus,
    StageStatus,
    Ticket,
    Workspace,
)
from loregarden.services.agent_scope import check_agent_scope
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.permission_allowlist import is_permission_allowed
from loregarden.services.run_errors import agent_timeout_message
from loregarden.services.subprocess_lines import SubprocessLineReader
from loregarden.services.workflow_state import set_stage_status
from loregarden.services.workspace_paths import resolve_workspace_root


@dataclass
class BridgeResult:
    status: RunStatus
    stdout: str
    stderr: str
    session_id: str = ""


def serialize_tool_input(tool_input: Any) -> str:
    """Persist full tool input JSON without truncation."""
    return json.dumps(tool_input, ensure_ascii=False)


def parse_stored_tool_input(raw: str) -> dict[str, Any]:
    """Parse stored tool input; tolerate legacy truncated payloads."""
    if not raw or raw == "{}":
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _parse_ndjson_line(line: str) -> dict[str, Any] | None:
    line = line.strip()
    if not line:
        return None
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def extract_permission_request(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize Claude/Cursor permission control messages."""
    msg_type = payload.get("type", "")
    if msg_type in {"control_request", "sdk_control_request"}:
        request = payload.get("request") or {}
        subtype = request.get("subtype", "")
        if subtype in {"permission", "can_use_tool"}:
            request_id = (
                payload.get("request_id") or request.get("request_id") or request.get("id") or ""
            )
            tool_name = request.get("tool_name") or request.get("tool") or "tool"
            tool_input = request.get("tool_input") or request.get("input") or {}
            return {
                "request_id": str(request_id),
                "tool_name": str(tool_name),
                "tool_input": tool_input,
                "raw": payload,
            }
    return None


ASK_USER_QUESTION_TOOL = "AskUserQuestion"

LOREGARDEN_MCP_PREFIX = "mcp__loregarden__"

_READ_ONLY_MCP_TOOLS = frozenset(
    {
        "loregarden_get_ticket",
        "loregarden_get_ticket_by_external",
        "loregarden_list_tickets",
        "loregarden_memory_status",
        "loregarden_search_memory",
    }
)

# Bookkeeping writes that land only in Loregarden's own stores — the Obsidian
# vault, the memory graph, and the artifacts table. They cannot touch the repo,
# the filesystem outside the vault, or workflow state, so gating them behind a
# human click buys no safety: it just spends the run's timeout budget. Agents are
# now told to route every report through these tools instead of writing markdown
# into the repo, which makes them hot-path rather than incidental.
#
# Deliberately excluded — these mutate workflow state or write repo files, and
# stay gated: complete_stage, skip_stage, block_ticket, update_ticket,
# write_handoff, request_approval, start/complete_orchestration, start_stage.
_CONTROL_PLANE_WRITE_MCP_TOOLS = frozenset(
    {
        "loregarden_append_checkpoint",
        "loregarden_append_learning",
        "loregarden_upsert_memory",
        "loregarden_create_memory_relation",
        "loregarden_upsert_blog_post",
        "loregarden_attach_artifact",
    }
)

AUTO_APPROVED_MCP_TOOLS = _READ_ONLY_MCP_TOOLS | _CONTROL_PLANE_WRITE_MCP_TOOLS


def bare_mcp_tool_name(tool_name: str) -> str | None:
    if tool_name.startswith(LOREGARDEN_MCP_PREFIX):
        return tool_name[len(LOREGARDEN_MCP_PREFIX) :]
    return None


def is_auto_approved_mcp_tool(tool_name: str) -> bool:
    bare = bare_mcp_tool_name(tool_name)
    return bare in AUTO_APPROVED_MCP_TOOLS if bare else False


def enrich_mcp_tool_input(
    *,
    bare_tool: str,
    tool_input: dict[str, Any],
    ticket: Ticket,
    workspace_slug: str,
) -> dict[str, Any]:
    enriched = dict(tool_input)
    if bare_tool == "loregarden_get_ticket" and not enriched.get("ticket_id"):
        enriched["ticket_id"] = ticket.id
    if bare_tool == "loregarden_get_ticket_by_external":
        if not enriched.get("workspace_slug"):
            enriched["workspace_slug"] = workspace_slug
        if not enriched.get("external_id"):
            enriched["external_id"] = ticket.external_id
    if bare_tool == "loregarden_list_tickets" and not enriched.get("workspace_slug"):
        enriched["workspace_slug"] = workspace_slug
    return enriched


@dataclass
class ApprovalResolution:
    approved: bool
    updated_input: dict[str, Any] | None = None
    message: str = ""


def is_ask_user_question(tool_name: str) -> bool:
    return tool_name == ASK_USER_QUESTION_TOOL


def build_ask_user_question_input(
    tool_input: dict[str, Any],
    *,
    answers: dict[str, str | list[str]],
    response: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "questions": tool_input.get("questions", []),
        "answers": answers,
    }
    if response.strip():
        payload["response"] = response.strip()
    return payload


def validate_question_answers(
    tool_input: dict[str, Any],
    answers: dict[str, str | list[str]] | None,
    *,
    response: str = "",
) -> None:
    if response.strip():
        return
    questions = tool_input.get("questions") or []
    if not questions:
        raise ValueError("Question approval is missing question payload")
    if not answers:
        raise ValueError("Answers required for agent questions")
    for item in questions:
        if not isinstance(item, dict):
            continue
        question_text = str(item.get("question") or "").strip()
        if not question_text:
            continue
        answer = answers.get(question_text)
        if isinstance(answer, list):
            if not any(str(part).strip() for part in answer):
                raise ValueError(f"Answer required for: {question_text}")
            continue
        if not str(answer or "").strip():
            raise ValueError(f"Answer required for: {question_text}")


def build_control_response(
    *,
    request_id: str,
    approved: bool,
    message: str = "",
    updated_input: dict[str, Any] | None = None,
) -> dict[str, Any]:
    inner: dict[str, Any]
    if approved:
        inner = {"behavior": "allow"}
        # Empty updatedInput overwrites Claude's original tool args and breaks Bash/MCP.
        if updated_input:
            inner["updatedInput"] = updated_input
    else:
        inner = {"behavior": "deny", "message": message or "Denied via Loregarden inbox"}
    return {
        "type": "control_response",
        "response": {
            "subtype": "success",
            "request_id": request_id,
            "response": inner,
        },
    }


def build_user_message(prompt: str, *, session_id: str | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {
        "type": "user",
        "message": {"role": "user", "content": prompt},
        "parent_tool_use_id": None,
    }
    if session_id:
        message["session_id"] = session_id
    return message


def result_payload_status(payload: dict[str, Any]) -> tuple[bool, bool]:
    """Return (finished, failed) for Claude/Cursor stream-json result events."""
    if payload.get("type") != "result":
        return False, False
    failed = bool(payload.get("is_error")) or payload.get("subtype") == "error"
    return True, failed


def _close_stdin(proc: Any) -> None:
    stdin = getattr(proc, "stdin", None)
    if not stdin:
        return
    close = getattr(stdin, "close", None)
    if not callable(close):
        return
    try:
        close()
    except OSError:
        pass


def _drain_stdout_after_result(
    proc: Any,
    stdout_reader: SubprocessLineReader,
    stdout_lines: list[str],
    *,
    streamer: RunLogStreamer | None,
    max_seconds: float = 5.0,
) -> None:
    """Read trailing stream-json lines and terminate a CLI that stayed alive after result."""
    import subprocess

    deadline = time.time() + max_seconds
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        line = stdout_reader.readline(timeout=0.2)
        if line is None:
            continue
        line = line.rstrip("\n")
        stdout_lines.append(line)
        if streamer:
            streamer.append_stream_line(line)

    if proc.poll() is None:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def wait_for_approval_resolution(
    approval_id: str,
    *,
    poll_seconds: float = 2.0,
    timeout_seconds: float | None = None,
) -> ApprovalResolution:
    deadline = time.time() + (timeout_seconds or settings.permission_approval_timeout_seconds)
    while time.time() < deadline:
        resolution = poll_approval_resolution(approval_id)
        if resolution is not None:
            return resolution
        time.sleep(poll_seconds)
    raise TimeoutError(f"Timed out waiting for approval {approval_id}")


def poll_approval_resolution(approval_id: str) -> ApprovalResolution | None:
    """Return approval outcome when resolved; None while still pending."""
    with Session(engine) as session:
        approval = session.get(Approval, approval_id)
        if not approval:
            raise ValueError(f"Approval not found: {approval_id}")
        if approval.status == ApprovalStatus.APPROVED:
            stored = json.loads(approval.response_json or "{}")
            updated_input = stored.get("updated_input")
            if updated_input is None and approval.kind == ApprovalKind.CLI_PERMISSION:
                updated_input = parse_stored_tool_input(approval.tool_input_json)
            return ApprovalResolution(
                approved=True,
                updated_input=updated_input if isinstance(updated_input, dict) else None,
            )
        if approval.status == ApprovalStatus.REJECTED:
            return ApprovalResolution(
                approved=False,
                message="Rejected in Loregarden approval inbox",
            )
    return None


@dataclass
class _RunContext:
    """Static-for-the-run info resolved once up front."""

    workspace_slug: str
    workspace_root: str
    auto_approve: bool
    agent_id: str
    agent_name: str


@dataclass
class _LoopState:
    """Everything the permission loop mutates across iterations, bundled so
    it can be threaded through the extracted step handlers below."""

    stdout_lines: list[str]
    session_id: str
    last_persist: float
    pending_approval: Approval | None = None
    pending_request_id: str = ""
    pending_tool_input: dict[str, Any] | None = None
    approval_deadline: float = 0.0
    finished_with_result: bool = False
    result_is_error: bool = False


@dataclass
class _LoopStep:
    """What the driving while-loop in run() should do after a step handler
    runs: keep looping ("continue"), stop the loop ("break"), or return a
    result immediately ("return", with `result` set)."""

    action: str
    result: BridgeResult | None = None


class PermissionBridgeRunner:
    """Run CLIs with permission prompts routed to the Loregarden inbox."""

    def __init__(self, session: Session, *, track_workflow_stage: bool = True) -> None:
        self.session = session
        self.orch = OrchestrationService(session)
        self.track_workflow_stage = track_workflow_stage

    def run(
        self,
        *,
        run_id: str,
        ticket: Ticket,
        invocation: CliInvocation,
        prompt: str,
        timeout_seconds: int,
        spawn_process: Callable[..., Any] | None = None,
        wait_for_approval: Callable[..., ApprovalResolution] | None = None,
        streamer: RunLogStreamer | None = None,
    ) -> BridgeResult:
        import subprocess

        spawn = spawn_process or subprocess.Popen
        ctx = self._prepare_context(ticket, run_id)
        custom_wait = wait_for_approval
        custom_wait_seen: set[str] = set()

        def resolve_poll(approval_id: str) -> ApprovalResolution | None:
            if custom_wait and approval_id not in custom_wait_seen:
                custom_wait_seen.add(approval_id)
                return custom_wait(approval_id, timeout_seconds=1)
            return poll_approval_resolution(approval_id)

        proc = None
        state = _LoopState(
            stdout_lines=[],
            session_id=invocation.resume_session_id or "",
            last_persist=time.time(),
        )
        try:
            proc = spawn(
                invocation.argv,
                cwd=invocation.cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
            assert proc.stdin and proc.stdout

            stdout_reader = SubprocessLineReader(proc.stdout)

            user_msg = build_user_message(
                prompt,
                session_id=invocation.resume_session_id or None,
            )
            proc.stdin.write((json.dumps(user_msg) + "\n").encode("utf-8"))
            proc.stdin.flush()

            stderr_lines: list[str] = []
            deadline = time.time() + timeout_seconds

            while time.time() < deadline:
                if proc.poll() is not None:
                    break

                if state.pending_approval is not None:
                    step = self._handle_pending_approval(
                        ticket=ticket,
                        run_id=run_id,
                        proc=proc,
                        streamer=streamer,
                        stdout_reader=stdout_reader,
                        state=state,
                        resolve_poll=resolve_poll,
                    )
                elif state.finished_with_result:
                    break
                else:
                    step = self._handle_next_line(
                        ctx=ctx,
                        ticket=ticket,
                        run_id=run_id,
                        proc=proc,
                        invocation=invocation,
                        streamer=streamer,
                        stdout_reader=stdout_reader,
                        deadline=deadline,
                        timeout_seconds=timeout_seconds,
                        state=state,
                    )

                if step.action == "return":
                    return step.result
                if step.action == "break":
                    break

            return self._finalize(
                proc=proc,
                stdout_reader=stdout_reader,
                deadline=deadline,
                timeout_seconds=timeout_seconds,
                state=state,
                stderr_lines=stderr_lines,
                streamer=streamer,
            )
        except subprocess.TimeoutExpired:
            if proc is not None:
                proc.kill()
            return BridgeResult(
                status=RunStatus.FAILED,
                stdout="\n".join(state.stdout_lines),
                stderr=agent_timeout_message(timeout_seconds),
                session_id=state.session_id,
            )

    def _prepare_context(self, ticket: Ticket, run_id: str) -> _RunContext:
        workspace = self.session.get(Workspace, ticket.workspace_id)
        workspace_slug = workspace.slug if workspace else ""
        workspace_root = str(resolve_workspace_root(workspace)) if workspace else ""
        run = self.session.get(AgentRun, run_id)
        auto_approve = bool(run and run.auto_approve)
        agent_id = run.agent_id if run else ""
        agent_config = get_agent(agent_id) if agent_id else None
        agent_name = (agent_config or {}).get("name", agent_id)
        return _RunContext(
            workspace_slug=workspace_slug,
            workspace_root=workspace_root,
            auto_approve=auto_approve,
            agent_id=agent_id,
            agent_name=agent_name,
        )

    @staticmethod
    def _send_response(proc: Any, response: dict[str, Any]) -> None:
        proc.stdin.write((json.dumps(response) + "\n").encode("utf-8"))
        proc.stdin.flush()

    def _handle_pending_approval(
        self,
        *,
        ticket: Ticket,
        run_id: str,
        proc: Any,
        streamer: RunLogStreamer | None,
        stdout_reader: SubprocessLineReader,
        state: _LoopState,
        resolve_poll: Callable[[str], ApprovalResolution | None],
    ) -> _LoopStep:
        assert state.pending_approval is not None
        if time.time() > state.approval_deadline:
            proc.kill()
            return _LoopStep(
                "return",
                BridgeResult(
                    status=RunStatus.FAILED,
                    stdout="\n".join(state.stdout_lines),
                    stderr=f"Timed out waiting for approval {state.pending_approval.id}",
                    session_id=state.session_id,
                ),
            )

        resolution = resolve_poll(state.pending_approval.id)
        if resolution is None:
            line = stdout_reader.readline(timeout=0.5)
            if line is not None:
                line = line.rstrip("\n")
                state.last_persist = time.time()
                state.stdout_lines.append(line)
                if streamer:
                    streamer.append_stream_line(line)
                payload = _parse_ndjson_line(line)
                if payload:
                    finished, failed = result_payload_status(payload)
                    if finished and state.pending_approval is None:
                        state.session_id = str(payload.get("session_id") or state.session_id)
                        state.finished_with_result = True
                        state.result_is_error = failed
                        return _LoopStep("break")
            elif streamer and time.time() - state.last_persist >= 2.0:
                streamer.touch()
                state.last_persist = time.time()
            return _LoopStep("continue")

        state.pending_approval = None
        allow_input = resolution.updated_input
        if (allow_input is None or allow_input == {}) and resolution.approved:
            allow_input = state.pending_tool_input
        response = build_control_response(
            request_id=state.pending_request_id,
            approved=resolution.approved,
            message=resolution.message or "Rejected in Loregarden approval inbox",
            updated_input=allow_input if resolution.approved else None,
        )
        state.pending_tool_input = None
        self._send_response(proc, response)

        if not resolution.approved:
            proc.kill()
            self._mark_stage_blocked(ticket, "Permission denied via Loregarden inbox")
            run = self.session.get(AgentRun, run_id)
            if run:
                run.status = RunStatus.FAILED
                self.session.add(run)
                self.session.commit()
            return _LoopStep(
                "return",
                BridgeResult(
                    status=RunStatus.FAILED,
                    stdout="\n".join(state.stdout_lines),
                    stderr="Permission denied via Loregarden inbox",
                    session_id=state.session_id,
                ),
            )

        self._mark_stage_running(ticket)
        run = self.session.get(AgentRun, run_id)
        if run:
            run.status = RunStatus.RUNNING
            self.session.add(run)
            self.session.commit()
        if streamer:
            streamer.set_live("Agent running…")
        return _LoopStep("continue")

    def _scope_denial_result(
        self,
        *,
        ctx: _RunContext,
        ticket: Ticket,
        run_id: str,
        proc: Any,
        request_id: str,
        permission: dict[str, Any],
        streamer: RunLogStreamer | None,
        state: _LoopState,
    ) -> BridgeResult | None:
        """Hard technical boundary, checked before auto-approve/allowlist/
        human-approval paths — a scoped agent writing outside its declared
        directory is refused outright, not merely flagged for someone to
        approve around. This is the backstop for role docs like "Modify only
        code within /server/**" that were previously prompt text only (see
        ticket 33 postmortem: a backend_implementer agent implemented
        frontend code because nothing actually stopped it)."""
        tool_input = permission["tool_input"] if isinstance(permission["tool_input"], dict) else {}
        scope_denial = check_agent_scope(
            agent_id=ctx.agent_id,
            agent_name=ctx.agent_name,
            tool_name=permission["tool_name"],
            tool_input=tool_input,
            workspace_root=ctx.workspace_root,
        )
        if not scope_denial:
            return None

        self._send_response(
            proc,
            build_control_response(request_id=request_id, approved=False, message=scope_denial),
        )
        if streamer:
            streamer.append("TOOL", f"Denied (out of scope): {scope_denial}", force=True)
        self._mark_stage_blocked(ticket, scope_denial)
        run = self.session.get(AgentRun, run_id)
        if run:
            run.status = RunStatus.FAILED
            self.session.add(run)
            self.session.commit()
        proc.kill()
        return BridgeResult(
            status=RunStatus.FAILED,
            stdout="\n".join(state.stdout_lines),
            stderr=scope_denial,
            session_id=state.session_id,
        )

    def _try_fast_approve(
        self,
        *,
        ctx: _RunContext,
        ticket: Ticket,
        proc: Any,
        request_id: str,
        permission: dict[str, Any],
        bare_mcp: str | None,
        question: bool,
        streamer: RunLogStreamer | None,
    ) -> bool:
        """Auto-approve via the read-only-MCP allowlist, the run's
        auto_approve flag, or the persisted permission allowlist. Returns
        True if a response was already written (caller should treat the
        permission as handled and move on to the next line)."""
        tool_input = permission["tool_input"] if isinstance(permission["tool_input"], dict) else {}

        if bare_mcp and is_auto_approved_mcp_tool(permission["tool_name"]):
            enriched = enrich_mcp_tool_input(
                bare_tool=bare_mcp,
                tool_input=tool_input,
                ticket=ticket,
                workspace_slug=ctx.workspace_slug,
            )
            self._send_response(
                proc,
                build_control_response(
                    request_id=request_id, approved=True, updated_input=enriched
                ),
            )
            if streamer:
                streamer.append("TOOL", f"Auto-approved Loregarden MCP: {bare_mcp}", force=True)
                streamer.set_live("Agent running…")
            return True

        if ctx.auto_approve and not question:
            if bare_mcp:
                tool_input = enrich_mcp_tool_input(
                    bare_tool=bare_mcp,
                    tool_input=tool_input,
                    ticket=ticket,
                    workspace_slug=ctx.workspace_slug,
                )
            self._send_response(
                proc,
                build_control_response(
                    request_id=request_id, approved=True, updated_input=tool_input
                ),
            )
            if streamer:
                streamer.append("TOOL", f"Auto-approved: {permission['tool_name']}", force=True)
                streamer.set_live("Agent running…")
            return True

        if not question:
            allow_scope = is_permission_allowed(
                self.session,
                workspace_id=ticket.workspace_id,
                ticket_id=ticket.id,
                stage_key=ticket.workflow_stage_key,
                tool_name=permission["tool_name"],
                tool_input=tool_input,
            )
            if allow_scope:
                allow_input = tool_input
                if bare_mcp:
                    allow_input = enrich_mcp_tool_input(
                        bare_tool=bare_mcp,
                        tool_input=tool_input,
                        ticket=ticket,
                        workspace_slug=ctx.workspace_slug,
                    )
                self._send_response(
                    proc,
                    build_control_response(
                        request_id=request_id, approved=True, updated_input=allow_input
                    ),
                )
                if streamer:
                    streamer.append(
                        "TOOL",
                        f"Auto-approved ({allow_scope} allowlist): {permission['tool_name']}",
                        force=True,
                    )
                    streamer.set_live("Agent running…")
                return True

        return False

    def _handle_next_line(
        self,
        *,
        ctx: _RunContext,
        ticket: Ticket,
        run_id: str,
        proc: Any,
        invocation: CliInvocation,
        streamer: RunLogStreamer | None,
        stdout_reader: SubprocessLineReader,
        deadline: float,
        timeout_seconds: int,
        state: _LoopState,
    ) -> _LoopStep:
        line = stdout_reader.readline(timeout=1.0)
        if line is None:
            if streamer and time.time() - state.last_persist >= 2.0:
                streamer.touch()
                state.last_persist = time.time()
            return _LoopStep("continue")

        line = line.rstrip("\n")
        state.last_persist = time.time()
        state.stdout_lines.append(line)
        if streamer:
            streamer.append_stream_line(line)
        payload = _parse_ndjson_line(line)
        if not payload:
            return _LoopStep("continue")

        if payload.get("type") == "system" and payload.get("subtype") == "init":
            state.session_id = str(payload.get("session_id") or state.session_id)

        finished, failed = result_payload_status(payload)
        if finished and state.pending_approval is None:
            state.session_id = str(payload.get("session_id") or state.session_id)
            state.finished_with_result = True
            state.result_is_error = failed
            return _LoopStep("break")

        permission = extract_permission_request(payload)
        if not permission:
            return _LoopStep("continue")

        request_id = permission["request_id"] or f"perm_{len(state.stdout_lines)}"
        bare_mcp = bare_mcp_tool_name(permission["tool_name"])
        question = is_ask_user_question(permission["tool_name"])

        scope_result = self._scope_denial_result(
            ctx=ctx,
            ticket=ticket,
            run_id=run_id,
            proc=proc,
            request_id=request_id,
            permission=permission,
            streamer=streamer,
            state=state,
        )
        if scope_result:
            return _LoopStep("return", scope_result)

        if self._try_fast_approve(
            ctx=ctx,
            ticket=ticket,
            proc=proc,
            request_id=request_id,
            permission=permission,
            bare_mcp=bare_mcp,
            question=question,
            streamer=streamer,
        ):
            return _LoopStep("continue")

        if streamer:
            if question:
                streamer.append("TOOL", "Agent asked clarifying questions", force=True)
                streamer.set_live("Awaiting your answers…")
            else:
                streamer.append(
                    "TOOL", f"Permission requested: {permission['tool_name']}", force=True
                )
                streamer.set_live(f"Awaiting approval for {permission['tool_name']}…")

        if question:
            approval = self._create_question_approval(
                run_id=run_id,
                ticket=ticket,
                request_id=request_id,
                tool_input=permission["tool_input"],
                cli_adapter=invocation.adapter,
                cli_session_id=state.session_id,
            )
        else:
            approval = self._create_permission_approval(
                run_id=run_id,
                ticket=ticket,
                request_id=request_id,
                tool_name=permission["tool_name"],
                tool_input=permission["tool_input"],
                cli_adapter=invocation.adapter,
                cli_session_id=state.session_id,
            )

        remaining_for_approval = deadline - time.time()
        if remaining_for_approval <= 0:
            proc.kill()
            return _LoopStep(
                "return",
                BridgeResult(
                    status=RunStatus.FAILED,
                    stdout="\n".join(state.stdout_lines),
                    stderr=agent_timeout_message(timeout_seconds),
                    session_id=state.session_id,
                ),
            )

        state.pending_approval = approval
        state.pending_request_id = request_id
        state.pending_tool_input = (
            permission["tool_input"] if isinstance(permission["tool_input"], dict) else {}
        )
        state.approval_deadline = time.time() + min(
            remaining_for_approval, settings.permission_approval_timeout_seconds
        )
        return _LoopStep("continue")

    def _finalize(
        self,
        *,
        proc: Any,
        stdout_reader: SubprocessLineReader,
        deadline: float,
        timeout_seconds: int,
        state: _LoopState,
        stderr_lines: list[str],
        streamer: RunLogStreamer | None,
    ) -> BridgeResult:
        import subprocess

        if state.finished_with_result:
            _close_stdin(proc)
            _drain_stdout_after_result(proc, stdout_reader, state.stdout_lines, streamer=streamer)

        remaining = deadline - time.time()
        if proc.poll() is None:
            if remaining <= 0:
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                return BridgeResult(
                    status=RunStatus.FAILED,
                    stdout="\n".join(state.stdout_lines),
                    stderr=agent_timeout_message(timeout_seconds),
                    session_id=state.session_id,
                )
            try:
                proc.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                return BridgeResult(
                    status=RunStatus.FAILED,
                    stdout="\n".join(state.stdout_lines),
                    stderr=agent_timeout_message(timeout_seconds),
                    session_id=state.session_id,
                )
        elif proc.returncode is None:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        stderr_stream = proc.stderr
        if stderr_stream:
            stderr_lines.extend(stderr_stream.read().decode("utf-8", errors="replace").splitlines())

        stdout = "\n".join(state.stdout_lines)
        stderr = "\n".join(stderr_lines)
        if state.finished_with_result:
            status = RunStatus.FAILED if state.result_is_error else RunStatus.SUCCEEDED
        else:
            status = RunStatus.SUCCEEDED if proc.returncode == 0 else RunStatus.FAILED
        return BridgeResult(
            status=status, stdout=stdout, stderr=stderr, session_id=state.session_id
        )

    def _create_question_approval(
        self,
        *,
        run_id: str,
        ticket: Ticket,
        request_id: str,
        tool_input: Any,
        cli_adapter: str,
        cli_session_id: str,
    ) -> Approval:
        questions = tool_input.get("questions") if isinstance(tool_input, dict) else []
        first_question = ""
        if questions and isinstance(questions[0], dict):
            first_question = str(questions[0].get("question") or "").strip()
        summary = first_question or "Agent needs input before continuing."
        if len(questions) > 1:
            summary = f"{summary} (+{len(questions) - 1} more)"

        if self.track_workflow_stage:
            instance, stages = self.orch._resolve_stages(ticket)
            if instance and stages and ticket.workflow_stage_key:
                set_stage_status(
                    ticket,
                    instance,
                    stages,
                    ticket.workflow_stage_key,
                    StageStatus.AWAITING,
                )
                self.session.add(instance)

        approval = Approval(
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            run_id=run_id,
            kind=ApprovalKind.CLI_QUESTION,
            title="Agent questions",
            level="medium",
            stage_key=ticket.workflow_stage_key if self.track_workflow_stage else "triage",
            impact=summary[:2000],
            permission_request_id=request_id,
            tool_name=ASK_USER_QUESTION_TOOL,
            tool_input_json=serialize_tool_input(tool_input),
            cli_adapter=cli_adapter,
            cli_session_id=cli_session_id,
            status=ApprovalStatus.PENDING,
        )
        ticket.revision += 1
        ticket.last_updated_by = "permission_bridge"
        ticket.updated_at = datetime.now(timezone.utc)
        self.session.add(ticket)
        self.session.add(approval)
        self.session.commit()
        self.session.refresh(approval)

        run = self.session.get(AgentRun, run_id)
        if run:
            run.status = RunStatus.AWAITING_PERMISSION
            self.session.add(run)
            self.session.commit()
        return approval

    def _create_permission_approval(
        self,
        *,
        run_id: str,
        ticket: Ticket,
        request_id: str,
        tool_name: str,
        tool_input: Any,
        cli_adapter: str,
        cli_session_id: str,
    ) -> Approval:
        if self.track_workflow_stage:
            instance, stages = self.orch._resolve_stages(ticket)
            if instance and stages and ticket.workflow_stage_key:
                set_stage_status(
                    ticket,
                    instance,
                    stages,
                    ticket.workflow_stage_key,
                    StageStatus.AWAITING,
                )
                self.session.add(instance)

        approval = Approval(
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            run_id=run_id,
            kind=ApprovalKind.CLI_PERMISSION,
            title=f"Allow {tool_name}?",
            level="high",
            stage_key=ticket.workflow_stage_key if self.track_workflow_stage else "triage",
            impact=(
                f"Agent requested `{tool_name}` during stage `{ticket.workflow_stage_key}`."
                if self.track_workflow_stage
                else f"Agent requested `{tool_name}` during triage."
            ),
            permission_request_id=request_id,
            tool_name=tool_name,
            tool_input_json=serialize_tool_input(tool_input),
            cli_adapter=cli_adapter,
            cli_session_id=cli_session_id,
            status=ApprovalStatus.PENDING,
        )
        ticket.revision += 1
        ticket.last_updated_by = "permission_bridge"
        ticket.updated_at = datetime.now(timezone.utc)
        self.session.add(ticket)
        self.session.add(approval)
        self.session.commit()
        self.session.refresh(approval)

        run = self.session.get(AgentRun, run_id)
        if run:
            run.status = RunStatus.AWAITING_PERMISSION
            self.session.add(run)
            self.session.commit()
        return approval

    def _mark_stage_running(self, ticket: Ticket) -> None:
        if not self.track_workflow_stage:
            return
        instance, stages = self.orch._resolve_stages(ticket)
        if instance and stages and ticket.workflow_stage_key:
            set_stage_status(
                ticket,
                instance,
                stages,
                ticket.workflow_stage_key,
                StageStatus.RUNNING,
            )
            self.session.add(instance)
            self.session.add(ticket)
            self.session.commit()

    def _mark_stage_blocked(self, ticket: Ticket, message: str) -> None:
        if not self.track_workflow_stage:
            return
        instance, stages = self.orch._resolve_stages(ticket)
        if instance and stages and ticket.workflow_stage_key:
            set_stage_status(
                ticket,
                instance,
                stages,
                ticket.workflow_stage_key,
                StageStatus.BLOCKED,
            )
            self.session.add(instance)
        ticket.blocking_issues = message[:2000]
        ticket.revision += 1
        self.session.add(ticket)
        self.session.commit()
