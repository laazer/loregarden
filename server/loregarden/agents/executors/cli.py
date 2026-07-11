import json
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from loregarden.agents.cli_adapters import (
    CliInvocation,
    resolve_cli_invocation,
    resolve_terminal_handoff_invocation,
)
from loregarden.agents.executors.permission_bridge import PermissionBridgeRunner
from loregarden.agents.mcp_context import (
    build_mcp_run_context,
    load_loregarden_mcp_doc,
    load_memory_protocol_doc,
)
from loregarden.agents.registry import get_agent
from loregarden.agents.stage_context import build_orchestration_context
from loregarden.models.domain import AgentRun, RunStatus, Ticket, Workspace
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.run_errors import agent_timeout_message
from loregarden.services.run_log_stream import RunLogStreamer
from loregarden.services.studio_service import build_studio_prompt_sections
from loregarden.services.subprocess_lines import SubprocessLineReader
from loregarden.services.workspace_paths import resolve_agent_context_dir, resolve_workspace_root
from loregarden.skills.registry import get_skill
from sqlmodel import Session


class CliAgentExecutor:
    """Spawn local CLI agents via subprocess."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.orchestration = OrchestrationService(session)

    def execute(
        self,
        run: AgentRun,
        ticket: Ticket,
        *,
        advance_workflow: bool = True,
        skip_git_branch: bool = False,
    ) -> AgentRun:
        agent = get_agent(run.agent_id)
        if not agent:
            return self.orchestration.complete_run(
                run,
                status=RunStatus.FAILED,
                stderr=f"Unknown agent: {run.agent_id}",
                advance_workflow=advance_workflow,
            )

        workspace = self.session.get(Workspace, ticket.workspace_id)
        if not workspace:
            return self.orchestration.complete_run(
                run,
                status=RunStatus.FAILED,
                stderr=f"Unknown workspace for ticket: {ticket.id}",
                advance_workflow=advance_workflow,
            )

        repo_root = resolve_workspace_root(workspace)
        agent_context_dir = resolve_agent_context_dir(workspace)
        if not repo_root.is_dir():
            return self.orchestration.complete_run(
                run,
                status=RunStatus.FAILED,
                stderr=f"Workspace repo path does not exist: {repo_root}",
                advance_workflow=advance_workflow,
            )

        from loregarden.services.git_branch import ensure_ticket_branch

        if not skip_git_branch:
            try:
                ensure_ticket_branch(repo_root, ticket)
            except (ValueError, subprocess.CalledProcessError) as exc:
                return self.orchestration.complete_run(
                    run,
                    status=RunStatus.FAILED,
                    stderr=f"Failed to checkout branch: {exc}",
                    advance_workflow=advance_workflow,
                )

        prompt = self._build_prompt(ticket, run, agent, agent_context_dir, workspace)
        with tempfile.TemporaryDirectory(prefix="loregarden-run-") as tmp:
            prompt_file = Path(tmp) / "prompt.md"
            prompt_file.write_text(prompt, encoding="utf-8")

            try:
                invocation = resolve_cli_invocation(
                    agent_id=run.agent_id,
                    adapter=agent.get("adapter", "local"),
                    prompt=prompt,
                    prompt_file=prompt_file,
                    skill_name=run.skill_name,
                    workspace_root=repo_root,
                    workspace=workspace,
                )
            except ValueError as exc:
                return self.orchestration.complete_run(
                    run,
                    status=RunStatus.FAILED,
                    stderr=str(exc),
                    advance_workflow=advance_workflow,
                )

            run.command = " ".join(invocation.argv)
            self.session.add(run)
            self.session.commit()

            streamer = RunLogStreamer(
                run_id=run.id,
                ticket_id=ticket.id,
                run_code=run.run_code,
                agent_id=run.agent_id,
                skill_name=run.skill_name,
            )
            streamer.start(run.command)

            timeout = agent.get("timeout", 120)
            try:
                if invocation.interactive:
                    bridge = PermissionBridgeRunner(self.session)
                    result = bridge.run(
                        run_id=run.id,
                        ticket=ticket,
                        invocation=invocation,
                        prompt=prompt,
                        timeout_seconds=timeout,
                        streamer=streamer,
                    )
                    stdout, stderr, status = result.stdout, result.stderr, result.status
                else:
                    stdout, stderr, status = self._run_print_mode(
                        invocation=invocation,
                        repo_root=repo_root,
                        timeout=timeout,
                        streamer=streamer,
                    )

                streamer.finalize(status=status, stderr=stderr)
                artifacts = self._build_context_artifact(ticket, run, status)
                completed = self.orchestration.complete_run(
                    run,
                    status=status,
                    stdout=stdout,
                    stderr=stderr,
                    artifacts=artifacts,
                    advance_workflow=advance_workflow,
                )
                self._touch_ticket_agent(ticket, agent.get("name", run.agent_id), status)
                return completed
            except subprocess.TimeoutExpired:
                msg = agent_timeout_message(timeout)
                streamer.finalize(status=RunStatus.FAILED, stderr=msg)
                return self.orchestration.complete_run(
                    run,
                    status=RunStatus.FAILED,
                    stderr=msg,
                    advance_workflow=advance_workflow,
                )
            except OSError as exc:
                streamer.finalize(status=RunStatus.FAILED, stderr=str(exc))
                return self.orchestration.complete_run(
                    run,
                    status=RunStatus.FAILED,
                    stderr=f"Failed to spawn agent CLI: {exc}",
                    advance_workflow=advance_workflow,
                )

    def prepare_terminal_handoff(
        self, run: AgentRun, ticket: Ticket
    ) -> tuple[CliInvocation, Path | None]:
        """Build a self-contained CLI invocation for this stage without spawning it.

        Used to hand a stage off to a human's own terminal instead of the app's subprocess
        supervision, which dies if the app server restarts mid-run. The system prompt is
        written to a real file on disk (not inlined into the returned command) since a full
        stage prompt can run tens of KB — pasting that much text directly into a terminal can
        overwhelm some terminals' paste handling. Returns the prompt file's containing
        directory as a cleanup path when one was written, else None.
        """
        agent = get_agent(run.agent_id)
        if not agent:
            raise ValueError(f"Unknown agent: {run.agent_id}")

        workspace = self.session.get(Workspace, ticket.workspace_id)
        if not workspace:
            raise ValueError(f"Unknown workspace for ticket: {ticket.id}")

        repo_root = resolve_workspace_root(workspace)
        agent_context_dir = resolve_agent_context_dir(workspace)
        if not repo_root.is_dir():
            raise ValueError(f"Workspace repo path does not exist: {repo_root}")

        from loregarden.services.git_branch import ensure_ticket_branch

        ensure_ticket_branch(repo_root, ticket)

        prompt = self._build_prompt(ticket, run, agent, agent_context_dir, workspace)
        prompt_dir = Path(tempfile.mkdtemp(prefix="loregarden-handoff-"))
        prompt_file = prompt_dir / "prompt.md"
        invocation = resolve_terminal_handoff_invocation(
            agent_id=run.agent_id,
            adapter=agent.get("adapter", "local"),
            prompt=prompt,
            prompt_file=prompt_file,
            skill_name=run.skill_name,
            workspace_root=repo_root,
            workspace=workspace,
        )
        cleanup_path: Path | None = None
        if invocation.use_prompt_file:
            prompt_file.write_text(prompt, encoding="utf-8")
            cleanup_path = prompt_dir
        else:
            prompt_dir.rmdir()

        run.command = f"[terminal-handoff] {' '.join(invocation.argv)}"
        self.session.add(run)
        self.session.commit()
        return invocation, cleanup_path

    def _run_print_mode(
        self,
        *,
        invocation,
        repo_root: Path,
        timeout: int,
        streamer: RunLogStreamer,
    ) -> tuple[str, str, RunStatus]:
        proc = subprocess.Popen(
            invocation.argv,
            cwd=invocation.cwd or str(repo_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE if invocation.stdin_prompt else None,
            bufsize=0,
        )
        if invocation.stdin_prompt and proc.stdin:
            proc.stdin.write(invocation.stdin_prompt.encode("utf-8"))
            proc.stdin.close()

        stdout_lines: list[str] = []
        assert proc.stdout is not None
        reader = SubprocessLineReader(proc.stdout)
        deadline = time.time() + timeout
        try:
            while True:
                if proc.poll() is not None and reader.readline(timeout=0) is None:
                    break
                if time.time() >= deadline:
                    proc.kill()
                    raise subprocess.TimeoutExpired(invocation.argv, timeout)
                line = reader.readline(timeout=0.5)
                if line is None:
                    if proc.poll() is not None:
                        break
                    continue
                line = line.rstrip("\n")
                stdout_lines.append(line)
                streamer.append_stream_line(line)
        finally:
            if proc.poll() is None:
                try:
                    proc.wait(timeout=max(0.1, deadline - time.time()))
                except subprocess.TimeoutExpired:
                    proc.kill()
                    raise subprocess.TimeoutExpired(invocation.argv, timeout) from None

        stderr = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
        stdout = "\n".join(stdout_lines)
        status = RunStatus.SUCCEEDED if proc.returncode == 0 else RunStatus.FAILED
        return stdout, stderr, status

    def _touch_ticket_agent(self, ticket: Ticket, agent_name: str, status: RunStatus) -> None:
        ticket.last_updated_by = agent_name
        ticket.revision += 1
        if status == RunStatus.SUCCEEDED:
            ticket.next_status = "Proceed"
        else:
            ticket.next_status = "Blocked"
        ticket.updated_at = datetime.now(timezone.utc)
        self.session.add(ticket)
        self.session.commit()

    def _build_prompt(
        self,
        ticket: Ticket,
        run: AgentRun,
        agent: dict,
        agent_context_dir: Path,
        workspace: Workspace,
    ) -> str:
        role_path = agent_context_dir / agent.get("role_file", "")
        role_body = agent.get("role_body") or ""
        if not role_body and role_path.is_file():
            role_body = role_path.read_text(encoding="utf-8")[:12000]

        skill_body = (
            get_skill(
                run.skill_name or agent.get("default_skill", ""),
                agent_context_dir=agent_context_dir,
            )
            or ""
        )
        ac = json.loads(ticket.acceptance_criteria_json or "[]")

        template = self.orchestration.get_template_for_ticket(ticket)
        stage_def = None
        if template:
            from loregarden.core.workflow_loader import get_template_stages

            stage_def = next(
                (stage for stage in get_template_stages(template) if stage.key == run.stage_key),
                None,
            )
        orchestration_context = build_orchestration_context(
            ticket=ticket,
            run=run,
            stage_def=stage_def,
        )
        mcp_context = build_mcp_run_context(ticket=ticket, run=run, workspace=workspace)
        mcp_doc = load_loregarden_mcp_doc(agent_context_dir)
        memory_doc = load_memory_protocol_doc(agent_context_dir)

        sections = [
            f"# Run: {run.run_code}",
            orchestration_context,
            "",
            mcp_context,
            "",
            f"Ticket: {ticket.external_id} — {ticket.title}",
            f"Stage: {run.stage_key}",
            f"Skill: {run.skill_name or '—'}",
            "",
            "## Description",
            ticket.description,
            "",
            "## Acceptance Criteria",
            *[f"- {item}" for item in ac],
        ]
        if skill_body:
            sections.extend(["", "## Skill", skill_body[:3000]])
        if role_body:
            sections.extend(["", "## Agent Role", role_body])
        studio_sections = build_studio_prompt_sections(agent)
        if studio_sections:
            sections.extend(["", studio_sections])
        if mcp_doc:
            sections.extend(["", "## Loregarden MCP module", mcp_doc[:12000]])
        if memory_doc:
            sections.extend(["", "## Memory protocol module", memory_doc[:8000]])
        sections.extend(
            [
                "",
                "## Permission policy",
                "Request human approval via Loregarden before destructive or high-risk tool use.",
                "Do not bypass workspace permission checks.",
            ]
        )
        return "\n".join(sections)

    def _build_context_artifact(
        self,
        ticket: Ticket,
        run: AgentRun,
        status: RunStatus,
    ) -> list[dict]:
        return [
            {
                "kind": "context",
                "title": "Run context",
                "content": {
                    "sections": [
                        {
                            "title": "Execution",
                            "rows": [
                                {"k": "Run", "v": run.run_code},
                                {"k": "Ticket", "v": ticket.external_id},
                                {"k": "Agent", "v": run.agent_id},
                                {"k": "Skill", "v": run.skill_name or "—"},
                                {"k": "Stage", "v": run.stage_key},
                                {"k": "Command", "v": run.command or "—"},
                                {"k": "Status", "v": status.value},
                            ],
                        }
                    ]
                },
            },
        ]
