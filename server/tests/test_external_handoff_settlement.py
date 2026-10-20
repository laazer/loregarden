"""What an external handoff does when it cannot prepare the tree.

`_checked_out_run_view` stamps the run RUNNING, commits, and *then* renders the
prompt. Two things were wrong with what happened after that, and this module
covers both.

**It raised the wrong exception.** The view resolved the target branch a second
time, one line *below* the `except ValueError` that exists to render a readable
failure, and that call caught nothing. So a `TargetBranchError` /
`IntegrationConflictError` there left `loregarden_begin_external_stage` as a
bare `RuntimeError` with none of lg-milestone-that-917's one-sentence text —
on the one path whose entire purpose is a readable error.

**It left the stage live with no agent behind it.** The run was already stamped
RUNNING and committed at the top of the call, and nothing settled it. The
terminal-handoff route has settled its own since 917; the external path had no
equivalent, so every failed checkout stranded a RUNNING run for
`fail_interrupted_runs` to kill on somebody else's pass.

Settling is per call site, three different answers (lg-milestone-that-974 R7):
a fresh single-agent checkout settles the one run it stamped; a parallel
fan-out settles every member it stamped and returns the path's existing error
shape; a resumed run is **not** settled, because this handoff did not stamp it
and `_resumable_stage_run` exists precisely so that work the harness is holding
is not reaped.
"""

from __future__ import annotations

import ast
import inspect
import logging
import re
import textwrap
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest
from loregarden.models.domain import (
    AgentRun,
    Artifact,
    ExternalStageRunView,
    OrchestratorDecision,
    ParallelAgentSpec,
    RunStatus,
    StageBudgetArtifactKind,
    StageStatus,
    Ticket,
    WorkflowStageDef,
    Workspace,
)
from loregarden.services import external_harness
from loregarden.services import orchestration_profile as orchestration_profile_module
from loregarden.services.external_harness import (
    begin_external_stage,
)
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.run_service import RunService
from loregarden.services.stage_retry_budget import (
    count_stage_dispatches,
    record_stage_dispatch,
)
from loregarden.services.target_branch import target_branch_name
from sqlmodel import Session
from tests.external_handoff_fixtures import (
    FORGED_STAGE_KEY,
    MERGE_OUTPUT,
    PLAN_LANES,
    PLAN_STAGE,
    RESOLUTION_BINDINGS,
    STAGE,
    WIDE_PLAN_LANES,
    _called_name,
    _calls_named,
    _conflict,
    _dispatch_marker_ids,
    _dispatch_markers,
    _function_def,
    _multiline_conflict,
    _one_line_settle_reason,
    _orch,
    _park_on,
    _raising_resolution,
    _reroute_exempt_marker_count,
    _runs,
    milestone_fixture,  # noqa: F401 - a fixture reaches pytest through this namespace
    parallel_ticket_fixture,  # noqa: F401 - same
    repo_fixture,  # noqa: F401 - same
    ticket_fixture,  # noqa: F401 - same
    wide_parallel_ticket_fixture,  # noqa: F401 - same
    workspace_fixture,  # noqa: F401 - same
)
from tests.handoff_lead_helpers import (
    no_target_lead,
    profile_lead,
)
from tests.history_helpers import decision_events, decision_payloads
from tests.worktree_helpers import commit_on, cut_ticket_worktree, git

# --- AC8: the module no longer resolves at all --------------------------------


def test_the_external_harness_resolves_no_target_branch_anywhere():
    """AC8. The unguarded call the ticket is named for is gone.

    It sat one line below the `except ValueError` and caught nothing, so a
    conflict there escaped as a bare `RuntimeError`. Deleting it is the fix, and
    the branch now comes from the `PreparedTree` `render_stage_prompt` returned.

    Asserted on the module, because with the second resolution agreeing with the
    first — which it does whenever nothing is wrong — no behaviour test can see
    the difference. `_workspace_for` and `resolve_workspace_root` stay: both are
    still used elsewhere in the module.
    """
    from loregarden.services import external_harness

    assert not hasattr(external_harness, "resolve_target_branch"), (
        "services/external_harness.py still binds the write-path resolver"
    )
    source = Path(external_harness.__file__).read_text(encoding="utf-8")
    assert "resolve_target_branch" not in source, (
        "services/external_harness.py still names resolve_target_branch"
    )
    assert external_harness._workspace_for is not None
    assert external_harness.resolve_workspace_root is not None


# --- AC11: the failure reaches the harness as a readable ValueError -----------


def test_an_integration_conflict_reaches_the_harness_as_a_readable_valueerror(
    db_session: Session, workspace: Workspace, ticket: Ticket, repo: Path
):
    """AC11. A `ValueError`, carrying `describe_handoff_failure`'s lead sentence.

    The type is the contract: the MCP dispatcher renders a `ValueError` as an
    `isError` tool result the harness can read, and anything else as an
    unclassified tool crash. `IntegrationConflictError` is a `RuntimeError`
    subclass (`TargetBranchError`, target_branch.py:62), so the unguarded call
    this change deletes handed the harness exactly that crash.

    The sentence is the other half. `.toast__message` clamps at four lines and
    an error toast auto-dismisses after 9s, so the first sentence carries the
    four facts an operator acts on — which ticket, what to do, which branch,
    which tree — and git's output follows behind it.
    """
    orch_run = _orch(db_session, ticket)
    target = target_branch_name(db_session, ticket, workspace)
    assert target.startswith("integration/"), target
    _calls, (cli_patch, worktree_patch) = _raising_resolution(_conflict(target))

    with cli_patch, worktree_patch, pytest.raises(ValueError) as excinfo:
        begin_external_stage(db_session, orch_run, stage_key=STAGE)

    assert type(excinfo.value) is ValueError, (
        f"the harness was handed a {type(excinfo.value).__name__}: a non-ValueError "
        "leaves loregarden_begin_external_stage as an unclassified tool crash"
    )
    detail = str(excinfo.value)
    head, _, _ = detail.partition(". ")
    first = head if head == detail else f"{head}."
    assert ticket.external_id in first, first
    assert "resolve the conflict with" in first, first
    assert target in first, first
    assert str(repo) in first, f"the tree the operator has to go and fix is not named: {first}"
    assert "shared.txt" in detail, (
        "git's own words — the only thing that says *what* conflicted — were dropped"
    )


def test_no_non_valueerror_leaves_the_external_checkout_for_a_target_failure(
    db_session: Session, workspace: Workspace, ticket: Ticket, repo: Path
):
    """AC11's last sentence — the defect this ticket is named for.

    The test above patches resolution so it raises on the way *in*, where
    `render_stage_prompt`'s own handler already converts it. This one sets the
    conflict up for real and leaves the only resolution that can still happen
    to the boundary: the tree is already cut, so `resolve_execution_root` takes
    its early return, and `render_stage_prompt`'s resolution stands in as the
    read it is. Anything that raises after that is raising from *below* the
    `except ValueError`, which is exactly where the bare `RuntimeError` came
    from (external_harness.py:184, one line under the handler, catching
    nothing).

    So there is no `pytest.raises` here on purpose. Against the code before this
    change the call escapes with `IntegrationConflictError` and the test errors,
    naming it; after it, there is no second resolution to fail and the checkout
    simply succeeds with the branch its prepared tree reported.
    """
    cut_ticket_worktree(db_session, workspace, ticket, repo)
    target = target_branch_name(db_session, ticket, workspace)
    commit_on(repo, target, "clash.txt", "integration side\n")
    commit_on(repo, "main", "clash.txt", "main side\n")
    orch_run = _orch(db_session, ticket)

    def reading(session, ticket_, workspace_, *, repo_root, **extra):
        return target_branch_name(session, ticket_, workspace_)

    with (
        mock.patch(RESOLUTION_BINDINGS[0], reading),
        mock.patch(RESOLUTION_BINDINGS[1], reading),
    ):
        view = begin_external_stage(db_session, orch_run, stage_key=STAGE)

    [run_view] = view.runs
    assert run_view.target_branch == target, (
        f"the view reports {run_view.target_branch!r}: the branch has to come from the "
        "tree the render prepared, not from a second resolution at the boundary"
    )
    assert run_view.prompt


# --- AC12: the fresh single-agent path settles what it stamped ----------------


def test_a_failed_fresh_external_handoff_settles_the_run_it_stamped(
    db_session: Session, workspace: Workspace, ticket: Ticket
):
    """AC12. Three readings, one test, because they are one failure.

    `_checked_out_run_view` stamps the run RUNNING and commits before it renders
    anything, and `start_stage_execution` charged the stage's retry budget one
    call earlier. Without the settle, a conflict leaves a RUNNING run with no
    agent behind it, a stage reading RUNNING, and a charge spent on work that
    was never attempted — and clearing the resulting breaker costs an operator a
    deliberate requeue with a reason string.

    The same three steps the terminal-handoff route has taken since 917, now
    through the one helper both surfaces call.
    """
    orch_run = _orch(db_session, ticket)
    target = target_branch_name(db_session, ticket, workspace)
    assert count_stage_dispatches(db_session, ticket.id, STAGE) == 0, "fixture: nothing charged"
    _calls, (cli_patch, worktree_patch) = _raising_resolution(_conflict(target))

    with cli_patch, worktree_patch, pytest.raises(ValueError) as excinfo:
        begin_external_stage(db_session, orch_run, stage_key=STAGE)

    [run] = _runs(db_session, ticket, STAGE)
    db_session.refresh(run)
    assert run.status == RunStatus.FAILED, (
        f"the run this handoff stamped RUNNING was left {run.status}: nothing else "
        "ever settles an external-harness run"
    )
    assert str(excinfo.value) in (run.stderr or ""), (
        f"the failure the harness was told is not the one recorded on the run: {run.stderr!r}"
    )
    assert OrchestrationService(db_session).stage_status(ticket, STAGE) != StageStatus.RUNNING, (
        "the stage still reads as running with nothing running it"
    )
    assert count_stage_dispatches(db_session, ticket.id, STAGE) == 0, (
        "the dispatch charged for a run that never attempted the work was not handed back"
    )


# --- AC13: the parallel fan-out leaves no member RUNNING ----------------------


def test_a_parallel_fan_out_that_fails_midway_leaves_no_member_running(
    db_session: Session, workspace: Workspace, parallel_ticket: Ticket
):
    """AC13. The fan-out stamps N, so it settles N.

    `_checked_out_run_view` runs once per member, inside a list comprehension,
    and each call commits its own stamp. A raise on member k therefore leaves
    members 1..k-1 RUNNING — the same defect AC12 names, N-1 times over, on the
    shape the live workflow's plan stage actually is.

    And this path returns rather than raises: `ExternalStageView(parallel=True,
    message=...)` is the error shape it already uses twelve lines above for a
    tree it could not prepare, so a harness meets one convention here and not
    two.
    """
    orch_run = _orch(db_session, parallel_ticket)
    target = target_branch_name(db_session, parallel_ticket, workspace)
    # One tree is prepared for the whole stage before any member starts, through
    # `parallel_stage`'s own binding and `ticket_worktree`'s — neither patched
    # here. Each member's render then resolves through `cli`'s exactly once, so
    # `after=1` puts the failure on member two with member one already stamped
    # and committed.
    calls, (cli_patch,) = _raising_resolution(
        _conflict(target), after=1, bindings=RESOLUTION_BINDINGS[:1]
    )
    raised: list[ValueError] = []
    view = None

    with cli_patch:
        try:
            view = begin_external_stage(db_session, orch_run, stage_key=PLAN_STAGE)
        except ValueError as exc:
            # Caught rather than expected, so the assertions below still run:
            # which runs were left RUNNING is the finding, and the return shape
            # is the second half of it.
            raised.append(exc)

    assert len(calls) == len(PLAN_LANES), (
        f"fixture: {len(calls)} per-member resolutions for {len(PLAN_LANES)} members — "
        "the failure must land on a member that is not the first, or no member was "
        "ever stamped before it"
    )
    runs = _runs(db_session, parallel_ticket, PLAN_STAGE)
    assert len(runs) == len(PLAN_LANES), [r.run_code for r in runs]
    for run in runs:
        db_session.refresh(run)
        assert run.status == RunStatus.FAILED, (
            f"member {run.skill_name} was left {run.status} after the stage failed: "
            "the fan-out stamps one run per member, so it settles one per member"
        )
    assert (
        OrchestrationService(db_session).stage_status(parallel_ticket, PLAN_STAGE)
        != StageStatus.RUNNING
    ), "the stage still reads as running with no member behind it"
    assert view is not None, (
        "the fan-out raised instead of returning its own error shape: a harness "
        "reading `message` for one tree failure and catching an exception for "
        f"another has to handle both ({raised})"
    )
    assert view.parallel, view
    assert view.message, "the error shape has to carry the detail, not an empty message"
    assert view.runs == [], "a failed checkout must not hand back half a fan-out"


# --- AC14: a resumed run is not reaped ---------------------------------------


