"""A land that would leave the target branch failing is refused (lg-milestone-that-1238).

AC5, AC13-AC15, AC20 and AC21. Every other criterion of 1238 is already pinned
by tests that landed without their implementations — AC2/AC17/AC18 by
`test_external_handoff_settlement.py`, `test_orchestrator_decisions_visible.py`
and `test_resume_reap_superset.py`, AC3 by `test_worktree_per_ticket.py`'s
lg-milestone-that-1200 block (count, resolved *value*, and the memo's own
laziness), AC19 by `client/src/utils/__tests__/ticketHistory.test.ts`. None of
those files is edited: this is the half of 1238 that has no test at all.

The design these tests pin, so the implementer is not guessing at names:

- `git_merge_noco.merge_tips` gains ONE optional keyword,
  `verify: Callable[[str], VerifyOutcome] | None = None`, called with the
  candidate merge sha BETWEEN its `commit-tree` (`git_merge_noco.py:360`) and
  its compare-and-swap `update-ref` (`:367`). `merge_without_checkout`
  forwards it; `advance_tips` and `squash_onto` do not take it. Omitted,
  `merge_tips` behaves as it does today.
- `services/land_verification.py` (new) owns the verifier and every line of
  its logic. It exposes `VerifyOutcome` and two seams these tests drive:
  `land_verifier(session, ticket, workspace)` (what `land_ticket` wires in,
  in no more than three lines) and `run_verify_command(command, cwd, *,
  footprint)` (one command, under a capacity lease, bounded by a timeout).
- `VerifyOutcome.outcome` is the EXISTING `GateOutcome`, not a second
  vocabulary: `PASSED`, `FAILED`, `UNAVAILABLE` for could-not-run, `DISABLED`
  for a workspace that configured no commands. That is the same reasoning
  AC5 gives for importing `gate_runner.GATE_EX_UNAVAILABLE` rather than
  spelling 69 again — "ran and failed" and "never ran" are the same answer
  everywhere — and it needs no schema change (AC21), because `GateOutcome`
  is an enum over a payload, not a column.

Why the refusal is gated inside `merge_tips` and not after it: "before
stamping `landed_sha` and state" then holds **by construction**. The ref never
moves, so there is no window in which a published branch holds the red tree
and no ordering discipline for a later edit to break. `test_verify_runs_after_
the_commit_tree_and_before_the_update_ref` is the assertion that the seam is
in that gap and not around the whole call.

AC13/AC14's one unstated collision, resolved here rather than left to be
discovered in an implement round. `record_blocking_issue`
(`artifact_service.py:1055`) replaces the WHOLE message with a pointer —
"Stage 'done' hit a blocking issue — see the Timeline tab for details." — once
it passes `BLOCKING_ISSUE_INLINE_LIMIT`, which is 200 characters. AC13 forbids
the refusal's first line being a pointer, and AC14 asks for the command, its
exit code and up to 20 lines of output "below that line". Twenty lines of
pytest output do not fit in 200 characters, so taken literally the two
criteria cancel: honour AC14 and AC13's line is replaced by the pointer, which
names neither branch nor target nor a failing suite. AC14's own words decide
it — "only what fits a block message" — so the inline block carries the ≤140
character first line, the command and its exit code, and as much of the output
tail as the limit leaves; the FULL output is the `kind=log` artifact AC14
requires, which is where the 20 lines an operator reads actually live. Pinned
by `test_the_block_stays_inline_and_is_never_replaced_by_a_pointer`.
"""

from __future__ import annotations

import ast
import inspect
import json
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest
from loregarden.agents.registry import REPAIR_AGENT_ID
from loregarden.models.domain import (
    Artifact,
    ArtifactKind,
    DockerFootprint,
    EventType,
    GateOutcome,
    StageStatus,
    Ticket,
    TicketState,
    WorkItemType,
    Workspace,
)
from loregarden.services.artifact_records import is_system_record, run_log_artifact_title
from loregarden.services.artifact_service import BLOCKING_ISSUE_INLINE_LIMIT
from loregarden.services.gate_runner import GATE_EX_UNAVAILABLE
from loregarden.services.git_merge_noco import (
    advance_tips,
    merge_tips,
    merge_without_checkout,
    read_branch_tips,
    squash_onto,
)
from loregarden.services.land_ticket import LandSkip, land_ticket
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_profile import GitAutomationConfig
from loregarden.services.target_branch import resolve_target_branch
from sqlmodel import Session, select
from tests.worktree_helpers import (
    at_terminal_stage,
    blocking_text,
    commit_on,
    git,
    make_repo,
)

TARGET = "integration/verify"
BRANCH = "feature/verify"

#: A command that fails the way a broken suite does: a non-zero status that is
#: not `GATE_EX_UNAVAILABLE`, with many lines on stdout.
FAILING_SUITE = (
    "python3 -c "
    '\'import sys; print(chr(10).join(f"FAILED tests/test_x.py::case_{i}" for i in range(40))); '
    "sys.exit(1)'"
)
PASSING_SUITE = "python3 -c 'print(\"42 passed\")'"
#: `sysexits.h` EX_UNAVAILABLE: the gate convention for "this never ran".
NEVER_RAN = f"python3 -c 'import sys; sys.exit({GATE_EX_UNAVAILABLE})'"


# --- fixtures ---------------------------------------------------------------


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    return make_repo(tmp_path)


@pytest.fixture(name="workspace")
def workspace_fixture(session, repo):
    ws = Workspace(slug="land-verify", name="land-verify", repo_path=str(repo))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


@pytest.fixture(name="milestone")
def milestone_fixture(session, workspace):
    ms = Ticket(
        external_id="lg-ms-1238",
        workspace_id=workspace.id,
        title="Milestone",
        work_item_type=WorkItemType.MILESTONE,
    )
    session.add(ms)
    session.commit()
    session.refresh(ms)
    return ms


@pytest.fixture(name="diverged")
def diverged_fixture(repo):
    """`TARGET` and `BRANCH`, each one commit past their shared base, no conflict.

    A real merge is therefore required: `merge_tips` reaches its `commit-tree`
    and its `update-ref`, which is the gap the verify seam lives in.
    """
    git(repo, "branch", TARGET, "main")
    git(repo, "branch", BRANCH, "main")
    commit_on(Path(repo), TARGET, "target.txt", "target side\n")
    commit_on(Path(repo), BRANCH, "branch.txt", "branch side\n")
    return repo


def _sha(repo, ref="HEAD"):
    return git(repo, "rev-parse", ref).stdout.strip()


def _tree_of(repo, ref):
    return git(repo, "rev-parse", f"{ref}^{{tree}}").stdout.strip()


def _merge(repo, **kwargs):
    tips = read_branch_tips(Path(repo), TARGET, BRANCH)
    return merge_tips(Path(repo), tips, target=TARGET, source=BRANCH, subject="Land it", **kwargs)


def _verifier(outcome):
    """A verifier that records the sha it was handed and answers `outcome`."""
    seen: list[str] = []

    def verify(sha: str):
        seen.append(sha)
        return outcome

    return seen, verify


def _outcome(ok: bool, gate_outcome: GateOutcome, **fields):
    """A `VerifyOutcome`, built through the real class so its shape is pinned too."""
    from loregarden.services.land_verification import VerifyOutcome

    return VerifyOutcome(ok=ok, outcome=gate_outcome, **fields)


