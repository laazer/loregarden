"""`loregarden_check_organization` — the on-demand organization gate.

One tool covers reading a workspace and rewriting that workspace's git hooks, so
the interesting part is that approval policy follows the *action*, not the tool
name: a read auto-approves, the write that edits another repo does not.
"""

import json
import subprocess
from unittest import mock

import pytest
from loregarden.agents.executors.tool_auto_approve import is_auto_approved_mcp_tool
from loregarden.mcp.tool_ids import AUTO_APPROVED_MCP_TOOLS, McpTool
from loregarden.mcp.tools import TOOL_DEFINITIONS
from loregarden.models.domain import Workspace
from loregarden.services import organization_gate_service as service
from loregarden.services.organization_gate_service import (
    CheckerResult,
    OrganizationAction,
    OrganizationScope,
    UnknownWorkspaceError,
    run_organization_gate,
    workspace_for_slug,
)

_TOOL = f"mcp__loregarden__{McpTool.CHECK_ORGANIZATION.value}"


def _workspace() -> Workspace:
    return Workspace(slug="demo", name="Demo", repo_path="/tmp/demo")


# --------------------------------------------------------------------------- #
# registration
# --------------------------------------------------------------------------- #


def test_tool_is_registered_with_a_schema():
    definition = next(t for t in TOOL_DEFINITIONS if t["name"] == McpTool.CHECK_ORGANIZATION)
    schema = definition["inputSchema"]
    assert schema["required"] == ["workspace_slug"]
    assert set(schema["properties"]["action"]["enum"]) == {a.value for a in OrganizationAction}
    assert set(schema["properties"]["scope"]["enum"]) == {s.value for s in OrganizationScope}


def test_tool_is_not_in_the_name_only_auto_approve_set():
    # Being listed there would auto-approve install_hooks along with the reads.
    assert McpTool.CHECK_ORGANIZATION not in AUTO_APPROVED_MCP_TOOLS


# --------------------------------------------------------------------------- #
# approval policy follows the action
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("action", ["check", "hooks_status"])
def test_read_only_actions_auto_approve(action):
    assert is_auto_approved_mcp_tool(_TOOL, {"action": action}) is True


def test_installing_hooks_needs_approval():
    assert is_auto_approved_mcp_tool(_TOOL, {"action": "install_hooks"}) is False


def test_omitted_action_is_the_read_only_default():
    assert is_auto_approved_mcp_tool(_TOOL, {}) is True


def test_absent_tool_input_refuses_rather_than_assuming_the_default():
    assert is_auto_approved_mcp_tool(_TOOL) is False


def test_unknown_action_refuses():
    assert is_auto_approved_mcp_tool(_TOOL, {"action": "nonsense"}) is False


def test_other_tools_are_unaffected():
    assert is_auto_approved_mcp_tool("mcp__loregarden__loregarden_get_ticket") is True
    assert is_auto_approved_mcp_tool("mcp__loregarden__loregarden_complete_stage") is False


# --------------------------------------------------------------------------- #
# what the tool reports
# --------------------------------------------------------------------------- #


def test_check_runs_both_checkers_and_sums_findings():
    with mock.patch.object(
        service,
        "check_workspace",
        return_value=[
            CheckerResult("python", ok=False, findings=["a.py:1: bad", "a.py:2: worse"]),
            CheckerResult("typescript", ok=True),
        ],
    ):
        report = run_organization_gate(_workspace(), OrganizationAction.CHECK)
    payload = report.as_payload()
    assert payload["ok"] is False
    assert payload["finding_count"] == 2
    assert [r["checker"] for r in payload["results"]] == ["python", "typescript"]


def test_clean_workspace_reports_ok():
    with mock.patch.object(
        service,
        "check_workspace",
        return_value=[CheckerResult("python", ok=True), CheckerResult("typescript", ok=True)],
    ):
        report = run_organization_gate(_workspace(), OrganizationAction.CHECK)
    assert report.ok is True
    assert report.as_payload()["finding_count"] == 0


def test_hooks_status_does_not_install():
    with mock.patch.object(service, "hooks_result") as hooks:
        hooks.return_value = CheckerResult("hooks", ok=False, message="missing")
        run_organization_gate(_workspace(), OrganizationAction.HOOKS_STATUS)
    assert hooks.call_args.kwargs["install"] is False


def test_install_hooks_installs():
    with mock.patch.object(service, "hooks_result") as hooks:
        hooks.return_value = CheckerResult("hooks", ok=True, message="installed")
        run_organization_gate(_workspace(), OrganizationAction.INSTALL_HOOKS)
    assert hooks.call_args.kwargs["install"] is True