def test_a_render_failure_does_not_settle_a_resumed_run(
    db_session: Session, workspace: Workspace, ticket: Ticket
):
    """AC14. The exemption, and the reason for it.

    A stage still RUNNING that is checked out again is being *re-served*: the
    run the harness is asking about is the one it already holds, in someone's
    terminal, doing work. This handoff did not stamp it RUNNING, so settling it
    would not be closing out an orphan — it would be killing live work, and
    `_resumable_stage_run`'s docstring says that nothing else ever settles an
    external run, so the harness could not get it back.

    The `ValueError` still propagates with AC11's text: the harness is told its
    re-checkout failed. What must not happen is the run dying with it.
    """
    orch_run = _orch(db_session, ticket)
    first = begin_external_stage(db_session, orch_run, stage_key=STAGE)
    [view] = first.runs
    run = db_session.get(AgentRun, view.agent_run_id)
    assert run.status == RunStatus.RUNNING, "fixture: the harness is holding this run"
    assert OrchestrationService(db_session).stage_status(ticket, STAGE) == StageStatus.RUNNING, (
        "fixture: the stage must read RUNNING, or the re-checkout is a fresh attempt"
    )
    target = target_branch_name(db_session, ticket, workspace)
    _calls, (cli_patch, worktree_patch) = _raising_resolution(_conflict(target))

    with cli_patch, worktree_patch, pytest.raises(ValueError) as excinfo:
        begin_external_stage(db_session, orch_run, stage_key=STAGE)

    assert ticket.external_id in str(excinfo.value), str(excinfo.value)
    db_session.refresh(run)
    assert run.status == RunStatus.RUNNING, (
        f"a re-checkout that failed to render settled the run it was resuming as "
        f"{run.status}: that is live work in someone's terminal, and nothing else "
        "will ever settle an external run"
    )
    assert not run.stderr, f"a resumed run was given a failure to carry: {run.stderr!r}"
    assert [r.id for r in _runs(db_session, ticket, STAGE)] == [run.id], (
        "a failed re-checkout handed back a second run for a single-agent stage"
    )


def test_a_render_failure_does_not_settle_an_adopted_fan_out_member(
    db_session: Session, workspace: Workspace, parallel_ticket: Ticket
):
    """AC14 on the fan-out, which is where settling the wrong list is invisible.

    A resuming fan-out's member list is two kinds of run mixed together: the
    ones this pass stamped, and the ones it *adopted* — a member whose latest
    run was still RUNNING is re-served, not duplicated. AC13 settles the first
    kind; AC14 exempts the second. Settling the whole list satisfies AC13's test
    and still destroys the adopted work, because nothing else ever settles an
    external run, so the mix is the case that has to be read.

    Here every member is adopted, which makes the answer unambiguous: nothing is
    settled, the stage stays RUNNING, and the harness keeps what it is holding.
    """
    orch_run = _orch(db_session, parallel_ticket)
    first = begin_external_stage(db_session, orch_run, stage_key=PLAN_STAGE)
    adopted = [db_session.get(AgentRun, view.agent_run_id) for view in first.runs]
    assert len(adopted) == len(PLAN_LANES), first
    for run in adopted:
        assert run.status == RunStatus.RUNNING, "fixture: the harness is holding every member"
    assert (
        OrchestrationService(db_session).stage_status(parallel_ticket, PLAN_STAGE)
        == StageStatus.RUNNING
    ), "fixture: the stage must read RUNNING, or the re-checkout is a fresh attempt"
    # AC7(a). The first of the three confirmed zero-charge cases, extended onto
    # the test that already builds the scenario rather than duplicated beside
    # it: with `stamped` empty this pass charged nothing, so a refund of "one"
    # is a refund of a *historical* pass's charge. The counter must be non-empty
    # for that to be visible at all, which the first checkout above makes it.
    charged = _dispatch_marker_ids(db_session, parallel_ticket.id, PLAN_STAGE)
    assert len(charged) == 1, f"fixture: the first fan-out charged {len(charged)} dispatches"

    target = target_branch_name(db_session, parallel_ticket, workspace)
    # `after=0`, so the very first per-member render raises — by then both
    # members have been adopted and neither was stamped by this pass. Only
    # `cli`'s binding, so the stage's one tree preparation still succeeds and the
    # re-checkout fails where AC13's does.
    _calls, (cli_patch,) = _raising_resolution(
        _conflict(target), after=0, bindings=RESOLUTION_BINDINGS[:1]
    )

    with cli_patch:
        view = begin_external_stage(db_session, orch_run, stage_key=PLAN_STAGE)

    for run in adopted:
        db_session.refresh(run)
        assert run.status == RunStatus.RUNNING, (
            f"a failed re-checkout settled adopted member {run.skill_name} as "
            f"{run.status}: that member was stamped by a prior pass and is live "
            "work the harness is still holding"
        )
        assert not run.stderr, f"an adopted member was given a failure to carry: {run.stderr!r}"
    assert (
        OrchestrationService(db_session).stage_status(parallel_ticket, PLAN_STAGE)
        == StageStatus.RUNNING
    ), (
        "the stage was reset to PENDING while its members are still running: the "
        "next checkout would fan the stage out again alongside them"
    )
    assert [r.id for r in _runs(db_session, parallel_ticket, PLAN_STAGE)] == [
        r.id for r in adopted
    ], "a failed re-checkout stamped new member runs beside the ones it adopted"
    assert view.message, "the harness still has to be told its re-checkout failed"
    assert _dispatch_marker_ids(db_session, parallel_ticket.id, PLAN_STAGE) == charged, (
        "a fan-out that stamped nothing handed a charge back: the only marker on this "
        "stage belongs to the pass whose members are still running, and deleting it "
        "lets the breaker be cleared by a re-checkout that failed to render"
    )


# --- AC15: the settle is a service both surfaces import -----------------------


def test_the_settle_is_one_service_both_handoff_surfaces_import():
    """AC15. One implementation, not two that drift.

    The terminal-handoff route is not an external harness, so importing the
    shared settle *from* `external_harness` would name the wrong owner — and
    that module is already 768 lines, a hotspot where new code belongs in a new
    module (CLAUDE.md). It takes no `surface`/`kind` string: a closed set of
    values is an enum here, and the distinction each caller wants is already in
    its own logging.

    Asserted by identity rather than by behaviour, because two copies that
    agree today is exactly the state this guards against.
    """
    import inspect

    from loregarden.api import ticket_handoff
    from loregarden.services import external_harness, handoff_settle

    assert not hasattr(ticket_handoff, "_settle_failed_handoff"), (
        "the router still carries its own copy of the settle"
    )
    assert ticket_handoff.settle_failed_handoff is handoff_settle.settle_failed_handoff
    assert external_harness.settle_failed_handoff is handoff_settle.settle_failed_handoff

    params = inspect.signature(handoff_settle.settle_failed_handoff).parameters
    assert list(params) == ["session", "run_svc", "run", "ticket", "detail"], list(params)
    assert params["detail"].kind is inspect.Parameter.KEYWORD_ONLY
    assert not {"surface", "kind"} & set(params), (
        "a stringly-typed vocabulary the py-organization gate rejects"
    )


# --- AC16: the payload's provenance, its fields, and what it refuses ----------


def test_the_handoff_payload_is_labelled_typed_and_takes_no_caller_instruction(
    db_session: Session, workspace: Workspace, ticket: Ticket, repo: Path
):
    """AC16. The crewAI survey finding (@3da2d4e, MIT), pinned, with no
    production code.

    Three requirements, all three already structurally true of this payload —
    which is why this is one pinning test and not a feature. Pinned the way the
    survey says the good repository pins it, so a later change cannot quietly
    take any of the three away:

    1. PROVENANCE. The tree warning is appended under its own
       `## The tree you were given` heading, so an agent reading the prompt can
       tell loregarden's instruction about its base from the stage's own
       instructions. A prompt with nothing to warn about carries no such block —
       a heading over an empty section trains agents to skip the heading.
    2. STRUCTURED FIELDS PRESERVED. `target_branch`, `repo_path` and
       `tree_warning` are typed fields on the view, over
       `HandoffTreeStatus.behind`/`dirty_files`, so a downstream gate reads
       fields rather than grepping a sentence.
    3. NO CALLER-SUPPLIED INSTRUCTION CROSSES. The prompt is composed locally by
       `_build_prompt`; the harness supplies no prompt field, and the one
       caller-supplied input the tool takes beyond the run reference is
       `stage_key`.

    REJECTED, recorded so it is not re-proposed: fencing the tree-warning block
    as quoted data. That warning is loregarden's OWN instruction to the
    operator, not upstream agent output, and quoting our own instruction to make
    a criterion look met is the ceremony the survey warns against.
    """
    from loregarden.mcp.external_harness_tools import EXTERNAL_HARNESS_TOOL_DEFINITIONS
    from loregarden.mcp.tool_ids import McpTool

    # (1a) a current tree carries no provenance block, because there is nothing
    # to attribute.
    clean_run = _orch(db_session, ticket)
    clean = begin_external_stage(db_session, clean_run, stage_key=STAGE)
    [clean_view] = clean.runs
    assert clean_view.tree_warning == "", (
        f"fixture: this tree must be current, not {clean_view.tree_warning!r}"
    )
    assert "## The tree you were given" not in clean_view.prompt, (
        "an empty provenance block teaches agents to skip the heading"
    )

    # (1b) a tree behind its target, left dirty so the refresh is skipped rather
    # than catching up: the warning reaches the prompt, under its own heading.
    target = target_branch_name(db_session, ticket, workspace)
    commit_on(repo, target, "sibling.txt", "a sibling landed after the tree was cut\n")
    tree = Path(clean_view.repo_path)
    (tree / "in-flight.txt").write_text("the operator is mid-edit\n", encoding="utf-8")
    assert git(tree, "status", "--porcelain").stdout.strip(), "fixture: the tree must be dirty"

    stale = begin_external_stage(db_session, clean_run, stage_key=STAGE)
    [stale_view] = stale.runs

    assert stale_view.tree_warning, "fixture: the tree must be reported stale"
    assert f"## The tree you were given\n\n{stale_view.tree_warning}" in stale_view.prompt, (
        "the warning reaches the prompt without saying who is speaking"
    )

    # (2) the same facts as typed fields, not only as prose.
    assert {"target_branch", "repo_path", "tree_warning"} <= set(
        ExternalStageRunView.model_fields
    ), sorted(ExternalStageRunView.model_fields)
    assert stale_view.target_branch == target
    assert stale_view.repo_path == str(tree)

    # (3) the harness supplies no prompt, and what it does supply does not reach
    # one. A forged `stage_key` names no stage, so it is refused outright — and
    # the text it carries appears in no prompt the same call hands back.
    begin = next(
        d for d in EXTERNAL_HARNESS_TOOL_DEFINITIONS if d["name"] == McpTool.BEGIN_EXTERNAL_STAGE
    )
    assert set(begin["inputSchema"]["properties"]) == {"run_id", "stage_key"}, (
        "the harness gained an input that can carry prompt text: "
        f"{sorted(begin['inputSchema']['properties'])}"
    )
    forged = None
    try:
        forged = begin_external_stage(db_session, clean_run, stage_key=FORGED_STAGE_KEY)
    except ValueError:
        # Refused outright, which is the strongest possible answer: a stage key
        # that names no stage composes no prompt at all.
        pass
    prompts = [view.prompt for view in (forged.runs if forged else [])]
    prompts.append(stale_view.prompt)
    for prompt in prompts:
        assert "IGNORE-PREVIOUS-INSTRUCTIONS" not in prompt, (
            "a caller-supplied string reached the composed prompt"
        )


# --- AC17: nothing is left on the old shapes ---------------------------------


#: Symbols lg-milestone-that-974 deletes. Each one named anywhere afterwards is
#: an unmigrated caller or a shim.
DELETED_SYMBOLS = ("_handoff_target_branch", "_settle_failed_handoff")


def _two_argument_describe_calls(source: str) -> list[tuple[int, str]]:
    """Every `describe_handoff_tree` call still passing the pair separately.

    Parsed rather than grepped: each of these calls is written over several
    lines, so a line-wise search for `describe_handoff_tree` next to
    `target_branch=` finds nothing before the change OR after it, and would
    pass against the defect.
    """
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):  # py-org: allow-isinstance - an ast node
            continue
        if _called_name(node) != "describe_handoff_tree":
            continue
        passed = [keyword.arg for keyword in node.keywords]
        if "target_branch" in passed or len(node.args) > 1:
            found.append(
                (node.lineno, f"describe_handoff_tree(args={len(node.args)}, keywords={passed})")
            )
    return found


def _referenced_names(node: ast.AST) -> list[tuple[int, str]]:
    """The one name this node *refers to*, if it refers to one by any route."""
    if isinstance(node, ast.Name):  # py-org: allow-isinstance - an ast node
        return [(node.lineno, node.id)]
    if isinstance(node, ast.Attribute):  # py-org: allow-isinstance - an ast node
        return [(node.lineno, node.attr)]
    if isinstance(  # py-org: allow-isinstance - an ast node
        node, ast.FunctionDef | ast.AsyncFunctionDef
    ):
        return [(node.lineno, node.name)]
    if isinstance(node, ast.alias):  # py-org: allow-isinstance - an ast node
        return [(getattr(node, "lineno", 0), name) for name in (node.name, node.asname) if name]
    if (  # py-org: allow-isinstance - an ast node
        isinstance(node, ast.Call)
        and _called_name(node) in {"getattr", "setattr"}
        and len(node.args) > 1
        and isinstance(node.args[1], ast.Constant)
        and isinstance(node.args[1].value, str)
    ):
        # `getattr(mod, "_settle_failed_handoff")` is a reference wearing a
        # string, and the one route a shim could take that no other arm here
        # would see. `hasattr` deliberately is not one: it cannot resurrect a
        # symbol, only assert it is gone — which is what AC7's own test does
        # with this exact string, and counting that as a caller would make
        # this test fail on a correct tree.
        return [(node.args[1].lineno, node.args[1].value)]
    return []


