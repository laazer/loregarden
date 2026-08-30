import os
import stat
import sys
import textwrap
from unittest.mock import patch

import pytest
from loregarden.models.domain import GateOutcome, Ticket, WorkflowStageDef, Workspace
from loregarden.services.gate_runner import (
    build_gate_context,
    format_gate_command,
    gates_can_run,
    run_gate_autofix,
    run_transition_gates,
    strip_ansi,
    transition_name,
)
from loregarden.services.orchestration_profile import GatesConfig, OrchestrationProfile
from sqlmodel import Session


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    """run_transition_gates reads the ticket's stored handoff to export it for the
    workspace gates; these fixtures' tickets have none, which is the normal
    no-handoff-yet path."""
    with Session(isolated_db) as db:
        yield db


def test_transition_name():
    assert transition_name("planning", "specification") == "planning_to_specification"


def test_format_gate_command_substitutes_context():
    cmd = format_gate_command(
        "echo {external_id} {transition}",
        {"external_id": "M57-01", "transition": "planning_to_spec"},
    )
    assert cmd == "echo M57-01 planning_to_spec"


def test_run_transition_gates_executes_script(session, tmp_path):
    script_dir = tmp_path / "ci" / "scripts"
    script_dir.mkdir(parents=True)
    script = script_dir / "run_workflow_transition_gates.py"
    script.write_text(
        textwrap.dedent(
            """\
            import sys
            if "--transition" in sys.argv:
                idx = sys.argv.index("--transition")
                print(sys.argv[idx + 1])
            sys.exit(0)
            """
        ),
        encoding="utf-8",
    )

    ws = Workspace(slug="demo", name="Demo", repo_path=str(tmp_path))
    ticket = Ticket(
        id="tid",
        external_id="M57-01",
        workspace_id="ws",
        title="Test",
    )
    profile = OrchestrationProfile(
        slug="demo",
        gates=GatesConfig(enabled=True),
    )

    result = run_transition_gates(
        session,
        profile,
        ws,
        ticket,
        from_stage="planning",
        to_stage="specification",
    )
    assert result.ok, result.message


def _write_transition_script(tmp_path, body: str):
    script_dir = tmp_path / "ci" / "scripts"
    script_dir.mkdir(parents=True, exist_ok=True)
    script = script_dir / "run_workflow_transition_gates.py"
    script.write_text(textwrap.dedent(body), encoding="utf-8")
    return script


def test_run_transition_gates_skips_transition_the_script_does_not_model(session, tmp_path):
    # A workspace gate script that rejects the transition NAME (argparse
    # `choices=` style: exit 2, "invalid choice" on stderr) means "no gate on
    # this edge" — the orchestrator must skip it, not wedge the workflow.
    _write_transition_script(
        tmp_path,
        """\
        import sys
        sys.stderr.write(
            "run_workflow_transition_gates.py: error: argument --transition: "
            "invalid choice: 'implementation_to_script_review'\\n"
        )
        sys.exit(2)
        """,
    )
    ws = Workspace(slug="demo", name="Demo", repo_path=str(tmp_path))
    ticket = Ticket(id="tid", external_id="M57-05", workspace_id="ws", title="Test")
    profile = OrchestrationProfile(slug="demo", gates=GatesConfig(enabled=True))

    result = run_transition_gates(
        session,
        profile,
        ws,
        ticket,
        from_stage="implementation",
        to_stage="script_review",
    )
    assert result.ok, result.message


def test_run_transition_gates_blocks_on_real_transition_gate_failure(session, tmp_path):
    # A gate that actually ran and FAILED (exit 1, no "invalid choice"/"unknown
    # transition" marker) must still block.
    _write_transition_script(
        tmp_path,
        """\
        import sys
        sys.stderr.write("handoff_validation_check FAIL: missing checkpoint\\n")
        sys.exit(1)
        """,
    )
    ws = Workspace(slug="demo", name="Demo", repo_path=str(tmp_path))
    ticket = Ticket(id="tid", external_id="M57-06", workspace_id="ws", title="Test")
    profile = OrchestrationProfile(slug="demo", gates=GatesConfig(enabled=True))

    result = run_transition_gates(
        session,
        profile,
        ws,
        ticket,
        from_stage="implementation",
        to_stage="static_qa",
    )
    assert not result.ok
    assert "FAIL" in result.message


