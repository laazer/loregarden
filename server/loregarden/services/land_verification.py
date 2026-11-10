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
import os
import shlex
import shutil
import signal
import subprocess
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
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
from loregarden.services.branch_names import resolve_ticket_branch
from loregarden.services.capacity_environment import grant_environment
from loregarden.services.capacity_progress import PROGRESS_ENV, LeaseProgress, ProgressSync
from loregarden.services.docker_capacity import CLASS_WEIGHTS, resolve_weights
from loregarden.services.gate_capacity import (
    GateCapacityUnavailable,
    GateLease,
    acquire_gate_capacity,
)
from loregarden.services.gate_runner import GATE_EX_UNAVAILABLE, strip_ansi
from loregarden.services.git_automation_config import resolve_git_automation
from loregarden.services.git_subprocess import run_git, scrubbed_git_env
from loregarden.services.target_branch import target_branch_name
from loregarden.services.workspace_paths import resolve_workspace_root
from loregarden.services.worktree_service import link_ignored_toolchains
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

#: The gate transition a land verification's capacity lease is held under. The
#: lease is how anyone outside this thread learns a landing is in line or
#: running its tests (`landing_status`), so the name is shared, not re-spelled.
LAND_VERIFY_TRANSITION = "land-verify"

#: Where the configured commands learn what the land adds: the target's tip,
#: which is the candidate merge's first parent. `server-tests.sh` reads it as
#: the base of `select_pytest_targets.py`, so a landing runs the tests its diff
#: can break rather than all of them. A full suite under a 4-cpu grant on a busy
#: machine ran past `LAND_VERIFY_TIMEOUT_SECONDS`; lg-milestone-that-2250 was
#: refused seven times in a row on 2026-11-06, every refusal a timeout.
LAND_BASE_ENV = "LOREGARDEN_TESTS_BASE"

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

    It also keeps the verdict it produced, so the receipt has ONE writer and
    that writer is in `land_ticket` (lg-milestone-that-1419). `merge_tips` calls
    `verify` only when a candidate merge exists, so a writer inside the callback
    would miss exactly the landings that produced no verdict at all — the
    landings nobody verified, which is the subject of that ticket. The verdict
    therefore rides back here and `land_ticket` records it on its way out.
    """

    note: str
    verify: Callable[[str], VerifyOutcome]
    #: Every (candidate, verdict) this verifier produced, in order. A list
    #: because the dataclass is frozen and the verdict is only known once
    #: `merge_tips` has written the candidate; `compare=False` so an accumulated
    #: verdict never changes what two verifiers compare as.
    verdicts: list[tuple[str, VerifyOutcome]] = field(default_factory=list, compare=False)

    def __call__(self, candidate: str) -> VerifyOutcome:
        outcome = self.verify(candidate)
        self.verdicts.append((candidate, outcome))
        return outcome

    @property
    def verdict(self) -> tuple[str, VerifyOutcome] | None:
        """The candidate and verdict this verifier last gave, or None if it was
        never asked — a landing `merge_tips` had nothing to verify."""
        return self.verdicts[-1] if self.verdicts else None


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
        return outcome

    return LandVerifier(note="", verify=verify)


def run_verify_command(
    command: str,
    cwd: Path,
    *,
    footprint: DockerFootprint,
    extra_env: dict[str, str] | None = None,
) -> VerifyOutcome:
    """Run one configured command in `cwd` and classify how it ended.

    `shlex.split` raises on an unterminated quote and yields `[]` for a blank
    string, which `subprocess.Popen` turns into IndexError. Both are commands that
    examined nothing, so both are UNAVAILABLE: "nothing to do" is not a pass,
    and letting either raise takes the landing down with no block message at
    all.

    `footprint` is the size the verification was granted, and it reaches the
    command as `LOREGARDEN_CAPACITY_CPUS` / `_WORKERS` — the same two variables
    every other held command on this machine reads to size its own `-n` /
    `--maxWorkers` — and as `PYTEST_XDIST_AUTO_NUM_WORKERS`, so a command
    configured as `pytest -n auto` starts the granted count rather than one
    worker per core. The *lease* for it is held by `_verify_candidate` around the
    whole command list, because that is where the session is and because the
    verification is one unit of host load rather than one per command.

    The environment is scrubbed of git's repo bindings. `GIT_DIR` beats `cwd`
    for every git process beneath it, and loregarden runs inside hooks that
    export it, so a configured command that resolves its own scope through git —
    which is every test selector this repo has — would otherwise read the shared
    checkout and exit 0: a pass over a tree nobody read, which is the channel
    this module closes.

    `extra_env` is what `_verify_candidate` adds for this landing: the diff
    base (`LAND_BASE_ENV`) and the progress file the lease copies
    (`PROGRESS_ENV`).
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
    granted = grant_environment(cpus) if cpus > 0 else {}
    try:
        returncode, raw_output = _run_to_deadline(
            argv, cwd=cwd, env={**scrubbed_git_env(), **granted, **(extra_env or {})}
        )
    except OSError as exc:
        # Not on PATH, or there and not executable. A fact about the machine,
        # not a claim about the code.
        return VerifyOutcome(
            ok=False, outcome=GateOutcome.UNAVAILABLE, command=command, detail=str(exc)
        )

    # The whole of it is carried: the excerpt is cut later, from the end, and the
    # artifact wants all of it.
    output = strip_ansi(decode_output(raw_output))
    if returncode is None:
        return VerifyOutcome(
            ok=False,
            outcome=GateOutcome.UNAVAILABLE,
            command=command,
            output=output,
            detail=(
                f"the command timed out after {LAND_VERIFY_TIMEOUT_SECONDS}s, so "
                "nothing it would have found is known"
            ),
        )
    if returncode == GATE_EX_UNAVAILABLE:
        # The command ran and said it could not do its job. Same number and same
        # meaning as the transition gates use, imported rather than re-spelled.
        return VerifyOutcome(
            ok=False,
            outcome=GateOutcome.UNAVAILABLE,
            command=command,
            exit_code=returncode,
            output=output,
            detail="the command reported that it could not run",
        )
    if returncode != 0:
        return VerifyOutcome(
            ok=False,
            outcome=GateOutcome.FAILED,
            command=command,
            exit_code=returncode,
            output=output,
        )
    return VerifyOutcome(
        ok=True, outcome=GateOutcome.PASSED, command=command, exit_code=0, output=output
    )


