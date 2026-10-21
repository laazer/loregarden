"""Let an agent merge the base into an integration branch that conflicts with it.

When `main` moves with a change that conflicts with a tree's integration
branch, every stage run in the tree refuses to start (the refresh in
`target_branch`). Before this, the only way forward was a person: the inbox
card in `integration_conflict_card` asked someone to do the merge by hand,
and until they did, an autopilot dispatching the tree's tickets watched each
one block within two seconds and paused itself (init-pre-tinkercg-3, after
#578/#583/#592 touched three files the tree had also changed).

A landing conflict already gets a resolution turn (`landing`), under the same
two switches this honours: `auto_resolve_conflicts`, and
`max_conflict_resolve_attempts` per integration tip. This is that turn for the
integration branch itself. It runs the `repair` agent in a throwaway worktree
detached at the branch's tip, mid-merge. The agent stages its resolution; this
module writes the merge commit with `commit-tree`, signed as `git_merge_noco`
signs, and moves the branch only when the commit holds both tips, no conflict
marker, and passes the workspace gates on what it adds over the base. The move
is a compare-and-swap, so a sibling that landed meanwhile is never lost; the
next dispatch merges again.

The commit is not made with `git commit` because pre-commit grades the staged
files, and a merge stages every file the base changed: a finding the base
already carries refused the merge as if this turn wrote it
(lg-milestone-that-491: three conflicts resolved, 214 tests green, refused for
`enums.py` being over the size cap on `main`). The gates run here instead,
scoped to the diff from the base tip, which is what the branch's own pull
request to the base will be graded on.

The turn is recorded as an `AgentRun` on the tree's root, so the operator can
read what it did, and its failures are what the attempt budget counts. When
the switch is off, the budget is spent or the turn fails, the caller files the
card as before, naming why the agent did not resolve it.

Kept free of `target_branch` imports so `target_branch` can call it.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import threading
from dataclasses import dataclass, replace
from pathlib import Path

from loregarden.agents.registry import REPAIR_AGENT_ID, get_agent
from loregarden.models.domain import AgentRun, BlockKind, RunStatus, Ticket, Workspace, utcnow
from loregarden.services.block_classification import classify_block_message
from loregarden.services.cli_agent_runner import (
    CliAgentProfile,
    CliTurnTimeout,
    resolve_agent_timeout,
    run_cli_agent_turn,
    stub_response,
)
from loregarden.services.cli_auth_errors import format_cli_auth_hint, format_cli_missing_hint
from loregarden.services.cli_output import extract_triage_reply
from loregarden.services.git_automation_config import resolve_git_automation
from loregarden.services.git_merge_noco import (
    is_ancestor,
    read_branch_tips,
    rev,
    signs_commits,
)
from loregarden.services.git_subprocess import run_git
from loregarden.services.integration_merge_budget import (
    INTEGRATION_MERGE_STAGE,
    harness_retry_at,
    merge_turn_refusal,
    refused_turns,
)
from loregarden.services.organization_gate_service import OrganizationScope, check_repo
from loregarden.services.run_concurrency import new_run_code
from loregarden.services.run_lease import lease_renewal
from loregarden.services.run_log_stream import bootstrap_run_log, finalize_run_log_artifact
from sqlmodel import Session

logger = logging.getLogger(__name__)

INTEGRATION_MERGE_CLI_PROFILE = CliAgentProfile(
    agent_id=REPAIR_AGENT_ID,
    assistant_label="Integration merge",
    cli_label="Integration merge",
    stub_env="LOREGARDEN_INTEGRATION_MERGE_STUB_RESPONSE",
    timeout_env="LOREGARDEN_INTEGRATION_MERGE_TIMEOUT",
    tmp_prefix="loregarden-integration-merge-",
    reply_cap=8000,
)

#: Markers git writes into a conflicted file. `=======` alone is left out: it is
#: ordinary Markdown and reStructuredText.
_MARKER_PATTERN = r"^(<<<<<<<|>>>>>>>)( |$)"

_LOCKS: dict[tuple[str, str], threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


@dataclass(frozen=True)
class MergeResolution:
    """Whether ``branch`` now holds the base; ``detail`` says why not.

    ``kind`` is why a turn failed, recorded on its run: `WORK` when it tried
    and was refused (it spends an attempt), `HARNESS` when it never got to try
    (it does not; `integration_merge_budget`). None on success, and when no
    turn was started.
    """

    ok: bool
    detail: str = ""
    kind: BlockKind | None = None


def _refused(detail: str) -> MergeResolution:
    """The turn tried, and what it produced was not accepted."""
    return MergeResolution(False, detail, BlockKind.WORK)


def _unable(detail: str) -> MergeResolution:
    """The turn never got to try, or loregarden failed around it."""
    return MergeResolution(False, detail, BlockKind.HARNESS)


def _lock_for(repo_root: Path, branch: str) -> threading.Lock:
    """One merge per branch at a time: two siblings dispatched together hit the
    same conflict, and the second should wait for the first's merge, not race it."""
    key = (str(repo_root.resolve()), branch)
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