def test_run_transition_gates_runs_profile_commands(session, tmp_path):
    gate_script = tmp_path / "gate.sh"
    gate_script.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    gate_script.chmod(gate_script.stat().st_mode | stat.S_IEXEC)

    ws = Workspace(slug="demo", name="Demo", repo_path=str(tmp_path))
    ticket = Ticket(id="tid", external_id="M57-02", workspace_id="ws", title="Test")
    profile = OrchestrationProfile(
        slug="demo",
        gates=GatesConfig(
            enabled=True,
            commands=["./gate.sh"],
        ),
    )

    result = run_transition_gates(
        session,
        profile,
        ws,
        ticket,
        from_stage="specification",
        to_stage="test_design",
    )
    assert result.ok, result.message


def test_run_transition_gates_blocks_on_failure(session, tmp_path):
    ws = Workspace(slug="demo", name="Demo", repo_path=str(tmp_path))
    ticket = Ticket(id="tid", external_id="M57-03", workspace_id="ws", title="Test")
    profile = OrchestrationProfile(
        slug="demo",
        gates=GatesConfig(
            enabled=True,
            commands=["false"],
        ),
    )

    result = run_transition_gates(
        session,
        profile,
        ws,
        ticket,
        from_stage="test_design",
        to_stage="test_break",
    )
    assert not result.ok


def test_run_transition_gates_includes_stage_gate_commands(session, tmp_path):
    marker = tmp_path / "marker.txt"
    ws = Workspace(slug="demo", name="Demo", repo_path=str(tmp_path))
    ticket = Ticket(id="tid", external_id="M57-04", workspace_id="ws", title="Test")
    profile = OrchestrationProfile(
        slug="demo",
        gates=GatesConfig(
            enabled=True,
            commands=["touch {workspace_root}/marker.txt"],
        ),
    )
    stage = WorkflowStageDef(
        key="implementation",
        name="Implementation",
        gate_commands=["touch {workspace_root}/stage-gate.txt"],
    )

    result = run_transition_gates(
        session,
        profile,
        ws,
        ticket,
        from_stage="implementation",
        to_stage="review",
        stage_def=stage,
    )
    assert result.ok, result.message
    assert marker.is_file()
    assert (tmp_path / "stage-gate.txt").is_file()


def test_build_gate_context():
    ws = Workspace(slug="blobert", name="Blobert", repo_path=".")
    ticket = Ticket(id="uuid-1", external_id="M12-01", workspace_id="ws", title="Feature")
    ctx = build_gate_context(
        workspace=ws,
        ticket=ticket,
        from_stage="planning",
        to_stage="specification",
    )
    assert ctx["external_id"] == "M12-01"
    assert ctx["transition"] == "planning_to_specification"


def test_strip_ansi_removes_escape_codes():
    assert strip_ansi("\x1b[31merror\x1b[0m: bad") == "error: bad"


def test_run_gate_autofix_runs_commands(session, tmp_path):
    ws = Workspace(slug="demo", name="Demo", repo_path=str(tmp_path))
    ticket = Ticket(id="tid", external_id="M57-07", workspace_id="ws", title="Test")
    profile = OrchestrationProfile(
        slug="demo",
        gates=GatesConfig(
            enabled=True,
            autofix_commands=["touch {workspace_root}/fixed.txt"],
        ),
    )

    result = run_gate_autofix(
        session,
        profile,
        ws,
        ticket,
        from_stage="implementation",
        to_stage="review",
    )
    assert result.ran
    assert len(result.commands) == 1
    assert (tmp_path / "fixed.txt").is_file()


def test_run_gate_autofix_noop_without_commands(session, tmp_path):
    ws = Workspace(slug="demo", name="Demo", repo_path=str(tmp_path))
    ticket = Ticket(id="tid", external_id="M57-08", workspace_id="ws", title="Test")
    profile = OrchestrationProfile(slug="demo", gates=GatesConfig(enabled=True))

    result = run_gate_autofix(
        session,
        profile,
        ws,
        ticket,
        from_stage="implementation",
        to_stage="review",
    )
    assert not result.ran
    assert result.commands == []


