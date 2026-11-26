"""Every failed agent run says what kind of failure it was.

`AgentRun.failure_kind` arrived with one writer, the integration merge turn;
426 of 509 failed runs on the live database (2026-11-18) carried none, so a
usage limit, a checkout conflict and a broken test all read the same. These
tests hold the seam that closes it: `set_run_status` is the only place a run's
status becomes FAILED, and the enumeration below fails a writer that goes
around it.
"""

from __future__ import annotations

import ast
import pathlib

import pytest
from loregarden.models.domain import AgentRun, BlockKind, RunStatus
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.run_failure import (
    CHECKOUT_FAILED_PREFIX,
    classify_run_failure,
    set_run_status,
)
from loregarden.services.run_failure_invariant import (
    FAILED_RUNS_HAVE_A_FAILURE_KIND,
    classify_failed_runs,
    failed_runs_without_a_kind,
)
from sqlmodel import Session
from tests.factories import make_agent_run, make_workspace
from tests.source_ast import parse_path, parse_source

_SOURCE_ROOT = pathlib.Path(__file__).resolve().parents[1] / "loregarden"
_HELPER = "services/run_failure.py"

#: Every function that settles a run as FAILED itself (rather than handing a
#: FAILED result to `complete_run`). Each must reach `set_run_status`.
_KNOWN_WRITERS = (
    ("agents/executors/permission_bridge.py", "_fail_after_rejection"),
    ("agents/executors/permission_bridge.py", "_scope_denial_result"),
    ("services/agent_turn_runner.py", "_finish_run"),
    ("services/baxter_chat_run_service.py", "_settle"),
    ("services/baxter_chat_run_service.py", "fail_interrupted_baxter_chat_turns"),
    ("services/branch_triage_run_service.py", "_settle"),
    ("services/branch_triage_run_service.py", "fail_interrupted_branch_triage_turns"),
    ("services/integration_merge_agent.py", "_finish_run"),
    ("services/orchestration.py", "complete_run"),
    ("services/run_resupervise.py", "_complete"),
    ("services/triage_run_service.py", "_finish"),
    ("services/triage_run_service.py", "execute_triage_turn_background"),
    ("services/triage_run_service.py", "fail_interrupted_triage_turns"),
    ("services/triage_run_service.py", "cancel_triage_turn"),
)


def _mentions_failed(node: ast.AST) -> bool:
    return any(
        isinstance(sub, ast.Attribute)
        and sub.attr == "FAILED"
        and isinstance(sub.value, ast.Name)
        and sub.value.id == "RunStatus"
        for sub in ast.walk(node)
    )