def _ticket_with_work(session, workspace, repo, milestone, external_id, **fields):
    ticket = Ticket(
        external_id=external_id,
        workspace_id=workspace.id,
        title=f"Ticket {external_id}",
        branch=f"loregarden/{external_id}",
        parent_ticket_id=milestone.id,
        state=TicketState.IN_PROGRESS,
        **fields,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    target = resolve_target_branch(session, ticket, workspace, repo_root=Path(repo))
    git(repo, "branch", ticket.branch, target)
    tip = commit_on(Path(repo), ticket.branch, f"{external_id}.txt", "work\n")
    return ticket, target, tip


def _configure(session, ticket, **override):
    """Set this ticket's git-automation override, which `resolve_git_automation` layers.

    `parse_override` keeps only keys in `GitAutomationConfig.model_fields` and
    silently drops the rest, so before AC20's fields exist every configuration
    in this file is a no-op — which would make the refusal tests pass by
    verifying nothing. The guard turns that into a loud failure here rather
    than a green test over an unconfigured land.
    """
    from loregarden.services.git_automation_config import parse_override

    ticket.git_automation_json = json.dumps(override, sort_keys=True)
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    kept = parse_override(ticket.git_automation_json)
    assert set(kept) == set(override), (
        f"the override was dropped: {sorted(set(override) - set(kept))} are not "
        f"fields of GitAutomationConfig, so nothing here is configured"
    )


def _finish(session, ticket):
    """What the orchestrator does when the last stage passes: advance with no route."""
    at_terminal_stage(session, ticket)
    OrchestrationService(session).advance_stage(ticket)
    session.refresh(ticket)


def _landed_events(session, ticket):
    from loregarden.models.domain import DomainEvent

    return session.exec(
        select(DomainEvent).where(
            DomainEvent.ticket_id == ticket.id, DomainEvent.type == EventType.TICKET_LANDED
        )
    ).all()


def _log_artifacts(session, ticket):
    return session.exec(
        select(Artifact).where(Artifact.ticket_id == ticket.id, Artifact.kind == ArtifactKind.LOG)
    ).all()


def _first_line(text: str) -> str:
    return (text or "").split("\n")[0]


# ---------------------------------------------------------------------------
# AC5, the seam. `merge_tips` separates the commit that would land from the ref
# move; the verification goes in that gap, so a refusal cannot leave a
# published ref holding the red tree even if every later edit gets the ordering
# wrong. These tests use a stub verifier: the gap is the criterion here, and
# what runs in it is section B's.
# ---------------------------------------------------------------------------


def test_a_refused_verification_leaves_the_target_ref_exactly_where_it_was(diverged):
    """AC5. The ref never moves, so `landed_sha` has nothing to be stamped from."""
    before = _sha(diverged, TARGET)
    branch_before = _sha(diverged, BRANCH)
    seen, verify = _verifier(
        _outcome(False, GateOutcome.FAILED, detail="the tests its merge reaches failed")
    )

    outcome = _merge(diverged, verify=verify)

    assert not outcome.ok, "a non-ok verdict must refuse the merge"
    assert "the tests its merge reaches failed" in outcome.detail, outcome.detail
    assert _sha(diverged, TARGET) == before, "the target ref moved despite the refusal"
    assert _sha(diverged, BRANCH) == branch_before, "the source is not touched either"
    assert seen, "the verifier was never called at all — the seam is not wired"
    assert not outcome.conflicted, "a red suite is not a conflict (AC5)"


def test_the_candidate_sha_handed_to_verify_is_the_merge_commit_that_would_land(diverged):
    """AC5. Verifying anything else — either tip, the merge *tree* — checks a
    tree that is not the one about to be published, which is the false-pass
    channel this whole ticket is about.
    """
    seen, verify = _verifier(_outcome(True, GateOutcome.PASSED))

    outcome = _merge(diverged, verify=verify)

    assert outcome.ok and len(seen) == 1, seen
    candidate = seen[0]
    assert candidate == outcome.sha, "the sha verified must be the sha that landed"
    parents = git(diverged, "rev-list", "--parents", "-n", "1", candidate).stdout.split()[1:]
    assert len(parents) == 2, f"the candidate must be the merge commit, not a tip: {parents}"
    assert _tree_of(diverged, candidate) != _tree_of(diverged, BRANCH), (
        "fixture: the merge result must differ from the branch tip, or verifying "
        "the tip would be indistinguishable from verifying the merge"
    )


def test_verify_runs_after_the_commit_tree_and_before_the_update_ref(diverged):
    """AC5, read from inside the verifier.

    When it is called, the candidate commit already exists as an object (so
    `commit-tree` has run and a worktree can be cut at it) and the target ref
    still points where it did (so `update-ref` has not). A seam placed around
    the whole of `merge_tips` fails the second half; one placed before the
    `commit-tree` fails the first.
    """
    before = _sha(diverged, TARGET)
    observed: dict[str, str] = {}

    def verify(sha: str):
        observed["type"] = git(diverged, "cat-file", "-t", sha).stdout.strip()
        observed["target"] = _sha(diverged, TARGET)
        return _outcome(True, GateOutcome.PASSED)

    outcome = _merge(diverged, verify=verify)

    assert outcome.ok, outcome.detail
    assert observed["type"] == "commit", "the candidate must be a real object to verify"
    assert observed["target"] == before, (
        "the target ref had already moved when the verification ran: a refusal "
        "would then be too late, which is the ordering AC5 makes structural"
    )
    assert _sha(diverged, TARGET) == outcome.sha, "and it moves once the verdict is ok"


def test_a_passing_verification_moves_the_ref_and_reports_the_same_sha(diverged):
    seen, verify = _verifier(_outcome(True, GateOutcome.PASSED))

    outcome = _merge(diverged, verify=verify)

    assert outcome.ok and outcome.sha == _sha(diverged, TARGET)
    assert outcome.detail == "", "an ok land carries no complaint"
    assert seen == [outcome.sha]


def test_merge_tips_with_no_verifier_behaves_exactly_as_it_does_today(diverged, tmp_path):
    """AC5. The keyword is optional and omitting it changes nothing, which is
    what keeps `publish_tree`, `squash_onto` and `advance_tips` untouched.

    Compared against a control: the same merge made in a second clone of the
    same two tips, so "as today" is measured rather than asserted.
    """
    control = make_repo(tmp_path, name="control")
    git(control, "fetch", "-q", str(diverged), f"{TARGET}:{TARGET}", f"{BRANCH}:{BRANCH}")

    plain = _merge(diverged)
    tips = read_branch_tips(Path(control), TARGET, BRANCH)
    expected = merge_tips(Path(control), tips, target=TARGET, source=BRANCH, subject="Land it")

    assert plain.ok == expected.ok is True
    assert _tree_of(diverged, plain.sha) == _tree_of(control, expected.sha)
    assert plain.already_contained == expected.already_contained
    assert plain.conflicted_files == expected.conflicted_files == ()
    assert _sha(diverged, TARGET) == plain.sha, "the ref still moves with no verifier"


def test_a_source_the_target_already_contains_is_never_verified(repo):
    """Nothing is written on that path, so there is no candidate to verify and
    no tree that could be published red. A verifier called here would run a
    multi-minute suite for every idempotent re-land.
    """
    git(repo, "branch", TARGET, "main")
    git(repo, "branch", BRANCH, "main")
    commit_on(Path(repo), TARGET, "ahead.txt", "ahead\n")
    seen, verify = _verifier(_outcome(False, GateOutcome.FAILED, detail="should never be asked"))

    outcome = _merge(repo, verify=verify)

    assert outcome.ok and outcome.already_contained
    assert seen == [], "an already-contained source must not be verified"


def test_a_conflict_is_never_verified(repo):
    """The merge fails before any candidate exists."""
    git(repo, "branch", TARGET, "main")
    git(repo, "branch", BRANCH, "main")
    commit_on(Path(repo), TARGET, "clash.txt", "target\n")
    commit_on(Path(repo), BRANCH, "clash.txt", "branch\n")
    seen, verify = _verifier(_outcome(False, GateOutcome.FAILED))

    outcome = _merge(repo, verify=verify)

    assert not outcome.ok and outcome.conflicted
    assert seen == [], "a conflict has no candidate commit to verify"


def test_only_merge_tips_and_its_wrapper_take_a_verifier():
    """AC5: ONE keyword, in one place. `advance_tips` and `squash_onto` keep
    their signatures, which is how `publish_tree` stays out of this ticket.
    """
    assert "verify" in inspect.signature(merge_tips).parameters
    assert "verify" in inspect.signature(merge_without_checkout).parameters, (
        "land_ticket calls `merge_without_checkout`, so the keyword has to reach it"
    )
    assert "verify" not in inspect.signature(advance_tips).parameters
    assert "verify" not in inspect.signature(squash_onto).parameters


# ---------------------------------------------------------------------------
# AC5/AC20, the verifier. Three verdicts that are not each other: it passed,
# it ran and failed, it never ran. The third is the one that earns its keep —
# a verification that could not run reporting "passed" is the silent pass this
# ticket exists to stop, and it is the same mistake `GateOutcome.UNAVAILABLE`
# was added for.
# ---------------------------------------------------------------------------


def test_a_workspace_with_no_commands_configured_is_not_verified_and_says_so(
    session, workspace, repo, milestone
):
    """AC20. The off state is an empty command list, and it is never silent.

    The land goes through exactly as it does today — that is the whole point of
    there being no boolean — but the outcome records that nothing checked it,
    because "nothing was configured" and "it passed" are two different facts.
    """
    from loregarden.services.land_verification import land_verifier

    ticket, _, _ = _ticket_with_work(session, workspace, repo, milestone, "lg-v-1")
    assert GitAutomationConfig().land_verify_commands == [], "the default is off"

    verify = land_verifier(session, ticket, workspace)
    outcome = verify(_sha(repo, ticket.branch))

    assert outcome.ok, "an unconfigured workspace must land as it does today"
    assert outcome.outcome is GateOutcome.DISABLED, (
        "not PASSED: nothing ran, and recording a pass nothing earned is the "
        "false-pass channel AC20 forbids"
    )
    assert "configur" in outcome.detail.lower(), (
        f"the off state must say so rather than being silent: {outcome.detail!r}"
    )


def test_a_command_that_fails_refuses_the_land_with_its_exit_code_and_output(
    session, workspace, repo, milestone
):
    from loregarden.services.land_verification import land_verifier

    ticket, _, _ = _ticket_with_work(session, workspace, repo, milestone, "lg-v-2")
    _configure(session, ticket, land_verify_commands=[FAILING_SUITE])

    outcome = land_verifier(session, ticket, workspace)(_sha(repo, ticket.branch))

    assert not outcome.ok and outcome.outcome is GateOutcome.FAILED
    assert outcome.exit_code == 1, outcome
    assert outcome.command == FAILING_SUITE, "which command failed"
    assert "FAILED tests/test_x.py::case_0" in outcome.output, outcome.output[:200]
    assert outcome.output.count("\n") >= 39, "the whole output is carried, not a tail"


def test_a_command_whose_exit_status_means_it_never_ran_reports_unavailable(
    session, workspace, repo, milestone
):
    """AC5. `GATE_EX_UNAVAILABLE` is imported, not re-spelled, so "ran and
    failed" and "never ran" are the same number here as in the gates.
    """
    from loregarden.services.land_verification import land_verifier

    ticket, _, _ = _ticket_with_work(session, workspace, repo, milestone, "lg-v-3")
    _configure(session, ticket, land_verify_commands=[NEVER_RAN])

    outcome = land_verifier(session, ticket, workspace)(_sha(repo, ticket.branch))

    assert outcome.outcome is GateOutcome.UNAVAILABLE, outcome
    assert not outcome.ok, "a verification that could not run must not pass the land"


def test_a_command_that_is_not_on_the_path_reports_unavailable_rather_than_failed(
    session, workspace, repo, milestone
):
    """A missing toolchain is a fact about the machine, not about the merge. It
    still refuses — nothing checked the tree — and it must not be handed back
    as a failing suite, which is a claim about the code.
    """
    from loregarden.services.land_verification import land_verifier

    ticket, _, _ = _ticket_with_work(session, workspace, repo, milestone, "lg-v-4")
    _configure(session, ticket, land_verify_commands=["loregarden-no-such-binary --run"])

    outcome = land_verifier(session, ticket, workspace)(_sha(repo, ticket.branch))

    assert outcome.outcome is GateOutcome.UNAVAILABLE and not outcome.ok
    assert outcome.detail, "the OS's own words, or the land is refused for no stated reason"


def test_a_verification_that_times_out_refuses_rather_than_passing(
    session, workspace, repo, milestone
):
    """AC5, risk 4: "A timeout REFUSES; it does not pass."

    The timeout is the mitigation for a multi-minute synchronous operation on
    the orchestration thread, and a mitigation that lets the land through on
    expiry removes the guard precisely when the suite is slowest.
    """
    from loregarden.services import land_verification as module

    ticket, _, _ = _ticket_with_work(session, workspace, repo, milestone, "lg-v-5")
    _configure(session, ticket, land_verify_commands=[PASSING_SUITE])

    real = subprocess.run

    def timing_out(argv, **kwargs):
        if "python3" in str(argv):
            raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs.get("timeout") or 1)
        return real(argv, **kwargs)

    with mock.patch.object(module.subprocess, "run", side_effect=timing_out):
        outcome = module.land_verifier(session, ticket, workspace)(_sha(repo, ticket.branch))

    assert not outcome.ok, "a timeout that passes the land is not a guard"
    assert outcome.outcome is GateOutcome.UNAVAILABLE, (
        "a suite that never finished did not report a failure about the code"
    )
    assert "time" in outcome.detail.lower(), outcome.detail


