import json
import logging
import subprocess
import tempfile
from collections.abc import Callable
from functools import partial
from pathlib import Path

from loregarden.agents.cli_adapters import (
    CliInvocation,
    resolve_cli_invocation,
    resolve_terminal_handoff_invocation,
)
from loregarden.agents.evidence_context import build_evidence_ledger
from loregarden.agents.executors.dirty_checkout import park_on_dirty_checkout
from loregarden.agents.executors.dispatch_warnings import (
    warn_before_run,
)
from loregarden.agents.executors.permission_bridge import PermissionBridgeRunner
from loregarden.agents.executors.print_mode import run_print_mode
from loregarden.agents.executors.prompt_size import record_prompt_size
from loregarden.agents.executors.report_resume import resume_for_missing_report
from loregarden.agents.executors.run_evidence import (
    record_run_evidence,
    run_context_artifacts,
    run_paths_before,
)
from loregarden.agents.inherited_wisdom import InheritedWisdom, build_inherited_wisdom
from loregarden.agents.mcp_context import (
    build_mcp_run_context,
    load_loregarden_mcp_doc,
    load_memory_protocol_doc,
    load_stage_report_contract_doc,
    load_ui_primitives_doc,
    resolve_control_plane_transport,
)
from loregarden.agents.plan_context import (
    SYNTHESIS_SKILL,
    build_plan_context,
    build_plan_synthesis_context,
)
from loregarden.agents.prompt_blocks import (
    AGENT_ROLE_HEADING,
    ROLE_BODY_CAP,
    PromptTruncation,
    raw_block,
    titled_block,
)
from loregarden.agents.registry import get_agent
from loregarden.agents.stage_context import build_orchestration_context
from loregarden.agents.verify_context import build_verify_context
from loregarden.models.domain import (
    AgentRun,
    ArtifactKind,
    DoctorStatus,
    MemoryBriefingAssembly,
    RunStatus,
    Ticket,
    WorkflowStageDef,
    Workspace,
)
from loregarden.models.domain.enums import utcnow
from loregarden.services.branch_takes_target import MergeTurnSeat
from loregarden.services.cli_settings import (
    get_ticket_orchestration_runtime,
    resolve_effective_adapter,
)
from loregarden.services.code_map import code_map_reference
from loregarden.services.compatibility_posture import resolve_compatibility_posture
from loregarden.services.doctor import park_for_environment, preflight_run, preflight_summary
from loregarden.services.evidence import FULL_SUITE_EVIDENCE_KIND
from loregarden.services.exit_action_dispatch import assign_dispatch_exit_actions
from loregarden.services.git_boundary import boundary_of_tree, read_boundary, stamp_run_boundary
from loregarden.services.git_branch import ensure_ticket_branch
from loregarden.services.handoff_boundary import (
    boundary_enforced,
    park_for_boundary,
    verdict_proceeds,
    verify_run_boundary,
)
from loregarden.services.learning_outcomes import confidence_for
from loregarden.services.memory_briefing_telemetry import record_briefing
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.preflight_ledger import PreflightLedger
from loregarden.services.primary_checkout import DirtyPrimaryCheckoutError
from loregarden.services.recorded_paths import ticket_recorded_paths
from loregarden.services.run_errors import (
    RunTimeout,
    agent_timeout_message,
)
from loregarden.services.run_log_stream import RunLogStreamer
from loregarden.services.studio_routing import VERIFY_STAGE_TYPE
from loregarden.services.studio_service import build_studio_prompt_sections
from loregarden.services.target_branch import TargetBranchError, resolve_target_branch
from loregarden.services.ticket_worktree import resolve_execution_root, resolve_ticket_root
from loregarden.services.workspace_paths import (
    resolve_agent_context_dir,
    resolve_workspace_root,
)
from loregarden.services.worktree_snapshot import TreeSnapshot, read_tree
from loregarden.skills.registry import (
    SkillNotFoundError,
    get_skill,
    skill_prompt_block,
    skill_search_dirs,
)
from sqlmodel import Session

logger = logging.getLogger(__name__)