# --- Explicit outcome + preserved message (88-gate-outcomes-are-indistinguishable) ---
#
# Ticket 88: a passing gate and a gate that never ran both collapsed to the
# same empty-string result, so nothing downstream could tell them apart.
# GateRunResult now carries an explicit `outcome` — "passed" | "skipped" |
# "disabled" | "failed" — and `message` is never discarded just because
# `ok` is True.


def test_run_transition_gates_disabled_reports_disabled_outcome(session, tmp_path):
    ws = Workspace(slug="demo", name="Demo", repo_path=str(tmp_path))
    ticket = Ticket(id="tid", external_id="M88-01", workspace_id="ws", title="Test")
    profile = OrchestrationProfile(slug="demo", gates=GatesConfig(enabled=False))

    result = run_transition_gates(
        session, profile, ws, ticket, from_stage="spec", to_stage="test_design"
    )

    assert result.ok
    assert result.outcome == "disabled"
    assert result.message  # non-empty — distinguishable from "ran and passed"


def test_run_transition_gates_no_commands_reports_skipped_outcome(session, tmp_path):
    # gates.enabled is True but nothing is configured to run: this must be
    # reported as "skipped", never as "passed" — a config that gates nothing
    # is not the same as a gate that actually ran clean.
    ws = Workspace(slug="demo", name="Demo", repo_path=str(tmp_path))
    ticket = Ticket(id="tid", external_id="M88-02", workspace_id="ws", title="Test")
    profile = OrchestrationProfile(slug="demo", gates=GatesConfig(enabled=True))

    result = run_transition_gates(
        session, profile, ws, ticket, from_stage="test_design", to_stage="test_break"
    )

    assert result.ok
    assert result.outcome == "skipped"
    assert result.message == "no gate commands configured"


def test_run_transition_gates_undefined_transition_script_edge_reports_skipped(session, tmp_path):
    # The workspace transition script rejects the transition *name* (an edge
    # it doesn't model) and no other commands are configured — the whole
    # evaluation ran nothing, so it must be "skipped", not "passed".
    _write_transition_script(
        tmp_path,
        """\
        import sys
        sys.stderr.write(
            "run_workflow_transition_gates.py: error: argument --transition: "
            "invalid choice: 'a_to_b'\\n"
        )
        sys.exit(2)
        """,
    )
    ws = Workspace(slug="demo", name="Demo", repo_path=str(tmp_path))
    ticket = Ticket(id="tid", external_id="M88-03", workspace_id="ws", title="Test")
    profile = OrchestrationProfile(slug="demo", gates=GatesConfig(enabled=True))

    result = run_transition_gates(session, profile, ws, ticket, from_stage="a", to_stage="b")

    assert result.ok
    assert result.outcome == "skipped"


def test_run_transition_gates_passing_reports_passed_outcome_with_message(session, tmp_path):
    ws = Workspace(slug="demo", name="Demo", repo_path=str(tmp_path))
    ticket = Ticket(id="tid", external_id="M88-04", workspace_id="ws", title="Test")
    profile = OrchestrationProfile(slug="demo", gates=GatesConfig(enabled=True, commands=["true"]))

    result = run_transition_gates(
        session, profile, ws, ticket, from_stage="implementation", to_stage="review"
    )

    assert result.ok
    assert result.outcome == "passed"
    assert result.message == "passed 1 gate command(s)"


def test_run_transition_gates_failure_reports_failed_outcome_and_preserves_message(
    session, tmp_path
):
    ws = Workspace(slug="demo", name="Demo", repo_path=str(tmp_path))
    ticket = Ticket(id="tid", external_id="M88-05", workspace_id="ws", title="Test")
    profile = OrchestrationProfile(slug="demo", gates=GatesConfig(enabled=True, commands=["false"]))

    result = run_transition_gates(
        session, profile, ws, ticket, from_stage="test_design", to_stage="test_break"
    )

    assert not result.ok
    assert result.outcome == "failed"
    assert result.message