def test_the_commands_run_in_a_detached_worktree_at_the_candidate_sha(
    session, workspace, repo, milestone
):
    """AC5. The tree verified is the candidate merge's tree and nothing else.

    Explicitly NOT the refreshed ticket worktree: that tree can hold
    uncommitted agent leftovers, and the leftover can be the very
    implementation whose absence this guard exists to catch.
    """
    from loregarden.services.land_verification import land_verifier

    ticket, target, tip = _ticket_with_work(session, workspace, repo, milestone, "lg-v-6")
    # A commit on the target that the ticket branch does not have, so the
    # merge's tree is neither tip's tree.
    sibling = commit_on(Path(repo), target, "sibling.txt", "a sibling landed\n")
    # Leftovers in the shared checkout, which a verification of the right tree
    # cannot see.
    (Path(repo) / "leftover.txt").write_text("uncommitted\n")
    _configure(
        session,
        ticket,
        land_verify_commands=[
            "python3 -c 'import pathlib,sys;"
            'sys.exit(0 if pathlib.Path("sibling.txt").exists() '
            'and pathlib.Path("lg-v-6.txt").exists() '
            'and not pathlib.Path("leftover.txt").exists() else 3)\''
        ],
    )
    candidate = _merge_candidate(repo, target, ticket.branch)

    outcome = land_verifier(session, ticket, workspace)(candidate)

    assert outcome.ok, (
        f"the commands did not see the candidate merge's tree: {outcome.outcome} "
        f"exit={outcome.exit_code} {outcome.output[:200]!r}"
    )
    assert sibling and tip
    assert git(repo, "status", "--porcelain").stdout.strip().endswith("leftover.txt"), (
        "the shared checkout is left exactly as it was"
    )


def test_the_detached_worktree_is_removed_whether_the_commands_pass_or_raise(
    session, workspace, repo, milestone
):
    """AC5: removal in `finally`. A worktree left behind at a refused candidate
    accumulates one per refusal and is a ref nothing prunes.
    """
    from loregarden.services import land_verification as module

    ticket, target, _ = _ticket_with_work(session, workspace, repo, milestone, "lg-v-7")
    candidate = _merge_candidate(repo, target, ticket.branch)
    before = git(repo, "worktree", "list", "--porcelain").stdout.count("worktree ")

    _configure(session, ticket, land_verify_commands=[PASSING_SUITE])
    assert module.land_verifier(session, ticket, workspace)(candidate).ok
    assert _worktree_count(repo) == before, "the passing path left a worktree behind"

    _configure(session, ticket, land_verify_commands=[FAILING_SUITE])
    assert not module.land_verifier(session, ticket, workspace)(candidate).ok
    assert _worktree_count(repo) == before, "the failing path left a worktree behind"

    raising = mock.patch.object(
        module, "run_verify_command", side_effect=RuntimeError("the runner blew up")
    )
    with raising, pytest.raises(RuntimeError):
        module.land_verifier(session, ticket, workspace)(candidate)
    assert _worktree_count(repo) == before, (
        "a raise inside the verification left a worktree behind: removal must be in `finally`"
    )


