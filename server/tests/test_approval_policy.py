"""Per-workspace auto-approval policy (lg-workflow-integrity-107).

The defaults encode the finding, and they are asymmetric on purpose: commands
that only report are auto-approved, writes are not. The asymmetry is the part
worth testing, because it is the part a later change is most likely to flatten.
"""

from __future__ import annotations

from loregarden.agents.executors.tool_auto_approve import approval_policy, cli_auto_approve
from loregarden.services.orchestration_profile import ApprovalPolicyConfig
from sqlmodel import Session
from tests.factories import make_workspace_ticket

OPEN = ApprovalPolicyConfig()


def _decide(tool: str, tool_input: dict, *, policy=OPEN, interactive: bool = False):
    return cli_auto_approve(tool, tool_input, interactive=interactive, policy=policy)


def test_a_test_run_no_longer_stops_the_pipeline():
    assert _decide("Bash", {"command": "python -m pytest tests/"}) is not None


def test_a_command_it_cannot_account_for_still_asks():
    assert _decide("Bash", {"command": "curl -X POST http://127.0.0.1:8000/mcp"}) is None


def test_file_writes_are_not_auto_approved_by_default():
    """`CHAT_WORKSPACE_CLI_TOOLS` already lets an interactive chat turn write and
    deliberately does not extend that to stage runs. This ticket is about the
    prompts nobody reads, not about lowering that bar by default."""
    assert _decide("Write", {"file_path": "/repo/x.py"}) is None
    assert _decide("Edit", {"file_path": "/repo/x.py"}) is None


def test_a_workspace_can_opt_into_file_writes():
    policy = ApprovalPolicyConfig(file_writes=True)
    assert _decide("Write", {"file_path": "/repo/x.py"}, policy=policy) is not None


def test_a_workspace_can_opt_out_of_safe_commands():
    policy = ApprovalPolicyConfig(safe_commands=False)
    assert _decide("Bash", {"command": "python -m pytest tests/"}, policy=policy) is None


def test_a_missing_or_non_string_command_is_not_read_as_safe():
    """A request this cannot read is a request it must not approve."""
    assert _decide("Bash", {}) is None
    assert _decide("Bash", {"command": None}) is None
    assert _decide("Bash", {"command": ["python", "-m", "pytest"]}) is None


def test_a_run_that_already_auto_approves_keeps_its_own_decision(db_session: Session):
    """That path returns the enriched input; this policy exists for runs that
    would otherwise stop and ask, so it stands down rather than pre-empting it."""
    ticket = make_workspace_ticket(db_session, "policy-auto")
    policy = approval_policy(db_session, ticket.workspace_id, auto_approving=True)
    assert policy.safe_commands is False
    assert policy.file_writes is False


def test_an_unknown_workspace_falls_back_to_the_default_policy(db_session: Session):
    policy = approval_policy(db_session, "no-such-workspace", auto_approving=False)
    assert policy.safe_commands is True
    assert policy.file_writes is False