def _run_status_names(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Names in `fn` that may hold RunStatus.FAILED: RunStatus parameters, and
    locals assigned from an expression naming FAILED."""
    params = fn.args.args + fn.args.kwonlyargs + fn.args.posonlyargs
    names = {
        a.arg
        for a in params
        if a.annotation is not None and ast.unparse(a.annotation) == "RunStatus"
    }
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and _mentions_failed(node.value):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    return names


def failed_status_writes(tree: ast.Module) -> list[str]:
    """Every place `tree` may write RunStatus.FAILED onto a row.

    Four shapes: `x.status = <FAILED or a RunStatus variable>`,
    `AgentRun(status=RunStatus.FAILED)`, and `.values(status=RunStatus.FAILED)`
    on a bulk update. `complete_run(status=...)` and result objects carrying a
    status are not writes; `complete_run` is, and is enumerated.
    """
    found: list[str] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        variables = _run_status_names(fn)
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if not (isinstance(target, ast.Attribute) and target.attr == "status"):
                        continue
                    value = node.value
                    if _mentions_failed(value) or (
                        isinstance(value, ast.Name) and value.id in variables
                    ):
                        found.append(f"{fn.name}:{node.lineno}")
            elif isinstance(node, ast.Call):
                called = (
                    node.func.attr
                    if isinstance(node.func, ast.Attribute)
                    else getattr(node.func, "id", "")
                )
                if called in {"AgentRun", "values"} and any(
                    kw.arg == "status" and _mentions_failed(kw.value) for kw in node.keywords
                ):
                    found.append(f"{fn.name}:{node.lineno}")
    return found


def _parse(relative: str) -> ast.Module:
    return parse_path(_SOURCE_ROOT / relative)


@pytest.fixture(name="trees", scope="module")
def trees_fixture() -> dict[str, ast.Module]:
    """Every server module, parsed once: the parse is most of this file's time."""
    return {
        path.relative_to(_SOURCE_ROOT).as_posix(): _parse(path.relative_to(_SOURCE_ROOT).as_posix())
        for path in sorted(_SOURCE_ROOT.rglob("*.py"))
    }


def test_no_writer_sets_a_failed_status_around_set_run_status(trees: dict[str, ast.Module]):
    offenders = {
        relative: writes
        for relative, tree in trees.items()
        if relative != _HELPER and (writes := failed_status_writes(tree))
    }
    assert not offenders, (
        "These write a FAILED status directly; route them through "
        f"run_failure.set_run_status so the run gets its failure_kind: {offenders}"
    )


def test_the_scan_sees_the_helpers_own_write():
    """A scan that finds nothing anywhere proves nothing; it must see this one."""
    writes = failed_status_writes(_parse(_HELPER))
    assert [w.split(":")[0] for w in writes] == ["set_run_status"], writes


@pytest.mark.parametrize(
    "source",
    [
        "def f(run):\n    run.status = RunStatus.FAILED\n",
        "def f(run, ok):\n    run.status = RunStatus.SUCCEEDED if ok else RunStatus.FAILED\n",
        "def f(run, status: RunStatus):\n    run.status = status\n",
        "def f(run, code):\n    s = RunStatus.FAILED if code else RunStatus.SUCCEEDED\n"
        "    run.status = s\n",
        "def f():\n    return AgentRun(status=RunStatus.FAILED)\n",
        "def f(q):\n    return q.values(status=RunStatus.FAILED)\n",
    ],
)
def test_the_scan_catches_each_shape_of_direct_write(source: str):
    assert failed_status_writes(parse_source(source)), source


@pytest.mark.parametrize(("relative", "function"), _KNOWN_WRITERS)
def test_every_known_writer_goes_through_the_helper(relative: str, function: str):
    tree = _parse(relative)
    fn = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function
    )
    called = {
        sub.func.attr if isinstance(sub.func, ast.Attribute) else getattr(sub.func, "id", "")
        for sub in ast.walk(fn)
        if isinstance(sub, ast.Call)
    }
    assert "set_run_status" in called, f"{relative}::{function} no longer sets a run's status"


# Real failure text, as stored on the live database's failed runs.
@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("ActionRequiredError: You've hit your usage limit You've saved", BlockKind.HARNESS),
        ("You've hit your session limit · resets 11:40am (America/New_York)", BlockKind.HARNESS),
        ("ActionRequiredError: Weekly usage limit reached. It resets in 3 days", BlockKind.HARNESS),
        (
            "Parent orchestration is already terminal; this run was left in flight.",
            BlockKind.HARNESS,
        ),
        (
            f"{CHECKOUT_FAILED_PREFIX}Branch 'loregarden/lg-x-954' could not take "
            "'integration/lg-x-463' before starting: CONFLICT (content): Merge conflict in a.py",
            BlockKind.CONFLICT,
        ),
        (
            f"{CHECKOUT_FAILED_PREFIX}'integration/lg-milestone-that-491' cannot take 'main': "
            "merge conflicts in server/loregarden/services/land_ticket.py",
            BlockKind.CONFLICT,
        ),
        (
            "'integration/lg-durable-remote-335' cannot take 'main': merge conflicts in a.tsx",
            BlockKind.CONFLICT,
        ),
        (
            f"{CHECKOUT_FAILED_PREFIX}Cannot refresh 'integration/x' from 'main': fatal: "
            "cannot lock ref",
            BlockKind.HARNESS,
        ),
        (
            f"{CHECKOUT_FAILED_PREFIX}Command '['git', 'checkout', '-B', 'b']' returned "
            "non-zero exit status 128.",
            BlockKind.HARNESS,
        ),
        ("API Error: 529 Overloaded. This is a server-side issue", BlockKind.HARNESS),
        (
            "Agent timed out after 600s with no output — killed as a presumed hang",
            BlockKind.HARNESS,
        ),
        (
            "Error: Cursor couldn't find your saved login in the macOS keychain. Log out",
            BlockKind.HUMAN_ACTION,
        ),
        ("Agent timed out after 600s", BlockKind.WORK),
        ("3 tests failed in test_runs.py", BlockKind.WORK),
        ("", BlockKind.WORK),
    ],
)
def test_failure_text_classifies_by_who_failed(text: str, kind: BlockKind):
    assert classify_run_failure(text) is kind


