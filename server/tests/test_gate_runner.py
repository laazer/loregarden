import stat
import textwrap

from loregarden.models.domain import Ticket, WorkflowStageDef, Workspace
from loregarden.services.gate_runner import (
    build_gate_context,
    format_gate_command,
    run_gate_autofix,
    run_transition_gates,
    strip_ansi,
    transition_name,
)
from loregarden.services.orchestration_profile import GatesConfig, OrchestrationProfile


def test_transition_name():
    assert transition_name("planning", "specification") == "planning_to_specification"


def test_format_gate_command_substitutes_context():
    cmd = format_gate_command(
        "echo {external_id} {transition}",
        {"external_id": "M57-01", "transition": "planning_to_spec"},
    )
    assert cmd == "echo M57-01 planning_to_spec"


def test_run_transition_gates_executes_script(tmp_path):
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


def test_run_transition_gates_skips_transition_the_script_does_not_model(tmp_path):
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
        profile,
        ws,
        ticket,
        from_stage="implementation",
        to_stage="script_review",
    )
    assert result.ok, result.message


def test_run_transition_gates_blocks_on_real_transition_gate_failure(tmp_path):
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
        profile,
        ws,
        ticket,
        from_stage="implementation",
        to_stage="static_qa",
    )
    assert not result.ok
    assert "FAIL" in result.message


def test_run_transition_gates_runs_profile_commands(tmp_path):
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
        profile,
        ws,
        ticket,
        from_stage="specification",
        to_stage="test_design",
    )
    assert result.ok, result.message


def test_run_transition_gates_blocks_on_failure(tmp_path):
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
        profile,
        ws,
        ticket,
        from_stage="test_design",
        to_stage="test_break",
    )
    assert not result.ok


def test_run_transition_gates_includes_stage_gate_commands(tmp_path):
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


def test_run_gate_autofix_runs_commands(tmp_path):
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
        profile,
        ws,
        ticket,
        from_stage="implementation",
        to_stage="review",
    )
    assert result.ran
    assert len(result.commands) == 1
    assert (tmp_path / "fixed.txt").is_file()


def test_run_gate_autofix_noop_without_commands(tmp_path):
    ws = Workspace(slug="demo", name="Demo", repo_path=str(tmp_path))
    ticket = Ticket(id="tid", external_id="M57-08", workspace_id="ws", title="Test")
    profile = OrchestrationProfile(slug="demo", gates=GatesConfig(enabled=True))

    result = run_gate_autofix(
        profile,
        ws,
        ticket,
        from_stage="implementation",
        to_stage="review",
    )
    assert not result.ran
    assert result.commands == []
