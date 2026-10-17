"""Prove the tree a land would publish before its ref moves (lg-milestone-that-1238).

Two tickets reached `state=done` with their tests landed on `main` and their
implementations never written, and nothing noticed: the gate and the review
lanes had passed, the merge was clean, and a clean merge of a red tree is still
a clean merge. Nothing in the landing path had ever run a test.

So the landing asks. `git_merge_noco.merge_tips` separates the `commit-tree`
that writes the candidate merge from the `update-ref` that publishes it, and the
verification goes in that gap: the configured commands run against the candidate
and a refusal means the ref never moves. "Nothing red is published" then holds by
construction rather than by ordering discipline somewhere further out.

Three verdicts, not two, and the third is the one that earns its keep. It
passed; it ran and failed; it never ran. Reporting a verification that could not
run as a failing suite sends a person after a red suite that does not exist and
leaves the broken machine broken, which is why `GateOutcome.UNAVAILABLE` exists
and why `gate_runner.GATE_EX_UNAVAILABLE` is imported here rather than spelled
again. A timeout, a missing toolchain, a command that cannot be parsed and a
lease that could not be had are all that third verdict, and every one of them
refuses the land.

The tree verified is a detached worktree cut at the candidate, and deliberately
NOT the ticket's refreshed worktree. That tree can hold uncommitted agent
leftovers — the orchestrator sweeps the working tree, so leftovers are ordinary
— and the leftover can be the very implementation whose absence this exists to
catch. A false-pass channel is exactly what this module is about.

What it is NOT: hardcoded to pytest. The commands are per workspace
(`GitAutomationConfig.land_verify_commands`) because this control plane is
workspace-agnostic, and an empty list is the off state. A workspace with none
configured lands exactly as it did before, and says so on the result rather than
passing silently.
"""

from __future__ import annotations

import json
import logging
import shlex
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from loregarden.models.domain import (
    Artifact,
    ArtifactKind,
    DockerFootprint,
    GateOutcome,
    Ticket,
    Workspace,
)
from loregarden.services.artifact_service import BLOCKING_ISSUE_INLINE_LIMIT
from loregarden.services.capacity_environment import CPUS_ENV, WORKERS_ENV, worker_count
from loregarden.services.docker_capacity import CLASS_WEIGHTS, resolve_weights
from loregarden.services.gate_capacity import GateCapacityUnavailable, acquire_gate_capacity
from loregarden.services.gate_runner import GATE_EX_UNAVAILABLE, strip_ansi
from loregarden.services.git_automation_config import resolve_git_automation
from loregarden.services.git_branch import resolve_ticket_branch
from loregarden.services.git_subprocess import run_git, scrubbed_git_env
from loregarden.services.target_branch import target_branch_name
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session

logger = logging.getLogger(__name__)

#: What `landing._block_message` writes in front of this refusal's first line.
#: It lives here, and `landing` formats it from here, because it is spent out of
#: the same two budgets as the words after it and there must not be two answers
#: to "how long is the lead?". It already names both branches and says the land
#: did not happen, which is most of what the teaser has to say.
LAND_BLOCK_LEAD = "Could not land {branch} on {target}: "

#: How long the configured commands get, in total. A land verification is a
#: suite — two files of this repo's own take 118s — so this is not the gates'
#: 300s. A timeout REFUSES: a mitigation that let the land through on expiry
#: would remove the guard exactly when the suite is slowest.
LAND_VERIFY_TIMEOUT_SECONDS = 1800

#: How long the capacity lease is held for, with the same reasoning.
_LEASE_TTL_SECONDS = LAND_VERIFY_TIMEOUT_SECONDS + 300

#: AC14's inline excerpt: at most the LAST twenty lines. A suite puts its
#: platform banner, its rootdir and its plugin list first and what failed last,
#: so a head slice spends the one budget an operator reads on the least
#: informative lines in the run.
_INLINE_OUTPUT_LINES = 20

#: The teaser `TicketAttention` renders (`TEASER_CHARS`). The first line has to
#: stand alone inside it, and the longest live branch-plus-target pair is 99
#: characters, so the verdicts below are short on purpose.
_TEASER_CHARS = 140

#: The verdict's own words, kept to what the teaser can afford. "never ran" and
#: "failed" route to different people, so they must not read the same.
_FAILED_VERDICT = "the tests failed"
_UNAVAILABLE_VERDICT = "the tests never ran"