def test_a_passing_command_set_verifies_the_land(session, workspace, repo, milestone):
    from loregarden.services.land_verification import land_verifier

    ticket, target, _ = _ticket_with_work(session, workspace, repo, milestone, "lg-v-8")
    _configure(session, ticket, land_verify_commands=[PASSING_SUITE, PASSING_SUITE])

    outcome = land_verifier(session, ticket, workspace)(
        _merge_candidate(repo, target, ticket.branch)
    )

    assert outcome.ok and outcome.outcome is GateOutcome.PASSED, outcome


def test_the_configured_footprint_is_what_the_verification_claims(
    session, workspace, repo, milestone
):
    """AC20's second field, which would otherwise be a setting nothing reads.

    The verification is a test suite on the orchestration thread, so it holds
    host capacity like any other suite; the footprint is per workspace because
    the commands are.
    """
    from loregarden.services import land_verification as module

    assert GitAutomationConfig().land_verify_capacity_footprint is DockerFootprint.HEAVY, (
        "the default is the heavy footprint: the configured command is a suite"
    )
    ticket, target, _ = _ticket_with_work(session, workspace, repo, milestone, "lg-v-9")
    _configure(
        session,
        ticket,
        land_verify_commands=[PASSING_SUITE],
        land_verify_capacity_footprint=DockerFootprint.STACK.value,
    )
    claimed: list[DockerFootprint] = []
    real = module.run_verify_command

    def recording(command, cwd, *, footprint):
        claimed.append(footprint)
        return real(command, cwd, footprint=footprint)

    with mock.patch.object(module, "run_verify_command", side_effect=recording):
        outcome = module.land_verifier(session, ticket, workspace)(
            _merge_candidate(repo, target, ticket.branch)
        )

    assert outcome.ok, outcome
    assert claimed == [DockerFootprint.STACK], (
        f"the workspace's footprint must reach the runner, not {claimed}"
    )


def _merge_candidate(repo, target, branch) -> str:
    """The merge commit `merge_tips` would write, made here so the verifier can
    be driven on its own without going through a land."""
    tips = read_branch_tips(Path(repo), target, branch)
    before = _sha(repo, target)
    outcome = merge_tips(Path(repo), tips, target=target, source=branch, subject="candidate")
    assert outcome.ok, outcome.detail
    candidate = outcome.sha
    # Put the ref back: the candidate exists as an object, which is all a
    # detached worktree needs, and the land under test has not happened yet.
    git(repo, "update-ref", f"refs/heads/{target}", before, candidate)
    return candidate


def _worktree_count(repo) -> int:
    return git(repo, "worktree", "list", "--porcelain").stdout.count("worktree ")


# ---------------------------------------------------------------------------
# AC5/AC13/AC14/AC15, the operator's end. A refusal that is correct and
# unreadable is the half of this ticket that reaches a person.
# ---------------------------------------------------------------------------


@pytest.fixture(name="refused")
def refused_fixture(session, workspace, repo, milestone):
    """A ticket whose every stage passed and whose land is refused by a red suite."""
    ticket, target, _ = _ticket_with_work(session, workspace, repo, milestone, "lg-v-30")
    _configure(session, ticket, land_verify_commands=[FAILING_SUITE])
    target_before = _sha(repo, target)

    _finish(session, ticket)

    assert ticket.state == TicketState.BLOCKED, (
        "fixture: the land must actually have been refused, or every test below "
        f"measures an ordinary land — state={ticket.state}, landed={ticket.landed_sha!r}"
    )
    return ticket, target, target_before


def test_a_land_whose_verification_fails_writes_no_landed_sha_and_never_derives_done(
    session, repo, refused
):
    """AC5. `done` is never derived and `landed_sha` is unwritten — both by
    construction, because the ref never moved.
    """
    ticket, target, target_before = refused

    assert ticket.state == TicketState.BLOCKED, "a red target must not be landed on"
    assert ticket.state != TicketState.DONE
    assert ticket.landed_sha == "", f"landed_sha was stamped: {ticket.landed_sha}"
    assert ticket.landed_branch == ""
    assert _sha(repo, target) == target_before, "the target branch moved"
    assert ticket.workflow_stage_status == StageStatus.BLOCKED, (
        "AC15: the attention row must read Blocked, not Rework"
    )


def test_the_refusals_first_line_stands_alone_in_a_hundred_and_forty_characters(
    session, repo, refused
):
    """AC13. `TicketAttention` renders only `firstLine(block)` and truncates
    past `TEASER_CHARS = 140`, so an unbounded first line is cut in the one
    place an operator looks first.
    """
    ticket, target, _ = refused
    line = _first_line(ticket.blocking_issues or "")

    assert line, "the refusal said nothing"
    assert len(line) <= 140, f"{len(line)} characters in the teaser: {line!r}"
    assert ticket.branch in line, f"which ticket branch: {line!r}"
    assert target in line, f"which target branch: {line!r}"
    assert "test" in line.lower(), f"and that it was the tests that failed: {line!r}"
    assert not any(
        pointer in line.lower() for pointer in ("see the", "see above", "for details")
    ), f"AC13: the first line must not be a pointer: {line!r}"
    assert not any(
        char.isdigit() for char in line.replace(ticket.branch, "").replace(target, "")
    ), "AC13: the line must not require parsing a test runner's counts"


def test_the_block_stays_inline_and_is_never_replaced_by_a_pointer(session, refused):
    """AC13 and AC14 together, at the one place they collide.

    `record_blocking_issue` swaps the WHOLE message for "…see the Timeline tab
    for details." past `BLOCKING_ISSUE_INLINE_LIMIT`. A refusal that spends the
    budget on output therefore loses the sentence AC13 requires, so the block
    is built to fit and the output lives in the artifact below.
    """
    ticket, _, _ = refused
    block = ticket.blocking_issues or ""

    assert len(block) <= BLOCKING_ISSUE_INLINE_LIMIT, (
        f"the block is {len(block)} characters and will be replaced by a pointer"
    )
    assert "see the Timeline tab" not in block, block
    assert "hit a blocking issue" not in block, (
        f"the refusal was replaced by a pointer and AC13's sentence is gone: {block!r}"
    )
    excerpt = [line for line in block.split("\n") if "FAILED tests/test_x.py" in line]
    assert len(excerpt) <= 20, (
        f"AC14 caps the inline excerpt at the last 20 lines; this block carries {len(excerpt)}"
    )


def test_the_block_names_the_command_and_its_exit_code(session, refused):
    """AC14. Whose problem it is, without opening anything."""
    ticket, _, _ = refused
    text = blocking_text(session, ticket)

    assert "python3" in text, f"the command that was run: {text[:400]!r}"
    assert "1" in text, "its exit code"