def test_set_run_status_clears_the_kind_off_a_non_failure():
    run = AgentRun(run_code="r", workspace_id="w", agent_id="a", failure_kind=BlockKind.WORK)
    set_run_status(run, RunStatus.SUCCEEDED, failure_text="usage limit")
    assert run.failure_kind is None


def test_an_explicit_kind_wins_over_the_text():
    run = AgentRun(run_code="r", workspace_id="w", agent_id="a")
    set_run_status(run, RunStatus.FAILED, failure_text="usage limit", failure_kind=BlockKind.WORK)
    assert run.failure_kind is BlockKind.WORK


@pytest.fixture
def running_run(db_session: Session) -> AgentRun:
    workspace = make_workspace(db_session)
    return make_agent_run(
        db_session,
        workspace_id=workspace.id,
        run_code="RUN-FAILURE-KIND",
        stage_key="implement",
        status=RunStatus.RUNNING,
    )


def test_complete_run_records_the_kind_with_the_failure(db_session: Session, running_run):
    OrchestrationService(db_session).complete_run(
        running_run,
        status=RunStatus.FAILED,
        stderr="ActionRequiredError: You've hit your usage limit",
        advance_workflow=False,
    )
    stored = db_session.get(AgentRun, running_run.id)
    assert stored.status is RunStatus.FAILED
    assert stored.failure_kind is BlockKind.HARNESS


def test_complete_run_classifies_the_recovered_reason(db_session: Session, running_run):
    """An empty stderr is replaced by what stdout says; the kind follows it."""
    OrchestrationService(db_session).complete_run(
        running_run,
        status=RunStatus.FAILED,
        stdout='{"type":"turn.failed","error":{"message":"You\'ve hit your usage limit."}}',
        stderr="",
        advance_workflow=False,
    )
    assert db_session.get(AgentRun, running_run.id).failure_kind is BlockKind.HARNESS


def test_a_succeeded_run_has_no_kind(db_session: Session, running_run):
    OrchestrationService(db_session).complete_run(
        running_run, status=RunStatus.SUCCEEDED, advance_workflow=False
    )
    assert db_session.get(AgentRun, running_run.id).failure_kind is None


def test_the_invariant_backfills_an_unkinded_failure(db_session: Session):
    workspace = make_workspace(db_session)
    unkinded = make_agent_run(
        db_session,
        workspace_id=workspace.id,
        run_code="RUN-UNKINDED",
        status=RunStatus.FAILED,
        stderr="",
        stdout="You've hit your session limit · resets 11:40am",
    )
    kinded = make_agent_run(
        db_session,
        workspace_id=workspace.id,
        run_code="RUN-KINDED",
        status=RunStatus.FAILED,
        stderr="3 tests failed",
        failure_kind=BlockKind.WORK,
    )
    make_agent_run(
        db_session, workspace_id=workspace.id, run_code="RUN-OK", status=RunStatus.SUCCEEDED
    )

    violations = failed_runs_without_a_kind(db_session)
    assert [v.subject_id for v in violations] == [unkinded.id]

    assert classify_failed_runs(db_session, violations) == 1
    db_session.refresh(unkinded)
    db_session.refresh(kinded)
    assert unkinded.failure_kind is BlockKind.HARNESS
    assert kinded.failure_kind is BlockKind.WORK
    assert failed_runs_without_a_kind(db_session) == []


def test_the_repair_rechecks_a_run_that_moved_since_detection(db_session: Session):
    workspace = make_workspace(db_session)
    run = make_agent_run(
        db_session,
        workspace_id=workspace.id,
        run_code="RUN-REQUEUED",
        status=RunStatus.FAILED,
        stderr="boom",
    )
    violations = failed_runs_without_a_kind(db_session)
    run.status = RunStatus.QUEUED  # requeued between detection and repair
    db_session.add(run)
    db_session.commit()
    assert classify_failed_runs(db_session, violations) == 0
    db_session.refresh(run)
    assert run.failure_kind is None


def test_the_invariant_is_registered_as_a_defect():
    assert FAILED_RUNS_HAVE_A_FAILURE_KIND.prevented_by.endswith(
        "::test_no_writer_sets_a_failed_status_around_set_run_status"
    )
