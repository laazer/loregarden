import stat
import textwrap

from loregarden.models.domain import Ticket, WorkflowStageDef, Workspace
from loregarden.services.gate_runner import (
    build_gate_context,
    format_gate_command,
    run_transition_gates,
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