def decode_output(raw: bytes) -> str:
    """What a verification command wrote, as text a person can read. Never raises.

    The pipe is read as bytes and decoded here, in the function whose callers
    already hold a `VerifyOutcome`, so no decode can escape between the command
    and its verdict. A suite that prints one byte that is not UTF-8 (a raw byte
    in a test, a latin-1 tool on stderr, a filename in another encoding) once
    raised `UnicodeDecodeError` inside a strict text-mode read, a fourth exit
    beside the three verdicts that took the landing down with none of them
    (lg-milestone-that-1421). The verdict is the exit status's to decide, so a
    bad byte costs one replacement character and never the output around it.
    """
    return raw.decode("utf-8", errors="replace")


def _run_to_deadline(
    argv: list[str], *, cwd: Path, env: dict[str, str]
) -> tuple[int | None, bytes]:
    """Run `argv` to `LAND_VERIFY_TIMEOUT_SECONDS`: (exit code, output), code None on timeout.

    Its own session, so a timeout ends the whole tree. `subprocess.run` kills
    only the direct child, which here is `uv run`; pytest and its xdist workers
    outlived it and kept running on a machine already too full for them, under
    the retry that followed (lg-milestone-that-2196, 2026-11-05).

    What the command printed before the deadline is kept, because it is the only
    record of how far it got. The retried `communicate` returns everything read
    so far. The output stays bytes until `decode_output`.
    """
    with subprocess.Popen(
        argv,
        cwd=str(cwd),
        env=env,
        # One stream, in the order it was written. Captured apart and joined
        # stdout-first, `uv run`'s setup lines (stderr, written before pytest
        # started) landed after pytest's summary, and the block's tail read
        # "Installed 68 packages" instead of naming the failing tests.
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    ) as process:
        try:
            # Reads the pipe to EOF, so a suite writing a megabyte cannot fill a
            # buffer and deadlock.
            output, _ = process.communicate(timeout=LAND_VERIFY_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            _kill_session(process)
            return None, _drain(process)
    return process.returncode, output or b""


#: How long a killed tree gets to close its end of the pipe. Only a process that
#: left the session on purpose can still hold it.
_DRAIN_SECONDS = 10


def _kill_session(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (
        ProcessLookupError
    ):  # silent-ok: the whole session already exited; nothing is left to stop
        pass


def _drain(process: subprocess.Popen[bytes]) -> bytes:
    try:
        output, _ = process.communicate(timeout=_DRAIN_SECONDS)
    except subprocess.TimeoutExpired as held:
        # Something started its own session and still holds the pipe. Keep what
        # was read; the session itself is dead, so the land is refused either way.
        logger.warning(
            "land verification: a process outside the killed session still holds "
            "the output pipe of %s",
            process.args,
        )
        return held.output or b""
    return output or b""


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

    # The same toolchain a ticket worktree gets. Without it every suite that
    # drives a client gate found no node_modules, and from 2026-11-02 every
    # landing in loregarden was refused on `test_ts_org_gate_cache` (84 of 84).
    link_ignored_toolchains(repo_root, tree)
    try:
        # A passing verdict carries the last command it ran and the output of
        # every one of them. `VerifyOutcome(ok=True, outcome=PASSED)` on its own
        # was enough while only refusals were ever recorded; the receipt answers
        # "what did the suite say?", not merely "did it pass?"
        # (lg-milestone-that-1419).
        said: list[str] = []
        ran = ""
        progress = holder / "progress.json"
        extra_env = {PROGRESS_ENV: str(progress)}
        base = _land_base(repo_root, candidate)
        if base:
            extra_env[LAND_BASE_ENV] = base
        with (
            _holding(session, ticket, workspace, footprint, candidate=candidate) as lease,
            _syncing_progress(session, lease, progress),
        ):
            for index, command in enumerate(commands, start=1):
                # Ours first, so the lease names the command even when it never
                # reports; a command that does (server-tests.sh) overwrites it.
                _announce(progress, f"{command} ({index}/{len(commands)})")
                outcome = run_verify_command(
                    command, tree, footprint=footprint, extra_env=extra_env
                )
                said.append(outcome.output)
                if not outcome.ok:
                    return _refusal(outcome, lead=lead)
                ran = command
        return VerifyOutcome(
            ok=True,
            outcome=GateOutcome.PASSED,
            command=ran,
            exit_code=0,
            output="".join(said),
        )
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
) -> Iterator[GateLease]:
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
        transition=f"{LAND_VERIFY_TRANSITION} {candidate[:12]}",
        ttl_seconds=_LEASE_TTL_SECONDS,
    )
    try:
        yield lease
    finally:
        lease.release(session)


