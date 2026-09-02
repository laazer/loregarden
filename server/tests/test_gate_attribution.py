"""A gate failing on someone else's code is not this ticket's rework.

The orchestrator had one vocabulary for a stage that did not advance — "the
agent needs rework" — so a worktree-scoped gate reading another ticket's
uncommitted file sent the implementer an instruction to fix code it had never
written. On the blobert milestone 14 run that happened to eight faults of nine,
and ticket 22's implementer was rerouted three times after correctly diagnosing
it could not act.

The classification has three outcomes, not two, and that is the load-bearing
design decision rather than a hedge. The ticket's side of the comparison comes
from `changed_paths_json`, and 91% of succeeded runs record none. Reading an
empty side as "none of these are mine" would call every failure foreign and stop
rerouting real rework — worse than the bug being fixed, and it would look like
the feature working.
"""

import json

from loregarden.models.domain import Artifact, GateFaultAttribution, Ticket, WorkItemType
from loregarden.services.gate_attribution import (
    attribute_gate_failure,
    gate_command,
    gate_output_paths,
    partition_gate_output,
)
from loregarden.services.stage_retry_budget import count_gate_fix_attempts
from sqlmodel import select

# -- reading paths out of what the configured gates actually print -------------


def test_paths_come_out_of_the_formats_the_configured_gates_emit():
    """Six gate commands, three shapes — not five bespoke parsers.

    Three of the six are this repo's own scripts and share one `path:line:`
    prefix; `ruff check` and `oxlint` have native JSON; `ruff format --check`
    prints one fixed line per file.
    """
    assert gate_output_paths('[{"filename": "server/a.py", "code": "F401"}]') == {"server/a.py"}
    assert gate_output_paths("creature_store/store.py:320: class is 250 lines") == {
        "creature_store/store.py"
    }
    assert gate_output_paths("Would reformat: server/b.py") == {"server/b.py"}


def test_output_naming_nothing_readable_yields_no_paths():
    """Which the caller must read as "cannot say", never as "no paths involved"."""
    assert gate_output_paths("the gate exploded") == set()
    assert gate_output_paths("") == set()


def test_a_bare_note_is_not_mistaken_for_a_path():
    """`note: something` has the colon but no line number, and a false path here
    would land on the wrong side of the intersection."""
    assert gate_output_paths("note: something happened") == set()


# -- the classification --------------------------------------------------------


def test_the_ticket_22_shape_is_not_this_ticket_s_rework():
    """AC5. A worktree-scoped gate failing on an uncommitted file from another
    ticket, which is the incident this ticket exists for."""
    attribution, paths = attribute_gate_failure(
        gate_output="creature_store/store.py:320: class `Store` is 250 lines",
        ticket_paths={"server/loregarden/services/orchestration.py"},
    )

    assert attribution is GateFaultAttribution.FOREIGN
    assert paths == {"creature_store/store.py"}


def test_a_failure_on_the_ticket_s_own_code_still_reroutes():
    """AC4. The change must not make real rework unreachable."""
    attribution, _ = attribute_gate_failure(
        gate_output="server/loregarden/services/orchestration.py:12: `isinstance(...)`",
        ticket_paths={"server/loregarden/services/orchestration.py"},
    )

    assert attribution is GateFaultAttribution.TICKET


def test_a_ticket_recording_no_paths_is_unknown_not_foreign():
    """The case that would have shipped a silent regression.

    91% of succeeded runs record no changed paths. Treating an empty side as
    "none of these are mine" would divert every gate failure away from rework for
    those tickets.
    """
    attribution, _ = attribute_gate_failure(
        gate_output="creature_store/store.py:320: too long",
        ticket_paths=set(),
    )

    assert attribution is GateFaultAttribution.UNKNOWN


def test_unreadable_gate_output_is_unknown_not_foreign():
    """A gate nobody has taught this to parse must not divert anything."""
    attribution, _ = attribute_gate_failure(
        gate_output="Segmentation fault", ticket_paths={"server/a.py"}
    )

    assert attribution is GateFaultAttribution.UNKNOWN


