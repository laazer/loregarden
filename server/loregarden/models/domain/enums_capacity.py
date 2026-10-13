"""Capacity-ledger vocabularies (host and Docker) — split from enums.py for the size gate."""

from enum import StrEnum


class CapacityResource(StrEnum):
    """One dimension a claim must fit in. A waiter is short of one or more."""

    CPUS = "cpus"
    MEMORY_MB = "memory_mb"
    #: A lease-count slot. Every top-level lease books one.
    SLOTS = "slots"


class DockerFootprint(StrEnum):
    """What a docker claim costs, as a named size rather than a bare number.

    A closed vocabulary the control plane owns, so it is an enum: a caller says
    ``stack`` and the pricing table in ``services.docker_capacity`` turns that
    into cpus and memory. ``CUSTOM`` is the escape hatch for a caller that knows
    its own numbers; ``NONE`` is what a workflow stage carries when it never
    touches docker, and is spelled as a member rather than as ``None`` so the
    template JSON has a value to round-trip.
    """

    NONE = "none"
    LIGHT = "light"
    SERVICE = "service"
    STACK = "stack"
    HEAVY = "heavy"
    CUSTOM = "custom"


class CapacityPool(StrEnum):
    """Which pool a lease claims from. The pools nest: docker runs on the host.

    On macOS the Docker daemon lives in a VM with its own cpu and memory limit,
    smaller than the machine's, so the two need separate ceilings. Containers
    still burn the host's cores, so a docker claim is charged to *both* pools,
    and a host claim (a test suite, a build) to the host alone. The lease
    records the pool it asked for; `CHARGED_POOLS` in `docker_ledger` says what
    that costs.
    """

    DOCKER = "docker"
    HOST = "host"


class DockerLeaseStatus(StrEnum):
    """Where a claim on docker capacity stands.

    One lifecycle, not two regimes — which is why waiters and holders share a
    table where ``queued_runs`` needed ``entry_kind`` to tell its two apart. A
    waiter becomes a holder in place; splitting them would put a delete and an
    insert either side of every grant, which is exactly where a double-booking
    would hide.
    """

    #: In line. Counted against nothing; holds a `position`.
    WAITING = "waiting"
    #: Granted and counted against the ceiling.
    HELD = "held"
    #: Finished, by release or by the reaper. `end_reason` says which.
    RELEASED = "released"
    #: Expired, but docker confirms its containers are still running. Still
    #: counted — the machine really is busy — and surfaced loudly, because this
    #: ledger never stops anything itself.
    ORPHANED = "orphaned"


class DockerHolderKind(StrEnum):
    """Who a lease belongs to, which decides how its liveness is judged.

    An `AGENT_RUN` or `ORCHESTRATION` lease inherits the heartbeat its run
    already renews, so it needs no second timer of its own. An `AD_HOC` lease
    has only its TTL and, when the caller supplied one, a pid.
    """

    AGENT_RUN = "agent_run"
    ORCHESTRATION = "orchestration"
    AD_HOC = "ad_hoc"


class DockerLeaseEndReason(StrEnum):
    """Why a lease stopped counting against the ceiling.

    Recorded because "it was reclaimed" and "the holder gave it back" look
    identical in the row and mean opposite things to whoever is reading the
    board.
    """

    #: The holder released it.
    RELEASED = "released"
    #: Its run finished; the completion path handed the lease back.
    RUN_COMPLETED = "run_completed"
    #: TTL expired and the lease named nothing docker could be asked about.
    TTL_EXPIRED = "ttl_expired"
    #: The holder's recorded pid is gone. Decisive without asking docker.
    PID_GONE = "pid_gone"
    #: TTL expired and docker confirmed none of its containers are running.
    CONTAINERS_GONE = "containers_gone"
    #: A waiter that stopped polling and was dropped from the line.
    ABANDONED = "abandoned"
    #: Taken back by an operator through the force-release tool.
    FORCE_RELEASED = "force_released"
    #: Its parent lease ended, and a child cannot outlive the grant it draws on.
    PARENT_RELEASED = "parent_released"
    #: The shell could not find or execute the held command (exit 126/127), so
    #: its hold time measures nothing and must not teach the wait estimator.
    COMMAND_NOT_RUN = "command_not_run"


class DockerProbeOutcome(StrEnum):
    """Whether a docker query answered, and if not, why not.

    The reason this vocabulary exists at all: ``docker ps`` with no match exits
    0 with empty stdout, and ``docker ps`` against a dead daemon exits 1 with
    empty stdout. Collapsing "nothing is running" into "I could not ask" is what
    would let the reaper free capacity a live stack is using.
    """

    OK = "ok"
    DAEMON_UNREACHABLE = "daemon_unreachable"
    BINARY_MISSING = "binary_missing"
    TIMED_OUT = "timed_out"
    MALFORMED_OUTPUT = "malformed_output"
    #: The caller asked for a verb the read-only allowlist refuses.
    REFUSED_VERB = "refused_verb"


class DockerLivenessState(StrEnum):
    """What a probe found out about a lease's containers.

    ``UNKNOWN`` is never treated as ``GONE``. That is the whole point of having
    three members instead of a bool.
    """

    ALIVE = "alive"
    GONE = "gone"
    UNKNOWN = "unknown"


class DockerCeilingSource(StrEnum):
    """Where the capacity ceiling in force came from.

    Carried into every payload because a number derived from a probe that failed
    an hour ago is still usable and is not the same claim as a fresh one.
    """

    PROBE = "probe"
    STALE_PROBE = "stale_probe"
    CONFIG_OVERRIDE = "config_override"
    #: No probe has ever succeeded and no override is set. Admission refuses
    #: rather than treating an unmeasured machine as unbounded.
    UNKNOWN = "unknown"


class DockerGrantState(StrEnum):
    """What a reservation request got.

    ``REJECTED`` is not ``QUEUED``: a claim larger than the whole ceiling, or one
    made while the ceiling is unknown, can never be granted, and parking it in
    line would block the head of the queue forever.
    """

    GRANTED = "granted"
    QUEUED = "queued"
    REJECTED = "rejected"


class DockerWaitBasis(StrEnum):
    """What kind of number an estimated wait is.

    A prediction and an upper bound are both useful and they are not the same
    claim, so the payload says which. A caller told "about 6 minutes, from
    history" can sleep; one told "at most 15 minutes, because that is the TTL"
    should check back sooner, since the real release is almost certainly earlier.
    Collapsing the two would make the second look like the first.
    """

    #: Median of what leases of this footprint have actually cost here.
    HISTORY = "history"
    #: Nothing has been measured yet, so the holder's remaining TTL stands in.
    #: An upper bound, not a forecast.
    TTL_BOUND = "ttl_bound"
    #: Neither is available. `seconds` is None and stays None.
    UNKNOWN = "unknown"