@contextmanager
def _syncing_progress(session: Session, lease: GateLease, path: Path) -> Iterator[None]:
    """Copy the commands' progress file onto the lease while they run.

    No lease (capacity accounting off) is no row to copy onto, so nothing runs.
    Its own sessions on the verifying session's engine: the copy happens on
    another thread, and a session is not shared across threads.
    """
    if not lease.lease_id:
        yield
        return
    bind = session.get_bind()
    with ProgressSync(lambda: Session(bind), lease.lease_id, path, _report_progress_problem):
        yield


def _report_progress_problem(message: str) -> None:
    # A lease with stale progress still reads as held, with its elapsed time;
    # the verification itself is unaffected, so this is a warning, not a refusal.
    logger.warning("land verification: %s", message)


def _announce(path: Path, step: str) -> None:
    """Write a progress report the way the commands do: whole file, by rename."""
    report = LeaseProgress(step=step)
    staging = path.with_suffix(".tmp")
    staging.write_text(report.model_dump_json(exclude_none=True), encoding="utf-8")
    staging.replace(path)


def _land_base(repo_root: Path, candidate: str) -> str:
    """The target's tip: the candidate merge's first parent (`merge_tips` writes
    it `-p target -p source`). "" when git cannot say, and the commands then fall
    back to a base of their own choosing — broader, never narrower."""
    parent = run_git(
        ["rev-parse", "--verify", "--quiet", f"{candidate}^1"],
        cwd=str(repo_root),
        check=False,
        capture_output=True,
        text=True,
    )
    if parent.returncode != 0:
        logger.warning(
            "land verification: could not read the first parent of %s in %s (%s); "
            "the commands will choose their own diff base",
            candidate,
            repo_root,
            (parent.stderr or "git gave no reason").strip(),
        )
        return ""
    return parent.stdout.strip()


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