def test_paths_match_across_the_directories_gates_run_from():
    """`ruff` runs in `server/`, `oxlint` in `client/`, the organization scripts
    at the repo root, while `changed_paths_json` is repo-relative. Comparing the
    tails avoids inventing a mapping between them.

    The cost is a same-named file in two directories reading as TICKET. That
    direction is chosen deliberately: a false TICKET keeps today's behaviour,
    while a false FOREIGN would swallow a real reroute.
    """
    attribution, _ = attribute_gate_failure(
        gate_output="loregarden/services/doctor.py:12: bad",
        ticket_paths={"server/loregarden/services/doctor.py"},
    )

    assert attribution is GateFaultAttribution.TICKET


# -- review findings -----------------------------------------------------------


def test_a_label_with_a_line_number_is_not_a_path():
    """`note:12: something` matched the first regex and yielded a path of "note".

    A false path is not cosmetic: it lands on one side of a set intersection, so
    it can turn a ticket's own failure into FOREIGN and block for a human.
    """
    assert gate_output_paths("note:12: something") == set()
    assert gate_output_paths("creature_store/store.py:320: real") == {"creature_store/store.py"}


def test_an_unrecorded_file_in_the_ticket_s_own_area_is_unknown_not_foreign():
    """Partial recording, which the first version left undefended.

    `changed_paths_json` is whatever the runs happened to record — empty 91% of
    the time, and incomplete some of the rest (a rename recorded under one name,
    a file a mechanical fixer touched, a hand-staged edit). Absence from that
    list is weak evidence, so FOREIGN also requires the failure to be somewhere
    the ticket is not working at all.
    """
    attribution, _ = attribute_gate_failure(
        gate_output="server/loregarden/services/b.py:9: bad",
        ticket_paths={"server/loregarden/services/a.py"},
    )

    assert attribution is GateFaultAttribution.UNKNOWN


def test_a_failure_in_a_tree_the_ticket_never_touches_is_still_foreign():
    """The incident this exists for: `creature_store/` against a ticket in `server/`."""
    attribution, _ = attribute_gate_failure(
        gate_output="creature_store/store.py:320: class is 250 lines",
        ticket_paths={"server/loregarden/services/orchestration.py"},
    )

    assert attribution is GateFaultAttribution.FOREIGN


def test_json_of_an_unmodelled_shape_yields_nothing_rather_than_raising():
    """A gate whose JSON this does not model must produce UNKNOWN, not a wrong
    answer — the records are modelled with pydantic rather than shape-sniffed."""
    assert gate_output_paths('["just", "strings"]') == set()
    assert gate_output_paths('{"unexpected": {"nested": 1}}') == set()


def test_the_partition_splits_a_mixed_failure_by_owner():
    """AC5. A failure naming one file the ticket wrote and one it did not is
    TICKET as a whole — and handing the agent both asks it to fix a file it does
    not own, which is the defect this ticket exists to close one level down."""
    output = (
        "server/loregarden/services/mine.py:10: something to fix\n"
        "asset_generation/python/src/store.py:320: `isinstance(..., dict)`\n"
        "(command: python3 py_organization_check.py --scope worktree)"
    )
    partition = partition_gate_output(
        gate_output=output, ticket_paths={"server/loregarden/services/mine.py"}
    )

    assert partition.attribution is GateFaultAttribution.TICKET
    assert partition.is_mixed
    assert partition.foreign_paths == frozenset({"asset_generation/python/src/store.py"})
    assert "mine.py" in partition.in_scope_detail
    assert "store.py" not in partition.in_scope_detail
    assert "store.py" in partition.foreign_detail
    assert "mine.py" not in partition.foreign_detail
    # The command carries no path, so it stays with the agent's own findings.
    assert "command:" in partition.in_scope_detail


def test_a_failure_that_is_entirely_the_ticket_s_own_is_not_mixed():
    partition = partition_gate_output(
        gate_output="server/mine.py:10: fix me", ticket_paths={"server/mine.py"}
    )
    assert partition.attribution is GateFaultAttribution.TICKET
    assert not partition.is_mixed
    assert partition.foreign_detail == ""