def test_run_transition_gates_missing_workspace_root_reports_failed_outcome(session):
    ws = Workspace(slug="demo", name="Demo", repo_path="/no/such/path-does-not-exist-anywhere")
    ticket = Ticket(id="tid", external_id="M88-06", workspace_id="ws", title="Test")
    profile = OrchestrationProfile(slug="demo", gates=GatesConfig(enabled=True, commands=["true"]))

    result = run_transition_gates(session, profile, ws, ticket, from_stage="a", to_stage="b")

    assert not result.ok
    assert result.outcome == "failed"
    assert result.message


# --- gates_can_run: does gates_enabled=true actually gate anything? ---


def test_gates_can_run_false_when_gates_disabled(tmp_path):
    ws = Workspace(slug="demo", name="Demo", repo_path=str(tmp_path))
    profile = OrchestrationProfile(slug="demo", gates=GatesConfig(enabled=False, commands=["true"]))
    assert gates_can_run(profile, ws) is False


def test_gates_can_run_false_when_enabled_but_nothing_configured(tmp_path):
    # The exact fail-open api/orchestration.py:57-59 reports as gates_enabled:
    # true — the Gates editor shows green for a config that gates nothing.
    ws = Workspace(slug="demo", name="Demo", repo_path=str(tmp_path))
    profile = OrchestrationProfile(slug="demo", gates=GatesConfig(enabled=True))
    assert gates_can_run(profile, ws) is False


def test_gates_can_run_true_when_commands_configured(tmp_path):
    ws = Workspace(slug="demo", name="Demo", repo_path=str(tmp_path))
    profile = OrchestrationProfile(slug="demo", gates=GatesConfig(enabled=True, commands=["true"]))
    assert gates_can_run(profile, ws) is True


def test_gates_can_run_true_when_transition_script_resolves_with_no_commands(tmp_path):
    _write_transition_script(tmp_path, "import sys\nsys.exit(0)\n")
    ws = Workspace(slug="demo", name="Demo", repo_path=str(tmp_path))
    profile = OrchestrationProfile(slug="demo", gates=GatesConfig(enabled=True))
    assert gates_can_run(profile, ws) is True


# --- Adversarial: blank/whitespace-only command entries ---
#
# `gates.commands` is a bare list[str]; nothing stops a Studio user (or a
# hand-edited profile YAML) from saving ["", "  "]. `bool(commands)` alone
# would call that "configured" when nothing real would run, and the current
# `_run_command` crashes outright on an empty command string:
# `shlex.split("")` is `[]`, and `subprocess.run([])` raises an unhandled
# IndexError before ever reaching the FileNotFoundError/TimeoutExpired
# handling — a blank command entry must not take down the whole evaluation.


def test_run_transition_gates_blank_and_whitespace_only_commands_do_not_crash(session, tmp_path):
    ws = Workspace(slug="demo", name="Demo", repo_path=str(tmp_path))
    ticket = Ticket(id="tid", external_id="M88-09", workspace_id="ws", title="Test")
    profile = OrchestrationProfile(
        slug="demo", gates=GatesConfig(enabled=True, commands=["", "   "])
    )

    result = run_transition_gates(
        session, profile, ws, ticket, from_stage="test_design", to_stage="test_break"
    )

    assert result.ok
    assert result.outcome == "skipped"


def test_gate_commands_do_not_inherit_an_ambient_git_dir(session, tmp_path):
    """GIT_DIR overrides `cwd`, so an inherited one aims a gate at another repo.

    Every transition gate resolves the scope it examines through git against the
    workspace it was handed. Under an ambient GIT_DIR — anything nested in a git
    hook, or a push from a worktree — git answers for that other repository
    instead: the gate finds nothing of this workspace, examines zero files and
    exits 0, which the orchestrator reads as a pass over unread work.
    """
    probe = tmp_path / "refuse_git_dir.py"
    probe.write_text("import os, sys\nsys.exit(1 if 'GIT_DIR' in os.environ else 0)\n")
    ws = Workspace(slug="demo", name="Demo", repo_path=str(tmp_path))
    ticket = Ticket(id="tid", external_id="M88-11", workspace_id="ws", title="Test")
    profile = OrchestrationProfile(
        slug="demo",
        gates=GatesConfig(enabled=True, commands=[f"{sys.executable} {probe}"]),
    )

    with patch.dict(os.environ, {"GIT_DIR": str(tmp_path / "elsewhere/.git")}):
        result = run_transition_gates(
            session, profile, ws, ticket, from_stage="implement", to_stage="review"
        )

    assert result.ok, result.message
    assert result.outcome == GateOutcome.PASSED