def _stale_symbols(source: str) -> list[tuple[int, str]]:
    """Every *reference* to a deleted symbol, parsed rather than grepped.

    A line-wise search reports the docstrings that explain why each symbol was
    deleted — which the modules and tests this change touches all carry, since
    naming what was removed is how the next reader finds out. So this test
    could not pass after a correct implementation without someone deleting that
    prose, and the cheapest way out of a test that fails on a correct tree is
    to weaken the test. Parsing is the same answer `_two_argument_describe_calls`
    already gives one line below: a name in a comment is not a caller.
    """
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        found += [
            (number, name) for number, name in _referenced_names(node) if name in DELETED_SYMBOLS
        ]
    return sorted(set(found))


def test_no_call_in_the_tree_still_passes_the_pair_as_two_arguments():
    """AC17. Internal posture, no shim.

    A missed call site fails as a tuple-unpack `TypeError`, which reads like a
    harness problem rather than an unmigrated caller — so the grep the spec
    prescribes is a test rather than something a reviewer has to remember to
    run. The server's own source is the fixture, the same way
    `test_no_dispatch_path_still_asks_whether_it_is_in_the_shared_checkout`
    makes 917's deletion checkable.
    """
    import loregarden

    root = Path(loregarden.__file__).parent
    tests_dir = Path(__file__).parent
    offenders: list[str] = []

    for path in [*sorted(root.rglob("*.py")), *sorted(tests_dir.glob("*.py"))]:
        if path == Path(__file__):
            continue
        source = path.read_text(encoding="utf-8")
        offenders += [f"{path.name}:{number}: {what}" for number, what in _stale_symbols(source)]
        offenders += [
            f"{path.name}:{number}: {what} still takes the pair as two arguments"
            for number, what in _two_argument_describe_calls(source)
        ]

    assert offenders == [], (
        "a caller is still on the pre-lg-milestone-that-974 shape, or a deleted "
        f"symbol is still named: {offenders}"
    )


# --- test-break: four mutants the designed suite above lets live --------------


def test_the_settled_stage_is_put_back_to_pending_and_the_cursor_does_not_move(
    db_session: Session, workspace: Workspace, ticket: Ticket
):
    """AC12 asserts `!= RunStatus.RUNNING` and `!= StageStatus.RUNNING`, which
    two settles that are *worse* than no settle both satisfy.

    `finalize_stage(..., status=StageStatus.FAILED)` or `BLOCKED` is not
    RUNNING, so the test above passes — and the stage a handoff never attempted
    is now one an operator has to clear by hand, which is the opposite of
    handing its dispatch charge back one line earlier. PENDING is the state it
    was in before a dispatch that did no work, and it is the only one that
    lets the harness simply ask again.

    `complete_run(..., advance_workflow=True)` also passes it, and loses the
    ticket entirely: the workflow advances past a stage whose work was never
    started, so the harness's next checkout serves the *next* stage and nobody
    ever notices the skipped one. That is why the cursor is read here too.
    """
    orch_run = _orch(db_session, ticket)
    target = target_branch_name(db_session, ticket, workspace)
    assert ticket.workflow_stage_key == STAGE, "fixture: parked on the stage under test"
    _calls, (cli_patch, worktree_patch) = _raising_resolution(_conflict(target))

    with cli_patch, worktree_patch, pytest.raises(ValueError):
        begin_external_stage(db_session, orch_run, stage_key=STAGE)

    db_session.refresh(ticket)
    assert OrchestrationService(db_session).stage_status(ticket, STAGE) == StageStatus.PENDING, (
        "a handoff that never attempted the work left the stage in a state an "
        "operator has to clear: PENDING is what it was before the dispatch"
    )
    assert ticket.workflow_stage_key == STAGE, (
        f"the workflow advanced to {ticket.workflow_stage_key!r} on a stage whose "
        "work was never started — the next checkout would serve the stage after it"
    )


def test_the_external_view_reports_the_tree_the_render_prepared(
    db_session: Session, ticket: Ticket, tmp_path: Path
):
    """AC8, closed against the swap its source grep cannot see.

    `test_the_external_harness_resolves_no_target_branch_anywhere` reads the
    module for the write-path resolver's name. A view that deletes that call
    and asks `target_branch_name` at the boundary instead passes it — the name
    is gone, no ref is written, and the answer agrees with the prepared tree's
    whenever nothing is wrong. It is still two readings of one question, which
    is the defect this ticket is named for rather than a symptom of it, and it
    is exactly how `_handoff_target_branch` survived on the terminal side.

    So the prepared tree is made to disagree with every other source in the
    process. Both facts the view reports about the tree have to come from it.
    """
    from loregarden.agents.executors.cli import CliAgentExecutor
    from loregarden.services.handoff_tree_status import PreparedTree

    sentinel_branch = "integration/no-resolver-returns-this"
    sentinel_root = tmp_path / "a-tree-the-boundary-could-not-have-guessed"
    sentinel_root.mkdir()
    real = CliAgentExecutor.render_stage_prompt

    def handing_back_a_different_tree(self, run, ticket_):
        prompt, _tree = real(self, run, ticket_)
        return prompt, PreparedTree(repo_root=sentinel_root, target_branch=sentinel_branch)

    orch_run = _orch(db_session, ticket)
    with mock.patch.object(CliAgentExecutor, "render_stage_prompt", handing_back_a_different_tree):
        view = begin_external_stage(db_session, orch_run, stage_key=STAGE)

    [run_view] = view.runs
    assert run_view.target_branch == sentinel_branch, (
        f"the view reports {run_view.target_branch!r}: the branch has to be the one "
        "the tree was brought up to, not a second reading taken at the boundary"
    )
    assert run_view.repo_path == sentinel_root.as_posix() or run_view.repo_path == str(
        sentinel_root
    ), f"the view reports {run_view.repo_path!r}, not the tree the render prepared"


def test_the_parallel_error_shape_carries_the_readable_failure_sentence(
    db_session: Session, workspace: Workspace, parallel_ticket: Ticket
):
    """AC13 asserts `view.message` is truthy, and the whole point of this path
    is *which* message.

    `message` is the only thing the harness is told — the fan-out returns
    rather than raising, so there is no exception text behind it and no run
    view to read. A settle that reports "checkout failed", or git's raw
    `CONFLICT (content): ...` with nothing naming the ticket, satisfies a
    truthiness check and leaves the operator with a stage that stopped and no
    way to tell which tree to go and fix.

    The single-agent path is held to the four facts of lg-milestone-that-917's
    lead sentence (AC11). A harness meeting one convention here and another
    there is the same split this ticket closes on the exception type.
    """
    orch_run = _orch(db_session, parallel_ticket)
    target = target_branch_name(db_session, parallel_ticket, workspace)
    calls, (cli_patch,) = _raising_resolution(
        _conflict(target), after=1, bindings=RESOLUTION_BINDINGS[:1]
    )

    with cli_patch:
        view = begin_external_stage(db_session, orch_run, stage_key=PLAN_STAGE)

    assert len(calls) == len(PLAN_LANES), f"fixture: {len(calls)} per-member resolutions"
    message = view.message or ""
    head, _, _ = message.partition(". ")
    first = head if head == message else f"{head}."
    assert parallel_ticket.external_id in first, first
    assert "resolve the conflict with" in first, (
        f"the fan-out's only channel to the operator says {message!r}: the single-agent "
        "path answers the same failure with lg-milestone-that-917's lead sentence"
    )
    assert target in first, first
    assert "shared.txt" in message, (
        "git's own words — the only thing that says *what* conflicted — were dropped"
    )


def test_a_failed_re_checkout_leaves_the_resumed_stage_running_and_its_charge_spent(
    db_session: Session, workspace: Workspace, ticket: Ticket
):
    """AC14's exemption has three halves and the test above reads one.

    It asserts the *run* is still RUNNING. A settle that skips `complete_run`
    but still calls `finalize_stage(..., PENDING)` and the refund passes it,
    and leaves a state worse than either outcome: a stage reading PENDING with
    a RUNNING run under it, so the harness's next checkout finds nothing to
    resume, reaps the live run as superseded, and starts a second one — which
    is precisely what `_resumable_stage_run` exists to prevent.

    And the charge. The first checkout spent a dispatch, an operator is
    working against it, and a failed *re*-checkout is not a reason to hand it
    back: a stage that keeps refunding never reaches the breaker that stops a
    loop.
    """
    orch_run = _orch(db_session, ticket)
    begin_external_stage(db_session, orch_run, stage_key=STAGE)
    charged = count_stage_dispatches(db_session, ticket.id, STAGE)
    assert charged == 1, f"fixture: the first checkout charged {charged} dispatches"
    target = target_branch_name(db_session, ticket, workspace)
    _calls, (cli_patch, worktree_patch) = _raising_resolution(_conflict(target))

    with cli_patch, worktree_patch, pytest.raises(ValueError):
        begin_external_stage(db_session, orch_run, stage_key=STAGE)

    db_session.refresh(ticket)
    assert OrchestrationService(db_session).stage_status(ticket, STAGE) == StageStatus.RUNNING, (
        "a failed re-checkout put the stage back to pending with its run still "
        "RUNNING: the next checkout reaps live work and hands back a second run"
    )
    assert count_stage_dispatches(db_session, ticket.id, STAGE) == charged, (
        "a failed re-checkout handed back the charge the run it is resuming spent"
    )


# --- lg-milestone-that-1213: the fan-out refunds once per PASS, not per member


def test_a_failed_fan_out_hands_back_only_the_charge_this_pass_made(
    db_session: Session, workspace: Workspace, wide_parallel_ticket: Ticket
):
    """AC1 and AC2, over S1 — the defect this ticket is named for.

    Three members, two charges already on the counter from passes that really
    happened, and one charge for this pass. `settle_failed_handoff` refunds
    once per call and `_settle_fanned_out_members` calls it once per stamped
    member, so three members hand back three markers: the counter goes 3 -> 0
    and the breaker reads zero attempts for a stage dispatched three times.
    The audit of the earlier passes goes with it.

    Two assertions, because the count alone does not say *which* rows survived.
    A refund that drops the two oldest leaves the same count and the wrong
    history, so the surviving ids are compared against the ids recorded before
    the pass began — AC2's identity attribution, read as the behaviour it is
    rather than as the mechanism underneath it.
    """
    record_stage_dispatch(db_session, wide_parallel_ticket.id, PLAN_STAGE)
    record_stage_dispatch(db_session, wide_parallel_ticket.id, PLAN_STAGE)
    historical = _dispatch_marker_ids(db_session, wide_parallel_ticket.id, PLAN_STAGE)
    assert len(historical) == 2, f"fixture: {len(historical)} historical charges, wanted 2"

    orch_run = _orch(db_session, wide_parallel_ticket)
    target = target_branch_name(db_session, wide_parallel_ticket, workspace)
    # `after=1` on `cli`'s binding only: the stage's one tree preparation goes
    # through the other bindings and must succeed, then member one renders and
    # member two raises — with all three members already stamped and committed,
    # since the fan-out opens every run before it renders any of them.
    calls, (cli_patch,) = _raising_resolution(
        _conflict(target), after=1, bindings=RESOLUTION_BINDINGS[:1]
    )

    with cli_patch:
        view = begin_external_stage(db_session, orch_run, stage_key=PLAN_STAGE)

    assert len(calls) == 2, (
        f"fixture: {len(calls)} per-member renders — the failure has to land on member "
        "two, with member one already rendered and all three members stamped"
    )
    runs = _runs(db_session, wide_parallel_ticket, PLAN_STAGE)
    assert len(runs) == len(WIDE_PLAN_LANES), [r.skill_name for r in runs]
    assert view.message, "fixture: the fan-out reports through `message` on this path"
    assert count_stage_dispatches(db_session, wide_parallel_ticket.id, PLAN_STAGE) == 2, (
        "the settle handed back one marker per MEMBER: a three-member fan-out charged "
        "one attempt and refunded three, so two earlier passes' charges were deleted "
        "and the stage retry breaker now reads fewer attempts than actually happened"
    )
    assert _dispatch_marker_ids(db_session, wide_parallel_ticket.id, PLAN_STAGE) == historical, (
        "the right NUMBER of markers survived and the wrong ones: what comes back is "
        "the charge this pass made, identified by id against the snapshot taken before "
        "the pass opened a run — never a marker an earlier pass wrote"
    )