@dataclass(frozen=True)
class VerifyOutcome:
    """What the configured commands said about one candidate merge commit.

    `outcome` is the existing `GateOutcome` rather than a second vocabulary for
    the same four answers: PASSED, FAILED, UNAVAILABLE for could-not-run, and
    DISABLED for a workspace that configured nothing. `ok` and `outcome` are
    both carried because they answer different questions — DISABLED is ok and
    did not pass, and collapsing the two is how "nothing ran" starts reading as
    "it passed".
    """

    ok: bool
    outcome: GateOutcome
    #: The command this verdict is about: the one that failed, or "" when none
    #: ran or all of them passed.
    command: str = ""
    #: Its exit status, or None when no command got as far as exiting.
    exit_code: int | None = None
    #: The FULL output, not the excerpt. AC14's artifact is what this is for; a
    #: tail kept in its place leaves the operator the same twenty lines twice.
    output: str = ""
    #: The operator's words, sized for `ticket.blocking_issues`.
    detail: str = ""


@dataclass(frozen=True)
class LandVerifier:
    """The `merge_tips` verifier for one ticket, and what to say if it checked nothing.

    Callable, so it is the `verify` keyword with no adapter in between, and it
    carries `note` so `land_ticket` can report an unverified land as unverified
    without resolving the workspace's configuration a second time.
    """

    note: str
    verify: Callable[[str], VerifyOutcome]

    def __call__(self, candidate: str) -> VerifyOutcome:
        return self.verify(candidate)


def land_verifier(session: Session, ticket: Ticket, workspace: Workspace) -> LandVerifier:
    """The verification this ticket's land must pass, as `merge_tips` wants it.

    Resolves the workspace's (and the ticket's) configuration once, here, so the
    candidate-shaped callback it returns needs nothing but a sha.
    """
    automation = resolve_git_automation(workspace, ticket)
    repo_root = resolve_workspace_root(workspace)
    branch = resolve_ticket_branch(ticket)
    target = target_branch_name(session, ticket, workspace)
    lead = len(LAND_BLOCK_LEAD.format(branch=branch, target=target))

    if not automation.land_verify_commands:
        # Off, and never silently: the land goes through exactly as it did
        # before this module existed, and the result says nobody checked it.
        # That is the whole of the off state — there is deliberately no boolean
        # beside the list, because a switch that can be turned off is a
        # per-workspace way to make the guard untrue.
        note = "no land verification is configured for this workspace"
        return LandVerifier(
            note=note,
            verify=lambda _candidate: VerifyOutcome(
                ok=True, outcome=GateOutcome.DISABLED, detail=note
            ),
        )

    def verify(candidate: str) -> VerifyOutcome:
        outcome = _verify_candidate(
            session,
            ticket,
            workspace,
            repo_root=repo_root,
            candidate=candidate,
            commands=automation.land_verify_commands,
            footprint=automation.land_verify_capacity_footprint,
            lead=lead,
        )
        if not outcome.ok and outcome.output:
            _record_full_output(session, ticket, branch, outcome)
        return outcome

    return LandVerifier(note="", verify=verify)


