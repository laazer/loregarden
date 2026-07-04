import json
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import Session

from loregarden.agents.cli_adapters import resolve_cli_invocation
from loregarden.agents.registry import get_agent
from loregarden.models.domain import AgentRun, RunStatus, Ticket, Workspace
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.workspace_paths import resolve_agent_context_dir, resolve_workspace_root
from loregarden.skills.registry import get_skill


class CliAgentExecutor:
    """Spawn local CLI agents via subprocess."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.orchestration = OrchestrationService(session)

    def execute(self, run: AgentRun, ticket: Ticket) -> AgentRun:
        agent = get_agent(run.agent_id)
        if not agent:
            return self.orchestration.complete_run(
                run,
                status=RunStatus.FAILED,
                stderr=f"Unknown agent: {run.agent_id}",
            )

        workspace = self.session.get(Workspace, ticket.workspace_id)
        if not workspace:
            return self.orchestration.complete_run(
                run,
                status=RunStatus.FAILED,
                stderr=f"Unknown workspace for ticket: {ticket.id}",
            )

        repo_root = resolve_workspace_root(workspace)
        agent_context_dir = resolve_agent_context_dir(workspace)
        if not repo_root.is_dir():
            return self.orchestration.complete_run(
                run,
                status=RunStatus.FAILED,
                stderr=f"Workspace repo path does not exist: {repo_root}",
            )

        prompt = self._build_prompt(ticket, run, agent, agent_context_dir)
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
                )
            except ValueError as exc:
                return self.orchestration.complete_run(
                    run,
                    status=RunStatus.FAILED,
                    stderr=str(exc),
                )

            run.command = " ".join(invocation.argv)
            self.session.add(run)
            self.session.commit()

            try:
                proc = subprocess.run(
                    invocation.argv,
                    cwd=str(repo_root),
                    capture_output=True,
                    text=True,
                    input=invocation.stdin_prompt,
                    timeout=agent.get("timeout", 120),
                    check=False,
                )
                stdout = proc.stdout
                stderr = proc.stderr
                status = RunStatus.SUCCEEDED if proc.returncode == 0 else RunStatus.FAILED
                artifacts = self._build_artifacts(ticket, run, stdout, stderr, status)
                completed = self.orchestration.complete_run(
                    run,
                    status=status,
                    stdout=stdout,
                    stderr=stderr,
                    artifacts=artifacts,
                )
                self._touch_ticket_agent(ticket, agent.get("name", run.agent_id), status)
                return completed
            except subprocess.TimeoutExpired as exc:
                return self.orchestration.complete_run(
                    run,
                    status=RunStatus.FAILED,
                    stderr=f"Agent timed out after {agent.get('timeout', 120)}s: {exc}",
                )
            except OSError as exc:
                return self.orchestration.complete_run(
                    run,
                    status=RunStatus.FAILED,
                    stderr=f"Failed to spawn agent CLI: {exc}",
                )

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
    ) -> str:
        role_path = agent_context_dir / agent.get("role_file", "")
        role_body = ""
        if role_path.is_file():
            role_body = role_path.read_text(encoding="utf-8")[:12000]

        skill_body = get_skill(run.skill_name, agent_context_dir=agent_context_dir) or ""
        ac = json.loads(ticket.acceptance_criteria_json or "[]")

        sections = [
            f"# Run: {run.run_code}",
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
        return "\n".join(sections)

    def _build_artifacts(
        self,
        ticket: Ticket,
        run: AgentRun,
        stdout: str,
        stderr: str,
        status: RunStatus,
    ) -> list[dict]:
        now = datetime.now(timezone.utc).strftime("%H:%M:%S")
        logs: list[dict] = [
            {"time": now, "tag": "RUN", "text": f"{run.agent_id} invoked · skill={run.skill_name or '—'}"},
        ]
        for line in stdout.strip().splitlines()[:40]:
            logs.append({"time": now, "tag": "OUT", "text": line})
        for line in stderr.strip().splitlines()[:20]:
            logs.append({"time": now, "tag": "ERR", "text": line})
        if status == RunStatus.SUCCEEDED:
            logs.append({"time": now, "tag": "OK", "text": "run completed"})
        else:
            logs.append({"time": now, "tag": "FAIL", "text": "run failed"})

        return [
            {
                "kind": "log",
                "title": f"Run {run.run_code}",
                "content": {"lines": logs, "live": None},
            },
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