def test_a_mixed_resuming_fan_out_charged_nothing_hands_nothing_back(
    db_session: Session, workspace: Workspace, parallel_ticket: Ticket
):
    """AC7(b). The hardest of the three zero-charge cases, and the one a
    time-based refund cannot get right.

    One member adopted live RUNNING from the first pass, one re-stamped because
    its own run is no longer RUNNING. `stamped` is therefore non-empty — so the
    settle runs, unlike AC7(a) — while `dispatch_pass_open` is true for the
    adopted member and the pass is charged nothing
    (`stage_retry_budget.evaluate_standalone_stage_dispatch`, counts=False). A
    refund of "one, whatever the history" deletes the first pass's charge; a
    refund keyed to the newest marker at or before the stamped member's
    `created_at` deletes it too, because that marker is older than this pass.

    The members this pass stamped are still settled: handing back no charge is
    not the same as doing nothing.
    """
    orch_run = _orch(db_session, parallel_ticket)
    first = begin_external_stage(db_session, orch_run, stage_key=PLAN_STAGE)
    opened = [db_session.get(AgentRun, view.agent_run_id) for view in first.runs]
    assert len(opened) == len(PLAN_LANES), first
    adopted, retired = opened
    charged = _dispatch_marker_ids(db_session, parallel_ticket.id, PLAN_STAGE)
    assert len(charged) == 1, f"fixture: the first fan-out charged {len(charged)} dispatches"

    # The second member's run leaves RUNNING without the stage leaving it, which
    # is what makes the next checkout adopt one member and stamp the other.
    retired.status = RunStatus.FAILED
    db_session.add(retired)
    db_session.commit()
    assert (
        OrchestrationService(db_session).stage_status(parallel_ticket, PLAN_STAGE)
        == StageStatus.RUNNING
    ), "fixture: the stage must read RUNNING, or this is a fresh pass and it is charged"

    target = target_branch_name(db_session, parallel_ticket, workspace)
    _calls, (cli_patch,) = _raising_resolution(
        _conflict(target), after=0, bindings=RESOLUTION_BINDINGS[:1]
    )

    with cli_patch:
        view = begin_external_stage(db_session, orch_run, stage_key=PLAN_STAGE)

    runs = _runs(db_session, parallel_ticket, PLAN_STAGE)
    assert len(runs) == len(PLAN_LANES) + 1, (
        f"fixture: {len(runs)} runs — this pass has to adopt one member and stamp one "
        "fresh, or the mixed case was never built"
    )
    stamped = [run for run in runs if run.id not in {adopted.id, retired.id}]
    assert len(stamped) == 1, [r.skill_name for r in stamped]
    db_session.refresh(stamped[0])
    assert stamped[0].status == RunStatus.FAILED, (
        f"the member this pass stamped was left {stamped[0].status}: a pass charged "
        "nothing still has to settle what it opened"
    )
    db_session.refresh(adopted)
    assert adopted.status == RunStatus.RUNNING, (
        f"the adopted member was settled as {adopted.status}: it was stamped by the "
        "prior pass and is live work the harness is still holding"
    )
    assert (
        OrchestrationService(db_session).stage_status(parallel_ticket, PLAN_STAGE)
        == StageStatus.PENDING
    ), "the stage this pass could not render was not put back to pending"
    assert view.message, "the harness still has to be told its checkout failed"
    assert _dispatch_marker_ids(db_session, parallel_ticket.id, PLAN_STAGE) == charged, (
        "a pass that was charged nothing handed back a marker anyway: the only one on "
        "this stage belongs to the pass whose adopted member is still running"
    )


def test_a_reroute_exempt_fan_out_charged_nothing_hands_nothing_back(
    db_session: Session, workspace: Workspace, parallel_ticket: Ticket
):
    """AC7(c). The second confirmed zero-charge path, and the simplest to reach.

    A pending scope-denial reroute pin exempts the dispatch
    (`stage_retry_budget_state` -> `exempt`), and
    `commit_standalone_stage_dispatch` then writes a reroute-exempt marker and
    NO dispatch marker. So the fan-out is fresh, every member is stamped, the
    settle runs — and there is no charge for it to hand back.

    The exemption's own marker is the fixture check: a pass that charged nothing
    leaves no trace on the counter, so without reading that row the test cannot
    tell the exempt path from a pass that charged normally and refunded nothing.
    """
    record_stage_dispatch(db_session, parallel_ticket.id, PLAN_STAGE)
    historical = _dispatch_marker_ids(db_session, parallel_ticket.id, PLAN_STAGE)
    # Pinned to an agent this stage does not run, so `_consume_scope_reroute_pin`
    # cannot clear the pin mid-pass and the exemption is still standing when the
    # first member is evaluated.
    parallel_ticket.scope_reroute_agent = "frontend_implementer"
    db_session.add(parallel_ticket)
    db_session.commit()

    orch_run = _orch(db_session, parallel_ticket)
    target = target_branch_name(db_session, parallel_ticket, workspace)
    calls, (cli_patch,) = _raising_resolution(
        _conflict(target), after=1, bindings=RESOLUTION_BINDINGS[:1]
    )

    with cli_patch:
        view = begin_external_stage(db_session, orch_run, stage_key=PLAN_STAGE)

    assert len(calls) == len(PLAN_LANES), f"fixture: {len(calls)} per-member renders"
    assert _reroute_exempt_marker_count(db_session, parallel_ticket.id, PLAN_STAGE) == 1, (
        "fixture: the pass did not take the reroute-exempt path, so this test is not "
        "exercising a zero-charge case at all"
    )
    assert view.message, "fixture: the fan-out reports through `message` on this path"
    assert _dispatch_marker_ids(db_session, parallel_ticket.id, PLAN_STAGE) == historical, (
        "a reroute-exempt pass handed back a dispatch marker it never wrote: the "
        "exemption's whole point is that the stage's counter is left where it was"
    )


def test_the_refund_drops_nothing_when_the_snapshot_already_covers_every_marker(
    db_session: Session, ticket: Ticket
):
    """AC2, on the function itself: no new marker means no refund.

    This is the branch both zero-charge cases above arrive at, reached directly
    so that it is pinned once rather than inferred three times. A snapshot taken
    before a pass that charged nothing is equal to the set afterwards, the
    difference is empty, and the right answer is 0 rows deleted — not "the
    newest", which is what every time-based refund in this module returns for
    the same input.
    """
    from loregarden.services.stage_retry_budget import (
        refund_stage_dispatch_charged_since,
        stage_dispatch_marker_ids,
    )

    record_stage_dispatch(db_session, ticket.id, STAGE)
    record_stage_dispatch(db_session, ticket.id, STAGE)
    before = stage_dispatch_marker_ids(db_session, ticket.id, STAGE)
    assert before == _dispatch_marker_ids(db_session, ticket.id, STAGE), (
        f"stage_dispatch_marker_ids reported {sorted(before)}, which is not the set of "
        "dispatch-marker ids on this (ticket, stage)"
    )

    dropped = refund_stage_dispatch_charged_since(
        db_session, ticket.id, STAGE, markers_before=before
    )

    assert dropped == 0, f"{dropped} markers handed back for a pass that charged none"
    assert _dispatch_marker_ids(db_session, ticket.id, STAGE) == before, (
        "a refund with nothing to refund deleted a row anyway"
    )


def test_two_new_markers_drop_one_and_say_so(
    db_session: Session, ticket: Ticket, caplog: pytest.LogCaptureFixture
):
    """AC8. A pass costs one attempt, so two new markers is a broken invariant.

    `dispatch_pass_open` is what makes an N-member pass cost one, so the set
    difference can only hold two rows if that rule broke upstream. Dropping
    both would over-refund, which is the defect this ticket exists to fix, and
    dropping one quietly would absorb the broken invariant: under-refunding is
    the safe direction for a breaker, and the warning is what stops it from
    being silent. No `silent-ok:` waiver is available for it, because nothing is
    being swallowed — the surfacing *is* the log record.

    The three created_at values are set explicitly: `Artifact.id` is a random
    uuid4, so which of two rows written in the same microsecond is "newest" is
    otherwise decided by a random value, and this assertion would be flaky.
    """
    from loregarden.services.stage_retry_budget import (
        refund_stage_dispatch_charged_since,
        stage_dispatch_marker_ids,
    )

    base = datetime(2026, 10, 17, 12, 0, 0)
    for _ in range(3):
        record_stage_dispatch(db_session, ticket.id, STAGE)
    rows = sorted(_dispatch_markers(db_session, ticket.id, STAGE), key=lambda row: row.id)
    assert len(rows) == 3, f"fixture: {len(rows)} markers, wanted 3"
    for offset, row in enumerate(rows):
        row.created_at = base + timedelta(minutes=offset)
        db_session.add(row)
    db_session.commit()
    historical, middle, newest = (row.id for row in rows)
    before = frozenset({historical})
    assert stage_dispatch_marker_ids(db_session, ticket.id, STAGE) == {
        historical,
        middle,
        newest,
    }, "fixture: the snapshot has to exclude exactly the two rows under test"

    with caplog.at_level(logging.WARNING, logger="loregarden.services.stage_retry_budget"):
        dropped = refund_stage_dispatch_charged_since(
            db_session, ticket.id, STAGE, markers_before=before
        )

    assert dropped == 1, (
        f"{dropped} markers handed back for one pass: two new markers means the "
        "one-charge-per-pass rule broke upstream, and refunding both is the "
        "over-refund this ticket is about"
    )
    assert _dispatch_marker_ids(db_session, ticket.id, STAGE) == {historical, middle}, (
        "the marker dropped was not the newest of the two the pass is accountable for"
    )
    assert [
        record
        for record in caplog.records
        if record.levelno >= logging.WARNING
        and record.name == "loregarden.services.stage_retry_budget"
    ], (
        "two new markers were reconciled in silence: a broken one-charge-per-pass "
        "invariant that is only visible as an under-refund is a failure nobody sees. "
        "The logger is named because any WARNING anywhere in the call satisfied this "
        "otherwise, and the handoff path logs one on every settle"
    )


def test_the_settle_splits_into_a_run_primitive_and_a_refunding_composition():
    """AC4. Two named functions, and no mode parameter spelled either way.

    The per-member loop is inside `_settle_fanned_out_members` and the refund is
    inside `settle_failed_handoff`, so a per-pass refund needs the two
    separated. The alternative shapes are both rejected here: a `mode`/`refund`
    flag (a stringly-typed vocabulary the py-organization gate rejects, and the
    same thing spelled shorter when it is a boolean), and `external_harness`
    re-implementing `complete_run` + `finalize_stage`, which
    `test_the_settle_is_one_service_both_handoff_surfaces_import` pins by
    identity and which is the second copy `handoff_settle` exists to prevent.

    Asserted on signatures and on which calls each body makes, because two
    functions that agree today is exactly the state the split has to survive.
    """
    import inspect

    from loregarden.services import handoff_settle

    expected = ["session", "run_svc", "run", "ticket", "detail"]
    for name in ("settle_failed_handoff", "settle_failed_handoff_run"):
        fn = getattr(handoff_settle, name, None)
        assert fn is not None, f"handoff_settle.{name} is missing"
        params = inspect.signature(fn).parameters
        assert list(params) == expected, f"{name}: {list(params)}"
        assert params["detail"].kind is inspect.Parameter.KEYWORD_ONLY, name
        assert not {"mode", "refund", "surface", "kind"} & set(params), (
            f"{name} carries a mode parameter: which surface failed is already in each "
            "caller's own logging, and a closed set of values is an enum here"
        )

    primitive = _function_def(inspect.getsource(handoff_settle), "settle_failed_handoff_run")
    refunds = [
        line
        for name in ("refund_stage_dispatch_charged_before", "refund_stage_dispatch_charged_since")
        for line in _calls_named(primitive, name)
    ]
    assert refunds == [], (
        f"settle_failed_handoff_run refunds the dispatch counter at {refunds}: the "
        "primitive the fan-out calls once per member must know nothing about it, or "
        "the per-member refund is back under a new name"
    )
    assert _calls_named(primitive, "complete_run"), (
        "settle_failed_handoff_run does not complete the run: it is the per-run half "
        "of the settle, not a wrapper"
    )
    assert _calls_named(primitive, "finalize_stage"), (
        "settle_failed_handoff_run does not finalize the stage"
    )

    composition = _function_def(inspect.getsource(handoff_settle), "settle_failed_handoff")
    assert _calls_named(composition, "settle_failed_handoff_run"), (
        "settle_failed_handoff does not call the primitive: a second copy of the "
        "log/complete/finalize steps is what the split exists to avoid"
    )
    assert _calls_named(composition, "refund_stage_dispatch_charged_before"), (
        "settle_failed_handoff no longer refunds: its single-run callers "
        "(api/ticket_handoff.py, the external single-agent checkout) rely on the "
        "time cutoff, and AC3 keeps that path unchanged"
    )


def test_the_fan_out_settle_docstring_names_what_actually_charges_the_pass():
    """AC5. The docstring asserted the defect was safe, and cited the wrong
    function for it.

    `charge_fanout_dispatch` has one caller, `stage_fanout_service.py` (the
    `POST /stage-fanout` surface), and is never reached on the external-harness
    path. What charges this pass is `evaluate_standalone_stage_dispatch` inside
    `orch.start_run`, with members 2..N grouped out by `dispatch_pass_open` —
    which is also why "the rest find nothing left to drop" only held when the
    pass's charge happened to be the stage's first.

    Pinned because the wrong citation is why the defect was filed as safe for a
    whole ticket, and a comment that argues for an over-refund outlives the
    over-refund unless something reads it.
    """
    from loregarden.services import external_harness

    doc = external_harness._settle_fanned_out_members.__doc__ or ""
    assert doc, "the settle lost its docstring"
    assert "charge_fanout_dispatch" not in doc, (
        "the docstring still cites charge_fanout_dispatch as what charges this "
        "fan-out: that function's only caller is the POST /stage-fanout surface"
    )
    for name in ("evaluate_standalone_stage_dispatch", "dispatch_pass_open"):
        assert name in doc, f"the docstring does not name {name}, which is what charges the pass"
    assert "nothing left to drop" not in doc, (
        "the docstring still argues that N per-member refunds hand back one charge"
    )