def _git(cwd: Path, *args: str):
    return run_git(list(args), cwd=str(cwd), check=False, capture_output=True, text=True)


def _words(result, fallback: str) -> str:
    return (result.stderr or result.stdout or fallback).strip()


def merge_base_by_agent(
    session: Session,
    workspace: Workspace,
    root: Ticket,
    *,
    repo_root: Path,
    branch: str,
    base: str,
    files: tuple[str, ...],
    about: str = "",
) -> MergeResolution:
    """Have the repair agent merge ``base`` into ``branch``. Blocks while it runs.

    ``about`` tells the agent what ``branch`` is and what each side holds; the
    default describes ``root``'s integration branch.

    ``ok`` means the caller's refresh can be retried: the branch was merged
    here, or by a concurrent caller while this one waited for the lock.
    """
    if not resolve_git_automation(workspace, root).auto_resolve_conflicts:
        return MergeResolution(False, "auto_resolve_conflicts is off for this tree")
    with _lock_for(repo_root, branch):
        tips = read_branch_tips(repo_root, branch, base)
        branch_sha, base_sha = tips.of(branch), tips.of(base)
        if not branch_sha or not base_sha:
            return MergeResolution(False, tips.error or f"cannot read {branch} and {base}")
        if is_ancestor(repo_root, base_sha, branch_sha):
            return MergeResolution(True, "already merged by a concurrent turn")
        refusal = merge_turn_refusal(session, workspace, root, branch_sha=branch_sha)
        if refusal:
            return MergeResolution(False, refusal)
        refused = refused_turns(session, root, branch_sha=branch_sha)
        run = _start_run(session, workspace, root, branch=branch, branch_sha=branch_sha)
        logger.warning(
            "Branch %s cannot take %s (conflicts in %s); running merge turn %s",
            branch,
            base,
            ", ".join(files),
            run.run_code,
        )
        try:
            # Renewed like every supervised run: a merge turn outlives the 10-minute
            # lease, and an unrenewed one was reaped mid-turn as abandoned (954,
            # 2026-10-16).
            with lease_renewal(run.id):
                resolution, reply = _merge_in_worktree(
                    workspace,
                    root,
                    run,
                    repo_root=repo_root,
                    branch=branch,
                    base=base,
                    tips=(branch_sha, base_sha),
                    files=files,
                    # A retry on the same tip is told why the last one was refused
                    # and what it said, so it is not the same turn again.
                    refused=_previous_turn(refused[-1]) if refused else "",
                    about=about,
                )
        except Exception as exc:
            # Loregarden's own failure, not the agent's: settle the run so it
            # neither stays RUNNING nor spends an attempt, then let it surface.
            _finish_run(session, run, _unable(f"the merge turn crashed: {exc}"), "")
            raise
        _finish_run(session, run, resolution, reply)
        if resolution.kind is BlockKind.HARNESS:
            retry_at = harness_retry_at(session, root, branch_sha=branch_sha)
            when = f"after {retry_at:%Y-%m-%d %H:%M} UTC" if retry_at else "on the next sweep"
            return replace(
                resolution,
                detail=(
                    f"the merge turn could not attempt the merge (harness failure, not "
                    f"counted against the attempt budget; the next turn runs {when}): "
                    f"{resolution.detail}"
                ),
            )
        return resolution