#: The receipt's three verdict words, and the whole of the client contract:
#: `verdictTone` colours `passed` good (it is in `PASSING`) and `failed` bad (it
#: is in `FAILING`), and `disabled` falls through to warn — the right tone for a
#: landing nobody verified. Three, not four, because the client colours three;
#: `GateOutcome`'s fourth value survives in `content_json.outcome`.
RECEIPT_PASSED = "passed"
RECEIPT_FAILED = "failed"
RECEIPT_DISABLED = "disabled"

#: What `content_json.outcome` says for a landing that produced no verdict at
#: all: the target already held the branch, so there was no candidate merge for a
#: suite to run against and no suite ran. `disabled` by status, because nobody
#: verified it, and distinguishable here from a workspace that configured
#: nothing.
RECEIPT_ALREADY_CONTAINED = "already_contained"

#: Every `GateOutcome` value onto three words, and TOTAL on purpose: this is
#: subscripted, not `.get`, and `record_land_verification` runs AFTER `_record`
#: has committed `landed_sha`, so a member missing here is a stamped landing
#: with no receipt — the one state the AC4 detector reads and the receipt census
#: cannot see. `SKIPPED` is `disabled` for the same reason as the no-verdict
#: recording: no suite ran, and nobody verified the landing.
#:
#: UNAVAILABLE is `failed` and not a word of its own: the land was refused
#: either way, so bad tone is right for both, and "the tests never ran" — which
#: routes to a different person than "the tests failed" — keeps its distinction
#: in `outcome` and `detail` rather than buying a fourth pill word the client
#: does not colour.
_RECEIPT_STATUS: dict[GateOutcome, str] = {
    GateOutcome.PASSED: RECEIPT_PASSED,
    GateOutcome.FAILED: RECEIPT_FAILED,
    GateOutcome.UNAVAILABLE: RECEIPT_FAILED,
    GateOutcome.TIMEOUT: RECEIPT_FAILED,
    GateOutcome.DISABLED: RECEIPT_DISABLED,
    GateOutcome.SKIPPED: RECEIPT_DISABLED,
}


def record_land_verification(
    session: Session,
    ticket: Ticket,
    *,
    branch: str,
    target: str,
    candidate_sha: str,
    outcome: VerifyOutcome | None,
) -> Artifact:
    """File the one row that answers "was a suite run against this landing?".

    One per landing, on every verdict, and it REPLACES the `kind=log`
    `Land verification output — <branch>` row this module used to write under
    `if not outcome.ok and outcome.output`. That guard is why the answer existed
    nowhere: `select count(*) from artifacts where title like 'Land verification
    output%'` was 0 across 19,418 live rows, so three recurrences of a ticket
    marked done with its implementation missing were invisible until somebody ran
    the full suite by hand (lg-milestone-that-1419).

    `outcome=None` is a landing that produced no verdict at all. The title and
    `content_json.status` carry the same three words so the Title column, the
    Outputs tab's `matches()` search and `reportVerdict`'s pill all read it
    without a client change; `stage_key` is the other key `reportVerdict`
    requires, and without both it renders nothing. The body carries the FULL
    output, never the inline excerpt `_trailing` cuts for the block message —
    the operator already has those twenty lines there.
    """
    status = _RECEIPT_STATUS[outcome.outcome] if outcome is not None else RECEIPT_DISABLED
    receipt = Artifact(
        ticket_id=ticket.id,
        kind=ArtifactKind.LAND_VERIFICATION,
        title=f"Land verification — {status} — {branch}",
        content_json=json.dumps(
            {
                "status": status,
                "stage_key": ticket.workflow_stage_key,
                "outcome": (
                    outcome.outcome.value if outcome is not None else RECEIPT_ALREADY_CONTAINED
                ),
                "branch": branch,
                "target": target,
                "candidate_sha": candidate_sha,
                "command": outcome.command if outcome is not None else "",
                # None, never 0: nothing exited, and a 0 here would read as a
                # clean run in the one field an operator checks first.
                "exit_code": outcome.exit_code if outcome is not None else None,
                "output": outcome.output if outcome is not None else "",
            }
        ),
    )
    session.add(receipt)
    session.commit()
    return receipt