def test_the_marker_snapshot_is_read_before_the_pass_charges_anything():
    """AC9. Position, which is the one thing the behaviour tests cannot see
    *failing safely*.

    Read the snapshot after the first `orch.start_run` and the set difference is
    empty on every path, so the refund becomes a silent no-op: AC1 catches it as
    3 -> 3, but every zero-charge test above still passes, and an under-refund
    on the charged path is the kind of defect that reads as "the fix worked" on
    four of five tests. So the call site is pinned where it has to be — above the
    member loop, before any run exists.

    The second half is C6/AC9's other clause: `external_harness` gains one read
    and one refund call, and the rule itself lives in `stage_retry_budget`. That
    module is a hotspot where new code belongs in a new module (CLAUDE.md), and
    a second refund call here would mean the attribution rule had drifted back
    into the caller.
    """
    from loregarden.services import external_harness

    source = Path(external_harness.__file__).read_text(encoding="utf-8")
    fanout = _function_def(source, "_begin_parallel_stage")
    reads = _calls_named(fanout, "stage_dispatch_marker_ids")
    starts = _calls_named(fanout, "start_run")
    loops = [
        node.lineno
        for node in ast.walk(fanout)
        if isinstance(node, ast.For)  # py-org: allow-isinstance - an ast node
        and getattr(node.iter, "attr", "") == "parallel_agents"
    ]

    assert reads, "_begin_parallel_stage never reads the dispatch markers it has to refund against"
    assert loops, "fixture: the member loop over stage_def.parallel_agents is not where it was"
    assert starts, "fixture: the member loop no longer opens runs"
    assert max(reads) < min(loops), (
        f"the snapshot is read at line {max(reads)}, at or below the member loop at "
        f"{min(loops)}: by then the pass has charged, the set difference is empty, and "
        "the refund is a no-op that no zero-charge test can see"
    )
    assert max(reads) < min(starts), (
        f"the snapshot is read at line {max(reads)}, after the first start_run at {min(starts)}"
    )

    whole_module = ast.parse(source)
    refunds = _calls_named(whole_module, "refund_stage_dispatch_charged_since")
    assert len(refunds) == 1, (
        f"external_harness calls the per-pass refund {len(refunds)} times ({refunds}): "
        "one pass, one refund, and the rule lives in stage_retry_budget"
    )
    assert len(_calls_named(whole_module, "stage_dispatch_marker_ids")) == 1, (
        "external_harness reads the marker snapshot more than once"
    )


# --- test-break: what the designed suite could not see fail -------------------
#
# Eight additions, each written against a mutant that passes every test above.
# The suite as designed pins the defect and the three zero-charge cases; what it
# does not pin is that the refund is *keyed* to the snapshot rather than merely
# gated by it, that it stays inside the (ticket, stage) pair it was handed, and
# that the properties hold at N=1 and across more than one pass.


#: A wall clock for the stamped markers below, far enough in the past that a
#: marker written at it is unambiguously older than any run this suite opens.
#: Not "today at noon": the time-cutoff refund selects `created_at <= run.created_at`,
#: so a marker stamped a few hours into today's future is skipped by it, and a
#: test meant to prove that an earlier pass's charge SURVIVES then passes
#: because the refund could not reach it in the first place. That is how the
#: two-pass test below passed on the unfixed tree the first time it was run.
_PAST = datetime(2020, 1, 1, 12, 0, 0)


def _record_marker_at(db_session: Session, ticket_id: str, stage_key: str, when: datetime) -> str:
    """Write one dispatch marker with an explicit `created_at`, and return its id.

    The clock is pinned rather than taken from `utcnow`: every assertion below
    that distinguishes identity from time needs two rows whose order in time is
    known and is *not* the order they were written in, which `record_stage_dispatch`
    cannot produce on its own.

    The row this call wrote is found by id difference, not as the newest by
    `created_at`. Taking the newest here is the same trap one level up: these
    tests pin their clock to a time that is *after* `utcnow` on a morning run,
    so a row already stamped forward is "newer" than the one just written, and
    the helper restamps and returns the same row on every call. Three markers
    asked for, one row three times.
    """
    before = _dispatch_marker_ids(db_session, ticket_id, stage_key)
    record_stage_dispatch(db_session, ticket_id, stage_key)
    new_rows = [
        row for row in _dispatch_markers(db_session, ticket_id, stage_key) if row.id not in before
    ]
    assert len(new_rows) == 1, f"helper: record_stage_dispatch wrote {len(new_rows)} rows"
    [written] = new_rows
    written.created_at = when
    db_session.add(written)
    db_session.commit()
    return written.id


def test_the_refund_drops_the_new_marker_even_when_an_older_pass_wrote_a_newer_row(
    db_session: Session, ticket: Ticket
):
    """AC2, at the one input where identity and time give different answers.

    Every test of the refund above is written over rows whose write order and
    whose `created_at` order agree, so "the newest marker not in the snapshot"
    and "the newest marker, once the snapshot proves there is one" are the same
    row in all of them. They are not the same rule. The second one is a time
    refund wearing an identity signature: it computes the set difference to
    decide *whether* to drop, then drops by `max(created_at)` over everything,
    and it passes S1, both zero-charge cases, the empty-difference unit test and
    AC8's two-marker case without a mark on it.

    Here they disagree. A historical marker carries a `created_at` later than
    the one this pass wrote — a clock that went backwards, a row edited by a
    repair, a `utcnow` read on either side of an NTP step — and only the
    identity rule hands back the row this pass is accountable for. The spec
    chose identity over a time window for exactly this reason (A2: `Artifact.id`
    is a random uuid4, so a tie is otherwise broken by a random value), and
    nothing else in the suite makes that choice load-bearing.
    """
    from loregarden.services.stage_retry_budget import refund_stage_dispatch_charged_since

    base = datetime(2026, 10, 17, 12, 0, 0)
    old_pass = _record_marker_at(db_session, ticket.id, STAGE, base)
    # Written before this pass, stamped *after* it. The row is history by
    # identity and the future by the clock.
    skewed_pass = _record_marker_at(db_session, ticket.id, STAGE, base + timedelta(hours=1))
    before = frozenset({old_pass, skewed_pass})
    this_pass = _record_marker_at(db_session, ticket.id, STAGE, base + timedelta(minutes=30))
    assert _dispatch_marker_ids(db_session, ticket.id, STAGE) == {
        old_pass,
        skewed_pass,
        this_pass,
    }, "fixture: the three markers are not all on the stage under test"

    dropped = refund_stage_dispatch_charged_since(
        db_session, ticket.id, STAGE, markers_before=before
    )

    assert dropped == 1, f"{dropped} markers handed back for a pass that charged one"
    assert _dispatch_marker_ids(db_session, ticket.id, STAGE) == {old_pass, skewed_pass}, (
        "the marker handed back was the newest by TIME, not the one this pass wrote: "
        "the snapshot was read and then thrown away, which is the time refund the "
        "plan rejected with an identity signature on it"
    )


def test_the_refund_stays_inside_the_ticket_and_stage_it_was_handed(
    db_session: Session, ticket: Ticket, milestone: Ticket
):
    """The (ticket, stage) scoping of the refund, which nothing above exercises.

    Every test of this function writes markers on one pair, so a refund that
    dropped the `WHERE title` or the `WHERE ticket_id` clause — the two filters
    `_markers` applies and a new query is free not to — is invisible: there is
    no second pair for it to reach into. Here there is. Both of the other rows
    are absent from `markers_before`, so a refund that queries wider will find
    a "newer new marker" on somebody else's counter and hand that back instead,
    clearing a breaker on a stage this pass never touched.
    """
    from loregarden.services.stage_retry_budget import refund_stage_dispatch_charged_since

    base = datetime(2026, 10, 17, 12, 0, 0)
    historical = _record_marker_at(db_session, ticket.id, STAGE, base)
    before = frozenset({historical})
    this_pass = _record_marker_at(db_session, ticket.id, STAGE, base + timedelta(minutes=1))
    # Both strictly newer, so a query missing either filter prefers them.
    other_stage = _record_marker_at(db_session, ticket.id, PLAN_STAGE, base + timedelta(minutes=2))
    other_ticket = _record_marker_at(db_session, milestone.id, STAGE, base + timedelta(minutes=3))

    dropped = refund_stage_dispatch_charged_since(
        db_session, ticket.id, STAGE, markers_before=before
    )

    assert dropped == 1, f"{dropped} markers handed back for a pass that charged one"
    assert _dispatch_marker_ids(db_session, ticket.id, STAGE) == {historical}, (
        "the refund did not hand back this pass's own charge"
    )
    assert this_pass not in _dispatch_marker_ids(db_session, ticket.id, STAGE)
    assert _dispatch_marker_ids(db_session, ticket.id, PLAN_STAGE) == {other_stage}, (
        "the refund reached a different stage's counter: the stage this pass failed "
        "keeps its charge and an unrelated stage's breaker is quietly cleared"
    )
    assert _dispatch_marker_ids(db_session, milestone.id, STAGE) == {other_ticket}, (
        "the refund reached a different ticket's counter"
    )


def test_the_marker_snapshot_reads_this_pair_and_only_the_dispatch_kind(
    db_session: Session, ticket: Ticket, milestone: Ticket
):
    """AC2's read half, under the same scoping pressure as the refund.

    `stage_dispatch_marker_ids` is the input the whole attribution rests on, and
    the test above pins only that it equals the dispatch rows when those are the
    only rows there are. Two ways it can be wrong are invisible in that setup. A
    snapshot that is too WIDE (another stage, another ticket, or the
    reroute-exempt kind, which shares the ticket and the stage and differs only
    by kind and title) still equals the real set on a clean fixture, and then
    makes the refund a no-op the first time a neighbour row exists. A snapshot
    that is too NARROW puts a historical marker in the difference and hands back
    somebody else's charge — the defect this ticket is about, arriving through
    the read instead of the write.
    """
    from loregarden.services.stage_retry_budget import stage_dispatch_marker_ids

    mine = {
        _record_marker_at(db_session, ticket.id, STAGE, datetime(2026, 10, 17, 12, 0, 0)),
        _record_marker_at(db_session, ticket.id, STAGE, datetime(2026, 10, 17, 12, 1, 0)),
    }
    _record_marker_at(db_session, ticket.id, PLAN_STAGE, datetime(2026, 10, 17, 12, 2, 0))
    _record_marker_at(db_session, milestone.id, STAGE, datetime(2026, 10, 17, 12, 3, 0))
    # Written as the row rather than through the exemption path: what is under
    # test is that the snapshot filters on kind, and the one surface that writes
    # this row needs a whole dispatch evaluation around it.
    db_session.add(
        Artifact(
            ticket_id=ticket.id,
            kind=StageBudgetArtifactKind.DISPATCH_REROUTE,
            title=f"stage-dispatch-reroute:{STAGE}",
        )
    )
    db_session.commit()
    assert _reroute_exempt_marker_count(db_session, ticket.id, STAGE) == 1, (
        "fixture: the reroute-exempt row the snapshot must not count was not written"
    )

    assert stage_dispatch_marker_ids(db_session, ticket.id, STAGE) == mine, (
        "the snapshot is not exactly this (ticket, stage)'s dispatch markers: a wider "
        "read makes the set difference empty and the refund a silent no-op, and a "
        "narrower one puts an earlier pass's charge in the difference"
    )
    assert stage_dispatch_marker_ids(db_session, milestone.id, STAGE) != mine, (
        "the snapshot ignores the ticket it was asked about"
    )
    assert (
        stage_dispatch_marker_ids(db_session, ticket.id, "a-stage-never-dispatched") == frozenset()
    ), "a stage with no dispatch markers reported some"


def test_the_run_primitive_leaves_the_dispatch_counter_where_it_found_it(
    db_session: Session, ticket: Ticket
):
    """AC4/C1 as behaviour, not as a call graph.

    `test_the_settle_splits_into_a_run_primitive_and_a_refunding_composition`
    reads the primitive's body with `ast` and asserts it names neither refund.
    That check is exactly as strong as the spelling: a primitive that reaches
    the counter through `refund_stage_dispatch_budget`, through
    `clear_stage_dispatches`, through a local alias, or through a helper one
    call deeper passes it unchanged, and the per-member refund is back under a
    name the AST pin was not told to look for.

    So the counter itself is the oracle. Two markers go in, the primitive
    settles the run it is given, and two markers come out — while the run really
    is FAILED and the stage really is back to PENDING, or the test would be
    satisfied by a primitive that does nothing at all.
    """
    from loregarden.services.handoff_settle import settle_failed_handoff_run

    orch_run = _orch(db_session, ticket)
    run = OrchestrationService(db_session).start_run(
        ticket,
        stage_key=STAGE,
        orchestration_run_id=orch_run.id,
        agent_id="backend_implementer",
    )
    record_stage_dispatch(db_session, ticket.id, STAGE)
    charged = _dispatch_marker_ids(db_session, ticket.id, STAGE)
    assert len(charged) >= 1, "fixture: nothing on the counter, so a refund would be invisible"

    settle_failed_handoff_run(
        db_session, RunService(db_session), run, ticket, detail="a tree it could not prepare"
    )

    db_session.refresh(run)
    assert run.status == RunStatus.FAILED, f"the primitive left the run {run.status}"
    assert OrchestrationService(db_session).stage_status(ticket, STAGE) == StageStatus.PENDING, (
        "the primitive did not put the stage back to pending"
    )
    assert _dispatch_marker_ids(db_session, ticket.id, STAGE) == charged, (
        "the per-run primitive handed back a dispatch charge: it is called once per "
        "member, so any route it has to the counter is the N-refunds-for-one-charge "
        "defect with a different function name on it"
    )