def run_verify_command(command: str, cwd: Path, *, footprint: DockerFootprint) -> VerifyOutcome:
    """Run one configured command in `cwd` and classify how it ended.

    `shlex.split` raises on an unterminated quote and yields `[]` for a blank
    string, which `subprocess.run` turns into IndexError. Both are commands that
    examined nothing, so both are UNAVAILABLE: "nothing to do" is not a pass,
    and letting either raise takes the landing down with no block message at
    all.

    `footprint` is the size the verification was granted, and it reaches the
    command as `LOREGARDEN_CAPACITY_CPUS` / `_WORKERS` — the same two variables
    every other held command on this machine reads to size its own `-n` /
    `--maxWorkers`. The *lease* for it is held by `_verify_candidate` around the
    whole command list, because that is where the session is and because the
    verification is one unit of host load rather than one per command.

    The environment is scrubbed of git's repo bindings. `GIT_DIR` beats `cwd`
    for every git process beneath it, and loregarden runs inside hooks that
    export it, so a configured command that resolves its own scope through git —
    which is every test selector this repo has — would otherwise read the shared
    checkout and exit 0: a pass over a tree nobody read, which is the channel
    this module closes.
    """
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        return VerifyOutcome(
            ok=False,
            outcome=GateOutcome.UNAVAILABLE,
            command=command,
            detail=f"the command could not be parsed: {exc}",
        )
    if not argv:
        return VerifyOutcome(
            ok=False,
            outcome=GateOutcome.UNAVAILABLE,
            command=command,
            detail="the command is empty, so nothing examined the tree",
        )
    cpus, _memory_mb = resolve_weights(footprint) if footprint in CLASS_WEIGHTS else (0.0, 0)
    granted = {CPUS_ENV: f"{cpus:g}", WORKERS_ENV: str(worker_count(cpus))} if cpus > 0 else {}
    try:
        completed = subprocess.run(
            argv,
            cwd=str(cwd),
            env={**scrubbed_git_env(), **granted},
            capture_output=True,
            text=True,
            timeout=LAND_VERIFY_TIMEOUT_SECONDS,
            check=False,
        )
    except OSError as exc:
        # Not on PATH, or there and not executable. A fact about the machine,
        # not a claim about the code.
        return VerifyOutcome(
            ok=False, outcome=GateOutcome.UNAVAILABLE, command=command, detail=str(exc)
        )
    except subprocess.TimeoutExpired:
        return VerifyOutcome(
            ok=False,
            outcome=GateOutcome.UNAVAILABLE,
            command=command,
            detail=(
                f"the command timed out after {LAND_VERIFY_TIMEOUT_SECONDS}s, so "
                "nothing it would have found is known"
            ),
        )

    # `capture_output` reads both pipes to EOF before returning, so a suite
    # writing a megabyte cannot fill a buffer and deadlock. The whole of it is
    # carried: the excerpt is cut later, from the end, and the artifact wants
    # all of it.
    output = strip_ansi((completed.stdout or "") + (completed.stderr or ""))
    if completed.returncode == GATE_EX_UNAVAILABLE:
        # The command ran and said it could not do its job. Same number and same
        # meaning as the transition gates use, imported rather than re-spelled.
        return VerifyOutcome(
            ok=False,
            outcome=GateOutcome.UNAVAILABLE,
            command=command,
            exit_code=completed.returncode,
            output=output,
            detail="the command reported that it could not run",
        )
    if completed.returncode != 0:
        return VerifyOutcome(
            ok=False,
            outcome=GateOutcome.FAILED,
            command=command,
            exit_code=completed.returncode,
            output=output,
        )
    return VerifyOutcome(
        ok=True, outcome=GateOutcome.PASSED, command=command, exit_code=0, output=output
    )


def _verify_candidate(
    session: Session,
    ticket: Ticket,
    workspace: Workspace,
    *,
    repo_root: Path,
    candidate: str,
    commands: list[str],
    footprint: DockerFootprint,
    lead: int,
) -> VerifyOutcome:
    """Run every configured command against `candidate`, stopping at the first refusal.

    The list is an ordered set a workspace chose — a fast check, then the suite
    — so a runner that reads only the first entry verifies the cheap half of it,
    and one that lets a later pass overwrite an earlier failure verifies none of
    it. The first command that does not pass decides.
    """
    holder = Path(tempfile.mkdtemp(prefix="loregarden-land-verify-"))
    tree = holder / "tree"
    added = run_git(
        ["worktree", "add", "--detach", str(tree), candidate],
        cwd=str(repo_root),
        check=False,
        capture_output=True,
        text=True,
    )
    if added.returncode != 0:
        shutil.rmtree(holder, ignore_errors=True)
        words = (added.stderr or added.stdout or "git worktree add failed").strip()
        return _refusal(
            VerifyOutcome(ok=False, outcome=GateOutcome.UNAVAILABLE, detail=words), lead=lead
        )

    try:
        with _holding(session, ticket, workspace, footprint, candidate=candidate):
            for command in commands:
                outcome = run_verify_command(command, tree, footprint=footprint)
                if not outcome.ok:
                    return _refusal(outcome, lead=lead)
        return VerifyOutcome(ok=True, outcome=GateOutcome.PASSED)
    except GateCapacityUnavailable as exc:
        # No agent can fix a busy machine, and a land nothing examined must not
        # pass. UNAVAILABLE, and refused.
        return _refusal(
            VerifyOutcome(ok=False, outcome=GateOutcome.UNAVAILABLE, detail=str(exc)), lead=lead
        )
    finally:
        # In `finally` because a worktree left at a refused candidate is a ref
        # nothing prunes, and refusals repeat. It must not raise over the verdict
        # it is cleaning up after, so a failure is logged rather than returned —
        # but it IS logged: a `worktree remove` that quietly does nothing leaks
        # one tree per refusal, which is the thing this block exists to stop.
        removed = run_git(
            ["worktree", "remove", "--force", str(tree)],
            cwd=str(repo_root),
            check=False,
            capture_output=True,
            text=True,
        )
        if removed.returncode != 0:
            logger.warning(
                "land verification could not remove its worktree at %s (%s); "
                "`git worktree prune` in %s will clear it",
                tree,
                (removed.stderr or removed.stdout or "git gave no reason").strip(),
                repo_root,
            )
        shutil.rmtree(holder, ignore_errors=True)