def test_gates_can_run_false_when_only_blank_commands_configured(tmp_path):
    ws = Workspace(slug="demo", name="Demo", repo_path=str(tmp_path))
    profile = OrchestrationProfile(
        slug="demo", gates=GatesConfig(enabled=True, commands=["", "   "])
    )
    assert gates_can_run(profile, ws) is False


# --- Adversarial: mutation/error-handling gaps beyond the happy-path contracts
# above. Each of these reproduces an unhandled crash or a miscount that a
# well-meaning `outcome` implementation could still leave in place. ---


def test_run_transition_gates_malformed_quoting_reports_unavailable_not_crash(session, tmp_path):
    # shlex.split raises ValueError on an unterminated quote. `_run_command`
    # today only catches FileNotFoundError/TimeoutExpired around
    # subprocess.run — the shlex.split call itself is unguarded, so one badly
    # quoted command entry (a typo'd Studio gate command) takes down the
    # entire evaluation with an unhandled exception instead of reporting a
    # normal outcome. It reports UNAVAILABLE rather than FAILED: a command that
    # cannot be parsed never ran, so there is nothing for the stage's agent to
    # fix and the orchestrator must not spend an agent attempt on it.
    ws = Workspace(slug="demo", name="Demo", repo_path=str(tmp_path))
    ticket = Ticket(id="tid", external_id="M88-10", workspace_id="ws", title="Test")
    profile = OrchestrationProfile(
        slug="demo", gates=GatesConfig(enabled=True, commands=["echo 'unterminated"])
    )

    result = run_transition_gates(
        session, profile, ws, ticket, from_stage="test_design", to_stage="test_break"
    )

    assert not result.ok
    assert result.outcome is GateOutcome.UNAVAILABLE
    assert result.message


def test_run_transition_gates_non_executable_script_reports_unavailable(session, tmp_path):
    # A gate command pointing at a file that exists but lacks the execute bit
    # (e.g. checked in without chmod +x, or edited on a filesystem that
    # dropped permissions) raises PermissionError from subprocess.run.
    # `_run_command` only catches FileNotFoundError — PermissionError is a
    # distinct OSError subclass and today propagates uncaught, crashing the
    # gate evaluation. Reported UNAVAILABLE, not FAILED — a missing execute bit
    # is a fact about the machine, not about the code under test.
    script = tmp_path / "noexec.sh"
    script.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    script.chmod(0o644)  # explicitly non-executable

    ws = Workspace(slug="demo", name="Demo", repo_path=str(tmp_path))
    ticket = Ticket(id="tid", external_id="M88-11", workspace_id="ws", title="Test")
    profile = OrchestrationProfile(
        slug="demo", gates=GatesConfig(enabled=True, commands=["./noexec.sh"])
    )

    result = run_transition_gates(
        session, profile, ws, ticket, from_stage="test_design", to_stage="test_break"
    )

    assert not result.ok
    assert result.outcome is GateOutcome.UNAVAILABLE
    assert result.message


def test_run_transition_gates_mixed_blank_and_real_commands_counts_only_real_ones(
    session, tmp_path
):
    # A blank entry must not be silently treated as "ran" — the passed-count
    # in the message exists specifically so an operator can tell "1 real gate
    # ran" from "1 real gate + N no-ops ran". Miscounting blanks as executed
    # gate commands would quietly inflate that number.
    ws = Workspace(slug="demo", name="Demo", repo_path=str(tmp_path))
    ticket = Ticket(id="tid", external_id="M88-12", workspace_id="ws", title="Test")
    profile = OrchestrationProfile(
        slug="demo", gates=GatesConfig(enabled=True, commands=["", "true", "   "])
    )

    result = run_transition_gates(
        session, profile, ws, ticket, from_stage="test_design", to_stage="test_break"
    )

    assert result.ok
    assert result.outcome == "passed"
    assert result.message == "passed 1 gate command(s)"