def test_a_one_member_fan_out_still_hands_back_the_charge_it_made(
    db_session: Session, workspace: Workspace, milestone: Ticket
):
    """AC1 at N=1, which is where "refund once per pass" and "do not refund"
    are the same number of markers for the wrong reason.

    The defect is an over-refund, so the obvious repair is "stop refunding more
    than once" — and the cheapest spelling of that is to key the hoist to the
    member count, or to move the refund somewhere only a multi-member fan-out
    reaches. Every test above is written over two or three members and cannot
    tell that apart from the correct fix. A single-member parallel stage is
    still a pass, is still charged one, and is still owed its one marker back;
    under-refunding here spends an attempt on work that never started, which is
    the breaker firing early on a stage that never ran.
    """
    solo = _park_on(
        db_session,
        workspace,
        milestone,
        stages=[
            WorkflowStageDef(
                key=PLAN_STAGE,
                name="Plan",
                order=1,
                stage_type="parallel",
                parallel_agents=[ParallelAgentSpec(agent_id="planner", skill_name="plan-simplest")],
            ),
            WorkflowStageDef(key="done", name="Done", order=2, terminal=True, stage_type="agent"),
        ],
        transitions=[{"from": PLAN_STAGE, "to": "done", "when": "pass"}],
        stage_key=PLAN_STAGE,
    )
    historical = _record_marker_at(db_session, solo.id, PLAN_STAGE, _PAST)

    orch_run = _orch(db_session, solo)
    target = target_branch_name(db_session, solo, workspace)
    calls, (cli_patch,) = _raising_resolution(
        _conflict(target), after=0, bindings=RESOLUTION_BINDINGS[:1]
    )

    with cli_patch:
        view = begin_external_stage(db_session, orch_run, stage_key=PLAN_STAGE)

    assert len(calls) == 1, f"fixture: {len(calls)} per-member renders on a one-member stage"
    runs = _runs(db_session, solo, PLAN_STAGE)
    assert len(runs) == 1, [r.skill_name for r in runs]
    db_session.refresh(runs[0])
    assert runs[0].status == RunStatus.FAILED, f"the one member was left {runs[0].status}"
    assert view.message, "fixture: the fan-out reports through `message` on this path"
    assert _dispatch_marker_ids(db_session, solo.id, PLAN_STAGE) == {historical}, (
        "a one-member fan-out did not hand back the charge it made: a pass is charged "
        "one whatever N is, and a refund that only fires for more than one member "
        "spends an attempt on work no agent ever started"
    )


def test_two_failed_fan_outs_in_a_row_each_hand_back_only_their_own_charge(
    db_session: Session, workspace: Workspace, parallel_ticket: Ticket
):
    """The scenario in the ticket's Impact paragraph: a fan-out that keeps
    failing, driven twice rather than reasoned about.

    One pass is where every test above stops, and a snapshot is the kind of
    state that is right once and wrong afterwards — read at import, cached on
    the orchestration run, or taken before the first pass and reused. Each of
    those passes S1 and fails here on the second pass, in the direction that
    matters: the second read would already contain the first pass's row, the
    difference would be empty, and the refund would silently stop happening.

    The invariant across both passes is that the stage's history is exactly
    where it started. Two passes that never attempted the work are owed their
    charges back, and the one earlier attempt that did happen is owed its place
    on the counter — so neither the breaker is cleared nor an attempt is
    invented.
    """
    historical = _record_marker_at(db_session, parallel_ticket.id, PLAN_STAGE, _PAST)
    target = target_branch_name(db_session, parallel_ticket, workspace)
    # One orchestration run across both passes: a second `start_external_orchestration`
    # is refused while the first is live, and the harness re-checking a stage out
    # after a failed checkout is exactly this shape.
    orch_run = _orch(db_session, parallel_ticket)

    for attempt in (1, 2):
        calls, (cli_patch,) = _raising_resolution(
            _conflict(target), after=0, bindings=RESOLUTION_BINDINGS[:1]
        )
        with cli_patch:
            view = begin_external_stage(db_session, orch_run, stage_key=PLAN_STAGE)
        assert view.message, f"fixture: pass {attempt} did not report a failure"
        assert calls, f"fixture: pass {attempt} never reached a per-member render"
        assert (
            OrchestrationService(db_session).stage_status(parallel_ticket, PLAN_STAGE)
            == StageStatus.PENDING
        ), f"fixture: pass {attempt} did not leave the stage pending, so the next "
        "checkout is a resume and is not charged"
        assert _dispatch_marker_ids(db_session, parallel_ticket.id, PLAN_STAGE) == {historical}, (
            f"after {attempt} failed fan-out(s) the stage's counter is not the one "
            "attempt that really happened: a pass charged one and owed one back has "
            "either cleared the breaker or invented an attempt"
        )


def test_the_external_single_agent_handoff_keeps_the_stage_s_earlier_charge(
    db_session: Session, workspace: Workspace, ticket: Ticket
):
    """AC3 for the caller AC3's named tests do not cover.

    AC3 pins the time-cutoff refund through
    `test_terminal_handoff_command.py`, which drives `api/ticket_handoff.py`.
    The other single-run caller is `external_harness`'s own single-agent
    checkout, and the test that covers it here
    (`test_a_failed_fresh_external_handoff_settles_the_run_it_stamped`) asserts
    the counter is 0 before and 0 after. That holds for "charged one, refunded
    one" and equally for "charged nothing, refunded nothing" and for a refund
    that drops everything it can find, because an empty counter cannot tell
    them apart.

    With one earlier attempt on the counter they separate: the pass must charge
    its own marker and hand back exactly that one, leaving the earlier attempt
    standing. This is the path the split in AC4 runs straight through, and it is
    the path a fix that pointed every caller at the new per-pass refund would
    silently stop refunding on, since no snapshot is taken here.
    """
    historical = _record_marker_at(db_session, ticket.id, STAGE, _PAST)
    orch_run = _orch(db_session, ticket)
    target = target_branch_name(db_session, ticket, workspace)
    _calls, (cli_patch, worktree_patch) = _raising_resolution(_conflict(target))

    with cli_patch, worktree_patch, pytest.raises(ValueError):
        begin_external_stage(db_session, orch_run, stage_key=STAGE)

    [run] = _runs(db_session, ticket, STAGE)
    db_session.refresh(run)
    assert run.status == RunStatus.FAILED, f"the run this handoff stamped was left {run.status}"
    assert _dispatch_marker_ids(db_session, ticket.id, STAGE) == {historical}, (
        "the single-agent external handoff did not hand back exactly its own charge: "
        "an empty set here is an earlier attempt deleted and the breaker cleared, and "
        "two markers is a charge spent on work that never started"
    )


# --- lg-milestone-that-1214: the harness is handed the sentence, not the YAML --
#
# Vector V2, for the same reason as the terminal route's own test.
# `begin_external_stage` reaches `start_stage_execution` ->
# `orchestration.start_run` -> `evaluate_standalone_stage_dispatch` ->
# `resolve_stage_retry_budget` (stage_retry_budget.py:579, 700) ->
# `resolve_orchestration_profile`, **unguarded**, before any tree is prepared.
# A profile that is malformed on disk therefore fails at dispatch and no run is
# stamped, so a test built that way would never reach the renderer at all.
# `resolve_orchestration_profile` is imported into each consumer's namespace, so
# patching `target_branch`'s reference leaves `stage_retry_budget`'s intact.

MALFORMED_PROFILE = (
    "Conditions cannot be auto-fixed: stage_thrash. Auto-fixable: emptied_group, stale_cursor."
)


def _unreadable_profile():
    """`target_branch`'s own view of the profile, raising the way a malformed
    YAML's `ValidationError` does: a `ValueError` subclass, and not the
    `TargetBranchError` `describe_handoff_failure` used to guard for."""
    return mock.patch(
        "loregarden.services.target_branch.resolve_orchestration_profile",
        side_effect=ValueError(MALFORMED_PROFILE),
    )


def test_a_profile_that_cannot_be_read_reaches_the_harness_as_the_lead_sentence(
    db_session: Session, workspace: Workspace, ticket: Ticket, repo: Path
):
    """AC4. The lead first, the validator's message behind it.

    `_checked_out_run_view` wraps a render failure in
    `ValueError(describe_handoff_failure(...))` (external_harness.py:183-189), so
    when the renderer *itself* raised, what reached the harness was the profile's
    own `ValidationError` — `"Conditions cannot be auto-fixed: …"`, which carries
    none of the facts the operator acts on. The type happened to stay a
    `ValueError`, so 974 AC12's settle still ran and the defect was invisible
    from the run's side: the only thing wrong was the one thing the harness
    reads.

    lg-milestone-that-1237 AC18: this edge inherits the new lead with no edit of
    its own, so only the expected string changes. `_unreadable_profile()` patches
    the resolver with a **plain `ValueError`**, not a `ProfileUnreadableError`,
    so this is input class **B** and the lead is AC3's no-target one. The
    profile-lead vector is the corrupt-file one below, where the file on disk is
    the defect.

    The ordering is still asserted and still the point, now against a lead built
    from the identifiers (AC13): the validator's message has to appear **after**
    the lead, never instead of it.
    """
    orch_run = _orch(db_session, ticket)

    with _unreadable_profile(), pytest.raises(ValueError) as excinfo:
        begin_external_stage(db_session, orch_run, stage_key=STAGE)

    assert type(excinfo.value) is ValueError, (
        f"the harness was handed a {type(excinfo.value).__name__}: a non-ValueError "
        "leaves loregarden_begin_external_stage as an unclassified tool crash"
    )
    detail = str(excinfo.value)
    lead = no_target_lead(ticket.external_id, repo)
    assert detail.startswith(lead), (
        f"the harness was given the validator's message instead of the lead:\n{lead!r}\n"
        f"got\n{detail!r}"
    )
    assert str(repo) in lead, f"the tree the operator has to run the stage in: {lead}"
    # AC10's cap is **not** measured here. This module's fixture ids are
    # `lg-external-handoff-<uuid4>` — 56 characters, nearly double the
    # 29-character live maximum AC11 measures the cap at — so a cap assertion
    # over this lead fails against a correct composer and reports the fixture's
    # own id as a defect in the sentence. The cap is pinned in
    # `test_handoff_tree_status.py`, on the fixtures built to the live maxima;
    # what this module is for is that the *edge* carries the same lead, which
    # the `startswith` above asserts against the shared composer.
    assert MALFORMED_PROFILE in detail, (
        "the profile's own words — the only text naming which field of which YAML is "
        f"wrong — were dropped: {detail!r}"
    )
    assert detail.index(MALFORMED_PROFILE) > len(lead) - 1, (
        f"the validator's message came before the lead: {detail!r}"
    )


def test_a_profile_that_cannot_be_read_still_settles_the_run_it_stamped(
    db_session: Session, workspace: Workspace, ticket: Ticket
):
    """AC4's last sentence: 974 AC12 still holds on this failure.

    These four readings also prove V2 reached the renderer rather than the
    unguarded dispatch guard in front of it — none of them can hold unless a run
    was actually stamped RUNNING first.
    """
    orch_run = _orch(db_session, ticket)
    assert count_stage_dispatches(db_session, ticket.id, STAGE) == 0, "fixture: nothing charged"

    with _unreadable_profile(), pytest.raises(ValueError) as excinfo:
        begin_external_stage(db_session, orch_run, stage_key=STAGE)

    runs = _runs(db_session, ticket, STAGE)
    assert len(runs) == 1, (
        "the dispatch guard raised before a run was stamped, so this never reached "
        f"the renderer at all: {[r.run_code for r in runs]}"
    )
    db_session.refresh(runs[0])
    assert runs[0].status == RunStatus.FAILED, (
        f"the run this handoff stamped RUNNING was left {runs[0].status}: nothing else "
        "ever settles an external-harness run"
    )
    assert runs[0].stderr == str(excinfo.value), (
        f"the failure the harness was told is not the one recorded on the run: {runs[0].stderr!r}"
    )
    assert (runs[0].stderr or "").startswith(f"{ticket.external_id}: "), (
        "the run view carries the validator's message with none of the four facts, "
        f"which is the only text a person later reads off the run: {runs[0].stderr!r}"
    )
    db_session.refresh(ticket)
    assert OrchestrationService(db_session).stage_status(ticket, STAGE) != StageStatus.RUNNING, (
        "the stage still reads as running with nothing running it"
    )
    assert count_stage_dispatches(db_session, ticket.id, STAGE) == 0, (
        "the dispatch charged for a run that never attempted the work was not handed back"
    )


# --- lg-milestone-that-1215: the sibling guard, on an unreadable profile ------
#
# `_checked_out_run_view`'s `except ValueError` (external_harness.py:192) is the
# one thing standing between a tree that could not be prepared and an
# unclassified tool crash at the MCP boundary. It handles a *schema-invalid*
# profile today, because pydantic's `ValidationError` is a `ValueError` — so
# giving the unreadable-profile error a `RuntimeError` base would turn a working
# readable failure into a bare crash here. That is a regression this ticket would
# introduce rather than inherited debt, which is why the guard is on its closed
# migration list.
#
# Measured on this branch, and worth recording because the ticket's own scope
# table reads otherwise: `POST /tickets/{id}/external_harness_prompt` never
# resolves an orchestration profile at all — it answered 200 for all three
# shapes of a corrupt `default.yaml`. The external surface that *does* read one
# on the way to `describe_handoff_failure` is `loregarden_begin_external_stage`,
# through `_checked_out_run_view` -> `render_stage_prompt`, so that is the seam
# these tests drive.
#
# The profile is written after the orchestration run is opened, deliberately:
# `start_external_orchestration` reads the profile itself (external_harness.py:157)
# and is explicitly out of this ticket's scope — it 500s on a corrupt profile
# before and after — so breaking the file first would measure that read instead
# of this guard.