# A run's configured timeout is treated as an *idle* budget — the longest the
# agent may go producing no output before it is presumed hung and killed (the
# same moment the old fixed wall-clock deadline would have fired). As long as it
# keeps streaming, it may run until an absolute ceiling of this multiple of that
# budget, so a long-but-progressing test run is no longer killed mid-progress
# while a chatty runaway is still bounded.
# The skill whose stage runs the full regression suite; it is told to record its
# green result as commit-scoped evidence. Consumers reuse that via the general
# evidence ledger (build_evidence_ledger), so only the producer is keyed on skill.
_FULL_SUITE_SKILL = "run_tests"


class CliAgentExecutor:
    """Spawn local CLI agents via subprocess."""

    def __init__(
        self, session: Session, *, preflight_ledger: PreflightLedger | None = None
    ) -> None:
        self.session = session
        self.orchestration = OrchestrationService(session)
        # One per orchestration run: the executor serving a run, and the
        # parallel workers it lends this to, check the repository's shape once.
        self.preflight_ledger = preflight_ledger or PreflightLedger()

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

        agent_context_dir = resolve_agent_context_dir(workspace)
        workspace_root = resolve_workspace_root(workspace)
        if not workspace_root.is_dir():
            return self.orchestration.complete_run(
                run,
                status=RunStatus.FAILED,
                stderr=f"Workspace repo path does not exist: {workspace_root}",
                advance_workflow=advance_workflow,
            )

        repo_root, stopped = self._prepare_tree(
            run,
            ticket,
            workspace,
            workspace_root=workspace_root,
            skip_git_branch=skip_git_branch,
            advance_workflow=advance_workflow,
        )
        if stopped is not None:
            return stopped

        stage_def = self._resolve_stage_def(ticket, run)
        ticket_runtime = get_ticket_orchestration_runtime(ticket)

        # One read serves the run's git boundary and the evidence ledger:
        # nothing between here and the agent's start touches the tree. What the
        # run inherited is read back off its own row afterwards
        # (`run_paths_before`), so the reattaching path and this one give one
        # answer rather than two.
        before = read_tree(repo_root)

        parked = self._record_and_check_boundary(
            run, ticket, workspace, repo_root=repo_root, before=before
        )
        if parked is not None:
            return parked

        try:
            prompt = self._build_prompt(
                ticket,
                run,
                agent,
                agent_context_dir,
                workspace,
                stage_def,
                assembly_source=MemoryBriefingAssembly.DISPATCH,
                tree=before,
            )
            record_prompt_size(self.session, run, prompt)
        except SkillNotFoundError as exc:
            return self.orchestration.complete_run(
                run,
                status=RunStatus.FAILED,
                stderr=str(exc),
                advance_workflow=advance_workflow,
            )
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
                    ticket_codex_model=ticket_runtime.codex_model,
                    ticket_lmstudio_model=ticket_runtime.lmstudio_model,
                    ticket_opencode_model=ticket_runtime.opencode_model,
                    ticket_claude_effort=ticket_runtime.claude_effort,
                    ticket_cursor_effort=ticket_runtime.cursor_effort,
                    ticket_lmstudio_effort=ticket_runtime.lmstudio_effort,
                    ticket_opencode_effort=ticket_runtime.opencode_effort,
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
                partial_output="--stream-partial-output" in invocation.argv,
            )
            streamer.start(run.command)
            warn_before_run(
                streamer=streamer,
                run=run,
                agent=agent,
                workspace=workspace,
                ticket_runtime=ticket_runtime,
                stage_def=stage_def,
                invocation=invocation,
            )

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
                    # The transport is resolved by whoever performs the spawn,
                    # and announced from the answer that spawn returned — see
                    # `print_mode.run_print_mode`. Resolved here as well it was
                    # two independent readings of one fact, and the SYS line was
                    # written BEFORE the spawn, so a tmux spawn that raised left
                    # a line in the feed naming a session that never existed.
                    stdout, stderr, status = run_print_mode(
                        invocation=invocation,
                        repo_root=repo_root,
                        timeout=timeout,
                        streamer=streamer,
                        run_id=run.id,
                        run_code=run.run_code,
                    )
                    # One resume when the turn ended with no report (848).
                    stdout, stderr, status = resume_for_missing_report(
                        invocation=invocation,
                        run=run,
                        stdout=stdout,
                        stderr=stderr,
                        status=status,
                        repo_root=repo_root,
                        timeout=timeout,
                        streamer=streamer,
                    )

                streamer.finalize(status=status, stderr=stderr)
                record_run_evidence(
                    self.session,
                    run,
                    repo_root=repo_root,
                    paths_before=run_paths_before(run),
                    stdout=stdout,
                    invocation=invocation,
                )
                artifacts = run_context_artifacts(ticket, run, status)
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
                    invocation=invocation,
                    fallback_timeout=timeout,
                    repo_root=repo_root,
                    paths_before=run_paths_before(run),
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
        invocation: CliInvocation,
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
        # The print-mode path raises RunTimeout and names its budget; the
        # permission bridge runs one deadline and raises stdlib TimeoutExpired,
        # which carries no kind to read. Foreign exception, not a type switch
        # over our own models.
        kind = exc.kind if isinstance(exc, RunTimeout) else None  # py-org: allow-isinstance
        msg = agent_timeout_message(exc.timeout or fallback_timeout, kind)
        streamer.finalize(status=RunStatus.FAILED, stderr=msg)
        # TimeoutExpired.output is bytes | str | None — a foreign union, not a schema.
        output = exc.output
        partial_stdout = output if isinstance(output, str) else ""  # py-org: allow-isinstance
        # A killed run still touched and read things, and a partial transcript is
        # a partial record rather than no record.
        record_run_evidence(
            self.session,
            run,
            repo_root=repo_root,
            paths_before=paths_before,
            stdout=partial_stdout,
            invocation=invocation,
        )
        return self.orchestration.complete_run(
            run,
            status=RunStatus.FAILED,
            stdout=partial_stdout,
            stderr=msg,
            advance_workflow=advance_workflow,
        )

    def render_stage_prompt(self, run: AgentRun, ticket: Ticket) -> tuple[str, Path]:
        """The exact prompt a supervised run of this stage would receive, plus its tree.

        Shared by every path that hands a stage to something this process does not
        spawn — a human's terminal, or an external harness driving the ticket over
        MCP. They get the prompt the built-in executor would have used, so their
        results are comparable against a supervised run rather than a different
        experiment.
        """
        agent = get_agent(run.agent_id)
        if not agent:
            raise ValueError(f"Unknown agent: {run.agent_id}")

        workspace = self.session.get(Workspace, ticket.workspace_id)
        if not workspace:
            raise ValueError(f"Unknown workspace for ticket: {ticket.id}")

        workspace_root = resolve_workspace_root(workspace)
        agent_context_dir = resolve_agent_context_dir(workspace)
        if not workspace_root.is_dir():
            raise ValueError(f"Workspace repo path does not exist: {workspace_root}")

        # The handed-off tree is the same one the supervised run would use: the
        # ticket's worktree, on its branch, brought up to its target. Checking
        # the branch out in the shared tree instead is what left it half-applied
        # when the handoff was abandoned, and skipping the refresh for a
        # worktree is what handed the operator a base that could not see a
        # prerequisite landed since the tree was cut.
        #
        # `resolve_execution_root` resolves the target branch itself, to cut a
        # worktree, so it raises before any `ensure_ticket_branch` call and sits
        # inside the same handler. The target is resolved against the shared
        # checkout on purpose: resolution writes refs, and refs are shared by
        # every worktree of a repository (lg-milestone-that-779 S3).
        #
        # A git tree is an input this requires, not one it prefers: there is no
        # branch to be on and no target to be brought up to without one, so a
        # non-git execution root surfaces as `ensure_ticket_branch`'s own
        # `ValueError` and reaches the operator as the 400 any other unpreparable
        # tree does.
        try:
            repo_root = resolve_execution_root(self.session, run, ticket, workspace)
            ensure_ticket_branch(
                repo_root,
                ticket,
                start_point=resolve_target_branch(
                    self.session, ticket, workspace, repo_root=workspace_root
                ),
                ignoring=ticket_recorded_paths(self.session, ticket),
                seat=MergeTurnSeat(self.session, workspace, ticket),
            )
        except (TargetBranchError, subprocess.CalledProcessError) as exc:
            # This path has no run to fail and no stage to block, so it raises —
            # as a `ValueError`, which both handoff edges already render (the
            # route maps it to a 400, the MCP dispatcher to an `isError`).
            # `_refresh_onto`'s own `ValueError` and `DirtyPrimaryCheckoutError`
            # (a `ValueError` subclass by design) pass through unwrapped: the
            # first already carries git's merge output, and re-wrapping the
            # second would lose the dirty-path list the park path is built on.
            raise ValueError(f"Could not prepare the tree for {ticket.external_id}: {exc}") from exc

        stage_def = self._resolve_stage_def(ticket, run)
        prompt = self._build_prompt(
            ticket,
            run,
            agent,
            agent_context_dir,
            workspace,
            stage_def,
            assembly_source=MemoryBriefingAssembly.RENDER,
        )
        return prompt, repo_root

    def prepare_terminal_handoff(
        self, run: AgentRun, ticket: Ticket
    ) -> tuple[CliInvocation, Path | None, Path]:
        """Build a self-contained CLI invocation for this stage without spawning it.

        Used to hand a stage off to a human's own terminal instead of the app's subprocess
        supervision, which dies if the app server restarts mid-run. The system prompt is
        written to a real file on disk (not inlined into the returned command) since a full
        stage prompt can run tens of KB — pasting that much text directly into a terminal can
        overwhelm some terminals' paste handling. Returns the prompt file's containing
        directory as a cleanup path when one was written (else None), and the tree the
        command will run in — the ticket's worktree, on its branch, brought up to its
        target. The caller cannot re-derive that tree without answering a question that
        has one correct answer twice, and it is what it reports on to the operator.
        """
        agent = get_agent(run.agent_id)
        if not agent:
            raise ValueError(f"Unknown agent: {run.agent_id}")

        workspace = self.session.get(Workspace, ticket.workspace_id)
        if not workspace:
            raise ValueError(f"Unknown workspace for ticket: {ticket.id}")

        prompt, repo_root = self.render_stage_prompt(run, ticket)

        # A handed-off run is still a run against a tree, and the terminal it is
        # pasted into is the surface most likely to be on a stale branch.
        stamp_run_boundary(self.session, run, read_boundary(repo_root))

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
        return invocation, cleanup_path, repo_root

    def _touch_ticket_agent(self, ticket: Ticket, agent_name: str, status: RunStatus) -> None:
        ticket.last_updated_by = agent_name
        ticket.revision += 1
        if status == RunStatus.SUCCEEDED:
            ticket.next_status = "Proceed"
        else:
            ticket.next_status = "Blocked"
        ticket.updated_at = utcnow()
        self.session.add(ticket)
        self.session.commit()

    def _resolve_template_stages(self, ticket: Ticket) -> list[WorkflowStageDef]:
        template = self.orchestration.get_template_for_ticket(ticket)
        if not template:
            return []
        from loregarden.core.workflow_loader import get_template_stages

        return list(get_template_stages(template))

    def _target_branch_once(
        self, ticket: Ticket, workspace: Workspace, *, workspace_root: Path
    ) -> Callable[[], str]:
        """A resolver for this ticket's target branch that resolves it at most once.

        Both consumers of the answer inside one dispatch — the worktree cut in
        `resolve_execution_root` and the `start_point` the refresh is brought up
        to — share this one call. Before, each resolved for itself: two readings
        and two merge attempts against the same refs for the same ticket on the
        most-travelled path in the app (lg-milestone-that-918).

        It is lazy on purpose, not hoisted. `resolve_target_branch` is a *write*
        (`ensure_integration_branch`, `_refresh_from_tips`, and behind them a
        blocking merge turn that files an inbox card), and three dispatch paths
        need the answer zero times — among them the dirty-park path, where the
        run never starts. Nothing resolves until a consumer asks.

        ``workspace_root`` is what the target branch is resolved against, never
        the execution root: resolution writes refs, refs are shared across every
        worktree of a repository, and the integration-branch bookkeeping belongs
        in the shared checkout (lg-milestone-that-779 S3).

        The memo's lifetime is one dispatch, which is why it is built here and
        not held on `self`: the cached *name* would not go stale, but the write
        behind it is what carries the base branch onto the target, so a memo
        that outlived its dispatch would leave the tree on a base it cannot see.

        A failure is not cached. It propagates and a later call retries, because
        a cached failure is a stale answer with no way to clear it.
        """
        resolved: list[str] = []

        def once() -> str:
            if not resolved:
                # The module-global name, looked up at call time: the tests that
                # pin where resolution happens patch this module's binding.
                resolved.append(
                    resolve_target_branch(self.session, ticket, workspace, repo_root=workspace_root)
                )
            return resolved[0]

        return once

    def _ensure_branch_or_fail(
        self,
        run: AgentRun,
        ticket: Ticket,
        workspace: Workspace,
        *,
        repo_root: Path,
        start_point: str,
        advance_workflow: bool,
    ) -> AgentRun | None:
        """Put the execution tree on the ticket's branch. Returns the failed run
        when the checkout could not be made, or None to carry on.

        Runs for the ticket's worktree too. Creating one cuts it from the target
        branch, but a tree cut three stages ago has seen nothing that landed
        since, so the refresh has to happen on every dispatch and not only on
        creation (lg-milestone-that-779). The branch is cut from — and an
        existing one brought up to — the ticket's target branch, so a sibling's
        landed work reaches it (lg-milestone-that-769).

        One tree and one job: `start_point` is the already-resolved target
        (`_target_branch_once` in `_prepare_tree` owns that question, and the
        decision to skip the refresh at all is the caller's), so this knows
        about exactly one root.
        """
        try:
            ensure_ticket_branch(
                repo_root,
                ticket,
                start_point=start_point,
                ignoring=ticket_recorded_paths(self.session, ticket),
                seat=MergeTurnSeat(self.session, workspace, ticket),
            )
        except DirtyPrimaryCheckoutError:
            raise  # parked by `_prepare_tree`, not failed as a checkout error
        except (ValueError, subprocess.CalledProcessError, TargetBranchError) as exc:
            return self.orchestration.complete_run(
                run,
                status=RunStatus.FAILED,
                stderr=f"Failed to checkout branch: {exc}",
                advance_workflow=advance_workflow,
            )
        return None

    def _prepare_tree(
        self,
        run: AgentRun,
        ticket: Ticket,
        workspace: Workspace,
        *,
        workspace_root: Path,
        skip_git_branch: bool,
        advance_workflow: bool,
    ) -> tuple[Path, AgentRun | None]:
        """The tree this run executes in, on its branch — or the run, stopped.

        The ticket's own worktree, cut on its first stage and reused by every
        later one. Without this every run, parallel or not, executed in the one
        shared checkout and fought over its working tree — and a crash mid-run
        left that shared tree on a half-applied ticket branch. A worktree that
        cannot be cut falls back to the shared checkout, unless that holds
        someone else's uncommitted work: then the run is parked, not started
        (864).

        Reused or freshly cut, the tree is put on the ticket's branch and
        refreshed onto its target on every dispatch — the only skip left is
        ``skip_git_branch``, which the parallel driver passes its members
        because `services.parallel_stage` already did it once for the stage
        (lg-milestone-that-779).
        """
        once = self._target_branch_once(ticket, workspace, workspace_root=workspace_root)
        try:
            repo_root = resolve_execution_root(
                self.session, run, ticket, workspace, resolve_target=once
            )
            failed = (
                None
                if skip_git_branch
                else self._ensure_branch_or_fail(
                    run,
                    ticket,
                    workspace,
                    repo_root=repo_root,
                    start_point=once(),
                    advance_workflow=advance_workflow,
                )
            )
        except DirtyPrimaryCheckoutError as exc:
            return workspace_root, park_on_dirty_checkout(
                self.session, self.orchestration, run=run, ticket=ticket, error=exc
            )
        except TargetBranchError as exc:
            # `resolve_execution_root` cuts the worktree from the ticket's
            # target branch, so an integration branch that cannot take its base
            # raises here rather than from `_ensure_branch_or_fail`. Now that
            # every dispatch resolves a target branch (779), this is the common
            # path, and letting it escape the executor loses the run.
            return workspace_root, self.orchestration.complete_run(
                run,
                status=RunStatus.FAILED,
                stderr=f"Failed to checkout branch: {exc}",
                advance_workflow=advance_workflow,
            )
        return repo_root, failed

    def _record_and_check_boundary(
        self,
        run: AgentRun,
        ticket: Ticket,
        workspace: Workspace,
        *,
        repo_root: Path,
        before: TreeSnapshot,
    ) -> AgentRun | None:
        """Record the tree this run inherited, then ask whether it is still the
        one the last handoff described. Returns the parked run when the stage
        must not proceed, or None to carry on.

        Called once the execution root and branch are settled — stamping any
        earlier would name the shared checkout for a run that goes on to execute
        in a worktree. Every dispatch path in the app converges on `execute`,
        which is why this lives in the executor rather than beside any one caller.

        A run carrying `dispatch_waiver_approval_id` was dispatched by a person
        who saw these checks fail and said run it anyway (see
        `services.stage_parking`). It skips the *parking*, never the checking:
        the checks still run and their failures are still recorded on the run,
        because "a human waived it" and "the environment was fine" must not read
        the same afterwards. `maybe_warn_dispatch_waiver` puts it in the log.
        """
        stamp_run_boundary(self.session, run, boundary_of_tree(before))
        waived = bool(run.dispatch_waiver_approval_id)

        # The environment first: a stage started in a checkout with core.bare set,
        # or under a leaked GIT_DIR, fails in ways that point at anything but the
        # cause. Recorded on the run whatever happens next.
        preflight = preflight_run(
            self.session, run, workspace, repo_root, ledger=self.preflight_ledger
        )
        if any(finding.status is DoctorStatus.FAIL for finding in preflight) and not waived:
            park_for_environment(
                self.session, run=run, ticket=ticket, summary=preflight_summary(preflight)
            )
            return self.orchestration.complete_run(
                run,
                status=RunStatus.CANCELLED,
                stderr="Environment preflight failed: awaiting human confirmation.",
                advance_workflow=False,
            )

        verdict = verify_run_boundary(self.session, run, ticket)
        if verdict_proceeds(verdict) or not boundary_enforced(workspace) or waived:
            return None

        park_for_boundary(self.session, run=run, ticket=ticket, verdict=verdict)
        return self.orchestration.complete_run(
            run,
            status=RunStatus.CANCELLED,
            stderr=f"Boundary check {verdict.value}: awaiting human confirmation.",
            advance_workflow=False,
        )

    def _resolve_stage_def(self, ticket: Ticket, run: AgentRun) -> WorkflowStageDef | None:
        return next(
            (
                stage
                for stage in self._resolve_template_stages(ticket)
                if stage.key == run.stage_key
            ),
            None,
        )

    def _full_suite_producer_note(self, skill_name: str) -> str:
        """Tell the stage that runs the full suite to record a green run as
        reusable, commit-scoped evidence, or "" for any other stage.

        The *consumer* side — a later stage reusing this instead of re-running —
        lives in the general evidence ledger (`build_evidence_ledger`), not here.
        """
        if skill_name != _FULL_SUITE_SKILL:
            return ""
        return (
            "When the full suite passes, record it as reusable proof so a later stage "
            "need not re-run it: call `loregarden_attach_evidence` with "
            f"`evidence_kind: {FULL_SUITE_EVIDENCE_KIND}` (the commit is stamped "
            "server-side). Do this only for a genuinely green full run — not a partial "
            "or scoped one."
        )

    def _inherited_context(
        self,
        ticket: Ticket,
        run: AgentRun,
        workspace: Workspace,
        *,
        is_verify: bool,
        assembly_source: MemoryBriefingAssembly,
    ) -> str:
        """The inherited-wisdom block, and THE single recording point for its telemetry.

        Every briefing assembly is recorded here, including the verify case,
        which records a SKIPPED row: a verify stage really did assemble a prompt
        and deliberately carried no briefing, and writing nothing would make it
        indistinguishable from a run whose telemetry write silently failed.

        Do not assemble a briefing anywhere else in the prompt blocks. The
        aggregate over `memory_briefings` is denominated over runs precisely so
        that a seam which stopped recording shows up as a hole; a second,
        unrecorded assembly reopens the blindness this exists to close.

        The observed-outcome ladder (178) rides on this seam: the briefing is
        ranked and annotated by `learning_outcomes.confidence_for`, and
        `record_briefing` links the learnings it injected to this run.

        Note for whoever extends the figures: `RECALL_CANDIDATE_CAP`
        (`services/memory_store.py`) is a second, still-unreported truncation
        bound. The `truncated` flag covers the prompt-character cap only, not a
        vault that has outgrown the candidate budget.

        Never raises — `record_briefing` swallows its own failures, and this
        method adds no guard of its own.
        """
        if is_verify:
            record_briefing(
                self.session,
                run,
                ticket,
                InheritedWisdom.not_attempted(),
                skipped=True,
                assembly_source=assembly_source,
            )
            return ""
        result = build_inherited_wisdom(
            ticket,
            workspace.slug,
            confidence_lookup=partial(confidence_for, self.session),
        )
        record_briefing(
            self.session,
            run,
            ticket,
            result,
            skipped=False,
            assembly_source=assembly_source,
        )
        return result.text

    def _build_prompt(
        self,
        ticket: Ticket,
        run: AgentRun,
        agent: dict,
        agent_context_dir: Path,
        workspace: Workspace,
        stage_def: WorkflowStageDef | None,
        *,
        assembly_source: MemoryBriefingAssembly,
        tree: TreeSnapshot | None = None,
    ) -> str:
        # The tree this run will work in — the ticket's worktree once it has
        # one. A prompt built from the shared checkout would describe a repo
        # without any of the ticket's own work in it.
        repo_root = resolve_ticket_root(self.session, ticket, workspace)
        # A dispatch hands in the read it bracketed the run with; anything
        # else — a render, or a tree that is not this one — reads its own.
        if tree is None or tree.repo_root != repo_root:
            tree = read_tree(repo_root)
        # Role body comes from the agent config (DB-backed studio agent, or the
        # registry fallback which loads it in get_agent). The executor no longer
        # reads role_file from the workspace filesystem — the DB is authoritative.
        role_body = (agent.get("role_body") or "")[:ROLE_BODY_CAP]

        stage_skill = (run.skill_name or "").strip()
        default_skill = (agent.get("default_skill") or "").strip()
        skill_name = stage_skill or default_skill
        skill_body = get_skill(skill_name, agent_context_dir=agent_context_dir) or ""
        if skill_name and not skill_body:
            # Declared skills used to log a warning and run with an empty Skill
            # block — the stage proceeded as if the procedure had been attached.
            raise SkillNotFoundError(
                skill_name,
                skill_search_dirs(agent_context_dir),
                agent_id="" if stage_skill else run.agent_id,
            )
        ac = json.loads(ticket.acceptance_criteria_json or "[]")

        adapter = resolve_effective_adapter(
            agent_adapter=agent.get("adapter", "local"),
            workspace=workspace,
            ticket_adapter=get_ticket_orchestration_runtime(ticket).cli_adapter,
        )
        assigned_exit_actions = assign_dispatch_exit_actions(
            self.session,
            run=run,
            workspace=workspace,
            stage_def=stage_def,
            adapter=adapter,
        )
        orchestration_context = build_orchestration_context(
            ticket=ticket,
            run=run,
            stage_def=stage_def,
            stages=self._resolve_template_stages(ticket),
            posture=resolve_compatibility_posture(self.session, ticket, workspace),
            session=self.session,
            assigned_exit_actions=assigned_exit_actions,
        )
        # Resolved from the wiring this run will actually get, not from the kind
        # of run it is: the prompt has to describe the channel the agent has.
        transport = resolve_control_plane_transport(
            run=run,
            adapter=adapter,
            project_root=repo_root,
        )
        mcp_context = build_mcp_run_context(
            ticket=ticket,
            run=run,
            workspace=workspace,
            stage_def=stage_def,
            transport=transport,
        )
        mcp_doc = load_loregarden_mcp_doc(transport=transport)
        memory_doc = load_memory_protocol_doc(agent_context_dir)
        ui_primitives_doc = load_ui_primitives_doc(agent_context_dir)
        stage_report_doc = load_stage_report_contract_doc(agent_context_dir)
        is_verify = stage_def is not None and stage_def.stage_type == VERIFY_STAGE_TYPE
        is_synthesis = skill_name == SYNTHESIS_SKILL
        full_suite_note = self._full_suite_producer_note(skill_name)
        evidence_ledger = build_evidence_ledger(self.session, ticket, tree, is_verify=is_verify)

        # Ordered prompt blocks. Add a section by inserting a block here rather
        # than threading another conditional through the assembly; each block
        # carries its own leading blank line and drops out when empty.
        truncations: list[PromptTruncation] = []
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
            # High in the prompt: these govern whether the agent runs or re-runs
            # work, so they must land before the role text that tells it to.
            titled_block("## Full test suite", full_suite_note),
            titled_block("## Already-established evidence (reuse, don't redo)", evidence_ledger),
            # A verifier is deliberately starved of inherited context. Handing it
            # the prior stage's settled decisions ("do not re-derive") would make
            # it a reader of that reasoning rather than an independent check, and
            # a verifier that agrees because it was told to proves nothing.
            titled_block(
                "## Inherited context (already decided — do not re-derive)",
                self._inherited_context(
                    ticket, run, workspace, is_verify=is_verify, assembly_source=assembly_source
                ),
            ),
            titled_block(
                "## Claim under review",
                build_verify_context(self.session, ticket, workspace) if is_verify else "",
            ),
            # Alongside inherited context, and withheld from a verifier for the
            # same reason: the plan is the reasoning a verifier must not inherit.
            titled_block(
                "## Plan (settled by the plan stage)",
                "" if is_verify else build_plan_context(self.session, ticket, run.stage_key),
            ),
            # The synthesizer gets the lanes instead of the settled plan — there
            # is no settled plan yet, producing it is the job.
            titled_block(
                "## Plans to reconcile",
                build_plan_synthesis_context(self.session, ticket) if is_synthesis else "",
            ),
            # Before the role, so an agent knows where the shape of the repo is
            # written down before it is told its job. A pointer rather than the
            # map itself: the file is in the tree this run is standing in, and
            # inlining it re-sent the same few thousand characters to every
            # stage of every ticket. Named explicitly because the implementers
            # run on cursor, which does not pick up CLAUDE.md the way Claude
            # Code does.
            titled_block("## Repository map", code_map_reference(repo_root)),
            skill_prompt_block(skill_name, skill_body),
            titled_block(AGENT_ROLE_HEADING, role_body),
            raw_block(build_studio_prompt_sections(agent, transport=transport)),
            titled_block(
                "## Loregarden control-plane module",
                mcp_doc,
                cap=12000,
                truncations=truncations,
            ),
            titled_block(
                "## Memory protocol module", memory_doc, cap=8000, truncations=truncations
            ),
            titled_block(
                "## Chat UI primitives", ui_primitives_doc, cap=6000, truncations=truncations
            ),
            [
                "",
                "## Permission policy",
                "Request human approval via Loregarden before destructive or high-risk tool use.",
                "Do not bypass workspace permission checks.",
            ],
            # Last, because it governs the last thing the agent emits.
            titled_block("## Stage report contract", stage_report_doc),
        ]
        prompt = "\n".join(line for block in blocks for line in block)
        self._report_prompt_truncations(ticket, run, truncations)
        return prompt

    def _report_prompt_truncations(
        self, ticket: Ticket, run: AgentRun, truncations: list[PromptTruncation]
    ) -> None:
        """Say out loud that an agent was handed less than the prompt intended.

        A cap that bites removes instructions the agent is then judged against,
        and it used to do so with no log, no artifact and no trace — the
        gatekeeper agent lost its entire approve/reject contract this way and
        the run looked normal (lg-workflow-integrity-91). `titled_block` already
        logged; this puts it where an operator reviewing the ticket will find
        it.
        """
        if not truncations:
            return
        detail = "\n".join(f" - {record.describe()}" for record in truncations)
        OrchestrationCallbackService(self.session).attach_artifact(
            ticket,
            kind=ArtifactKind.ERROR,
            title=f"Prompt truncated — {run.stage_key or run.agent_id}",
            content={
                "message": (
                    "This run's prompt exceeded its size caps and was cut before the "
                    f"agent saw it:\n{detail}\n\n"
                    "The agent was judged on instructions it did not fully receive. Cuts "
                    "are made at a section boundary, so what arrived is whole sections — "
                    "but the dropped ones are gone. Shorten the source document or raise "
                    "the cap."
                ),
                "run_code": run.run_code,
                "agent_id": run.agent_id,
                "stage_key": run.stage_key or "",
                "command": "",
            },
            run_id=run.id,
        )
