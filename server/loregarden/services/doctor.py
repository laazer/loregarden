"""Check the environment traps instead of remembering them.

Every check here corresponds to a failure this control plane has actually had,
and to a paragraph in CLAUDE.md or an agent memory file written afterwards so the
next person would remember. That is the part worth replacing: the knowledge is
mechanical, the diagnosis is a shell command, and the remediation is one line.
Prose telling an agent to remember something is the weakest possible enforcement.

Two rules hold for every check. None of them changes the repository, fetches
from a remote, or reads the value of a credential — a diagnostic that changes
what it is diagnosing is not one. And none of them raises: an exception inside a
check becomes a FAIL for that check alone, because a doctor that dies on its
first surprise is worse than no doctor.

One check writes, and the rule above says "changes" rather than "writes" because
of it. `check_git_writable` cannot answer its question by reading: `os.access`
reports the mode bits, and the failure it exists to catch is a sandbox that
denies the write while the mode bits still allow it. So it writes a uniquely
named probe inside the git directory and removes it in the same call, leaving
the repository as it found it. `tests/test_doctor.py` asserts that absence
directly rather than excluding the git directory from scrutiny.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from loregarden.agents.mcp_context import (
    STAGE_REPORT_SECTION_TITLE,
    WORKFLOW_ENFORCEMENT_DOC_REL,
    load_stage_report_contract_doc,
)
from loregarden.config import resolved_database_path, settings
from loregarden.models.domain import (
    AgentRun,
    Approval,
    DoctorCheck,
    DoctorFinding,
    DoctorStatus,
    PortabilityState,
    Ticket,
    Workspace,
)
from loregarden.services.git_subprocess import GIT_LOCATION_ENV_VARS, run_git
from loregarden.services.orchestration_profile import resolve_orchestration_profile
from loregarden.services.stage_parking import park_stage
from loregarden.services.studio_drift import detect_all_drift
from loregarden.services.workspace_paths import resolve_agent_context_dir
from sqlmodel import Session, select

#: Checks cheap and decisive enough to run before every agent dispatch. The rest
#: are informational, and a doctor that adds a second to every stage gets turned
#: off. See `services.handoff_boundary` for the sibling pre-dispatch check.
#:
#: Both of these describe *this machine* rather than the work, and both make the
#: run fail somewhere far from the cause — which is the whole argument for
#: stopping ahead of the dispatch instead of letting it report its own error.
#:
#: REPO_HAS_COMMIT is deliberately not here, though it is a real check. A
#: workspace whose repo has no commit is caught by `ensure_ticket_branch` a few
#: lines later, which fails the run with a precise git message; parking on it
#: instead replaces that message with an approval a human has to clear, and turns
#: every not-yet-a-repo workspace into a stalled ticket.
#: STAGE_REPORT_CONTRACT and GIT_WRITABLE are here on the same test
#: REPO_HAS_COMMIT fails: nothing downstream reports them well. An empty
#: contract is reported nowhere at all — the stage just fails on a report it was
#: never told how to write. An unwritable git directory is not discovered until
#: commit time, after the whole run has been spent.
#:
#: GATE_COMMANDS_RESOLVE is deliberately absent, for REPO_HAS_COMMIT's reason:
#: `gate_runner` already turns an exec failure into `GateOutcome.UNAVAILABLE`
#: carrying the OS error and the command, and parking would replace that precise
#: message with an approval someone has to clear.
#:
#: TOOLCHAIN_INSTALLED is absent for a harder reason: it cannot tell a toolchain
#: a run needs from one it does not, and parking on the difference is worse than
#: not asking. Loregarden's own root `package.json` is the Tauri desktop host —
#: devDependencies only, never installed for an agent run, with the real
#: `node_modules` under `client/`. The check reads that as a missing toolchain
#: and would park every dispatch in this very workspace. It stays valuable on
#: demand, where a human reads the finding and knows which manifests matter.
DISPATCH_PREFLIGHT_CHECKS = (
    DoctorCheck.GIT_CORE_BARE,
    DoctorCheck.GIT_ENV_LEAK,
    DoctorCheck.STAGE_REPORT_CONTRACT,
    DoctorCheck.GIT_WRITABLE,
)


def _ok(check: DoctorCheck, finding: str) -> DoctorFinding:
    return DoctorFinding(check=check, status=DoctorStatus.PASS, finding=finding)


def check_git_core_bare(session: Session, workspace: Workspace, repo_root: Path) -> DoctorFinding:
    """`core.bare` set true in a checkout that has a working tree.

    Agent worktree creation has left this behind, and the symptom is unrelated to
    the cause: every work-tree operation afterwards fails with an exit-128
    checkout error naming a path that is perfectly fine.
    """
    proc = run_git(
        ["config", "--local", "--get", "core.bare"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 or proc.stdout.strip() != "true":
        return _ok(DoctorCheck.GIT_CORE_BARE, "core.bare is not set on this checkout.")
    return DoctorFinding(
        check=DoctorCheck.GIT_CORE_BARE,
        status=DoctorStatus.FAIL,
        finding=f"core.bare=true in {repo_root}, which has a working tree.",
        remediation="git config --local core.bare false",
    )


def check_git_env_leak(session: Session, workspace: Workspace, repo_root: Path) -> DoctorFinding:
    """GIT_DIR / GIT_WORK_TREE in the ambient environment.

    They beat `cwd`, so a leaked pair silently points every git call at another
    repository. `run_git` scrubs them for this process's own calls; what this
    catches is the environment a *subprocess* would inherit — an agent shelling
    out to git itself, or a hook running under one.
    """
    leaked = sorted(name for name in GIT_LOCATION_ENV_VARS if os.environ.get(name))
    if not leaked:
        return _ok(DoctorCheck.GIT_ENV_LEAK, "No git location variables in the environment.")
    return DoctorFinding(
        check=DoctorCheck.GIT_ENV_LEAK,
        status=DoctorStatus.FAIL,
        finding=f"{', '.join(leaked)} set in the environment; they override cwd for git.",
        remediation=(
            f"unset {' '.join(leaked)} — or run through .lefthook/scripts/"
            "hook-noninteractive.sh, which unsets them for hooks."
        ),
    )


def check_repo_has_commit(session: Session, workspace: Workspace, repo_root: Path) -> DoctorFinding:
    """A repository with no commit. Several helpers here assume HEAD resolves."""
    proc = run_git(
        ["rev-parse", "--verify", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if proc.returncode == 0:
        return _ok(DoctorCheck.REPO_HAS_COMMIT, "HEAD resolves.")
    return DoctorFinding(
        check=DoctorCheck.REPO_HAS_COMMIT,
        status=DoctorStatus.FAIL,
        finding=f"{repo_root} has no commit, or is not a git repository.",
        remediation="git init && git commit --allow-empty -m 'initial'",
    )


def check_db_resolution(session: Session, workspace: Workspace, repo_root: Path) -> DoctorFinding:
    """The database this process resolved, and whether anything is in it.

    A worktree resolves `data/loregarden.db` relative to itself, finds an empty
    file or none, and answers every ticket query with a silent zero — which reads
    as "no such ticket" rather than "wrong database".
    """
    path = resolved_database_path()
    if not path.is_file():
        return DoctorFinding(
            check=DoctorCheck.DB_RESOLUTION,
            status=DoctorStatus.FAIL,
            finding=f"No database at {path}.",
            remediation=(
                f"Point LOREGARDEN_REPO_ROOT at the main checkout (currently {settings.repo_root})."
            ),
        )
    if not session.exec(select(Ticket).limit(1)).first():
        return DoctorFinding(
            check=DoctorCheck.DB_RESOLUTION,
            status=DoctorStatus.WARN,
            finding=f"The database at {path} holds no tickets.",
            remediation=(
                "Expected? If not, this is probably a worktree resolving its own empty "
                f"copy — set LOREGARDEN_REPO_ROOT to the main checkout "
                f"(currently {settings.repo_root})."
            ),
        )
    return _ok(DoctorCheck.DB_RESOLUTION, f"Database at {path} has tickets.")


def check_backend_reload_sentinel(
    session: Session, workspace: Workspace, repo_root: Path
) -> DoctorFinding:
    """Backend sources newer than the reload sentinel.

    WARN rather than FAIL: it only bites when a dev server is up, and it is not
    this code's business whether one is. When it does bite it costs a debugging
    session, because the fix under test is stale code behaving exactly as the bug
    it was meant to remove.
    """
    server_root = repo_root / "server"
    sentinel = server_root / ".self-improve-restart"
    if not server_root.is_dir():
        return _ok(DoctorCheck.BACKEND_RELOAD_SENTINEL, "No server tree; not applicable.")
    if not sentinel.exists():
        return DoctorFinding(
            check=DoctorCheck.BACKEND_RELOAD_SENTINEL,
            status=DoctorStatus.WARN,
            finding="No reload sentinel; a running dev server would not pick up .py edits.",
            remediation=f"touch {sentinel}",
        )

    sentinel_mtime = sentinel.stat().st_mtime
    newer = next(
        (
            source
            for source in server_root.rglob("*.py")
            if ".venv" not in source.parts and source.stat().st_mtime > sentinel_mtime
        ),
        None,
    )
    if newer is None:
        return _ok(
            DoctorCheck.BACKEND_RELOAD_SENTINEL, "Sentinel is newer than every backend source."
        )
    return DoctorFinding(
        check=DoctorCheck.BACKEND_RELOAD_SENTINEL,
        status=DoctorStatus.WARN,
        finding=f"{newer.relative_to(repo_root)} is newer than the reload sentinel.",
        remediation=f"touch {sentinel}",
    )


def check_cli_credentials(session: Session, workspace: Workspace, repo_root: Path) -> DoctorFinding:
    """Whether a credential *source* exists for the agent CLIs.

    Presence only — no value is read, logged, or returned. A missing one makes
    the CLI exit reporting "not logged in", which reads as a bug in the code that
    spawned it rather than as an expired session.
    """
    sources = {
        "claude": bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip())
        or (settings.repo_root / "data" / ".claude-oauth-token").is_file(),
        "cursor": bool(os.environ.get("CURSOR_API_KEY", "").strip()),
    }
    present = sorted(name for name, found in sources.items() if found)
    if present:
        return _ok(
            DoctorCheck.CLI_CREDENTIALS, f"Credential source present for: {', '.join(present)}."
        )
    return DoctorFinding(
        check=DoctorCheck.CLI_CREDENTIALS,
        status=DoctorStatus.WARN,
        finding="No credential source found for any agent CLI.",
        remediation=(
            "claude setup-token, saved to data/.claude-oauth-token — or export "
            "CLAUDE_CODE_OAUTH_TOKEN before launching the backend."
        ),
    )


def portability_state(repo_root: Path) -> PortabilityState:
    """Where the branch stands against its upstream, from refs already on disk.

    Deliberately without a fetch: a diagnostic that touches the network answers a
    different question each time it runs, and a slow one gets switched off.
    """
    upstream = run_git(
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if upstream.returncode != 0:
        return PortabilityState.LOCAL_ONLY

    counts = run_git(
        ["rev-list", "--left-right", "--count", "HEAD...@{upstream}"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if counts.returncode != 0:
        return PortabilityState.LOCAL_ONLY

    ahead_raw, _, behind_raw = counts.stdout.strip().partition("\t")
    ahead, behind = int(ahead_raw or 0), int(behind_raw or 0)
    if ahead and behind:
        return PortabilityState.REMOTE_DIVERGED
    if ahead:
        return PortabilityState.PUSH_REQUIRED
    return PortabilityState.REMOTE_READY


def check_git_portability(session: Session, workspace: Workspace, repo_root: Path) -> DoctorFinding:
    """Report, not verdict. PUSH_REQUIRED is the normal state mid-ticket, and
    only REMOTE_DIVERGED needs a decision from anyone."""
    state = portability_state(repo_root)
    if state is PortabilityState.REMOTE_DIVERGED:
        return DoctorFinding(
            check=DoctorCheck.GIT_PORTABILITY,
            status=DoctorStatus.WARN,
            finding="The branch is both ahead of and behind its upstream.",
            remediation="Rebase or merge before pushing; a push will be rejected as is.",
        )
    return _ok(DoctorCheck.GIT_PORTABILITY, f"Branch is {state.value}.")


def check_stage_report_contract(
    session: Session, workspace: Workspace, repo_root: Path
) -> DoctorFinding:
    """The stage-report contract reaches the agent's prompt with content in it.

    `load_stage_report_contract_doc` returns "" for two unrelated reasons and
    says nothing about either: the workflow-enforcement doc is absent from this
    workspace's agent_context, or the doc is there but its STAGE REPORT CONTRACT
    section title never matched. Either way the prompt is assembled with an
    empty contract, the agent is never told how to write its report, and every
    stage in the workspace fails on a report it could not have produced.

    This is the check with the clearest claim on running before dispatch:
    nothing downstream reports it at all. The failure surfaces as unparseable
    output several minutes and one agent turn later, naming the report rather
    than the missing instructions.
    """
    contract = load_stage_report_contract_doc(resolve_agent_context_dir(workspace))
    if contract.strip():
        return _ok(
            DoctorCheck.STAGE_REPORT_CONTRACT,
            f"Stage-report contract is {len(contract)} characters.",
        )
    return DoctorFinding(
        check=DoctorCheck.STAGE_REPORT_CONTRACT,
        status=DoctorStatus.FAIL,
        finding=("The stage-report contract is empty, so agents are given no report format."),
        remediation=(
            f"Check {WORKFLOW_ENFORCEMENT_DOC_REL} exists under this workspace's "
            f"agent_context and still carries its '{STAGE_REPORT_SECTION_TITLE}' section."
        ),
    )


#: What a declared toolchain requires before an agent can use it, as
#: (manifest, installed directory). Derived from the tree rather than assumed:
#: hardcoding `node_modules` and `.venv` would be loregarden's own shape imposed
#: on every workspace, and a repo with no `package.json` is not missing one.
_TOOLCHAIN_REQUIREMENTS: tuple[tuple[str, str], ...] = (
    ("package.json", "node_modules"),
    ("pyproject.toml", ".venv"),
)


def check_toolchain_installed(
    session: Session, workspace: Workspace, repo_root: Path
) -> DoctorFinding:
    """A toolchain the execution tree declares but has not installed.

    Checked against `repo_root`, which is the tree the agent will actually run
    in — a per-ticket worktree, not the shared checkout. That distinction is the
    whole point: a worktree is created empty of ignored directories, so
    `node_modules` and `.venv` are exactly what it lacks while the checkout
    beside it has both.

    ON DEMAND ONLY, not a dispatch check. A manifest does not tell you whether
    the run needs what it declares. Loregarden's own root `package.json` is the
    Tauri desktop host with devDependencies it never installs, while the real
    `node_modules` sits under `client/` — so this reports a missing toolchain for
    a tree that is working perfectly, and parking on it would stop every dispatch
    in this workspace. A human reading the finding knows which manifests matter;
    the preflight does not.
    """
    missing = [
        installed
        for manifest, installed in _TOOLCHAIN_REQUIREMENTS
        if (repo_root / manifest).is_file() and not (repo_root / installed).is_dir()
    ]
    if not missing:
        return _ok(DoctorCheck.TOOLCHAIN_INSTALLED, "Declared toolchains are installed.")
    return DoctorFinding(
        check=DoctorCheck.TOOLCHAIN_INSTALLED,
        status=DoctorStatus.FAIL,
        finding=f"{repo_root} declares a toolchain it has not installed: {', '.join(missing)}.",
        remediation=(
            "Install them in this tree, not the shared checkout — a worktree does not "
            "inherit ignored directories (npm ci / uv sync, as the manifest requires)."
        ),
    )


def check_git_writable(session: Session, workspace: Workspace, repo_root: Path) -> DoctorFinding:
    """The git directory the run must write to accepts a write.

    Asked by writing rather than by reading a permission bit: the case this
    exists for is a sandbox that denies the write while the mode bits still say
    it is allowed, which is how agents produced work they could never stage.
    """
    proc = run_git(
        ["rev-parse", "--git-dir"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return _ok(DoctorCheck.GIT_WRITABLE, "No git directory to write to.")
    git_dir = Path(proc.stdout.strip())
    if not git_dir.is_absolute():
        git_dir = repo_root / git_dir

    # Unique per call. Parallel stages fan out against one ticket's worktree, so
    # a fixed name lets one run's unlink race another's write — and the
    # FileNotFoundError that follows is an OSError, which this would report as an
    # unwritable git directory and park a run whose tree was perfectly fine.
    probe = git_dir / f".loregarden-write-probe-{os.getpid()}-{uuid4().hex}"
    try:
        probe.write_text("probe", encoding="utf-8")
        probe.unlink()
    except OSError as error:
        return DoctorFinding(
            check=DoctorCheck.GIT_WRITABLE,
            status=DoctorStatus.FAIL,
            finding=f"The git directory {git_dir} is not writable: {error}.",
            remediation=(
                "Grant the agent write access to it. Work produced without it cannot be "
                "staged or committed, and the failure surfaces at commit time instead."
            ),
        )
    return _ok(DoctorCheck.GIT_WRITABLE, f"{git_dir} is writable.")


def check_gate_commands_resolve(
    session: Session, workspace: Workspace, repo_root: Path
) -> DoctorFinding:
    """Every configured transition-gate command names something that resolves.

    A path with a separator in it is resolved against `repo_root` — the tree the
    agent runs in, not the shared checkout, which is how three hooks reported a
    missing file that was never missing. A bare name is resolved on PATH, because
    that is what a shell does with one; note this is the doctor process's PATH,
    which is not always the PATH the agent's shell will have.

    Only the executable is resolved, not the arguments: the rest of the command
    line is the gate's own business, and a check that tried to validate it would
    be guessing at shell semantics it does not own.

    DELIBERATELY NOT IN `DISPATCH_PREFLIGHT_CHECKS`, for the reason that constant
    gives for excluding REPO_HAS_COMMIT: `gate_runner` already catches OSError at
    exec time and reports `GateOutcome.UNAVAILABLE` with the OS error and the
    command, which is a better message than an approval a human has to clear.
    This earns its place as an on-demand check — knowing before a run starts that
    a gate cannot start — not as a park.

    WHAT IT DOES NOT CATCH, stated because the ticket's motivating case is one of
    them: a gate script that starts fine and then mis-resolves paths internally.
    Blobert's `asset_python.sh` fell back to a `cd` that re-resolved relative
    script paths, so three hooks reported a missing file that was never missing —
    the executable resolved, and this check would have passed it. Catching that
    needs the gate to run, which a read-only preflight must not do.
    """
    gates = resolve_orchestration_profile(workspace).gates
    configured = [command for command in gates.commands if command.strip()]
    if gates.transition_script.strip():
        configured.append(gates.transition_script)
    if not configured:
        return _ok(DoctorCheck.GATE_COMMANDS_RESOLVE, "No gate commands configured.")

    unresolved: list[str] = []
    for command in configured:
        parts = shlex.split(command)
        if not parts:
            continue
        executable = parts[0]
        if "/" in executable:
            if not (repo_root / executable).exists():
                unresolved.append(executable)
        elif shutil.which(executable, path=os.environ.get("PATH", "")) is None:
            unresolved.append(executable)

    if not unresolved:
        return _ok(
            DoctorCheck.GATE_COMMANDS_RESOLVE,
            f"All {len(configured)} gate command(s) resolve from {repo_root}.",
        )
    return DoctorFinding(
        check=DoctorCheck.GATE_COMMANDS_RESOLVE,
        status=DoctorStatus.FAIL,
        finding=f"Gate command(s) do not resolve from {repo_root}: {', '.join(unresolved)}.",
        remediation=(
            "Point them at a path that exists in the tree the agent runs in, or install "
            "the executable. A gate that cannot start reports a missing file that is "
            "usually present somewhere else."
        ),
    )


def check_studio_draft_drift(
    session: Session, workspace: Workspace, repo_root: Path
) -> DoctorFinding:
    """Studio drafts that have fallen out of step with their published template.

    The drift that motivated this was invisible without a check: the
    `loregarden-tdd-v3` draft sat at 9 stages against a live 12-stage template,
    and would have dropped `verify` and the terminal stage on the next publish.
    Nobody had opened Studio in between, so nothing had a reason to look.

    Reported, never repaired. Which copy is right depends on whether the draft is
    an unfinished edit or a stale one, and only a person knows that.
    """
    drifted = [item for item in detect_all_drift(session) if item.drifted]
    if not drifted:
        return _ok(DoctorCheck.STUDIO_DRAFT_DRIFT, "Every Studio draft matches its template.")
    detail = "; ".join(
        f"{item.slug}: "
        + ", ".join(
            part
            for part in (
                f"+{len(item.stages_added)} stage(s)" if item.stages_added else "",
                f"-{len(item.stages_removed)} stage(s)" if item.stages_removed else "",
                f"{len(item.stages_changed)} stage(s) changed" if item.stages_changed else "",
                (f"{item.draft_transition_count} vs {item.template_transition_count} transitions")
                if item.draft_transition_count != item.template_transition_count
                else "",
            )
            if part
        )
        for item in drifted
    )
    return DoctorFinding(
        check=DoctorCheck.STUDIO_DRAFT_DRIFT,
        status=DoctorStatus.FAIL,
        finding=f"{len(drifted)} Studio draft(s) differ from their published template — {detail}",
        remedy=(
            "Open Studio and reconcile the draft with its template before publishing. "
            "Publishing now would overwrite the live workflow with the draft."
        ),
    )


CHECKS: dict[DoctorCheck, Callable[[Session, Workspace, Path], DoctorFinding]] = {
    DoctorCheck.GIT_CORE_BARE: check_git_core_bare,
    DoctorCheck.GIT_ENV_LEAK: check_git_env_leak,
    DoctorCheck.REPO_HAS_COMMIT: check_repo_has_commit,
    DoctorCheck.DB_RESOLUTION: check_db_resolution,
    DoctorCheck.BACKEND_RELOAD_SENTINEL: check_backend_reload_sentinel,
    DoctorCheck.CLI_CREDENTIALS: check_cli_credentials,
    DoctorCheck.GIT_PORTABILITY: check_git_portability,
    DoctorCheck.STAGE_REPORT_CONTRACT: check_stage_report_contract,
    DoctorCheck.TOOLCHAIN_INSTALLED: check_toolchain_installed,
    DoctorCheck.GIT_WRITABLE: check_git_writable,
    DoctorCheck.GATE_COMMANDS_RESOLVE: check_gate_commands_resolve,
    DoctorCheck.STUDIO_DRAFT_DRIFT: check_studio_draft_drift,
}


def preflight_run(
    session: Session, run: AgentRun, workspace: Workspace, repo_root: Path
) -> list[DoctorFinding]:
    """Run the fast subset before a stage, and record which checks failed.

    Recorded on the run rather than raised: knowing that a stage started in a
    broken environment is worth having afterwards even when nobody stopped it at
    the time. The caller decides what a failure means; this only observes.
    """
    findings = run_checks(session, workspace, repo_root, checks=DISPATCH_PREFLIGHT_CHECKS)
    failures = [f.check.value for f in findings if f.status is DoctorStatus.FAIL]
    run.start_preflight_failures_json = json.dumps(failures)
    session.add(run)
    session.commit()
    return findings


def park_for_environment(
    session: Session, *, run: AgentRun, ticket: Ticket, summary: str
) -> Approval:
    """Send a failed preflight to the approval inbox instead of blocking.

    A broken checkout is a fact about this machine, not about the ticket — see
    `services.stage_parking`.
    """
    return park_stage(
        session,
        run=run,
        ticket=ticket,
        title=f"Environment preflight failed on {ticket.external_id}",
        impact=summary,
    )


def preflight_summary(findings: list[DoctorFinding]) -> str:
    """The failing checks and their remediations, for an approval's impact text.

    Written for whoever opens the inbox: the remediation is the whole point, and
    it is the part that otherwise lives only in someone's memory.
    """
    failures = [f for f in findings if f.status is DoctorStatus.FAIL]
    lines = ["Environment preflight failed before this stage could run."]
    for finding in failures:
        lines.append(f"  {finding.check.value}: {finding.finding}")
        lines.append(f"    fix: {finding.remediation}")
    lines.append(
        "Approving lets the stage run anyway. It does not fix any of the above, and "
        "the run will most likely fail the same way."
    )
    return "\n".join(lines)


def run_checks(
    session: Session,
    workspace: Workspace,
    repo_root: Path,
    *,
    checks: tuple[DoctorCheck, ...] | None = None,
) -> list[DoctorFinding]:
    """Run `checks` (all of them by default) against `repo_root`.

    A check that raises becomes a FAIL naming the exception, and the rest still
    run. Reporting six results and one broken check beats reporting a traceback.
    """
    selected = checks if checks is not None else tuple(CHECKS)
    findings: list[DoctorFinding] = []
    for check in selected:
        try:
            findings.append(CHECKS[check](session, workspace, repo_root))
        except Exception as exc:  # noqa: BLE001 - a broken check must not hide the others
            findings.append(
                DoctorFinding(
                    check=check,
                    status=DoctorStatus.FAIL,
                    finding=f"Check raised {type(exc).__name__}: {exc}",
                    remediation="This is a bug in the check itself, not in the workspace.",
                )
            )
    return findings