def test_the_full_output_is_an_artifact_of_its_own_that_the_timeline_shows(session, refused):
    """AC14. Thousands of lines do not fit a block message; the reader does.

    The title must be the artifact's own, never `run_log_artifact_title`, which
    `is_system_record` filters out of the timeline feed — a refusal whose
    evidence is filtered out of the feed is a refusal nobody can act on.
    """
    ticket, _, _ = refused
    logs = _log_artifacts(session, ticket)

    assert logs, "the full output was recorded nowhere"
    carrying = [a for a in logs if "FAILED tests/test_x.py::case_39" in (a.content_json or "")]
    assert carrying, f"no log artifact holds the whole output: {[a.title for a in logs]}"
    artifact = carrying[0]
    assert artifact.title, "an artifact with no title of its own"
    assert artifact.title != run_log_artifact_title(""), artifact.title
    assert not is_system_record(kind=artifact.kind, title=artifact.title, run_code=None), (
        f"{artifact.title!r} is filtered out of the timeline feed by is_system_record"
    )
    assert (artifact.content_json or "").count("FAILED tests/test_x.py") == 40, (
        "the artifact is the FULL output, not the same tail the block carries"
    )


def test_a_refused_landing_is_published_as_a_ticket_landed_event_with_ok_false(session, refused):
    """AC15. 16 of 93 live `TicketLanded` events already carry `ok=0`, so this
    is the path operators already read rather than a new one.
    """
    ticket, target, _ = refused
    events = _landed_events(session, ticket)

    assert events, "the refusal was never published"
    payloads = [json.loads(e.payload_json) if e.payload_json else {} for e in events]
    assert payloads[-1].get("ok") is False, payloads[-1]
    assert payloads[-1].get("landed_sha") == ""
    assert payloads[-1].get("target") == target
    assert payloads[-1].get("conflicted_files") == [], "a red suite is not a conflict"


def test_a_refused_landing_does_not_go_to_the_conflict_resolver(session, refused):
    """AC5. It is not a conflict, and that resolver's brief is merging. Arming
    it would spend a resolution attempt on a turn that cannot help and would
    hand an implementer a merge it has no reason to make.
    """
    ticket, _, _ = refused

    assert ticket.next_agent != REPAIR_AGENT_ID, ticket.next_agent


def test_an_unverified_workspace_lands_exactly_as_today_but_the_result_says_so(
    session, workspace, repo, milestone
):
    """AC20. No commands configured: the land happens, and it is not silent
    about having been unchecked.
    """
    ticket, target, tip = _ticket_with_work(session, workspace, repo, milestone, "lg-v-31")
    before = _sha(repo, target)

    result = land_ticket(session, ticket, workspace)

    assert result.ok and result.skipped is None, result
    assert _sha(repo, target) != before, "an unconfigured workspace lands as it does today"
    assert result.landed_sha == _sha(repo, target)
    session.refresh(ticket)
    assert ticket.landed_sha == result.landed_sha
    assert "configur" in result.detail.lower(), (
        f"AC20: the off state must say so on the result: {result.detail!r}"
    )
    assert tip


def test_a_verified_land_that_passes_is_unchanged_in_behaviour(session, workspace, repo, milestone):
    """AC5's last clause. The guard costs a configured workspace a suite, not a
    different landing.
    """
    ticket, target, tip = _ticket_with_work(session, workspace, repo, milestone, "lg-v-32")
    _configure(session, ticket, land_verify_commands=[PASSING_SUITE])

    _finish(session, ticket)

    assert ticket.state == TicketState.DONE, ticket.blocking_issues
    assert ticket.landed_sha == _sha(repo, target)
    assert tip in git(repo, "rev-list", "--parents", "-n", "1", ticket.landed_sha).stdout.split()


def test_reverting_the_land_ticket_wiring_turns_the_refusal_into_a_land(
    session, workspace, repo, milestone
):
    """AC21's non-vacuity, as a control rather than as a claim.

    The wiring is the three lines in `land_ticket` that hand `merge_tips` a
    verifier. With no verifier the same red suite lands, the same ticket
    reaches `done`, and every other test in this file still passes — which is
    exactly the false pass this ticket exists to stop. If this test ever goes
    green with the mutation in place, the guard is decoration.
    """
    from loregarden.services import land_ticket as module

    ticket, target, _ = _ticket_with_work(session, workspace, repo, milestone, "lg-v-33")
    _configure(session, ticket, land_verify_commands=[FAILING_SUITE])
    before = _sha(repo, target)

    with mock.patch.object(module, "land_verifier", return_value=None):
        _finish(session, ticket)

    assert ticket.state == TicketState.DONE, (
        "control: without the wiring the red land must succeed — if it does not, "
        "something other than the guard is refusing and the guard is unmeasured"
    )
    assert _sha(repo, target) != before and ticket.landed_sha != ""


# ---------------------------------------------------------------------------
# AC20/AC21: the configuration, and the switch that deliberately does not exist.
# ---------------------------------------------------------------------------


def test_the_land_verification_settings_are_per_workspace_fields_with_the_right_defaults():
    fields = GitAutomationConfig.model_fields

    assert "land_verify_commands" in fields and "land_verify_capacity_footprint" in fields
    default = GitAutomationConfig()
    assert default.land_verify_commands == []
    assert default.land_verify_capacity_footprint is DockerFootprint.HEAVY
    loaded = GitAutomationConfig.model_validate(
        {"land_verify_commands": ["pytest -q"], "land_verify_capacity_footprint": "stack"}
    )
    assert loaded.land_verify_commands == ["pytest -q"]
    assert loaded.land_verify_capacity_footprint is DockerFootprint.STACK


def test_a_ticket_may_override_the_land_verification_commands(session, workspace, milestone):
    """`OVERRIDABLE` is `GitAutomationConfig.model_fields`, so this follows from
    the fields existing — asserted because it is the mechanism every test above
    configures through, and a field added outside the model would break it
    silently.
    """
    from loregarden.services.git_automation_config import resolve_git_automation

    ticket = Ticket(
        external_id="lg-v-40",
        workspace_id=workspace.id,
        title="Override",
        parent_ticket_id=milestone.id,
    )
    session.add(ticket)
    session.commit()
    _configure(session, ticket, land_verify_commands=["pytest -q tests/"])

    assert resolve_git_automation(workspace, ticket).land_verify_commands == ["pytest -q tests/"]


def test_there_is_no_boolean_that_turns_land_verification_off():
    """AC20. A switch that can be turned off is a per-workspace way to make AC5
    untrue, which is the silent pass AC5 forbids. The off state is an empty
    command list, which says so.
    """
    names = set(GitAutomationConfig.model_fields)

    assert not {
        name
        for name in names
        if "verify" in name and GitAutomationConfig.model_fields[name].annotation is bool
    }, "a boolean that disables the land guard"
    assert "verify_before_land" not in names


def test_each_new_switch_carries_the_comment_its_siblings_carry():
    """AC20. Every switch on `GitAutomationConfig` is documented where it is
    declared, because the resolved config is what an operator reads. A field
    with no `#:` above it is a setting whose meaning lives only in a ticket.
    """
    from loregarden.services import orchestration_profile

    source = Path(inspect.getfile(orchestration_profile)).read_text().splitlines()
    for field in ("land_verify_commands", "land_verify_capacity_footprint"):
        declared = next(i for i, line in enumerate(source) if line.strip().startswith(f"{field}:"))
        above = [line.strip() for line in source[max(0, declared - 8) : declared]]
        assert any(line.startswith("#:") for line in above), (
            f"{field} is declared with no `#:` comment above it"
        )


def test_the_unavailable_exit_status_is_imported_and_not_re_spelled():
    """AC5/AC21. `gate_runner.GATE_EX_UNAVAILABLE` is imported so "never ran"
    is the same number everywhere; `_run_command` is deliberately NOT extracted
    into a shared primitive in this ticket.
    """
    from loregarden.services import land_verification

    assert land_verification.GATE_EX_UNAVAILABLE is GATE_EX_UNAVAILABLE == 69
    assert not hasattr(land_verification, "_run_command"), (
        "AC21: the DRY observation is a filed follow-up, not this ticket's refactor"
    )


