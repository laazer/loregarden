import json
import logging
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
from loregarden.agents.inherited_wisdom import build_inherited_wisdom
from loregarden.agents.mcp_context import (
    build_mcp_run_context,
    load_loregarden_mcp_doc,
    load_memory_protocol_doc,
    load_stage_report_contract_doc,
)
from loregarden.agents.plan_context import (
    SYNTHESIS_SKILL,
    build_plan_context,
    build_plan_synthesis_context,
)
from loregarden.agents.registry import get_agent
from loregarden.agents.stage_context import build_orchestration_context
from loregarden.agents.verify_context import build_verify_context
from loregarden.models.domain import AgentRun, RunStatus, Ticket, WorkflowStageDef, Workspace
from loregarden.services.cli_settings import (
    get_ticket_orchestration_runtime,
    resolve_claude_model,
    weak_mcp_model_warning,
)
from loregarden.services.code_map import render_code_map
from loregarden.services.compatibility_posture import resolve_compatibility_posture
from loregarden.services.evidence import FULL_SUITE_EVIDENCE_KIND, full_suite_green_at_head
from loregarden.services.git_branch import ensure_ticket_branch
from loregarden.services.git_commit_push_service import working_tree_paths
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.run_errors import agent_timeout_message
from loregarden.services.run_log_stream import RunLogStreamer
from loregarden.services.studio_routing import VERIFY_STAGE_TYPE
from loregarden.services.studio_service import build_studio_prompt_sections
from loregarden.services.subprocess_lines import SubprocessLineReader
from loregarden.services.workspace_paths import resolve_agent_context_dir, resolve_workspace_root
from loregarden.skills.registry import SKILL_PROMPT_CAP, get_skill
from sqlmodel import Session

logger = logging.getLogger(__name__)

# A run's configured timeout is treated as an *idle* budget — the longest the
# agent may go producing no output before it is presumed hung and killed (the
# same moment the old fixed wall-clock deadline would have fired). As long as it
# keeps streaming, it may run until an absolute ceiling of this multiple of that
# budget, so a long-but-progressing test run is no longer killed mid-progress
# while a chatty runaway is still bounded.
_TIMEOUT_HARD_CAP_MULTIPLIER = 4

# Skills whose stages run or consume the full regression suite. The suite-runner
# is told to record its green result as commit-scoped evidence; the reviewer is
# told it may skip re-running the suite when that evidence already covers the
# exact tree it is about to test. Keyed on skill rather than stage key so a
# workflow that renames its stages still gets the behaviour by using these skills.
_FULL_SUITE_SKILL = "run_tests"
_SUITE_REVIEW_SKILL = "ac_gate"


def _titled_block(title: str, body: str, *, cap: int = 0) -> list[str]:
    """A titled prompt block, or nothing when the body is empty.

    The leading blank line lives here so callers only declare order.
    """
    if not body:
        return []
    return ["", title, body[:cap] if cap else body]