def _previous_turn(run: AgentRun) -> str:
    """Why ``run`` was refused, then what its agent said, for the retry's brief."""
    said = run.stdout.strip()
    if not said:
        return run.stderr
    return f"{run.stderr.strip()}\n\nWhat that turn said before it ended:\n{said[-2000:]}"


def _start_run(
    session: Session, workspace: Workspace, root: Ticket, *, branch: str, branch_sha: str
) -> AgentRun:
    run = AgentRun(
        run_code=new_run_code(),
        ticket_id=root.id,
        workspace_id=workspace.id,
        agent_id=REPAIR_AGENT_ID,
        stage_key=INTEGRATION_MERGE_STAGE,
        status=RunStatus.RUNNING,
        started_at=utcnow(),
        start_branch=branch,
        start_head_sha=branch_sha,
        auto_approve=True,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _finish_run(session: Session, run: AgentRun, resolution: MergeResolution, reply: str) -> None:
    run.status = RunStatus.SUCCEEDED if resolution.ok else RunStatus.FAILED
    if not resolution.ok:
        if resolution.kind is None:
            # Every failure path below names its kind; one that does not is
            # counted, so a missed path costs a retry rather than buying endless ones.
            logger.warning("Merge turn %s failed without a kind; counting it", run.run_code)
        run.failure_kind = resolution.kind or BlockKind.WORK
    run.stdout = reply
    run.stderr = "" if resolution.ok else resolution.detail[:4000]
    run.finished_at = utcnow()
    session.add(run)
    session.commit()
    finalize_run_log_artifact(run, status=run.status, stderr=run.stderr)
    if resolution.ok:
        logger.info("Merge turn %s resolved %s", run.run_code, run.start_branch)
    else:
        logger.warning(
            "Merge turn %s left %s unresolved: %s",
            run.run_code,
            run.start_branch,
            resolution.detail,
        )


def _merge_in_worktree(
    workspace: Workspace,
    root: Ticket,
    run: AgentRun,
    *,
    repo_root: Path,
    branch: str,
    base: str,
    tips: tuple[str, str],
    files: tuple[str, ...],
    refused: str,
    about: str,
) -> tuple[MergeResolution, str]:
    branch_sha, base_sha = tips
    holder = Path(tempfile.mkdtemp(prefix="loregarden-integration-merge-"))
    tree = holder / "tree"
    added = _git(repo_root, "worktree", "add", "--detach", str(tree), branch_sha)
    if added.returncode != 0:
        shutil.rmtree(holder, ignore_errors=True)
        return _unable(f"cannot cut a worktree: {_words(added, 'git worktree add')}"), ""
    try:
        # `--no-commit` either way: the commit is written below, never by a hook-run `git commit`.
        merged = _git(tree, "merge", "--no-ff", "--no-commit", base_sha)
        turn = AgentTurn(reply="git merged cleanly in a checkout; no agent turn was needed.")
        if merged.returncode != 0:
            # Exit 1 with conflicts is the expected start; the agent finishes it.
            turn = _attempt(
                workspace,
                root,
                run,
                tree=tree,
                branch=branch,
                base=base,
                tips=tips,
                files=files,
                refused=refused,
                about=about,
            )
        verdict = _settle(repo_root, tree, branch=branch, base=base, tips=tips, files=files)
        if not verdict.ok:
            if turn.error:
                # An agent that never got to try leaves the conflicts as git left
                # them; that is the harness's failure, not a refused resolution.
                kind = BlockKind.HARNESS if turn.never_ran else verdict.kind
                return MergeResolution(
                    False, f"the merge turn failed ({turn.error}); {verdict.detail}", kind
                ), turn.reply
            return verdict, turn.reply
        return _move_branch(repo_root, tree, branch=branch, expected=branch_sha), turn.reply
    finally:
        _remove_worktree(repo_root, tree)
        shutil.rmtree(holder, ignore_errors=True)


@dataclass(frozen=True)
class AgentTurn:
    """What the agent's turn returned, or why it did not finish."""

    reply: str = ""
    #: The error the turn ended on; "" when it returned.
    error: str = ""
    #: True when the error left the agent no chance at the merge: the CLI could
    #: not start, the agent is not configured, or the CLI refused the turn.
    never_ran: bool = False


def _cli_refused(detail: str) -> bool:
    """Whether a CLI's failure is a refusal to run at all: a usage limit, auth."""
    return (
        format_cli_auth_hint(detail) is not None
        or classify_block_message(detail) is BlockKind.HARNESS
    )


def _attempt(
    workspace: Workspace,
    root: Ticket,
    run: AgentRun,
    *,
    tree: Path,
    branch: str,
    base: str,
    tips: tuple[str, str],
    files: tuple[str, ...],
    refused: str,
    about: str,
) -> AgentTurn:
    """Run the agent. Its failure is not final on its own: a turn that staged the
    resolution and then timed out still did the work, and the tree, not the
    reply, is what `_settle` checks."""
    try:
        return AgentTurn(
            reply=_run_agent(
                workspace,
                root,
                run,
                tree=tree,
                branch=branch,
                base=base,
                tips=tips,
                files=files,
                refused=refused,
                about=about,
            )
        )
    except CliTurnTimeout as exc:
        # First: TimeoutError is an OSError. The agent ran out of time on the merge;
        # what it said until then is kept, so the run shows how far it got.
        logger.warning("Merge turn %s for %s timed out: %s", run.run_code, branch, exc)
        return AgentTurn(reply=extract_triage_reply(exc.output), error=str(exc))
    except TimeoutError as exc:
        logger.warning("Merge turn %s for %s timed out: %s", run.run_code, branch, exc)
        return AgentTurn(error=str(exc))
    except (OSError, ValueError) as exc:
        # The CLI could not be started, or the agent is not configured.
        detail = format_cli_missing_hint(str(exc), exc) or str(exc)
        logger.warning("Merge turn %s for %s could not start: %s", run.run_code, branch, detail)
        return AgentTurn(error=detail, never_ran=True)
    except RuntimeError as exc:
        # The CLI exited non-zero: a usage limit or auth refusal never reached the merge.
        detail = str(exc)
        logger.warning("Merge turn %s for %s did not finish: %s", run.run_code, branch, detail)
        # The CLI's own words, not a hint: `harness_retry_at` reads a reset time from them.
        return AgentTurn(error=detail, never_ran=_cli_refused(detail))


def _settle(
    repo_root: Path,
    tree: Path,
    *,
    branch: str,
    base: str,
    tips: tuple[str, str],
    files: tuple[str, ...],
) -> MergeResolution:
    """Commit what the turn staged, then check the commit; ``tree``'s HEAD is it on success."""
    branch_sha, base_sha = tips
    committed = _commit_staged_merge(
        repo_root, tree, subject=f"Merge {base} into {branch}", tips=tips
    )
    if not committed.ok:
        return committed
    verdict = _verify(tree, branch_sha=branch_sha, base_sha=base_sha, files=files)
    if not verdict.ok:
        return verdict
    return _grade(tree, base=base, base_sha=base_sha)


def _paths(tree: Path, *args: str) -> tuple[str, str]:
    """The paths a listing command names, or why it could not list them."""
    listed = _git(tree, *args)
    if listed.returncode != 0:
        return "", f"cannot read the merge tree: {_words(listed, 'git ' + args[0])}"
    return ", ".join(sorted(set(listed.stdout.split()))), ""


def _commit_staged_merge(
    repo_root: Path, tree: Path, *, subject: str, tips: tuple[str, str]
) -> MergeResolution:
    """Write the merge the turn staged as a commit of both tips, and point HEAD at it.

    A turn that committed the merge itself has nothing staged left; its commit
    is checked by `_verify` like this one.
    """
    branch_sha, base_sha = tips
    if not rev(tree, "MERGE_HEAD"):
        return MergeResolution(True)
    if rev(tree, "HEAD") != branch_sha:
        return _refused("HEAD moved during the merge; nothing was committed")
    for args, problem in (
        (("diff", "--name-only", "--diff-filter=U"), "left conflicts unresolved in"),
        (("diff", "--name-only"), "left changes it did not stage in"),
        (("ls-files", "--others", "--exclude-standard"), "left untracked files"),
    ):
        named, error = _paths(tree, *args)
        if error or named:
            return _unable(error) if error else _refused(f"the merge turn {problem}: {named}")
    written = _git(tree, "write-tree")
    if written.returncode != 0:
        return _unable(f"cannot write the merge: {_words(written, 'write-tree')}")
    sign = ["-S"] if signs_commits(repo_root) else []
    commit = _git(
        tree,
        "commit-tree",
        written.stdout.strip(),
        *sign,
        "-p",
        branch_sha,
        "-p",
        base_sha,
        "-m",
        subject,
    )
    if commit.returncode != 0:
        return _unable(f"cannot commit the merge: {_words(commit, 'commit-tree')}")
    # Leave the merge (index and files stay) and stand on the commit, so the
    # gates' diff from the base tip reads this commit and nothing else.
    for args in (("merge", "--quit"), ("reset", "--soft", commit.stdout.strip())):
        moved = _git(tree, *args)
        if moved.returncode != 0:
            return _unable(f"cannot check out the merge: {_words(moved, args[0])}")
    return MergeResolution(True)


def _run_agent(
    workspace: Workspace,
    root: Ticket,
    run: AgentRun,
    *,
    tree: Path,
    branch: str,
    base: str,
    tips: tuple[str, str],
    files: tuple[str, ...],
    refused: str,
    about: str,
) -> str:
    stub = stub_response(INTEGRATION_MERGE_CLI_PROFILE)
    if stub is not None:
        return stub
    agent = get_agent(REPAIR_AGENT_ID)
    if not agent:
        raise ValueError(f"Unknown integration merge agent: {REPAIR_AGENT_ID}")
    budget = resolve_agent_timeout(agent, INTEGRATION_MERGE_CLI_PROFILE.timeout_env)
    return run_cli_agent_turn(
        INTEGRATION_MERGE_CLI_PROFILE,
        workspace=workspace,
        prompt=merge_brief(
            root,
            branch=branch,
            base=base,
            tips=tips,
            files=files,
            refused=refused,
            about=about,
            budget_seconds=budget,
        ),
        run_id=run.id,
        workspace_slug=workspace.slug or "",
        workspace_root=tree,
        # Streamed into the run's log as it arrives: a turn killed by its timeout
        # still shows what it did (both turns on lg-milestone-that-1216 left nothing).
        thinking_sink=bootstrap_run_log(run),
    )


def merge_brief(
    root: Ticket,
    *,
    branch: str,
    base: str,
    tips: tuple[str, str],
    files: tuple[str, ...],
    refused: str = "",
    about: str = "",
    budget_seconds: int | None = None,
) -> str:
    branch_sha, base_sha = tips
    about = about or (
        f"`{branch}` is the integration branch of {root.external_id} ({root.title}): every "
        f"ticket in that tree lands on it, and none can start while it cannot take `{base}`. "
        f"`{base}` holds work that already shipped; `{branch}` holds this tree's landed tickets."
    )
    listed = "\n".join(f"- {path}" for path in files) or "- (git named no paths; run `git status`)"
    previous = (
        "## The last attempt on this tip was refused\n"
        f"```\n{refused.strip()[:3000]}\n```\n"
        "Resolve the conflicts again, and fix what that names too.\n\n"
        if refused.strip()
        else ""
    )
    # Both turns on lg-milestone-that-1216 (2026-10-19) had every conflict
    # resolved within seven minutes and then ran tests until the timeout killed
    # them with nothing staged, so the resolution was thrown away with the tree.
    # Staging comes before testing, and the budget is stated, so a turn that runs
    # out of time still hands over its resolution.
    budget = (
        f"You have {budget_seconds // 60} minutes. When they run out the turn is killed, and "
        "only what you have staged by then counts.\n\n"
        if budget_seconds
        else ""
    )
    return (
        f"# Merge `{base}` into `{branch}`\n\n"
        f"{about}\n\n"
        f"This working tree is detached at `{branch}` ({branch_sha[:12]}) and mid-merge with "
        f"`{base}` ({base_sha[:12]}). git stopped on conflicts in:\n{listed}\n\n"
        f"{budget}"
        f"{previous}"
        "## Do\n"
        f"1. For each file, read what each side meant: `git log --oneline HEAD...MERGE_HEAD -- <file>` "
        "and the commits it lists.\n"
        "2. Resolve every conflict so both intents survive. Do not drop either side to make "
        "the conflict go away.\n"
        "3. `git add` each file as soon as it is resolved, before running anything.\n"
        "4. Run the tests that cover the files you resolved, once, and fix what the merge "
        "broke. `git add` every fix as you make it. Leave no unstaged change and no "
        "untracked file.\n\n"
        "Do not commit. Loregarden writes the merge commit from what you staged, then runs the "
        f"workspace gates on what the merge adds over `{base}`, and moves `{branch}` only if "
        "they pass.\n\n"
        "## Do not\n"
        "- commit, push, switch branches, rebase, reset, or abort the merge;\n"
        "- build another checkout or worktree to compare test results against; a failure "
        "this merge did not cause is not yours to fix here;\n"
        "- run the whole suite, or rerun a test file that already passed;\n"
        "- edit files the merge did not touch, beyond what a broken test or a gate finding "
        "needs.\n\n"
        "If a conflict needs a decision only a person can make, leave it unresolved and say why "
        "in your reply: the branch is not moved, and a person is asked instead."
    )


def _verify(
    tree: Path, *, branch_sha: str, base_sha: str, files: tuple[str, ...]
) -> MergeResolution:
    """HEAD is a finished merge of both tips, with nothing left half-resolved."""
    status = _git(tree, "status", "--porcelain")
    if status.returncode != 0:
        return _unable(f"cannot read the merge tree: {_words(status, 'git status')}")
    if status.stdout.strip():
        return _refused(f"the merge turn left uncommitted changes:\n{status.stdout.strip()[:1500]}")
    head = rev(tree, "HEAD")
    if not (is_ancestor(tree, branch_sha, head) and is_ancestor(tree, base_sha, head)):
        return _refused(
            f"HEAD {head[:12] or '?'} does not contain both {branch_sha[:12]} and {base_sha[:12]}"
        )
    if files:
        markers = _git(tree, "grep", "-n", "-E", _MARKER_PATTERN, "HEAD", "--", *files)
        if markers.returncode == 0:
            return _refused(f"conflict markers were committed:\n{markers.stdout.strip()[:1500]}")
        if markers.returncode != 1:
            return _unable(f"cannot check for markers: {_words(markers, 'git grep')}")
    return MergeResolution(True)


def _grade(tree: Path, *, base: str, base_sha: str) -> MergeResolution:
    """The workspace gates on what the merge at HEAD adds over the base tip.

    `worktree` scope diffs from `merge-base(base_sha, HEAD)`, which is the base
    tip itself once HEAD contains it, so the base's own content is never graded
    here: only the branch's work and the resolution.
    """
    results = check_repo(tree, OrganizationScope.WORKTREE, base=base_sha)
    failing = [result for result in results if not result.ok]
    if not failing:
        return MergeResolution(True)
    lines: list[str] = []
    for result in failing:
        # A gate that could not run has a message and no findings.
        lines += [f"{result.checker}: {finding}" for finding in result.findings] or [
            f"{result.checker}: {result.message}"
        ]
    detail = (
        f"the merge fails the workspace gates on what it adds over {base}:\n"
        + "\n".join(lines)[:3000]
    )
    # A finding is the resolution's to fix; a gate that could not run is not.
    if any(result.findings for result in failing):
        return _refused(detail)
    return _unable(detail)


def _move_branch(repo_root: Path, tree: Path, *, branch: str, expected: str) -> MergeResolution:
    head = rev(tree, "HEAD")
    moved = _git(repo_root, "update-ref", f"refs/heads/{branch}", head, expected)
    if moved.returncode != 0:
        # A sibling landed while the turn ran. Its work is kept; the next
        # dispatch finds the branch still behind the base and merges again.
        return _unable(
            f"{branch} moved while the merge ran, so {head[:12]} was not applied: "
            f"{_words(moved, 'git update-ref refused')}"
        )
    logger.warning("Merged the base into %s as %s (was %s)", branch, head[:12], expected[:12])
    return MergeResolution(True)


def _remove_worktree(repo_root: Path, tree: Path) -> None:
    removed = _git(repo_root, "worktree", "remove", "--force", str(tree))
    if removed.returncode != 0:
        logger.warning(
            "Could not remove merge worktree %s: %s; `git worktree prune` clears it once the "
            "directory is gone",
            tree,
            _words(removed, "git worktree remove"),
        )