def test_findings_are_parsed_from_checker_output():
    completed = mock.Mock(
        returncode=1, stdout=" - a.py:1: bad\nheader line\n - b.py:2: worse\n", stderr=""
    )
    with mock.patch.object(service, "_run", return_value=completed):
        result = service._checker_result("python", ["true"])
    assert result.findings == ["a.py:1: bad", "b.py:2: worse"]
    assert result.ok is False


def test_missing_interpreter_is_reported_not_raised():
    with mock.patch.object(service, "_run", side_effect=OSError("node not found")):
        result = service._checker_result("typescript", ["node", "x"])
    assert result.ok is False
    assert "node not found" in result.message


def test_payload_is_json_serializable():
    with mock.patch.object(
        service, "check_workspace", return_value=[CheckerResult("python", ok=True)]
    ):
        report = run_organization_gate(_workspace(), OrganizationAction.CHECK)
    assert json.loads(json.dumps(report.as_payload()))["action"] == "check"


def test_unknown_slug_is_a_caller_error(db_session):
    with pytest.raises(UnknownWorkspaceError):
        workspace_for_slug(db_session, "nope-not-a-workspace")


# --------------------------------------------------------------------------- #
# the gate list comes from workspace-gates.sh
# --------------------------------------------------------------------------- #


def _dispatcher_list() -> list[str]:
    """What pre-commit and orchestration run, asked of the real dispatcher."""
    completed = subprocess.run(
        ["bash", str(service._scripts_dir() / service.GATES_DISPATCHER), "--list"],
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.split()


def _passing(argv: list[str]) -> mock.Mock:
    return mock.Mock(returncode=0, stdout="", stderr="")


def test_check_workspace_covers_every_gate_the_dispatcher_lists():
    listed = _dispatcher_list()
    assert listed, "workspace-gates.sh --list printed no gates"
    gate_calls: list[list[str]] = []
    real_run = service._run

    def run(argv: list[str]):
        if argv[-1] == "--list":
            return real_run(argv)
        gate_calls.append(argv)
        return _passing(argv)

    with (
        mock.patch.object(service, "_run", side_effect=run),
        mock.patch.object(service, "resolve_workspace_root", return_value="/tmp/demo"),
    ):
        results = service.check_workspace(_workspace(), OrganizationScope.WORKTREE)

    assert [r.checker for r in results] == listed
    assert all(r.ok for r in results), [r.message for r in results if not r.ok]
    for argv in gate_calls:
        assert argv[-4:] == ["--repo", "/tmp/demo", "--scope", "worktree"]
        if argv[-5].endswith(".py"):
            # Never a bare python3: that is 3.9 on macOS and the checkers need 3.11.
            assert argv[:2] == ["bash", str(service._scripts_dir() / "server_python.sh")]


def test_dispatcher_list_failure_is_a_failing_result_not_an_empty_one():
    failed = mock.Mock(returncode=127, stdout="", stderr="bash: no such file")
    with (
        mock.patch.object(service, "_run", return_value=failed),
        mock.patch.object(service, "resolve_workspace_root", return_value="/tmp/demo"),
    ):
        results = service.check_workspace(_workspace(), OrganizationScope.WORKTREE)
    assert len(results) == 1
    assert results[0].ok is False
    assert results[0].checker == service.GATES_DISPATCHER


def test_unknown_gate_kind_fails_instead_of_being_skipped():
    def run(argv: list[str]):
        if argv[-1] == "--list":
            return mock.Mock(returncode=0, stdout="new_gate.rb\n", stderr="")
        raise AssertionError(f"nothing should run: {argv}")

    with (
        mock.patch.object(service, "_run", side_effect=run),
        mock.patch.object(service, "resolve_workspace_root", return_value="/tmp/demo"),
    ):
        results = service.check_workspace(_workspace(), OrganizationScope.WORKTREE)
    assert [(r.checker, r.ok) for r in results] == [("new_gate.rb", False)]


def test_unavailable_gate_is_distinguished_from_a_failing_one():
    unavailable = mock.Mock(returncode=service.EX_UNAVAILABLE, stdout="", stderr="needs 3.11")
    failing = mock.Mock(returncode=1, stdout=" - a.py:1: bad\n", stderr="")
    with mock.patch.object(service, "_run", side_effect=[unavailable, failing]):
        could_not_run = service._checker_result("a.py", ["x"])
        failed = service._checker_result("b.py", ["x"])
    assert could_not_run.ok is False
    assert could_not_run.message.startswith("could not run")
    assert failed.ok is False
    assert failed.message.startswith("failed")