@contextmanager
def _holding(
    session: Session,
    ticket: Ticket,
    workspace: Workspace,
    footprint: DockerFootprint,
    *,
    candidate: str,
) -> Iterator[None]:
    """Hold host capacity for the whole verification, and give it back.

    A land verification is a test suite on the orchestration thread, so it
    queues in the ledger every other suite on this machine queues in rather than
    piling onto them. Released in `finally`: a lease left held by a refusal
    starves the machine for its TTL, and a refused land is the case that repeats.
    """
    lease = acquire_gate_capacity(
        session,
        footprint,
        ticket=ticket,
        workspace=workspace,
        transition=f"land-verify {candidate[:12]}",
        ttl_seconds=_LEASE_TTL_SECONDS,
    )
    try:
        yield
    finally:
        lease.release(session)


def _refusal(outcome: VerifyOutcome, *, lead: int) -> VerifyOutcome:
    """The refusal with the operator's words on it, sized to the block message."""
    verdict = (
        _UNAVAILABLE_VERDICT if outcome.outcome is GateOutcome.UNAVAILABLE else _FAILED_VERDICT
    )
    return VerifyOutcome(
        ok=False,
        outcome=outcome.outcome,
        command=outcome.command,
        exit_code=outcome.exit_code,
        output=outcome.output,
        detail=_inline_block(
            lead=lead,
            verdict=verdict,
            command=outcome.command,
            exit_code=outcome.exit_code,
            reason=outcome.detail,
            output=outcome.output,
        ),
    )


def _inline_block(
    *,
    lead: int,
    verdict: str,
    command: str,
    exit_code: int | None,
    reason: str,
    output: str,
) -> str:
    """The refusal as it has to look to survive `BLOCKING_ISSUE_INLINE_LIMIT`.

    Past that limit `record_blocking_issue` replaces the WHOLE message with
    "Stage '…' hit a blocking issue — see the Timeline tab for details.", which
    names neither branch, nor target, nor a failing suite — in the one place
    `TicketAttention` looks. So this is built to the budget rather than written
    and hoped for: the verdict first, inside the teaser; then the command and
    its exit code, so whose problem it is needs nothing opened; then as much of
    the output's tail as is left, and nothing at all when nothing is left. The
    full output is the artifact, which is where the twenty lines an operator
    reads actually live.
    """
    budget = BLOCKING_ISSUE_INLINE_LIMIT - lead
    lines = [verdict[: max(0, min(len(verdict), _TEASER_CHARS - lead))]]
    spent = len(lines[0])

    def take(line: str) -> None:
        nonlocal spent
        room = budget - spent - 1
        if room <= 0 or not line:
            return
        lines.append(line[:room])
        spent += 1 + len(lines[-1])

    said = reason or (f"exited {exit_code}" if exit_code is not None else "")
    # One line, so the exit code is never the part that gets dropped: it is
    # shorter than the command and it is what says whose problem this is.
    take(f"{said} · $ {command}" if said and command else (said or f"$ {command}"))
    # Chosen from the END. Taking the last twenty and then filling the budget
    # from the front of THAT keeps the head of the tail, which on a real suite
    # is the middle of the run — neither what it was doing nor what failed.
    for line in _trailing(output, room=budget - spent):
        take(line)
    return "\n".join(lines)


def _trailing(output: str, *, room: int) -> list[str]:
    """The last of `output`'s lines that fit in `room`, in the order they ran."""
    kept: list[str] = []
    spent = 0
    for line in reversed(output.strip().splitlines()[-_INLINE_OUTPUT_LINES:]):
        stripped = line.strip()
        if not stripped or spent + 1 + len(stripped) > room:
            break
        kept.append(stripped)
        spent += 1 + len(stripped)
    kept.reverse()
    return kept


def _record_full_output(
    session: Session, ticket: Ticket, branch: str, outcome: VerifyOutcome
) -> None:
    """File the whole output where the timeline shows it.

    A title of its own, never `run_log_artifact_title`: `is_system_record`
    filters that one out of the timeline feed, and a refusal whose evidence is
    filtered out of the feed is a refusal nobody can act on. The command is
    deliberately not repeated in the body — it is already in the block message
    above, and the body is the output, nothing else.
    """
    session.add(
        Artifact(
            ticket_id=ticket.id,
            kind=ArtifactKind.LOG,
            title=f"Land verification output — {branch}",
            content_json=json.dumps(
                {"exit_code": outcome.exit_code, "output": outcome.output},
            ),
        )
    )
    session.commit()