def test_run_transition_gates_skipped_transition_script_plus_passing_command_is_passed(
    session,
    tmp_path,
):
    # Combinatorial case: the workspace transition script rejects this edge
    # (an "unmodeled transition" skip) but a real profile gate command is
    # also configured and passes. The overall outcome must be "passed" —
    # driven by the command that actually ran — not "skipped", which would
    # hide that a real gate did run and pass.
    _write_transition_script(
        tmp_path,
        """\
        import sys
        sys.stderr.write(
            "run_workflow_transition_gates.py: error: argument --transition: "
            "invalid choice: 'a_to_b'\\n"
        )
        sys.exit(2)
        """,
    )
    ws = Workspace(slug="demo", name="Demo", repo_path=str(tmp_path))
    ticket = Ticket(id="tid", external_id="M88-13", workspace_id="ws", title="Test")
    profile = OrchestrationProfile(slug="demo", gates=GatesConfig(enabled=True, commands=["true"]))

    result = run_transition_gates(session, profile, ws, ticket, from_stage="a", to_stage="b")

    assert result.ok
    assert result.outcome == "passed"


def test_run_transition_gates_disabled_outcome_still_carries_stage_context(session, tmp_path):
    # The GATE_EVALUATED event payload is expected to carry from_stage/to_stage
    # (see test_gate_domain_events.py) on every evaluation including disabled
    # ones. If the disabled short-circuit in run_transition_gates returns
    # before building gate context, the caller has nothing to attach that
    # context from for the "disabled" case specifically — pin that the
    # from_stage/to_stage passed in are still recoverable off the result or
    # its context, not just for passed/skipped/failed outcomes.
    ws = Workspace(slug="demo", name="Demo", repo_path=str(tmp_path))
    ticket = Ticket(id="tid", external_id="M88-14", workspace_id="ws", title="Test")
    profile = OrchestrationProfile(slug="demo", gates=GatesConfig(enabled=False))

    result = run_transition_gates(
        session, profile, ws, ticket, from_stage="implementation", to_stage="review"
    )

    assert result.outcome == "disabled"
    context = build_gate_context(
        workspace=ws, ticket=ticket, from_stage="implementation", to_stage="review"
    )
    assert context["from_stage"] == "implementation"
    assert context["to_stage"] == "review"


def test_a_timed_out_gate_is_unavailable_not_failed(session, tmp_path):
    """The distinction that stops an hour-long loop.

    A gate reported FAILED is handed to the stage's own agent to fix, then the
    stage re-runs. `cd client && npx oxlint .` in a ticket worktree has no
    node_modules — node_modules is gitignored, so no worktree ever does — so npx
    tries to fetch oxlint and blows the 300s budget. Each cycle cost the timeout
    plus a full agent re-run of a stage that had already passed, and no agent
    can install a toolchain it cannot see.
    """
    ws = Workspace(slug="demo", name="Demo", repo_path=str(tmp_path))
    ticket = Ticket(id="tid", external_id="G-1", workspace_id="ws", title="Test")
    profile = OrchestrationProfile(
        slug="demo", gates=GatesConfig(enabled=True, commands=["sleep 5"])
    )

    with patch("loregarden.services.gate_runner.GATE_TIMEOUT_SECONDS", 1):
        result = run_transition_gates(session, profile, ws, ticket, from_stage="a", to_stage="b")

    assert result.ok is False
    assert result.outcome is GateOutcome.UNAVAILABLE
    assert "timed out" in result.message


def test_a_real_lint_failure_is_still_failed(session, tmp_path):
    """The other side of it: a gate that ran and rejected the code is FAILED,
    and must keep reaching the agent that can fix it."""
    ws = Workspace(slug="demo", name="Demo", repo_path=str(tmp_path))
    ticket = Ticket(id="tid", external_id="G-2", workspace_id="ws", title="Test")
    profile = OrchestrationProfile(
        slug="demo", gates=GatesConfig(enabled=True, commands=["sh -c 'echo nope; exit 1'"])
    )

    result = run_transition_gates(session, profile, ws, ticket, from_stage="a", to_stage="b")

    assert result.ok is False
    assert result.outcome is GateOutcome.FAILED
