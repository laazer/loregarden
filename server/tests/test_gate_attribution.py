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

from loregarden.models.domain import GateFaultAttribution, Ticket, WorkItemType
from loregarden.services.gate_attribution import attribute_gate_failure, gate_output_paths

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


def test_the_escalation_actually_runs(db_session):
    """The gap that let two Criticals ship.

    Every test above exercises the classifier. None reached the function the
    classifier triggers, so `_escalate_foreign_gate_failure` called both
    `record_blocking_issue` and `block_ticket` with argument shapes that do not
    exist and raised `TypeError` on every invocation — while the suite stayed
    green at 3694 passing. The ticket still ended up blocked, through the generic
    exception handler, carrying a traceback fragment instead of the attribution
    message this feature exists to produce.

    Calling it for real is the whole point of this test. It asserts the ticket is
    blocked and that the message names the offending path, because those are the
    two things the escalation is for.
    """
    from loregarden.models.domain import OrchestrationRun, TicketState, Workspace
    from loregarden.services.builtin_orchestrator import BuiltinOrchestrator

    workspace = Workspace(slug="esc", name="Esc", repo_path="/nonexistent/esc")
    db_session.add(workspace)
    db_session.commit()
    db_session.refresh(workspace)

    ticket = Ticket(
        external_id="esc-1",
        workspace_id=workspace.id,
        title="Escalation",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key="implement",
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)

    orch_run = OrchestrationRun(ticket_id=ticket.id, workspace_id=workspace.id, run_code="esc_run")
    db_session.add(orch_run)
    db_session.commit()
    db_session.refresh(orch_run)

    BuiltinOrchestrator(db_session)._escalate_foreign_gate_failure(
        ticket,
        None,
        [],
        orch_run,
        "implement",
        "creature_store/store.py:320: class `Store` is 250 lines",
        {"creature_store/store.py"},
    )
    db_session.refresh(ticket)

    assert ticket.state is TicketState.BLOCKED
    assert "creature_store/store.py" in ticket.blocking_issues