def test_land_ticket_keeps_its_logic_in_land_verification(session, workspace, repo, milestone):
    """AC5/risk 6. `land_ticket.py` is 319 lines and the size and organization
    gates run on the whole staged file, so the wiring is three lines and every
    line of verification logic lives in `land_verification.py`.
    """
    from loregarden.services import land_ticket as module

    source = Path(inspect.getfile(module)).read_text()
    assert "land_verifier" in source, "land_ticket is the only wiring"
    # `subprocess` was in this list and is not: it matched land_ticket's own
    # pre-existing `from ...git_subprocess import run_git`, which is the landing
    # refresh's worktree repair and not verification logic. That binding is
    # load-bearing — `test_land_ticket.py::test_a_worktree_that_vanishes_mid_
    # refresh_blocks_rather_than_raising` patches `land_ticket.run_git` to make
    # `read-tree` raise — so the substring cannot be removed without moving a
    # frozen test's patch target. The three below are the verification's own and
    # appear nowhere else.
    for leaked in ("worktree add", "shlex.split", "GATE_EX_UNAVAILABLE"):
        assert leaked not in source, f"{leaked!r} belongs in land_verification.py"
    assert LandSkip and land_ticket


# ---------------------------------------------------------------------------
# lg-milestone-that-1238, test-break: nine mutants the designed suite above
# lets through, and the one first line that real branch names do not fit.
#
# Every test above drives the verifier with ONE command, so the loop that runs
# the configured list is unmeasured; and every one of them configures a command
# that runs, so the two ways a configured command cannot run are unmeasured at
# the land. The mutants, each a plausible implementation rather than a
# contrived one:
#
# (1) **Only the first command runs.** `commands[0]` instead of a loop passes
#     every test above: the failing case configures one command and the passing
#     case configures two that both pass. A workspace whose first command is a
#     fast lint and whose second is the suite then lands every red tree, which
#     is this ticket's own defect with the guard installed.
# (2) **A later pass overwrites an earlier failure.** The loop's accumulator
#     written rather than short-circuited: `[FAILING, PASSING]` lands.
# (3) **A command that cannot be parsed or is empty passes.** `shlex.split("")`
#     is `[]`, which `subprocess.run` turns into IndexError, and an
#     unterminated quote raises ValueError — the two cases
#     `gate_runner._run_command` has a comment block about. Either one caught
#     and absorbed into "nothing to do" is a silent pass; either one left to
#     raise takes down the landing with no block message at all.
# (4) **The ambient git environment aims the commands at another repository.**
#     `GIT_DIR` beats `cwd` (CLAUDE.md, `gate_runner.py:200-205`), and a land
#     can run inside a hook's environment. A configured `pytest` that resolves
#     its scope through git then examines the shared checkout and exits 0: a
#     pass over a tree nobody read, which is precisely the channel AC5 closes.
# (5) **The exit code is reported as a constant.** The block test above asserts
#     `"1" in text`, and `1` appears in any text carrying `lg-ms-1238`. A
#     hardcoded 1, or no code at all, survives it.
# (6) **The inline excerpt is the head of the output.** AC14 says the LAST 20
#     lines. A head slice carries pytest's platform banner instead of the
#     failures, and no test above looks at which end it came from.
# (7) **A verification that could not run is reported to the operator as a
#     failing suite.** The verifier-level test pins UNAVAILABLE on the outcome;
#     nothing follows it to the land, where AC13's sentence claims the tests
#     failed. "The machine is broken" and "your code is broken" route to
#     different people.
# (8) **A megabyte of output deadlocks or is thrown away.** A suite's real
#     output is not 40 lines; `Popen` + `wait()` over a pipe hangs on it, and a
#     tail kept in place of the output leaves AC14's artifact without the
#     evidence it exists to carry.
# (9) **The verification is run unreserved, or leaks its lease.** AC5 requires
#     the capacity lease because this is a multi-minute suite on the
#     orchestration thread; the footprint test above proves only that the
#     configured footprint reaches the runner, which a runner that reserves
#     nothing also satisfies.
#
# And the measurement no fixture above takes: `lg-v-30` is a 15-character
# branch. Measured read-only against `data/loregarden.db`, the longest live
# branch-plus-target pair is **99** characters
# (`loregarden/106-server-reload-kills-in-flight-orchestration-runs` onto
# `integration/lg-workflow-integrity-86`; three live tickets tie at 99, five at
# 98). AC13's sentence has 140. A first line built by naming both in a sentence
# fits `lg-v-30` with 90 characters to spare and overflows on a real ticket, in
# the one place `TicketAttention` looks.
# ---------------------------------------------------------------------------

#: `seq` rather than a python one-liner: the command string itself is spent out
#: of AC13's 200-character inline budget, so a long command would decide
#: whether any output tail fits at all. 30 lines, head `line1`, tail `line30`.
NUMBERED_FAILURE = "sh -c 'seq 1 30 | sed s/^/line/; exit 1'"
#: The longest live branch-plus-target pair, rebuilt: a 52-character external
#: id makes a 63-character branch, under a 25-character milestone slug.
LONG_TICKET_ID = "brk-server-reload-kills-in-flight-orchestration-runs"
LONG_MILESTONE_ID = "lg-workflow-integrity-br"


def _refused_land(session, workspace, repo, milestone, external_id, commands):
    """A ticket whose every stage passed, landing with `commands` configured."""
    ticket, target, _ = _ticket_with_work(session, workspace, repo, milestone, external_id)
    _configure(session, ticket, land_verify_commands=commands)
    before = _sha(repo, target)

    _finish(session, ticket)

    return ticket, target, before


def _inline_output_lines(block: str, candidates: set[str]) -> list[str]:
    """Which of `candidates` the inline block carries, in the order it carries them."""
    return [token for token in block.replace("\n", " ").split() if token in candidates]


def test_every_configured_command_is_run_and_a_later_failure_refuses(
    session, workspace, repo, milestone
):
    """Mutants (1) and (2). The list is a list, and the first failure decides.

    A workspace configures an ordered set — a fast check, then the suite — and
    a runner that stops reading after the first entry verifies the cheap half
    of it. The second case is the same loop written with an accumulator rather
    than a short circuit: a pass after a failure is not a pass.
    """
    from loregarden.services.land_verification import land_verifier

    ticket, target, _ = _ticket_with_work(session, workspace, repo, milestone, "lg-brk-many")
    candidate = _merge_candidate(repo, target, ticket.branch)

    _configure(session, ticket, land_verify_commands=[PASSING_SUITE, FAILING_SUITE])
    second_fails = land_verifier(session, ticket, workspace)(candidate)

    assert not second_fails.ok, (
        "only the first configured command was run: a workspace whose suite is "
        "the second entry lands every red tree with the guard installed"
    )
    assert second_fails.outcome is GateOutcome.FAILED, second_fails
    assert second_fails.command == FAILING_SUITE, (
        f"the refusal names {second_fails.command!r}, which passed"
    )
    assert second_fails.exit_code == 1, second_fails

    _configure(session, ticket, land_verify_commands=[FAILING_SUITE, PASSING_SUITE])
    first_fails = land_verifier(session, ticket, workspace)(candidate)

    assert not first_fails.ok, "a command that passed after the failure rescued the land"
    assert first_fails.outcome is GateOutcome.FAILED, first_fails
    assert first_fails.command == FAILING_SUITE, first_fails