def test_the_exact_command_is_recovered_from_the_detail():
    """AC3. `clean_gate_detail` appends it, so the invocation that found the
    violation is reportable without threading the result object through."""
    assert (
        gate_command("boom (command: python3 py_organization_check.py --scope worktree)")
        == "python3 py_organization_check.py --scope worktree"
    )
    assert gate_command("no command here") == ""


def test_a_compact_json_report_still_partitions():
    """A single-line JSON report parses as its own line, so it splits like any
    other. Asserted because the reasoning that produced this function assumed it
    would not, and the assumption was wrong in the safe direction."""
    output = '[{"filename": "somebody/else.py"}]'
    partition = partition_gate_output(gate_output=output, ticket_paths={"server/mine.py"})
    assert partition.foreign_detail == output
    assert partition.in_scope_detail == ""


def test_a_pretty_printed_json_report_degrades_to_all_in_scope():
    """Split across lines, no single line parses as JSON, so no line is
    attributable and everything routes as it does today. That is the floor: a
    wrong split of a machine-readable report would drop real findings, while an
    unsplit one only declines to add the new behaviour."""
    output = '[\n  {\n    "filename": "somebody/else.py"\n  }\n]'
    partition = partition_gate_output(gate_output=output, ticket_paths={"server/mine.py"})
    assert partition.foreign_detail == ""
    assert "somebody/else.py" in partition.in_scope_detail


def test_a_foreign_gate_failure_leaves_the_ticket_running(db_session):
    """AC2 and AC7, reproducing blobert ticket 8028's shape.

    The first answer to a foreign failure (lg-workflow-integrity-452) blocked for
    a human. That was too narrow: FOREIGN means the violation sits where this
    ticket is not working, so the same uncommitted file stops *every* ticket
    crossing the gate. This asserts the ticket keeps running, keeps its budget,
    and that the debt is still written down with the command that found it.
    """
    from loregarden.models.domain import AgentRun, OrchestrationRun, TicketState, Workspace
    from loregarden.services.builtin_orchestrator import BuiltinOrchestrator, _GateDecision
    from loregarden.services.orchestration_profile import OrchestrationProfile

    workspace = Workspace(slug="esc", name="Esc", repo_path="/nonexistent/esc")
    db_session.add(workspace)
    db_session.commit()
    db_session.refresh(workspace)

    ticket = Ticket(
        external_id="esc-1",
        workspace_id=workspace.id,
        title="Godot shader work",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key="implement",
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)

    # The ticket touched shaders. The gate is complaining about Python.
    db_session.add(
        AgentRun(
            ticket_id=ticket.id,
            workspace_id=workspace.id,
            run_code="esc_impl",
            agent_id="engine_integration",
            stage_key="implement",
            changed_paths_json=json.dumps(["game/shaders/blend_shell.gdshader"]),
        )
    )
    orch_run = OrchestrationRun(ticket_id=ticket.id, workspace_id=workspace.id, run_code="esc_run")
    db_session.add(orch_run)
    db_session.commit()
    db_session.refresh(orch_run)

    detail = (
        "Python organization check failed:\n"
        " - asset_generation/python/src/store.py:320: `isinstance(..., dict)`\n"
        "(command: python3 py_organization_check.py --repo . --scope worktree)"
    )
    orchestrator = BuiltinOrchestrator(db_session)
    decision = orchestrator._decide_unfixed_gate_failure(
        ticket,
        None,
        [],
        orch_run,
        OrchestrationProfile(slug="esc"),
        "implement",
        detail,
    )
    db_session.refresh(ticket)

    assert decision is _GateDecision.PASS
    assert ticket.state is TicketState.IN_PROGRESS
    assert not (ticket.blocking_issues or "")
    assert count_gate_fix_attempts(db_session, ticket.id, "implement") == 0

    artifacts = db_session.exec(
        select(Artifact).where(Artifact.ticket_id == ticket.id, Artifact.kind == "error")
    ).all()
    assert len(artifacts) == 1
    body = artifacts[0].content_json
    assert "store.py" in body
    assert "py_organization_check.py" in body