def _raw_block(body: str) -> list[str]:
    """An untitled prompt block that supplies its own headings."""
    return ["", body] if body else []


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

        stage_def = self._resolve_stage_def(ticket, run)
        ticket_runtime = get_ticket_orchestration_runtime(ticket)

        # Bracket the run so its commit can be scoped to what it touched. Paths
        # already dirty beforehand belong to whatever else is in the workspace
        # and must not be attributed to this ticket.
        paths_before = working_tree_paths(repo_root)

        prompt = self._build_prompt(ticket, run, agent, agent_context_dir, workspace, stage_def)
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
                    ticket_adapter=ticket_runtime.cli_adapter,
                    ticket_claude_model=ticket_runtime.claude_model,
                    ticket_cursor_model=ticket_runtime.cursor_model,
                    stage_model=stage_def.model if stage_def else "",
                    agent_model=agent.get("default_model", ""),
                    run_id=run.id,
                    workspace_slug=workspace.slug,
                    granted_tools=agent.get("mcp_tools") or [],
                    db_session=self.session,
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

            resolved_claude_model = resolve_claude_model(
                workspace,
                ticket_model=ticket_runtime.claude_model,
                stage_model=stage_def.model if stage_def else "",
                agent_model=agent.get("default_model", ""),
            )
            model_warning = weak_mcp_model_warning(
                resolved_claude_model, agent.get("adapter", "local")
            )
            if model_warning:
                logger.warning("run %s (%s): %s", run.run_code, run.agent_id, model_warning)
                streamer.append("WARN", model_warning, force=True)

            timeout = (
                run.timeout_override_seconds
                if run.timeout_override_seconds is not None
                else agent.get("timeout", 120)
            )
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
                self._record_changed_paths(run, repo_root, paths_before)
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
            except subprocess.TimeoutExpired as exc:
                return self._complete_timed_out_run(
                    run,
                    exc,
                    fallback_timeout=timeout,
                    repo_root=repo_root,
                    paths_before=paths_before,
                    streamer=streamer,
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

    def _complete_timed_out_run(
        self,
        run: AgentRun,
        exc: subprocess.TimeoutExpired,
        *,
        fallback_timeout: int,
        repo_root: Path,
        paths_before: set[str],
        streamer: RunLogStreamer,
        advance_workflow: bool,
    ) -> AgentRun:
        """Complete a run the agent was killed for exceeding its budget.

        Preserves the work done before the kill: the changed files are recorded
        (so they are scoped to this run's commit rather than lost or swept into
        an unrelated ticket) and the partial stdout is kept for the run log. A
        FAILED run can never advance the stage, so preserving output cannot
        mis-mark the stage done.
        """
        msg = agent_timeout_message(exc.timeout or fallback_timeout)
        streamer.finalize(status=RunStatus.FAILED, stderr=msg)
        self._record_changed_paths(run, repo_root, paths_before)
        return self.orchestration.complete_run(
            run,
            status=RunStatus.FAILED,
            stdout=exc.output if isinstance(exc.output, str) else "",
            stderr=msg,
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

        stage_def = self._resolve_stage_def(ticket, run)
        prompt = self._build_prompt(ticket, run, agent, agent_context_dir, workspace, stage_def)
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
        # Two independent limits. `idle_deadline` fires after `timeout` seconds
        # with no output — a presumed hang, killed exactly when the old fixed
        # deadline would have. `hard_deadline` is a generous absolute ceiling so
        # an agent that keeps streaming (e.g. a long test run emitting progress)
        # survives past `timeout`, yet a runaway that streams forever is still
        # bounded.
        start = time.time()
        idle_deadline = start + timeout
        hard_deadline = start + timeout * _TIMEOUT_HARD_CAP_MULTIPLIER
        try:
            while True:
                if proc.poll() is not None and reader.readline(timeout=0) is None:
                    break
                now = time.time()
                if now >= idle_deadline or now >= hard_deadline:
                    proc.kill()
                    raise self._timeout_expired(invocation.argv, start, stdout_lines)
                line = reader.readline(timeout=0.5)
                if line is None:
                    if proc.poll() is not None:
                        break
                    continue
                line = line.rstrip("\n")
                stdout_lines.append(line)
                streamer.append_stream_line(line)
                # Output is progress: extend the idle budget. The hard cap never
                # moves.
                idle_deadline = time.time() + timeout
        finally:
            if proc.poll() is None:
                try:
                    proc.wait(timeout=max(0.1, hard_deadline - time.time()))
                except subprocess.TimeoutExpired:
                    proc.kill()
                    raise self._timeout_expired(invocation.argv, start, stdout_lines) from None

        stderr = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
        stdout = "\n".join(stdout_lines)
        status = RunStatus.SUCCEEDED if proc.returncode == 0 else RunStatus.FAILED
        return stdout, stderr, status

    @staticmethod
    def _timeout_expired(argv, start: float, stdout_lines: list[str]) -> subprocess.TimeoutExpired:
        """A TimeoutExpired carrying the real elapsed time and whatever the agent
        streamed before it was killed, so the caller can report an accurate
        duration and preserve the partial output."""
        return subprocess.TimeoutExpired(
            argv, int(time.time() - start), output="\n".join(stdout_lines)
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

    def _resolve_template_stages(self, ticket: Ticket) -> list[WorkflowStageDef]:
        template = self.orchestration.get_template_for_ticket(ticket)
        if not template:
            return []
        from loregarden.core.workflow_loader import get_template_stages

        return list(get_template_stages(template))

    def _resolve_stage_def(self, ticket: Ticket, run: AgentRun) -> WorkflowStageDef | None:
        return next(
            (
                stage
                for stage in self._resolve_template_stages(ticket)
                if stage.key == run.stage_key
            ),
            None,
        )

    def _full_suite_evidence_note(
        self, skill_name: str, ticket: Ticket, workspace: Workspace
    ) -> str:
        """Guidance about the full regression suite for this stage, or "".

        The suite-runner is told to record a green run as commit-scoped evidence.
        The reviewer is told it may skip re-running the suite, but only when that
        evidence already proves the exact tree it is about to test — same HEAD,
        clean working tree; otherwise it says nothing and the reviewer runs it.
        """
        if skill_name == _FULL_SUITE_SKILL:
            return (
                "When the full suite passes, record it as reusable proof so a later "
                "stage need not re-run it: call `loregarden_attach_evidence` with "
                f"`evidence_kind: {FULL_SUITE_EVIDENCE_KIND}` (the commit is stamped "
                "server-side). Do this only for a genuinely green full run — not a "
                "partial or scoped one."
            )
        if skill_name == _SUITE_REVIEW_SKILL and full_suite_green_at_head(
            self.session, ticket, resolve_workspace_root(workspace)
        ):
            return (
                "The full regression suite already passed at the current commit with a "
                "clean working tree (recorded as "
                f"`{FULL_SUITE_EVIDENCE_KIND}` evidence). Do not re-run the full suite; "
                "review the change itself and run only the fast static checks. If you "
                "make any edit, that proof no longer holds — run the full suite before "
                "reporting `pass`."
            )
        return ""

    def _build_prompt(
        self,
        ticket: Ticket,
        run: AgentRun,
        agent: dict,
        agent_context_dir: Path,
        workspace: Workspace,
        stage_def: WorkflowStageDef | None,
    ) -> str:
        # Role body comes from the agent config (DB-backed studio agent, or the
        # registry fallback which loads it in get_agent). The executor no longer
        # reads role_file from the workspace filesystem — the DB is authoritative.
        role_body = (agent.get("role_body") or "")[:12000]

        skill_name = run.skill_name or agent.get("default_skill", "")
        skill_body = get_skill(skill_name, agent_context_dir=agent_context_dir) or ""
        if skill_name and not skill_body:
            # A stage naming a skill that has no file used to produce an empty
            # section and no other trace, so the template kept claiming guidance
            # the agent never received.
            logger.warning(
                "run %s: stage %s declares skill %r, which is not registered",
                run.run_code,
                run.stage_key,
                skill_name,
            )
        ac = json.loads(ticket.acceptance_criteria_json or "[]")

        orchestration_context = build_orchestration_context(
            ticket=ticket,
            run=run,
            stage_def=stage_def,
            stages=self._resolve_template_stages(ticket),
            posture=resolve_compatibility_posture(self.session, ticket, workspace),
        )
        mcp_context = build_mcp_run_context(
            ticket=ticket, run=run, workspace=workspace, stage_def=stage_def
        )
        mcp_doc = load_loregarden_mcp_doc(agent_context_dir)
        memory_doc = load_memory_protocol_doc(agent_context_dir)
        stage_report_doc = load_stage_report_contract_doc(agent_context_dir)
        is_verify = stage_def is not None and stage_def.stage_type == VERIFY_STAGE_TYPE
        is_synthesis = skill_name == SYNTHESIS_SKILL
        full_suite_note = self._full_suite_evidence_note(skill_name, ticket, workspace)

        # Ordered prompt blocks. Add a section by inserting a block here rather
        # than threading another conditional through the assembly; each block
        # carries its own leading blank line and drops out when empty.
        blocks: list[list[str]] = [
            [
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
            ],
            # High in the prompt: it governs whether the agent runs the suite at
            # all, so it must land before the role text that tells it to.
            _titled_block("## Full test suite", full_suite_note),
            # A verifier is deliberately starved of inherited context. Handing it
            # the prior stage's settled decisions ("do not re-derive") would make
            # it a reader of that reasoning rather than an independent check, and
            # a verifier that agrees because it was told to proves nothing.
            _titled_block(
                "## Inherited context (already decided — do not re-derive)",
                "" if is_verify else build_inherited_wisdom(ticket, workspace.slug),
            ),
            _titled_block(
                "## Claim under review",
                build_verify_context(self.session, ticket, workspace) if is_verify else "",
            ),
            # Alongside inherited context, and withheld from a verifier for the
            # same reason: the plan is the reasoning a verifier must not inherit.
            _titled_block(
                "## Plan (settled by the plan stage)",
                "" if is_verify else build_plan_context(self.session, ticket, run.stage_key),
            ),
            # The synthesizer gets the lanes instead of the settled plan — there
            # is no settled plan yet, producing it is the job.
            _titled_block(
                "## Plans to reconcile",
                build_plan_synthesis_context(self.session, ticket) if is_synthesis else "",
            ),
            # Before the role, so an agent knows the shape of the repo before it
            # is told its job. The implementers run on cursor, which does not
            # pick up CLAUDE.md the way Claude Code does, so without this they
            # rediscover the layout by grepping on every run.
            _titled_block("## Repository map", render_code_map(resolve_workspace_root(workspace))),
            _titled_block("## Skill", skill_body, cap=SKILL_PROMPT_CAP),
            _titled_block("## Agent Role", role_body),
            _raw_block(build_studio_prompt_sections(agent)),
            _titled_block("## Loregarden MCP module", mcp_doc, cap=12000),
            _titled_block("## Memory protocol module", memory_doc, cap=8000),
            [
                "",
                "## Permission policy",
                "Request human approval via Loregarden before destructive or high-risk tool use.",
                "Do not bypass workspace permission checks.",
            ],
            # Last, because it governs the last thing the agent emits.
            _titled_block("## Stage report contract", stage_report_doc),
        ]
        return "\n".join(line for block in blocks for line in block)

    def _record_changed_paths(self, run: AgentRun, repo_root: Path, before: set[str]) -> None:
        """Store the paths this run made dirty, so its commit can be scoped.

        Only the delta: a path already dirty when the run started belongs to
        whatever else is in the workspace, and attributing it here is exactly how
        unrelated work used to get swept into a ticket's commit.
        """
        touched = sorted(working_tree_paths(repo_root) - before)
        if not touched:
            return
        run.changed_paths_json = json.dumps(touched)
        self.session.add(run)
        self.session.commit()

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
