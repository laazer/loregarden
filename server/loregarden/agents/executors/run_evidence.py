"""What a run consumed, touched and read, recorded in one place.

Three writes that always happen together at the end of a dispatch, lifted out of
`CliAgentExecutor` because that class sits exactly on its 1000-line cap — the
same pressure that already moved `prompt_size` and `read_paths` out. Each only
ever needed the session, never the executor.

Keeping them together is not just tidying: `execute` called three recorders in a
row, and a fourth (the reads, lg-workflow-integrity-681) pushed that method over
its statement cap. One call site means the next piece of run evidence is added
here rather than in the middle of dispatch.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from loregarden.agents.cli_adapters import CliAdapter, CliInvocation
from loregarden.agents.executors.read_paths import record_read_paths
from loregarden.agents.run_usage import parse_run_usage, usage_status_for
from loregarden.models.domain import AgentRun
from loregarden.services.git_commit_push_service import (
    paths_committed_since,
    working_tree_paths,
)
from sqlmodel import Session

logger = logging.getLogger(__name__)


def record_changed_paths(
    session: Session, run: AgentRun, repo_root: Path, before: set[str]
) -> None:
    """Store the paths this run made dirty, so its commit can be scoped.

    Only the delta: a path already dirty when the run started belongs to
    whatever else is in the workspace, and attributing it here is exactly how
    unrelated work used to get swept into a ticket's commit.
    """
    after = working_tree_paths(repo_root)
    if after is None:
        # Not the same as "nothing changed", and this column is the record
        # of what a run touched — lg-workflow-integrity-452's gate
        # attribution reads it, and an empty value there means "cannot say".
        # Leaving it empty silently is what made that unanswerable.
        logger.warning(
            "could not read the working tree for run %s in %s; changed paths not recorded",
            run.id,
            repo_root,
        )
        return
    # Dirty paths alone miss everything the agent COMMITTED during its turn:
    # once committed, the file is no longer dirty, the delta is empty, and the
    # run records nothing — indistinguishable from an agent that wrote no
    # code. Reproduced against real git (lg-workflow-integrity-406).
    #
    # Unioned rather than swapped: uncommitted work is real too, and a run
    # can leave both. `paths_committed_since` returns None when git cannot
    # answer, which is not the same as "it committed nothing" — so a failure
    # there degrades to the dirty set rather than silently narrowing it.
    committed = paths_committed_since(repo_root, run.start_head_sha or "")
    touched = sorted((after - before) | (committed or set()))
    # Written even when empty. An early return left the column at its old
    # "[]" default, which said the same thing as never having looked — the
    # very collapse `working_tree_paths` returning None exists to prevent,
    # repeated one level up. NULL now means no record; `[]` means this run
    # looked and touched nothing (lg-workflow-integrity-675).
    run.changed_paths_json = json.dumps(touched)
    run.changed_paths_recorded_at = datetime.now(timezone.utc)
    session.add(run)
    session.commit()


def record_usage(
    session: Session, run: AgentRun, *, stdout: str, invocation: CliInvocation
) -> None:
    """Store what this run consumed, and what it was charged against.

    Two sources, in that order of authority. The CLI's own usage event is
    what the provider billed, so it wins; the invocation's pins are the
    fallback for the model and effort, and are all there is for an adapter
    that reports no usage at all.

    Anything neither source knows is left NULL. A killed run, an adapter
    with no usage surface and a stream that ended before its usage event
    all land here, and every one of them is *unmeasured* — writing a zero
    would put them in a cost average as free work.
    """
    usage = parse_run_usage(stdout, adapter=CliAdapter(invocation.adapter))
    run.input_tokens = usage.input_tokens
    run.output_tokens = usage.output_tokens
    run.cache_read_tokens = usage.cache_read_tokens
    run.cache_write_tokens = usage.cache_write_tokens
    run.model = usage.model or invocation.model or None
    run.effort = usage.effort or invocation.effort or None
    run.usage_status = usage_status_for(usage, adapter=CliAdapter(invocation.adapter))
    session.add(run)
    session.commit()


def record_run_evidence(
    session: Session,
    run: AgentRun,
    *,
    repo_root: Path,
    paths_before: set[str],
    stdout: str,
    invocation: CliInvocation,
) -> None:
    """Everything a finished dispatch has to say about itself.

    Order matters only in that the reads are recorded from the same transcript
    the usage is parsed from, so a run that produced no transcript records
    neither rather than one of the two.
    """
    record_changed_paths(session, run, repo_root, paths_before)
    record_read_paths(session, run, stdout, repo_root)
    record_usage(session, run, stdout=stdout, invocation=invocation)