def test_a_blank_or_malformed_command_refuses_rather_than_passing(
    session, workspace, repo, milestone
):
    """Mutant (3). Both halves of it: it must not pass, and it must not raise.

    These are the two cases `gate_runner._run_command` carries a comment block
    about — `shlex.split` yields `[]` for a blank string, which `subprocess.run`
    turns into IndexError, and raises ValueError on an unterminated quote. A
    configured command that cannot be run checked nothing, so the only honest
    verdict is UNAVAILABLE; "it passed" is the silent pass, and an exception out
    of the verifier is a land that blocks with no block message at all.
    """
    from loregarden.services.land_verification import land_verifier

    ticket, target, _ = _ticket_with_work(session, workspace, repo, milestone, "lg-brk-blank")
    candidate = _merge_candidate(repo, target, ticket.branch)

    for command in ("   ", 'sh -c "never closed'):
        _configure(session, ticket, land_verify_commands=[command])

        outcome = land_verifier(session, ticket, workspace)(candidate)

        assert not outcome.ok, (
            f"a command that cannot be run verified the land: {command!r} — "
            "nothing examined the tree, and 'nothing to do' is not a pass"
        )
        assert outcome.outcome is GateOutcome.UNAVAILABLE, (
            f"{command!r} is not a failing suite — a claim about the code — it is a "
            f"command that never ran: {outcome}"
        )
        assert outcome.detail, f"refused with no stated reason: {outcome}"


def test_the_commands_cannot_be_aimed_at_another_repository_by_the_ambient_environment(
    session, workspace, repo, milestone
):
    """Mutant (4), and the most expensive one to find later.

    `GIT_DIR` overrides `cwd` for every git process beneath it, and loregarden
    runs inside hooks that export it (CLAUDE.md, *Pushing from a worktree*).
    A configured command that resolves its own scope through git — which is
    every test selector this repo has, including the
    `select_pytest_targets.py --repo PATH --base REF` AC5 names — then reads
    the shared checkout rather than the candidate's tree and exits 0. The
    verification passes, nothing was examined, and the red tree lands: this
    ticket's defect, with the guard installed and green.
    """
    from loregarden.services import land_verification as module

    ticket, target, _ = _ticket_with_work(session, workspace, repo, milestone, "lg-brk-gitdir")
    commit_on(Path(repo), target, "sibling.txt", "a sibling landed\n")
    candidate = _merge_candidate(repo, target, ticket.branch)
    assert _sha(repo, "HEAD") != candidate, "fixture: the shared checkout is elsewhere"
    _configure(
        session,
        ticket,
        land_verify_commands=[f"sh -c 'test \"$(git rev-parse HEAD)\" = {candidate}'"],
    )

    leaked = {"GIT_DIR": str(Path(repo) / ".git"), "GIT_WORK_TREE": str(repo)}
    with mock.patch.dict("os.environ", leaked):
        outcome = module.land_verifier(session, ticket, workspace)(candidate)

    assert outcome.ok, (
        "the commands ran against the repository the ambient GIT_DIR named, not "
        f"the candidate's tree: {outcome.outcome} exit={outcome.exit_code} "
        f"{outcome.output[:200]!r} — the environment must be scrubbed "
        "(services/git_subprocess.scrubbed_git_env), as the gates already do"
    )


def test_the_verification_leaves_the_shared_checkout_on_its_own_branch(
    session, workspace, repo, milestone
):
    """The detached tree is a tree of its own, not a checkout of the candidate.

    `git checkout --detach <candidate>` in the shared root also makes the
    candidate's files readable and passes the tree assertions above, while
    moving the checkout every other run on this machine is using out from
    under them.
    """
    from loregarden.services.land_verification import land_verifier

    ticket, target, _ = _ticket_with_work(session, workspace, repo, milestone, "lg-brk-shared")
    candidate = _merge_candidate(repo, target, ticket.branch)
    head_before = git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    sha_before = _sha(repo, "HEAD")
    _configure(session, ticket, land_verify_commands=[PASSING_SUITE])

    assert land_verifier(session, ticket, workspace)(candidate).ok

    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == head_before, (
        "the shared checkout was detached onto the candidate: every other run "
        "against this workspace is now on a commit that was never published"
    )
    assert _sha(repo, "HEAD") == sha_before
    assert git(repo, "status", "--porcelain").stdout == "", "the shared index was written"


def test_a_megabyte_of_output_is_neither_a_deadlock_nor_a_tail(session, workspace, repo, milestone):
    """Mutant (8). A real suite's output is not forty lines.

    Two failures live here. A runner built on `Popen` + `wait()` over a pipe
    blocks once the OS buffer fills, which on this path is a multi-minute land
    that never returns and a lease held until its TTL. And an outcome carrying
    only the tail it will inline leaves AC14's artifact — the one place the
    full output is supposed to survive — with the same twenty lines as the
    block.
    """
    from loregarden.services.land_verification import land_verifier

    ticket, target, _ = _ticket_with_work(session, workspace, repo, milestone, "lg-brk-volume")
    candidate = _merge_candidate(repo, target, ticket.branch)
    _configure(
        session,
        ticket,
        land_verify_commands=[
            "python3 -c 'import sys;"
            'sys.stdout.write("FAILED tests/test_x.py::case\\n" * 40000); sys.exit(1)\''
        ],
    )

    outcome = land_verifier(session, ticket, workspace)(candidate)

    assert not outcome.ok and outcome.outcome is GateOutcome.FAILED, outcome
    assert len(outcome.output) >= 100_000, (
        f"the outcome carries {len(outcome.output)} characters of a ~1.2 MB run: the "
        "full output is what AC14's artifact exists to hold, and a tail kept in its "
        "place leaves the operator the same twenty lines twice"
    )


def test_the_verification_holds_host_capacity_and_leaves_no_lease_behind(
    session, workspace, repo, milestone
):
    """Mutant (9). AC5: the commands run "under a capacity lease".

    The footprint test above proves the configured footprint reaches the
    runner, which a runner that reserves nothing satisfies too. This asserts
    the claim itself through one of the three entry points that exist for it —
    `gate_capacity.acquire_gate_capacity` is the sibling this is (gates holding
    a lease for commands at a stage transition), `capacity_run.run_holding` and
    `capacity_children.reserve_child` are the two AC5 names — and that whatever
    it takes is given back: a lease left held by a refusal starves the machine
    for its TTL, and a refused land is the case that happens repeatedly.
    """
    from loregarden.models.domain import DockerLease, DockerLeaseStatus
    from loregarden.services import land_verification as module

    entry_points = {"acquire_gate_capacity", "run_holding", "reserve_child"}
    tree = ast.parse(Path(inspect.getfile(module)).read_text())
    imported = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)  # py-org: allow-isinstance - an ast node
        for alias in node.names
    }
    assert entry_points & imported, (
        "land_verification reserves nothing: a multi-minute suite on the "
        "orchestration thread, outside the ledger every other suite on this "
        f"machine queues in, and AC5 requires one of {sorted(entry_points)}. A "
        "mention in a comment is not a lease: this reads the module's imports."
    )

    ticket, target, _ = _ticket_with_work(session, workspace, repo, milestone, "lg-brk-lease")
    candidate = _merge_candidate(repo, target, ticket.branch)
    _configure(session, ticket, land_verify_commands=[FAILING_SUITE])

    assert not module.land_verifier(session, ticket, workspace)(candidate).ok

    held = session.exec(
        select(DockerLease).where(DockerLease.status == DockerLeaseStatus.HELD)
    ).all()
    assert held == [], (
        f"the refusal left {len(held)} lease(s) held: {[lease.holder_label for lease in held]}"
    )


def test_the_exit_code_the_operator_reads_is_the_commands_own(session, workspace, repo, milestone):
    """Mutant (5). AC14 asks for "its exit code", not for a plausible one.

    `test_the_block_names_the_command_and_its_exit_code` asserts `"1" in text`
    over a ticket carrying `lg-ms-1238`, so a hardcoded 1 — or no code at all —
    passes it. Seven is the code here and appears in neither branch name, so
    the assertion is about the command and not about the fixture.
    """
    ticket, _, before = _refused_land(
        session,
        workspace,
        repo,
        milestone,
        "lg-brk-exit",
        ["python3 -c 'import sys; print(\"boom\"); sys.exit(7)'"],
    )

    assert ticket.state == TicketState.BLOCKED, (
        f"the land was not refused at all: state={ticket.state} landed={ticket.landed_sha!r}"
    )
    text = blocking_text(session, ticket)
    assert "7" in text, f"the code the command actually exited with: {text[:400]!r}"
    assert before, "fixture: the target had a tip before the land"
    for wrong in (" 1\n", "exit 1", "exit code 1", "status 1"):
        assert wrong not in text, (
            f"the block reports {wrong!r} for a command that exited 7: {text[:400]!r}"
        )


