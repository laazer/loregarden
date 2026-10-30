"""A pid, plus enough to know it is still the same process.

317 detaches the agent subprocess so a server restart stops killing live turns.
The moment a run outlives the process that spawned it, "is pid 4821 alive?"
stops being a useful question: pids are recycled, and on a busy machine the
number that named an agent an hour ago may name a shell, a test runner, or
nothing at all.

Reattaching to a stranger is worse than declaring the run dead. A wrong
"orphaned" costs one re-run; a wrong "still mine" means the control plane
adopts, reports on, and eventually signals a process it does not own.

So a pid is recorded together with a fingerprint the OS will not repeat: the
process start time, which the kernel assigns and which no later process with the
same pid can share. `identify` reads it, `liveness` re-reads it and compares.

`ps` rather than `/proc`, because this control plane runs on macOS as well as
Linux and `/proc` does not exist there. `psutil` would be the obvious answer and
is not a dependency; adding one for two fields is not worth it while `ps -o
lstart=` answers on both platforms.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from loregarden.db.session import engine
from loregarden.models.domain import AgentRun, ProcessState
from sqlmodel import Session

logger = logging.getLogger(__name__)

#: Long enough that a loaded machine still answers, short enough that a wedged
#: `ps` cannot stall a boot sequence that is deciding what to reattach to.
_PS_TIMEOUT_SECONDS = 5


@dataclass(frozen=True)
class IdentityReading:
    """One `ps` reading of a pid. `stamp` is set only when `state` is ALIVE."""

    state: ProcessState
    stamp: str = ""


def identify(pid: int) -> IdentityReading:
    """A fingerprint for `pid` that a later process reusing it cannot match.

    The process start time as the kernel reports it. GONE when `ps` answered
    that nothing holds the pid; UNKNOWN when it could not answer at all — a
    timeout, a `ps` that would not run. The two used to be one `None`, so a slow
    `ps` read as a dead process (lg-run-durability-862).

    A zombie is GONE too. It has exited and only waits for its parent to reap
    it, but it still holds its pid and `ps` still prints its start time, so it
    read as a live agent forever. A tmux pane's parent is the tmux server, and
    tmux 3.4 on Linux sometimes leaves a pane unreaped once its server has no
    sessions left: 2/100 instant-exit panes were still zombies 2s later.
    """
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "stat=", "-o", "lstart="],
            capture_output=True,
            text=True,
            timeout=_PS_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        logger.warning("Could not read process identity for pid %s", pid, exc_info=True)
        return IdentityReading(ProcessState.UNKNOWN)
    printed = result.stdout.strip()
    if result.returncode != 0 and not printed:
        # `ps -p` exits non-zero with nothing printed when no process matches.
        return IdentityReading(ProcessState.GONE)
    # `stat` is one token (`Ss`, `Z+`); `lstart` is the rest, spaces and all.
    stat, _, stamp = printed.partition(" ")
    stamp = stamp.strip()
    if result.returncode != 0 or not stamp:
        logger.warning(
            "ps gave no usable identity for pid %s (exit %s): %s",
            pid,
            result.returncode,
            (result.stderr or printed).strip()[:200],
        )
        return IdentityReading(ProcessState.UNKNOWN)
    if stat.startswith("Z"):
        return IdentityReading(ProcessState.GONE)
    return IdentityReading(ProcessState.ALIVE, stamp)


def session_of(pid: int) -> int | None:
    """The session id of `pid`, or None when the OS could not prove one.

    Sibling of `identify`, not a change to it. Register uses this to refuse a
    child that still shares the agent's or the server's session — a restart
    must not take the child with the CLI. A `ps` probe with the same 5s timeout
    as `identify` proves the pid is still listable; `os.getsid` is the sid
    itself (`ps -o sid=` is not a keyword on macOS, and `sess=` is not POSIX
    sid). An unanswered `ps` cannot prove detach, so the caller refuses rather
    than guessing.
    """
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "pid="],
            capture_output=True,
            text=True,
            timeout=_PS_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        logger.warning("Could not prove pid %s is listable for a session id", pid, exc_info=True)
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        return os.getsid(pid)
    except OSError:
        logger.warning("Could not read session id for pid %s", pid, exc_info=True)
        return None


def liveness(pid: int | None, identity: str | None) -> ProcessState:
    """Whether `pid` is alive *and* still the process `identity` came from.

    ALIVE only on a match. A pid nothing holds, or one a stranger holds, is GONE:
    either way this run's process is not there. Without a recorded identity the
    answer is GONE rather than a bare liveness check — a run from before
    identity was recorded is one whose pid cannot be trusted, and treating it as
    alive is the adoption mistake this module exists to prevent.

    UNKNOWN when `ps` could not answer, and every caller decides for itself what
    an unanswered question means — none may read it as GONE.
    """
    if pid is None or not identity:
        return ProcessState.GONE
    reading = identify(pid)
    if reading.state is not ProcessState.ALIVE:
        return reading.state
    return ProcessState.ALIVE if reading.stamp == identity else ProcessState.GONE


def record_process_identity(run_id: str, pid: int) -> None:
    """Store the pid and its fingerprint on the run, in one write.

    Its own short-lived session: the session driving the run may sit in a
    transaction old enough that a reader on another connection — which is
    exactly who reattaches — would not see this.

    Best-effort. A run whose identity could not be recorded still runs; it is
    simply one that a later process will decline to adopt, which is the safe
    direction.
    """
    identity = identify(pid).stamp
    try:
        with Session(engine) as session:
            run = session.get(AgentRun, run_id)
            if run is None:
                return
            run.agent_pid = pid
            run.agent_pid_identity = identity
            session.add(run)
            session.commit()
    except Exception:  # noqa: BLE001 — never fail a run over its own bookkeeping
        logger.warning("Could not record process identity for run %s", run_id, exc_info=True)


# ---------------------------------------------------------------------------
# The durable claim: a pid, its birth, and the boot both were read under.
#
# `liveness` above answers "is the process I recorded still running?", and that
# question does not survive a reboot — nothing it guards does. A worktree's
# claim is different: it sits on a row in the database and outlives the process,
# the server and the machine, so it carries a third component scoped to the
# boot.
#
# THE RATIONALE, RESTATED FROM OUR OWN MECHANISM. `identify` records
# `ps -o lstart=`, an absolute wall-clock date at one-second granularity, chosen
# because this control plane runs on macOS where `/proc` does not exist. So the
# post-reboot pid+birth collision that "birth is monotonic since boot" would
# describe does NOT arise by that mechanism here. What a boot-scoped component
# defeats in THIS tree is a clock step — NTP, suspend/resume, a VM restore —
# making two processes' `lstart` strings agree, plus a same-second pid reuse.
# Writing the other sentence into this module would ship a comment false about
# its own function, which is the failure `lg-machine-resource-854` records.
# ---------------------------------------------------------------------------

#: Where linux publishes a per-boot uuid. A module attribute so a test can
#: point it somewhere else; the read still goes through `boot_identity`.
_LINUX_BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")

#: `sysctl -n kern.boottime` answers
#: `{ sec = 1785351732, usec = 726777 } Wed Jul 29 15:02:12 2026`. Measured on
#: this box 2026-10-16: the `usec` field and the trailing formatted date BOTH
#: move when the clock is stepped, so an identity built from either changes
#: without a reboot — and a boot identity that moves reads every live agent's
#: tree as claimed by a dead boot. Only `sec` decides.
_DARWIN_BOOT_SEC_RE = re.compile(r"\bsec\s*=\s*(\d+)")

_SYSCTL_TIMEOUT_SECONDS = 5


class BootIdentityUnavailable(RuntimeError):
    """The boot identity could not be read, and there is no answer to give.

    The single most important thing about this exception is that it exists at
    all. An unset column reads back as `""`, so a function that defaulted to
    `""` would make a stamped claim and an unstamped row compare EQUAL — every
    pre-migration checkout would look like it belonged to this boot, which is
    the fail-closed direction turned inside out. There is no default, no
    sentinel and no empty string.

    Caught at the decision site in `worktree_lifecycle` and nowhere else:
    swallowing it here means picking a value to return, and every value
    available recreates the bug the raise prevents.
    """


def boot_identity() -> str:
    """An identity for the boot this process is running under.

    `/proc/sys/kernel/random/boot_id` on linux; the `sec` field of
    `sysctl -n kern.boottime` on darwin. Raises
    :class:`BootIdentityUnavailable` for an empty, missing or unparseable
    reading, for a read that fails for any reason, and on any other platform —
    falling off the end of two branches is how a function annotated `-> str`
    acquires the `""` this one must never return.

    The one place the read happens, so it is the one seam tests stub.
    """
    if sys.platform.startswith("linux"):
        return _linux_boot_id()
    if sys.platform == "darwin":
        return _darwin_boot_sec()
    raise BootIdentityUnavailable(
        f"No boot identity is defined for platform {sys.platform!r}; a worktree's claim "
        "cannot be proved on this machine"
    )


def _linux_boot_id() -> str:
    try:
        raw = _LINUX_BOOT_ID_PATH.read_text()
    except OSError as exc:
        # Every OSError, not FileNotFoundError: an EACCES on a hardened
        # container's /proc, or an EIO off a failing read, would otherwise
        # escape as itself and end the sweep that called us.
        raise BootIdentityUnavailable(f"Could not read {_LINUX_BOOT_ID_PATH}: {exc}") from exc
    boot_id = raw.strip()
    if not boot_id:
        raise BootIdentityUnavailable(f"{_LINUX_BOOT_ID_PATH} is empty")
    return boot_id


def _darwin_boot_sec() -> str:
    try:
        result = subprocess.run(
            ["sysctl", "-n", "kern.boottime"],
            capture_output=True,
            text=True,
            timeout=_SYSCTL_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BootIdentityUnavailable(f"sysctl kern.boottime did not run: {exc}") from exc
    found = _DARWIN_BOOT_SEC_RE.search(result.stdout or "")
    if not found:
        raise BootIdentityUnavailable(
            f"sysctl kern.boottime gave no sec field: {(result.stdout or '').strip()[:120]!r}"
        )
    return found.group(1)


@dataclass(frozen=True)
class ClaimIdentity:
    """Who holds a worktree, durably enough to survive the holder's death.

    Frozen because it travels to a decision about deleting a directory: a claim
    an intermediate layer can edit after the fact is a claim that proves
    nothing.
    """

    pid: int
    birth: str
    boot_id: str

    @classmethod
    def current(cls) -> ClaimIdentity:
        """This process, this start time, this boot.

        Raises :class:`BootIdentityUnavailable` on a box that cannot say which
        boot it is on, for the reason that exception's docstring gives.
        """
        return cls(pid=os.getpid(), birth=identify(os.getpid()).stamp, boot_id=boot_identity())


def claim_fields(claim: ClaimIdentity) -> dict[str, str]:
    """The claim as the three TEXT columns that carry it."""
    return {
        "claim_pid": str(claim.pid),
        "claim_birth": claim.birth,
        "claim_boot_id": claim.boot_id,
    }


def parse_claim(*, claim_pid: str, claim_birth: str, claim_boot_id: str) -> ClaimIdentity | None:
    """The claim a row carries, or `None` when it carries none anybody can read.

    `None` means two different things to the caller and that is deliberate:
    "nobody claims this" (three empty columns, which is every row written
    before the migration) and "this row is not readable" are both answered
    here, and the DECISION site tells them apart by looking at whether the
    columns were empty. What this function must never do is raise: it is read
    inside a sweep, `int("abc")` is a `ValueError`, and neither of the decision
    site's two handlers catches one — so the first garbled row would end the
    pass.

    Two of the three is not a claim either. A half-written row must not be
    treated as one, and must not be treated as this boot's.
    """
    pid_text = claim_pid.strip()
    if not (pid_text and claim_birth and claim_boot_id):
        return None
    if not pid_text.isdigit():
        return None
    pid = int(pid_text)
    if pid <= 0:
        return None
    return ClaimIdentity(pid=pid, birth=claim_birth, boot_id=claim_boot_id)


def claim_is_live(claim: ClaimIdentity) -> ProcessState:
    """Whether the process that claimed a worktree is still there.

    THE COMPARISON RULE, AND ITS DIRECTION. A boot MISMATCH alone is never "the
    claimant is gone": it defers to `liveness(pid, birth)`, which is still the
    pair it always was. A clock step or a restored VM can move the boot stamp
    under a process that never died, and reading that as "gone" would hand a
    live agent's checkout to the reaper — the single worst thing this gate
    could do. So the boot component may only ever be more conservative than
    `liveness`, never less.

    So the boot stamp is READ — an unreadable one raises
    :class:`BootIdentityUnavailable`, which the decision site converts into a
    refusal — and is never a discriminator. Deferring to `liveness` in both
    directions is the comparison rule, not an omission of one: there is no
    answer a boot mismatch could give that is both more conservative than
    `liveness` and not already covered by it. What the third column buys is
    the raise above and a durable record of which boot the claim was taken
    under, for the reader of a tree that has been sitting there for a month.
    """
    boot_identity()
    return liveness(claim.pid, claim.birth)