HARNESS_PROFILE_SHAPES = {
    "corrupt-syntax": "slug: default\nname: [unclosed\n",
    "non-mapping": "- one\n- two\n",
    "schema-invalid": "slug: default\nname: Default\nmonitor:\n  autofix: [stage_thrash]\n",
}


@pytest.fixture(name="unreadable_default_profile")
def unreadable_default_profile_fixture(request, tmp_path: Path, monkeypatch):
    """Install `request.param`'s broken `default.yaml` and hand back its path.

    A factory rather than an autouse fixture because the call order matters: see
    the note above.
    """

    def install(shape: str) -> Path:
        orchestration = tmp_path / f"orchestration-{shape}"
        orchestration.mkdir()
        path = orchestration / "default.yaml"
        path.write_text(HARNESS_PROFILE_SHAPES[shape], encoding="utf-8")
        monkeypatch.setattr(
            orchestration_profile_module, "orchestration_dir", lambda: orchestration
        )
        return path.resolve()

    return install


@pytest.mark.parametrize("shape", sorted(HARNESS_PROFILE_SHAPES))
def test_an_unreadable_profile_reaches_the_harness_as_a_readable_valueerror(
    db_session: Session,
    workspace: Workspace,
    ticket: Ticket,
    repo: Path,
    unreadable_default_profile,
    shape: str,
):
    """AC9. The type is the contract, and the file is the content.

    The MCP dispatcher renders a `ValueError` as an `isError` tool result the
    harness can read, and anything else as an unclassified tool crash. So the
    assertion is on `type(...) is ValueError` and not on a base class: a
    `ProfileUnreadableError` escaping this guard *is* a `RuntimeError`, which is
    exactly the crash lg-milestone-that-974 removed from this surface.

    `schema-invalid` is in the parameters as the regression, and measured on this
    branch it does not reach this guard at all today: `start_stage_execution`'s
    retry-budget read raises pydantic's `ValidationError` one call earlier, with
    no run stamped, so the harness is handed that instead of a sentence. R5's
    fallback is what lets the file reach `render_stage_prompt`, where it arrives
    as a `ProfileUnreadableError` — a `RuntimeError` this guard's `except
    ValueError` does not catch. That is why the guard is migrated rather than
    left alone.

    lg-milestone-that-1237 AC2/AC18: this is the vector that really is input
    class **A** — the renderer catches a `ProfileUnreadableError` — and the
    harness inherits the profile lead with no edit of its own. The file moves
    **inside** the lead, which inverts what this test used to assert: the
    worktree is deliberately not named, because nothing in a worktree fixes
    malformed YAML, and the path that is named is the one the operator opens.
    """
    worktree = cut_ticket_worktree(db_session, workspace, ticket, repo)
    orch_run = _orch(db_session, ticket)
    profile_path = unreadable_default_profile(shape)

    with pytest.raises(ValueError) as excinfo:
        begin_external_stage(db_session, orch_run, stage_key=STAGE)

    assert type(excinfo.value) is ValueError, (
        f"the harness was handed a {type(excinfo.value).__name__}: a non-ValueError "
        "leaves loregarden_begin_external_stage as an unclassified tool crash"
    )
    detail = str(excinfo.value)
    lead = profile_lead(ticket.external_id, profile_path)
    assert detail.startswith(lead), (
        f"the harness was not given the profile lead:\n{lead!r}\ngot\n{detail!r}"
    )
    assert str(profile_path) in lead, (
        f"the file that will not parse is not named inside the lead, which is the one "
        f"place the toast clamp cannot eat it: {detail!r}"
    )
    assert str(worktree) not in lead, (
        f"the lead names a second absolute path, which pushes the actionable one past "
        f"the clamp: {lead}"
    )
    # AC10's cap is **not** measured here. This module's fixture ids are
    # `lg-external-handoff-<uuid4>` — 56 characters, nearly double the
    # 29-character live maximum AC11 measures the cap at — so a cap assertion
    # over this lead fails against a correct composer and reports the fixture's
    # own id as a defect in the sentence. The cap is pinned in
    # `test_handoff_tree_status.py`, on the fixtures built to the live maxima;
    # what this module is for is that the *edge* carries the same lead, which
    # the `startswith` above asserts against the shared composer.


@pytest.mark.parametrize("shape", sorted(HARNESS_PROFILE_SHAPES))
def test_an_unreadable_profile_settles_the_external_run_it_stamped(
    db_session: Session,
    workspace: Workspace,
    ticket: Ticket,
    repo: Path,
    unreadable_default_profile,
    shape: str,
):
    """AC9's other half. The run, the stage and the charge, on this surface too.

    `_checked_out_run_view` stamps the run RUNNING and commits before it renders
    anything, and the dispatch charged the stage's budget one call earlier.
    Nothing else ever settles an external-harness run — they are exempt from the
    restart reapers by design — so a failure that escapes the guard strands the
    run forever rather than until somebody else's pass.
    """
    cut_ticket_worktree(db_session, workspace, ticket, repo)
    orch_run = _orch(db_session, ticket)
    assert count_stage_dispatches(db_session, ticket.id, STAGE) == 0, "fixture: nothing charged"
    unreadable_default_profile(shape)

    with pytest.raises(ValueError) as excinfo:
        begin_external_stage(db_session, orch_run, stage_key=STAGE)

    [run] = _runs(db_session, ticket, STAGE)
    db_session.refresh(run)
    assert run.status == RunStatus.FAILED, (
        f"the run this handoff stamped RUNNING was left {run.status}"
    )
    assert str(excinfo.value) in (run.stderr or ""), (
        f"the failure the harness was told is not the one recorded on the run: {run.stderr!r}"
    )
    assert OrchestrationService(db_session).stage_status(ticket, STAGE) != StageStatus.RUNNING, (
        "the stage still reads as running with nothing running it"
    )
    assert count_stage_dispatches(db_session, ticket.id, STAGE) == 0, (
        "the dispatch charged for a run that never attempted the work was not handed back"
    )


def test_the_external_guard_names_the_unreadable_profile_alongside_valueerror():
    """AC9, read off `_checked_out_run_view`'s own handler.

    Asserted on the source because the behaviour tests above cannot distinguish
    "the guard names the new error" from "something upstream stopped raising it":
    this is the only place on the external path that turns a tree failure into
    the sentence the harness reads, and if it stops covering the schema-invalid
    shape nothing here goes red for the right reason.
    """
    source = textwrap.dedent(inspect.getsource(external_harness._checked_out_run_view))
    names: set[str] = set()
    for handler in ast.walk(ast.parse(source)):
        if isinstance(handler, ast.ExceptHandler):  # py-org: allow-isinstance - AST node walk
            names |= {
                node.id
                for node in ast.walk(handler.type)
                if isinstance(node, ast.Name)  # py-org: allow-isinstance - AST node walk
            }
    assert "ProfileUnreadableError" in names, (
        f"the external handoff guard catches {sorted(names)}: ProfileUnreadableError is a "
        "RuntimeError, so `except ValueError` no longer reaches an unreadable profile and "
        "the harness is handed an unclassified crash"
    )
    assert "ValueError" in names, (
        f"the guard catches {sorted(names)}: the tuple is additive — every other tree "
        "failure on this path still arrives as a ValueError"
    )
    assert not names & {"Exception", "BaseException"}, (
        f"the guard was widened past what any criterion asked for: {sorted(names)}"
    )


@pytest.mark.parametrize("shape", sorted(HARNESS_PROFILE_SHAPES))
def test_an_unreadable_profile_settles_the_fanned_out_members_it_stamped(
    db_session: Session,
    workspace: Workspace,
    parallel_ticket: Ticket,
    repo: Path,
    unreadable_default_profile,
    shape: str,
):
    """AC9 on the fan-out branch, which reads the profile at a second seam.

    The parallel path does not reach `_checked_out_run_view`'s guard with this
    failure at all. `prepare_tree_for_parallel_stage` runs first, and both of
    the resolvers it calls read the orchestration profile
    (`resolve_target_branch` -> `resolve_orchestration_profile`), so the raise
    lands in `parallel_stage`'s own handler twelve lines earlier.

    That handler is why this test exists rather than being a duplicate of the
    single-agent one: it caught the schema-invalid shape *before* this ticket,
    as pydantic's `ValidationError` — a `ValueError` — and `ProfileUnreadableError`
    is a `RuntimeError`. Unnamed there, it escapes `begin_external_stage` with
    one `AgentRun` per member already stamped and committed, which is the exact
    failure mode AC9 closes on the other branch: members left RUNNING with
    nothing behind them and nothing that ever settles an external run.

    Like the fan-out's own midway failure, this path returns its error shape
    rather than raising — `ExternalStageView(parallel=True, message=...)`.
    """
    orch_run = _orch(db_session, parallel_ticket)
    profile_path = unreadable_default_profile(shape)
    raised: list[Exception] = []
    view = None

    try:
        view = begin_external_stage(db_session, orch_run, stage_key=PLAN_STAGE)
    except Exception as exc:  # noqa: BLE001 - the escape IS the finding; assertions below
        # Caught rather than expected so the run assertions still run: which
        # members were left RUNNING is the finding, and the escaping type is
        # the second half of it.
        raised.append(exc)

    runs = _runs(db_session, parallel_ticket, PLAN_STAGE)
    assert len(runs) == len(PLAN_LANES), (
        f"fixture: {len(runs)} members stamped for {len(PLAN_LANES)} lanes — the "
        f"profile read has to fail with members already behind it (raised={raised})"
    )
    for run in runs:
        db_session.refresh(run)
        assert run.status != RunStatus.RUNNING, (
            f"member {run.skill_name} was left RUNNING by an unreadable profile: "
            f"the fan-out stamps one run per member, so it settles one per member "
            f"(raised={raised})"
        )
    assert not raised, (
        f"an unreadable profile escaped the fan-out as {type(raised[0]).__name__ if raised else None}: "
        "ProfileUnreadableError is a RuntimeError, so `except (ValueError, ...)` in "
        "prepare_tree_for_parallel_stage no longer reaches it"
    )
    assert (
        OrchestrationService(db_session).stage_status(parallel_ticket, PLAN_STAGE)
        != StageStatus.RUNNING
    ), "the stage still reads as running with no member behind it"
    assert view is not None and view.parallel, view
    assert view.message, "the error shape has to carry the detail, not an empty message"
    assert str(profile_path) in view.message, (
        f"the file that will not parse is not named where the harness can see it: {view.message!r}"
    )
    assert view.runs == [], "a failed checkout must not hand back half a fan-out"


# --- 976: the settle records a decision an operator can read -------------------


def test_a_failed_single_run_handoff_records_one_decision_a_person_can_read(
    db_session: Session, workspace: Workspace, ticket: Ticket
):
    """AC8(a). The composition's entry path, read off the history rail.

    Three of the four effects of settling a dispatch that never attempted the
    work were already here (the run fails, the charge comes back, the stage goes
    back to PENDING). The fourth was a `logger.warning`, which nothing in the UI
    reads — so a stage that went RUNNING -> PENDING with no agent behind it left
    an operator a stage that had moved for no recorded reason.

    Asserted on the published event rather than on log text: the rail is the
    only record a person has, and `caplog` would pass on the shape that was
    already there.
    """
    orch_run = _orch(db_session, ticket)
    target = target_branch_name(db_session, ticket, workspace)
    _calls, (cli_patch, worktree_patch) = _raising_resolution(_multiline_conflict(target))

    with cli_patch, worktree_patch, pytest.raises(ValueError) as excinfo:
        begin_external_stage(db_session, orch_run, stage_key=STAGE)

    detail = str(excinfo.value)
    assert "\n" in detail, (
        f"fixture: the detail is one line ({detail!r}) — this test cannot tell a reason "
        "that stops at the first newline from one that carries the whole string"
    )
    [run] = _runs(db_session, ticket, STAGE)
    db_session.refresh(run)

    payloads = decision_payloads(db_session, ticket.id)
    settles = [p for p in payloads if p["decision"] == OrchestratorDecision.SETTLED_FAILED_HANDOFF]
    assert len(settles) == 1, (
        "a dispatch that never attempted the work settled without recording a decision: "
        f"the ticket's decisions are {[p['decision'] for p in payloads]}"
    )
    payload = settles[0]
    assert payload["stage_key"] == STAGE, payload
    reason = _one_line_settle_reason(payload, stage_key=STAGE, run_code=run.run_code)
    # The cause clause is `detail` up to the first newline: the lead sentence
    # reaches the rail and git's output does not.
    first_line = detail.split("\n")[0]
    assert "merge conflicts in shared.txt" in first_line, f"fixture: {first_line!r}"
    assert "merge conflicts in shared.txt" in reason, (
        f"the reason does not say what went wrong: {reason!r} — the operator's question "
        "is why the stage went back to PENDING with no agent behind it"
    )
    assert "Automatic merge failed" not in reason, (
        f"the reason carries more than the first line of the detail: {reason!r}"
    )
    # ...and the whole of it travels as evidence, so the rail's `<details>` can
    # show it without the operator leaving for the run's stderr (AC9).
    assert payload["detail"] == detail, (
        "the full failure text is not on the decision: the first line alone leaves the "
        f"rest reachable only from the run view ({payload.get('detail')!r})"
    )
    assert payload["run_code"] == run.run_code, payload
    assert payload["stage_reset"] is True, payload
    # The event's own column, not the payload: that is what every reader joining
    # a decision to its run reads.
    events = [event for event in decision_events(db_session, ticket.id) if event.run_id == run.id]
    assert len(events) == 1, (
        "the decision is not tied to the run it is about: a decision event carries its "
        "own run_id, and an operator reading the run's history finds nothing without it"
    )