def test_the_inline_excerpt_is_the_tail_of_the_output_and_not_its_head(
    session, workspace, repo, milestone
):
    """Mutant (6). AC14: "at most the last 20 lines of its output".

    A suite puts its platform banner, its rootdir and its plugin list first and
    its failures last, so a head slice spends the one budget the operator reads
    on the least informative lines in the run. Thirty numbered lines, so which
    end was kept is observable; the command is deliberately short, because the
    command string is spent out of the same 200 characters.

    The excerpt assertion is a mutation guard rather than a volume requirement:
    if AC13's sentence and the command leave no room, the block carries no
    output line and there is nothing to be the wrong end of. The artifact
    assertion below is unconditional — the full output goes somewhere.
    """
    ticket, _, _ = _refused_land(
        session, workspace, repo, milestone, "lg-brk-tail", [NUMBERED_FAILURE]
    )

    assert ticket.state == TicketState.BLOCKED, ticket.state
    block = ticket.blocking_issues or ""
    lines = {f"line{n}" for n in range(1, 31)}
    inline = _inline_output_lines(block, lines)
    if inline:
        assert "line30" in inline, (
            f"the block carries {inline} — the head of the output. AC14 keeps the "
            "last lines, which is where a suite puts what failed"
        )
        assert "line1" not in inline or len(inline) >= 29, (
            f"the block starts the excerpt at the first line: {inline}"
        )
    full = [a for a in _log_artifacts(session, ticket) if "line30" in (a.content_json or "")]
    assert full, (
        "the full output of a refused land is recorded nowhere: "
        f"{[a.title for a in _log_artifacts(session, ticket)]}"
    )
    recorded = full[0].content_json or ""
    assert all(f"line{n}" in recorded for n in (1, 15, 30)), (
        f"the artifact holds a slice, not the output: {recorded[:200]!r}"
    )


def test_a_verification_that_could_not_run_refuses_the_land_without_blaming_the_tests(
    session, workspace, repo, milestone
):
    """Mutant (7). The third verdict, followed all the way to the operator.

    `GateOutcome.UNAVAILABLE` exists because "the gate could not run" and "the
    gate failed" route to different people: no agent can fix a missing
    toolchain, and no operator can fix a suite by reading that it is red when
    it never started. The verifier-level test pins the verdict; nothing above
    checks that the land says it, and AC13's sentence — the only words
    `TicketAttention` shows — claims the tests failed.
    """
    ticket, target, before = _refused_land(
        session, workspace, repo, milestone, "lg-brk-norun", [NEVER_RAN]
    )

    assert ticket.state == TicketState.BLOCKED, (
        "a verification that could not run passed the land, which is the silent "
        f"pass AC5 forbids: state={ticket.state} landed={ticket.landed_sha!r}"
    )
    assert ticket.landed_sha == "" and _sha(repo, target) == before
    line = _first_line(ticket.blocking_issues or "")
    assert line, "refused and said nothing"
    assert any(
        words in line.lower()
        for words in ("could not", "never ran", "did not run", "unavailable", "unable")
    ), (
        f"the operator is told the tests failed when none of them ran: {line!r} — "
        "a person sent after a red suite that does not exist comes back to the "
        "same block, and the machine that could not run it is still broken"
    )


def test_a_refusal_on_a_real_branch_name_still_fits_the_teaser(session, workspace, repo):
    """AC13 at the length live data actually has, which no fixture above uses.

    `lg-v-30` gives a 15-character branch and a 22-character target, so a first
    line naming both has 103 of its 140 characters left for the sentence.
    Measured read-only against `data/loregarden.db`: the longest live
    branch-plus-target pair is 99 characters, three tickets tie at it and five
    more sit at 98. The same sentence then has 41, and
    `TicketAttention.firstLine` truncates the rest at `TEASER_CHARS = 140` in
    the one place an operator looks first.

    So the sentence has to be built against the budget — eliding, or wording
    that fits — rather than concatenated and hoped for. The ticket must still
    be identifiable, or a teaser that fits says nothing.
    """
    long_milestone = Ticket(
        external_id=LONG_MILESTONE_ID,
        workspace_id=workspace.id,
        title="Milestone with a realistic slug",
        work_item_type=WorkItemType.MILESTONE,
    )
    session.add(long_milestone)
    session.commit()
    session.refresh(long_milestone)

    ticket, target, _ = _refused_land(
        session, workspace, repo, long_milestone, LONG_TICKET_ID, [FAILING_SUITE]
    )

    assert len(ticket.branch) + len(target) == 99, (
        f"fixture: {len(ticket.branch)} + {len(target)} is not the measured live "
        f"worst case ({ticket.branch} -> {target})"
    )
    assert ticket.state == TicketState.BLOCKED, ticket.state
    line = _first_line(ticket.blocking_issues or "")
    assert len(line) <= 140, (
        f"{len(line)} characters: the teaser is cut mid-sentence on a branch length "
        f"three live tickets already have: {line!r}"
    )
    assert LONG_TICKET_ID in line or ticket.external_id in line, (
        f"a teaser that fits and names no ticket: {line!r}"
    )


def test_the_verifier_reaches_land_ticket_without_a_lazy_import():
    """The import this stage ran into, pinned where it is cheap to read.

    Measured in this worktree: giving `land_verification` a module-level
    `from loregarden.services.gate_runner import GATE_EX_UNAVAILABLE` — which
    `test_the_unavailable_exit_status_is_imported_and_not_re_spelled` requires
    — and `land_ticket` a module-level `land_verifier` makes
    `import loregarden.services.orchestration` raise:

        orchestration -> landing -> land_ticket -> land_verification ->
        gate_runner -> gate_capacity -> capacity_run -> docker_reaper ->
        parallel_queue -> queue_repair -> run_interruption -> triage_service ->
        agent_turn_runner -> permission_bridge -> orchestration (partial)

        ImportError: cannot import name 'OrchestrationService' from partially
        initialized module 'loregarden.services.orchestration'

    `gate_capacity` alone closes the same loop, so AC5's capacity lease reaches
    it even without the constant. Nothing in the land path imports either
    module today — `gate_runner`'s only importers are `api/orchestration.py`,
    `gate_recovery` and `gate_observability`, all outside this chain — so the
    cycle is new scope the plan did not name, and it is order-dependent:
    importing `gate_runner` first hides it, which is why it would survive a
    suite that happens to collect in that order.

    Measured again with the import in place: `tests/conftest.py` itself fails
    to load, so the whole server suite reports a collection error and this test
    never runs. Its executable half is therefore the second assertion — the
    dodge is a function-local import, which this repo's Python review rejects
    by name ("no function-local imports to dodge cycles — fix the cycle") and
    which no other test here can see. The first assertion and this docstring
    are what a collection error that names no ticket is read against.
    """
    done = subprocess.run(
        [sys.executable, "-c", "import loregarden.services.orchestration"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert done.returncode == 0, (
        "the orchestration import chain no longer loads with the land guard in "
        f"it:\n{done.stderr[-2000:]}"
    )
    from loregarden.services import land_ticket as module

    tree = ast.parse(Path(inspect.getfile(module)).read_text())
    top_level = {
        alias.asname or alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom)  # py-org: allow-isinstance - an ast node
        for alias in node.names
    }
    nested = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)  # py-org: allow-isinstance - an ast node
        for alias in node.names
    } - top_level
    assert "land_verifier" in top_level, (
        f"land_verifier is imported inside a function ({sorted(nested)}): a local "
        "import hides the cycle above rather than fixing it, and the next module "
        "to need the capacity ledger from the land path meets it again"
    )