def test_a_failed_fan_out_records_one_decision_per_member_against_one_refund(
    db_session: Session, workspace: Workspace, wide_parallel_ticket: Ticket
):
    """AC8(b). The surface that stamps the most runs, and the asymmetry AC6 writes down.

    `external_harness._settle_fanned_out_members` calls the per-run **primitive**
    once per stamped member and refunds once for the pass. So recording the
    decision in the composition would satisfy "one named operation records a
    decision" literally and leave this path — three members, three runs stamped
    RUNNING and committed — with no decision at all. That is R1, the half-work
    trap on this ticket, and it is only visible over N > 1.

    The accepted consequence is asserted in the same test, so nobody later
    "fixes" the asymmetry by moving the decision: N decisions, each naming its
    own run, and exactly one marker handed back.
    """
    record_stage_dispatch(db_session, wide_parallel_ticket.id, PLAN_STAGE)
    record_stage_dispatch(db_session, wide_parallel_ticket.id, PLAN_STAGE)
    historical = _dispatch_marker_ids(db_session, wide_parallel_ticket.id, PLAN_STAGE)
    assert len(historical) == 2, f"fixture: {len(historical)} historical charges, wanted 2"

    orch_run = _orch(db_session, wide_parallel_ticket)
    target = target_branch_name(db_session, wide_parallel_ticket, workspace)
    # `after=1` on `cli`'s binding only, as in AC1's refund test: the stage's one
    # tree preparation goes through the other bindings and succeeds, member one
    # renders, member two raises — with all three members already stamped.
    calls, (cli_patch,) = _raising_resolution(
        _multiline_conflict(target), after=1, bindings=RESOLUTION_BINDINGS[:1]
    )

    with cli_patch:
        view = begin_external_stage(db_session, orch_run, stage_key=PLAN_STAGE)

    assert len(calls) == 2, f"fixture: {len(calls)} per-member renders, wanted the second to raise"
    assert view.message, "fixture: the fan-out reports through `message` on this path"
    runs = _runs(db_session, wide_parallel_ticket, PLAN_STAGE)
    assert len(runs) == len(WIDE_PLAN_LANES), [r.skill_name for r in runs]
    for run in runs:
        db_session.refresh(run)

    payloads = [
        p
        for p in decision_payloads(db_session, wide_parallel_ticket.id)
        if p["decision"] == OrchestratorDecision.SETTLED_FAILED_HANDOFF
    ]
    assert len(payloads) == len(WIDE_PLAN_LANES), (
        f"{len(payloads)} decisions for {len(WIDE_PLAN_LANES)} stamped members: the "
        "fan-out calls the per-run primitive once per member, so recording the decision "
        "in the refunding composition leaves this path — the one that stamps the most "
        "runs — silent"
    )
    by_run_code = {p["run_code"]: p for p in payloads}
    assert set(by_run_code) == {run.run_code for run in runs}, (
        f"the decisions do not name one member each: {sorted(by_run_code)} against "
        f"{sorted(run.run_code for run in runs)} — adjacent lines on the rail are only "
        "told apart by the run code in the reason"
    )
    for run in runs:
        payload = by_run_code[run.run_code]
        reason = _one_line_settle_reason(payload, stage_key=PLAN_STAGE, run_code=run.run_code)
        for other in runs:
            if other.id != run.id:
                assert other.run_code not in reason, (
                    f"{run.run_code}'s reason names {other.run_code} too: {reason!r}"
                )
        assert payload["detail"] == view.message, payload
    assert {event.run_id for event in decision_events(db_session, wide_parallel_ticket.id)} == {
        run.id for run in runs
    }, "each decision must carry the run it is about"
    assert _dispatch_marker_ids(db_session, wide_parallel_ticket.id, PLAN_STAGE) == historical, (
        "N decisions and N refunds: the decision is per run and the dispatch charge is "
        "one marker for the whole pass (lg-milestone-that-1213), so this pass owes "
        "exactly its own one marker back"
    )


def test_the_settle_reason_drops_the_cause_rather_than_saying_half_a_sentence(
    db_session: Session, ticket: Ticket
):
    """R7/AC5. A detail whose first line is empty.

    `describe_handoff_failure` leads with a sentence, so the first line is
    normally the cause — but the primitive takes whatever `detail` its caller
    built, and a caller that leads with git's output rather than with prose
    hands it a first line of nothing. Interpolating that leaves a sentence with
    a dangling connector and no cause, which is worse on the rail than a
    sentence that simply does not have one: the stage, the run and PENDING are
    the facts the operator acts on, and all three survive.
    """
    from loregarden.services.handoff_settle import settle_failed_handoff_run

    orch_run = _orch(db_session, ticket)
    run = OrchestrationService(db_session).start_run(
        ticket,
        stage_key=STAGE,
        orchestration_run_id=orch_run.id,
        agent_id="backend_implementer",
    )
    detail = f"\n{MERGE_OUTPUT}"

    settle_failed_handoff_run(db_session, RunService(db_session), run, ticket, detail=detail)

    settles = [
        p
        for p in decision_payloads(db_session, ticket.id)
        if p["decision"] == OrchestratorDecision.SETTLED_FAILED_HANDOFF
    ]
    assert len(settles) == 1, (
        "the primitive recorded no decision for the run it settled: an empty first line "
        "is a reason with one clause fewer, not a reason to say nothing"
    )
    payload = settles[0]
    reason = _one_line_settle_reason(payload, stage_key=STAGE, run_code=run.run_code)
    assert "  " not in reason, f"an empty cause was interpolated into the sentence: {reason!r}"
    assert not re.search(r"(—|--|:)\s*(\.|$)", reason), (
        f"the sentence has a connector with nothing after it: {reason!r}"
    )
    # The cause is gone from the sentence and still readable from the evidence.
    assert payload["detail"] == detail, payload


def test_the_decision_is_recorded_by_the_per_run_primitive_and_said_to_be(
    db_session: Session,
):
    """AC1 and AC6. Where the call is, and the paragraph that stops it moving.

    The behaviour tests above are the oracle for both, and this is the pin on
    the one thing they cannot see: the *reason* the N-decisions-one-refund
    asymmetry is deliberate. A reader who finds N adjacent settle lines and no
    explanation tidies them into the composition, and the busiest surface goes
    silent with every test above still green except the fan-out's count — which
    is exactly the review that let the half-work through once.
    """
    from loregarden.services import handoff_settle

    source = inspect.getsource(handoff_settle)
    primitive = _function_def(source, "settle_failed_handoff_run")
    assert _calls_named(primitive, "record_orchestrator_decision"), (
        "settle_failed_handoff_run records no decision: a stage put back to PENDING "
        "with no agent behind it leaves the run ledger no reason, which is the whole of "
        "this ticket"
    )
    composition = _function_def(source, "settle_failed_handoff")
    assert _calls_named(composition, "record_orchestrator_decision") == [], (
        "the composition records a decision of its own: its callers would then get two "
        "for one run, and the decision is a statement about one run"
    )
    # `ast.Constant.value` IS the string. The extra hop this read used to take
    # raised AttributeError, which the assertion above had been masking: the
    # test could not pass against any implementation, only fail earlier.
    doc = "\n".join(
        node.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant)  # py-org: allow-isinstance - an ast node
        and isinstance(node.value, str)  # py-org: allow-isinstance - an ast node
    )
    assert "lg-milestone-that-1213" in doc, (
        "nothing in handoff_settle.py says why the refund is per pass while the decision "
        "is per run — the next reader has no reason not to move one of them"
    )
    assert "external_harness" in doc, (
        "the asymmetry is not attributed to the surface it protects: moving the decision "
        "into the composition leaves external_harness's per-member settle with none"
    )


# --- 976, break tests: the branches the stated cases do not reach --------------


def test_a_run_with_no_stage_does_not_have_a_stage_reset_claimed_for_it(
    db_session: Session, ticket: Ticket
):
    """AC5's `stage_reset` evidence is a bool, and nothing above exercises False.

    The primitive guards the stage reset on `run.stage_key` and AC5 carries
    `bool(run.stage_key)` in the evidence, so the False arm is a stated branch:
    a run with no stage behind it is settled with no stage put anywhere. The
    decision is still the record that it was settled — it is the only one — so
    skipping it on this arm leaves exactly the silence this ticket removes.

    The sentence then cannot say the thing the other arm's sentence exists to
    say. `_one_line_settle_reason` is deliberately NOT used: it requires
    "PENDING" and "again" of every reason, which are true only where a stage
    really was reset, and asserting them here would pin a lie onto the rail for
    the same reason R7 refuses a dangling cause clause. What the operator acts
    on — the run, and one line — still has to hold.
    """
    from loregarden.services.handoff_settle import settle_failed_handoff_run

    orch_run = _orch(db_session, ticket)
    run = OrchestrationService(db_session).start_run(
        ticket,
        stage_key=STAGE,
        orchestration_run_id=orch_run.id,
        agent_id="backend_implementer",
    )
    # The shape the guard is written for: a stamped run that names no stage.
    run.stage_key = ""
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)

    settle_failed_handoff_run(
        db_session, RunService(db_session), run, ticket, detail=f"no stage here\n{MERGE_OUTPUT}"
    )

    settles = [
        p
        for p in decision_payloads(db_session, ticket.id)
        if p["decision"] == OrchestratorDecision.SETTLED_FAILED_HANDOFF
    ]
    assert len(settles) == 1, (
        "a run settled with no stage behind it recorded nothing: the decision is the "
        "only record that it was settled at all, and this arm has no stage transition "
        "to stand in for it"
    )
    payload = settles[0]
    assert payload["stage_reset"] is False, (
        f"the evidence claims a stage was reset for a run that names none: {payload!r}"
    )
    assert payload["stage_key"] == "", payload
    reason = payload["reason"]
    assert "\n" not in reason, f"the settle reason is more than one line: {reason!r}"
    assert run.run_code in reason, (
        f"the reason does not name the run: {reason!r} — with no stage to name, the run "
        "code is the whole of what the operator can click through to"
    )
    assert "PENDING" not in reason, (
        f"the reason says the stage went back to PENDING when none was touched: {reason!r} "
        "— a line asserting a transition that did not happen is worse than no line"
    )


def test_an_entirely_empty_detail_still_records_a_readable_decision(
    db_session: Session, ticket: Ticket
):
    """R7 at its boundary: not a leading newline, but nothing at all.

    The R7 case above hands the primitive `"\\n" + MERGE_OUTPUT`, so `detail`
    still has a tail for the evidence and the cause clause is the only casualty.
    A caller whose failure text is empty — a `ValueError("")`, a command that
    failed with no output — is the same branch one step further, and it is where
    a cause clause built by indexing rather than by guarding raises instead of
    dropping a clause.

    The facts the operator acts on do not depend on the cause, so all of them
    still have to be there.
    """
    from loregarden.services.handoff_settle import settle_failed_handoff_run

    orch_run = _orch(db_session, ticket)
    run = OrchestrationService(db_session).start_run(
        ticket,
        stage_key=STAGE,
        orchestration_run_id=orch_run.id,
        agent_id="backend_implementer",
    )

    settle_failed_handoff_run(db_session, RunService(db_session), run, ticket, detail="")

    settles = [
        p
        for p in decision_payloads(db_session, ticket.id)
        if p["decision"] == OrchestratorDecision.SETTLED_FAILED_HANDOFF
    ]
    assert len(settles) == 1, (
        "nothing was recorded for a run settled with an empty detail: a failure with no "
        "text is still a stage that went back to PENDING with no agent behind it"
    )
    payload = settles[0]
    reason = _one_line_settle_reason(payload, stage_key=STAGE, run_code=run.run_code)
    assert not re.search(r"(—|--|:)\s*(\.|$)", reason), (
        f"the sentence has a connector with nothing after it: {reason!r}"
    )
    assert payload["detail"] == "", payload


def test_no_decision_is_published_when_the_stage_reset_it_describes_fails(
    db_session: Session, ticket: Ticket
):
    """AC5's call position, pinned by its one observable consequence.

    AC5 places the record after `finalize_stage` and before `session.refresh`.
    That ordering is not decoration: `event_bus.publish` commits inline
    (`core/event_bus.py:90-91`), so a decision recorded before the reset is
    committed whether or not the reset then happens — and its sentence says the
    stage was put back to PENDING and can be dispatched again, which is the one
    fact the operator acts on.

    So a reset that fails must leave no line rather than a line that is wrong.
    The AST pin two tests above sees the call but not where it is; this is the
    difference between the two orderings that a reader can observe.
    """
    from loregarden.services.handoff_settle import settle_failed_handoff_run

    orch_run = _orch(db_session, ticket)
    run_svc = RunService(db_session)
    run = OrchestrationService(db_session).start_run(
        ticket,
        stage_key=STAGE,
        orchestration_run_id=orch_run.id,
        agent_id="backend_implementer",
    )

    with (
        mock.patch.object(
            run_svc.orchestration, "finalize_stage", side_effect=RuntimeError("stage write failed")
        ),
        pytest.raises(RuntimeError),
    ):
        settle_failed_handoff_run(db_session, run_svc, run, ticket, detail=MERGE_OUTPUT)

    settles = [
        p
        for p in decision_payloads(db_session, ticket.id)
        if p["decision"] == OrchestratorDecision.SETTLED_FAILED_HANDOFF
    ]
    assert settles == [], (
        "the rail carries a decision saying the stage was put back to PENDING and can be "
        f"dispatched again, and the reset raised: {settles!r} — the record belongs after "
        "the write it describes, not before it"
    )
