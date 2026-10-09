"""A reattached run is resumed, not merely watched (spec S6-S7; AC11-AC16).

`reattach_surviving_runs` already adopts a surviving run and renews its lease.
That is not a resume: nothing reads the detached agent's exit status or its
`<<<LOREGARDEN_STAGE_REPORT>>>`, so the stage never completes, commits or
routes — it settles at the lease boundary as un-supervised. 82 runs in the live
database failed with "interrupted before completion (server reload…)"; this is
the population the ticket converts into survivors.

`resupervise` is the daemon thread that finishes the job: drain the output file
from the stored offset, renew while the process lives, and on GONE settle
through `complete_run` — the same floor `execute()` and `finish_external_stage`
already converge on. No real server restart is needed to test it: a faked GONE
pid and pre-written output files are the whole input.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from unittest import mock
from uuid import uuid4

import pytest
from loregarden.config import settings
from loregarden.models.domain import (
    AgentRun,
    AgentTransport,
    Artifact,
    ProcessState,
    RunLogLine,
    RunStatus,
    StageStatus,
    Ticket,
    TicketState,
    WorkflowInstance,
    WorkflowStageDef,
    WorkflowTemplate,
    WorkItemType,
    Workspace,
)
from loregarden.services import external_harness, run_reattach, run_resupervise
from loregarden.services.git_subprocess import run_git
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.run_output_files import paths_for
from loregarden.services.run_resupervise import resupervise
from loregarden.services.workflow_state import initial_stages_json, set_stage_status
from sqlmodel import Session, select

IMPLEMENT = "implement"
RUN_CODE = "run_resup1"
#: The verbatim sentence an operator reads when the agent left no exit code.
NO_RC_STDERR = (
    "Agent exited without recording an exit code; its run is reported failed "
    "rather than assumed complete."
)


def _report(status: str = "pass") -> str:
    return (
        "<<<LOREGARDEN_STAGE_REPORT>>>\n"
        + json.dumps({"status": status, "confidence": 0.9})
        + "\n<<<END_STAGE_REPORT>>>\n"
    )


def _throwaway_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    run_git(["init", "-b", "main"], cwd=root, check=True, capture_output=True)
    run_git(["config", "user.email", "test@example.com"], cwd=root, check=True, capture_output=True)
    run_git(["config", "user.name", "Test"], cwd=root, check=True, capture_output=True)
    (root / "README.md").write_text("# resupervise\n", encoding="utf-8")
    run_git(["add", "."], cwd=root, check=True, capture_output=True)
    run_git(["commit", "-m", "init"], cwd=root, check=True, capture_output=True)
    return root


@pytest.fixture(name="run_log_dir", autouse=True)
def run_log_dir_fixture(tmp_path, monkeypatch) -> Path:
    target = tmp_path / "run-logs"
    target.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "run_log_dir", target)
    return target


@pytest.fixture(name="adopted")
def adopted_fixture(db_session: Session, tmp_path) -> AgentRun:
    """A RUNNING run on an `implement` stage, as a boot-time reattach finds it."""
    workspace = Workspace(
        slug=f"resup-{uuid4()}",
        name="Resupervise",
        repo_path=str(_throwaway_repo(tmp_path / "resup-repo")),
    )
    db_session.add(workspace)
    db_session.commit()
    db_session.refresh(workspace)

    stages = [
        WorkflowStageDef(key=IMPLEMENT, name="Implement", order=1, agent_id="backend_implementer"),
        WorkflowStageDef(key="verify", name="Verify", order=2, agent_id="static_qa"),
        WorkflowStageDef(key="done", name="Done", order=3, terminal=True),
    ]
    template = WorkflowTemplate(
        slug=f"resup-tpl-{uuid4()}",
        name="Resupervise template",
        stages_json=json.dumps([s.model_dump(mode="json") for s in stages]),
        transitions_json=json.dumps(
            [
                {"from": IMPLEMENT, "to": "verify", "when": "pass"},
                {"from": IMPLEMENT, "to": IMPLEMENT, "when": "reject"},
            ]
        ),
    )
    db_session.add(template)
    db_session.commit()
    db_session.refresh(template)

    ticket = Ticket(
        external_id=f"resup-{uuid4()}",
        workspace_id=workspace.id,
        title="A run that outlived its server",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key=IMPLEMENT,
        workflow_stage_status=StageStatus.RUNNING,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)
    instance = WorkflowInstance(
        ticket_id=ticket.id,
        template_id=template.id,
        current_stage_key=IMPLEMENT,
        stages_json=initial_stages_json(stages),
    )
    # The stage the surviving run belongs to is RUNNING on the instance too, not
    # just on the ticket. `initial_stages_json` marks every stage pending, so a
    # fixture that stopped there describes a ticket nobody has started — and the
    # cursor assertions are read off this map, not off the ticket column.
    set_stage_status(ticket, instance, stages, IMPLEMENT, StageStatus.RUNNING)
    db_session.add(instance)

    run = AgentRun(
        workspace_id=workspace.id,
        ticket_id=ticket.id,
        run_code=RUN_CODE,
        agent_id="backend_implementer",
        stage_key=IMPLEMENT,
        status=RunStatus.RUNNING,
        agent_pid=4242,
        agent_pid_identity="a start time no later pid can wear",
        agent_transport=AgentTransport.FILE,
        external_harness=None,
        start_dirty_paths_json=json.dumps(["server/loregarden/", "client/src/"]),
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)
    return run


def _write_output(run: AgentRun, *, out: str, rc: str | None = "0", err: str = "") -> None:
    paths = paths_for(run.run_code, run.id)
    paths.out.write_text(out, encoding="utf-8")
    if err:
        paths.err.write_text(err, encoding="utf-8")
    if rc is not None:
        paths.rc.write_text(rc, encoding="utf-8")


def _gone(*_args, **_kwargs) -> ProcessState:
    """The reading that ends the loop: `ps` says nothing holds the pid."""
    return ProcessState.GONE


def _log_lines(db_session: Session, run: AgentRun) -> list[str]:
    rows = db_session.exec(
        select(RunLogLine).where(RunLogLine.run_id == run.id).order_by(RunLogLine.seq)
    ).all()
    return [row.text for row in rows]


def _stage_status(db_session: Session, ticket_id: str) -> StageStatus:
    return _stage_status_of(db_session, ticket_id, IMPLEMENT)


def _stage_status_of(db_session: Session, ticket_id: str, stage_key: str) -> StageStatus:
    ticket = db_session.get(Ticket, ticket_id)
    assert ticket
    return OrchestrationService(db_session).stage_status(ticket, stage_key)


# --- AC11: reattach starts a resume, not a watch -----------------------------


def test_reattach_starts_resupervise_for_every_adopted_run(db_session: Session, adopted):
    """AC11. The adopted run gets a resume, not a lease renewer.

    Patched on `run_resupervise` rather than on `run_reattach`: the import moved
    into `_supervise` so that importing the boot reaper does not pull
    `OrchestrationService` onto the boot path (AC11's "`run_reattach` keeps only
    its predicates"). The assertion is unchanged — one resume per adopted run,
    with this run's id — only the seam it is observed through moved with the
    name.
    """
    with (
        mock.patch.object(run_reattach, "liveness", return_value=ProcessState.ALIVE),
        mock.patch.object(run_resupervise, "resupervise") as started,
    ):
        run_reattach.reattach_surviving_runs(db_session, interval_seconds=0.01)
        # It is started on a daemon thread, so `start()` returning does not mean
        # the target has been entered. Waited for inside the patch window —
        # outside it the real `resupervise` would run against a live row.
        deadline = time.monotonic() + 10
        while started.call_count == 0 and time.monotonic() < deadline:
            time.sleep(0.01)

    assert started.call_count == 1
    assert (
        started.call_args.args[0] == adopted.id
        or started.call_args.kwargs.get("run_id") == adopted.id
    )


def test_the_watcher_no_longer_lives_in_run_reattach():
    """AC11. `_watch` MOVES; `run_reattach` keeps only its predicates.

    Leaving it behind would put `OrchestrationService` imports behind the boot
    reaper's predicates, dragging orchestration onto the boot path.
    """
    assert not hasattr(run_reattach, "_watch")
    assert hasattr(run_reattach, "surviving_runs")
    assert hasattr(run_reattach, "runs_to_spare")


# --- AC12: the reattached run settles ----------------------------------------


def test_a_reattached_run_whose_agent_exited_zero_succeeds(db_session: Session, adopted):
    """AC12. `.rc` decides the status."""
    _write_output(adopted, out="work happened\n" + _report("pass"), rc="0")

    with mock.patch.object(run_resupervise, "liveness", _gone):
        resupervise(adopted.id, interval_seconds=0)

    db_session.expire_all()
    assert db_session.get(AgentRun, adopted.id).status is RunStatus.SUCCEEDED


def test_a_reattached_run_whose_agent_exited_non_zero_fails(db_session: Session, adopted):
    _write_output(adopted, out="it broke\n", rc="2", err="Traceback …\n")

    with mock.patch.object(run_resupervise, "liveness", _gone):
        resupervise(adopted.id, interval_seconds=0)

    db_session.expire_all()
    assert db_session.get(AgentRun, adopted.id).status is RunStatus.FAILED


def test_a_missing_exit_code_fails_the_run_and_says_why(db_session: Session, adopted):
    """AC12. Reported failed rather than assumed complete, in these words.

    This is the honest outcome of tmux's own server dying, and the reason tmux
    is sequenced last: a run with no `.rc` cannot be claimed to have succeeded.
    """
    _write_output(adopted, out="said some things\n", rc=None)

    with mock.patch.object(run_resupervise, "liveness", _gone):
        resupervise(adopted.id, interval_seconds=0)

    db_session.expire_all()
    settled = db_session.get(AgentRun, adopted.id)
    assert settled.status is RunStatus.FAILED
    assert NO_RC_STDERR in (settled.stderr or "")


def test_an_unparseable_exit_code_fails_the_run_the_same_way(db_session: Session, adopted):
    """A truncated `.rc` is not a zero."""
    _write_output(adopted, out="said some things\n", rc="")

    with mock.patch.object(run_resupervise, "liveness", _gone):
        resupervise(adopted.id, interval_seconds=0)

    db_session.expire_all()
    settled = db_session.get(AgentRun, adopted.id)
    assert settled.status is RunStatus.FAILED
    assert NO_RC_STDERR in (settled.stderr or "")


def test_the_stage_cursor_moves_when_a_reattached_run_passes(db_session: Session, adopted):
    """AC12, asserted on the STAGE CURSOR rather than on log text.

    The whole point of gap #2: today the stage never completes, commits or
    routes, so the ticket sits on `implement` RUNNING with nothing alive behind
    it, until the lease boundary settles it as un-supervised.

    The cursor is the (stage, status) pair, and a pass leaves it on the finished
    stage reading `done` — it is the next DISPATCH that moves `workflow_stage_key`
    on, not completion (`tests/test_cursor_move_status.py` is the record of why:
    a cursor move that implied a status once fabricated a completed stage). This
    test asserted `workflow_stage_key == "verify"` when it was written, which
    `execute()` does not produce either: both paths converge on `complete_run`,
    which is AC13. So what is asserted is that the stage really finished and the
    next one is now the one waiting to run.
    """
    ticket_id = adopted.ticket_id
    assert _stage_status(db_session, ticket_id) is StageStatus.RUNNING
    _write_output(adopted, out="done\n" + _report("pass"), rc="0")

    with mock.patch.object(run_resupervise, "liveness", _gone):
        resupervise(adopted.id, interval_seconds=0)

    db_session.expire_all()
    assert _stage_status(db_session, ticket_id) is StageStatus.DONE
    ticket = db_session.get(Ticket, ticket_id)
    assert ticket.workflow_stage_status is StageStatus.DONE
    assert _stage_status_of(db_session, ticket_id, "verify") is StageStatus.PENDING


def test_the_stage_report_from_the_file_is_what_routes(db_session: Session, adopted):
    """A reject in the detached transcript must route as a reject.

    The report is the last thing the agent writes, so this also proves the
    final drain happened before settlement rather than after it.
    """
    ticket_id = adopted.ticket_id
    _write_output(adopted, out="not good enough\n" + _report("needs_rework"), rc="0")

    with mock.patch.object(run_resupervise, "liveness", _gone):
        resupervise(adopted.id, interval_seconds=0)

    db_session.expire_all()
    assert db_session.get(Ticket, ticket_id).workflow_stage_key == IMPLEMENT


def test_post_restart_output_reaches_the_log_with_no_gap_and_no_duplication(
    db_session: Session, adopted
):
    """AC12. The offset is what makes this a resume rather than a replay."""
    already_seen = "before the restart\n"
    _write_output(
        adopted,
        out=already_seen + "after the restart\nand more\n" + _report("pass"),
        rc="0",
    )
    db_session.add(
        Artifact(
            ticket_id=adopted.ticket_id,
            run_id=adopted.id,
            kind="log",
            title=f"Run {RUN_CODE}",
            content_json=json.dumps(
                {"live": None, "storage": "rows", "tail_offset": len(already_seen.encode("utf-8"))}
            ),
        )
    )
    db_session.commit()

    with mock.patch.object(run_resupervise, "liveness", _gone):
        resupervise(adopted.id, interval_seconds=0)

    db_session.expire_all()
    texts = _log_lines(db_session, adopted)
    assert "before the restart" not in texts, "a line already persisted was ingested twice"
    assert "after the restart" in texts
    assert "and more" in texts


def test_the_resume_point_is_announced_as_one_sys_line(db_session: Session, adopted):
    """AC29. A line, not a banner: a 6,451-minute run can survive two restarts
    and a banner can only describe one. Appended BEFORE the first drain."""
    seen = "earlier output\n"
    _write_output(adopted, out=seen + "later output\n" + _report("pass"), rc="0")
    offset = len(seen.encode("utf-8"))
    db_session.add(
        Artifact(
            ticket_id=adopted.ticket_id,
            run_id=adopted.id,
            kind="log",
            title=f"Run {RUN_CODE}",
            content_json=json.dumps({"live": None, "storage": "rows", "tail_offset": offset}),
        )
    )
    db_session.commit()

    with mock.patch.object(run_resupervise, "liveness", _gone):
        resupervise(adopted.id, interval_seconds=0)

    db_session.expire_all()
    texts = _log_lines(db_session, adopted)
    expected = f"reattached · control-plane restart, resumed at byte {offset}"
    assert expected in texts
    assert texts.index(expected) < texts.index("later output"), (
        "the marker has to sit at the byte it resumed from, not after the drain"
    )


def test_the_resume_marker_is_tagged_sys(db_session: Session, adopted):
    """AC29. `SYS` already maps to the `info` variant, so no new tag and no CSS."""
    _write_output(adopted, out="x\n" + _report("pass"), rc="0")

    with mock.patch.object(run_resupervise, "liveness", _gone):
        resupervise(adopted.id, interval_seconds=0)

    db_session.expire_all()
    rows = db_session.exec(select(RunLogLine).where(RunLogLine.run_id == adopted.id)).all()
    reattached = [row for row in rows if row.text.startswith("reattached · ")]
    assert reattached, "no resume marker was written"
    assert {row.tag for row in reattached} == {"SYS"}


def test_an_unterminated_last_line_survives_the_final_drain(db_session: Session, adopted):
    """AC5's reattached twin: `flush_partial` once, after the writer is gone."""
    _write_output(adopted, out="complete\nno trailing newline", rc="0")

    with mock.patch.object(run_resupervise, "liveness", _gone):
        resupervise(adopted.id, interval_seconds=0)

    db_session.expire_all()
    assert "no trailing newline" in _log_lines(db_session, adopted)


def test_a_reading_that_cannot_answer_does_not_settle_the_run(db_session: Session, adopted):
    """AC12/lg-run-durability-862. An unanswered `ps` is not a death.

    Settling on UNKNOWN is the reaper killing a working agent's row — the
    defect 862 fixed, re-reachable here because this loop also settles.
    """
    _write_output(adopted, out="still going\n", rc=None)
    readings = iter([ProcessState.UNKNOWN, ProcessState.ALIVE, ProcessState.GONE])

    with mock.patch.object(run_resupervise, "liveness", side_effect=lambda *_: next(readings)):
        resupervise(adopted.id, interval_seconds=0)

    # It only left the loop on GONE — the two readings before it did not settle.
    assert list(readings) == []


def test_the_lease_is_renewed_while_the_process_lives(db_session: Session, adopted):
    """AC11's inherited half: without renewal the adopted run is reaped at the
    lease boundary by the very sweep this ticket keeps it away from."""
    _write_output(adopted, out="working\n", rc="0")
    readings = iter([ProcessState.ALIVE, ProcessState.ALIVE, ProcessState.GONE])

    with (
        mock.patch.object(run_resupervise, "liveness", side_effect=lambda *_: next(readings)),
        mock.patch.object(run_resupervise, "renew_agent_run_lease") as renew,
    ):
        resupervise(adopted.id, interval_seconds=0)

    assert renew.call_count == 2


def test_renewal_is_skipped_on_a_reading_that_cannot_answer(db_session: Session, adopted):
    """Renewing on UNKNOWN would vouch for a process nothing vouched for."""
    _write_output(adopted, out="working\n", rc="0")
    readings = iter([ProcessState.UNKNOWN, ProcessState.ALIVE, ProcessState.GONE])

    with (
        mock.patch.object(run_resupervise, "liveness", side_effect=lambda *_: next(readings)),
        mock.patch.object(run_resupervise, "renew_agent_run_lease") as renew,
    ):
        resupervise(adopted.id, interval_seconds=0)

    assert renew.call_count == 1


def test_the_beat_skips_on_unknown_and_stops_only_on_gone(db_session: Session, adopted):
    """A transient `ps` failure mid-supervision must not end renewal for good.

    Moved here with the watcher it tests (AC11): this was
    `test_run_reattach.py::test_the_watch_skips_a_beat_on_unknown_and_stops_only_on_gone`
    against `run_reattach._watch`, which is now `_supervise_until_gone`. The
    assertion is unchanged — four readings, two of them ALIVE, two renewals.
    """
    _write_output(adopted, out="working\n", rc="0")
    readings = iter(
        [ProcessState.ALIVE, ProcessState.UNKNOWN, ProcessState.ALIVE, ProcessState.GONE]
    )

    with (
        mock.patch.object(run_resupervise, "liveness", side_effect=lambda *_: next(readings)),
        mock.patch.object(run_resupervise, "renew_agent_run_lease") as renew,
    ):
        resupervise(adopted.id, interval_seconds=0)

    assert renew.call_count == 2


# --- AC13: one settlement path -----------------------------------------------


def test_resupervise_never_calls_finish_external_stage(db_session: Session, adopted):
    """AC13. An adopted run has `external_harness=None` and keeps it.

    Routing a local run through the external protocol is a second settlement
    path with its own bugs; the floor is `complete_run`, which `execute()` and
    `finish_external_stage` both already converge on.
    """
    _write_output(adopted, out="done\n" + _report("pass"), rc="0")

    with (
        mock.patch.object(run_resupervise, "liveness", _gone),
        mock.patch.object(external_harness, "finish_external_stage") as external,
    ):
        resupervise(adopted.id, interval_seconds=0)

    external.assert_not_called()
    db_session.expire_all()
    assert db_session.get(AgentRun, adopted.id).external_harness is None


def test_the_member_results_helper_has_one_home(db_session: Session):
    """AC13. Lifted into `services.parallel_stage`, which already owns
    `reconcile_parallel_stage`; `finish_external_stage` imports it from there."""
    from loregarden.services import parallel_stage

    assert hasattr(parallel_stage, "member_runs_and_results")
    assert not hasattr(external_harness, "_member_runs_and_results"), (
        "a second copy of the parallel-member helper"
    )


# --- AC14: settle once --------------------------------------------------------


def test_a_second_settlement_is_refused(db_session: Session, adopted):
    """AC14, in the same test that proves the first.

    Two settlements means two commits of the whole working tree into one
    checkout — and the original server coming back is exactly how a second
    `resupervise` gets started.
    """
    _write_output(adopted, out="done\n" + _report("pass"), rc="0")

    with mock.patch.object(run_resupervise, "liveness", _gone):
        resupervise(adopted.id, interval_seconds=0)
    db_session.expire_all()
    first = db_session.get(AgentRun, adopted.id)
    assert first.status is RunStatus.SUCCEEDED
    finished_at = first.finished_at

    with (
        mock.patch.object(run_resupervise, "liveness", _gone),
        mock.patch.object(OrchestrationService, "complete_run") as completed,
    ):
        resupervise(adopted.id, interval_seconds=0)

    completed.assert_not_called()
    db_session.expire_all()
    assert db_session.get(AgentRun, adopted.id).finished_at == finished_at


def test_a_run_already_terminal_before_the_loop_starts_is_not_settled(db_session: Session, adopted):
    """The status is re-read inside the settling transaction, not trusted from
    whatever the thread loaded minutes earlier."""
    _write_output(adopted, out="done\n" + _report("pass"), rc="0")
    adopted.status = RunStatus.CANCELLED
    db_session.add(adopted)
    db_session.commit()

    with (
        mock.patch.object(run_resupervise, "liveness", _gone),
        mock.patch.object(OrchestrationService, "complete_run") as completed,
    ):
        resupervise(adopted.id, interval_seconds=0)

    completed.assert_not_called()
    db_session.expire_all()
    assert db_session.get(AgentRun, adopted.id).status is RunStatus.CANCELLED


# --- AC15/AC16: evidence and the lease exemption ------------------------------


def test_a_reattached_run_records_its_git_evidence(db_session: Session, adopted):
    """AC15. Read off the row, so a process that never saw the live tree can
    still say what the run inherited."""
    _write_output(adopted, out="edited things\n" + _report("pass"), rc="0")

    with mock.patch.object(run_resupervise, "liveness", _gone):
        resupervise(adopted.id, interval_seconds=0)

    db_session.expire_all()
    settled = db_session.get(AgentRun, adopted.id)
    assert settled.changed_paths_recorded_at is not None, (
        "no record of what this run touched — the column's empty value means "
        "'cannot say', which is what AC15 exists to prevent"
    )


def test_the_lease_exemption_is_logged_rather_than_silent(db_session: Session, adopted, caplog):
    """AC16. Re-acquiring would queue a run behind work it is already ahead of —
    but a silent exemption is not acceptable, matching `external_harness`'s
    existing 'no queue' rule."""
    _write_output(adopted, out="done\n" + _report("pass"), rc="0")

    with (
        caplog.at_level(logging.INFO, logger="loregarden.services.run_resupervise"),
        mock.patch.object(run_resupervise, "liveness", _gone),
    ):
        resupervise(adopted.id, interval_seconds=0)

    messages = [record.getMessage().lower() for record in caplog.records]
    assert any("exempt" in message for message in messages), messages


def test_an_adopted_run_does_not_re_acquire_the_host_capacity_lease(db_session: Session, adopted):
    """AC16. The run is already ahead of the queue it would be joining."""
    _write_output(adopted, out="done\n" + _report("pass"), rc="0")

    with (
        mock.patch.object(run_resupervise, "liveness", _gone),
        mock.patch(
            "loregarden.services.capacity_run.acquire", side_effect=AssertionError
        ) as reserved,
    ):
        resupervise(adopted.id, interval_seconds=0)

    reserved.assert_not_called()


# --- S6.8: the files are cleaned up ------------------------------------------


def test_the_output_files_are_deleted_once_the_run_is_settled(db_session: Session, adopted):
    """Their content is durable in `run_log_lines` by now. Orphans are swept at
    boot by the existing `reconcile_worktrees`; there is no second policy."""
    _write_output(adopted, out="done\n" + _report("pass"), rc="0", err="a warning\n")
    paths = paths_for(adopted.run_code, adopted.id)
    assert paths.out.exists()

    with mock.patch.object(run_resupervise, "liveness", _gone):
        resupervise(adopted.id, interval_seconds=0)

    assert not paths.out.exists()
    assert not paths.err.exists()
    assert not paths.rc.exists()


def test_a_run_whose_output_file_is_missing_still_settles(db_session: Session, adopted):
    """A pre-change run has no file at all. "Nothing to tail" must not mean
    "hang forever" — the row still has to reach a terminal status."""
    with mock.patch.object(run_resupervise, "liveness", _gone):
        resupervise(adopted.id, interval_seconds=0)

    db_session.expire_all()
    assert db_session.get(AgentRun, adopted.id).status is RunStatus.FAILED


def test_the_process_identity_is_what_the_loop_asks_about(db_session: Session, adopted):
    """The pid alone is not evidence: `liveness` is handed the recorded
    fingerprint too, so a reused pid cannot keep an adopted run alive."""
    _write_output(adopted, out="done\n" + _report("pass"), rc="0")
    seen: list[tuple] = []

    def _record(*args, **kwargs):
        seen.append((args, kwargs))
        return ProcessState.GONE

    with mock.patch.object(run_resupervise, "liveness", _record):
        resupervise(adopted.id, interval_seconds=0)

    assert seen, "the loop never asked whether the process was alive"
    flat = [value for args, kwargs in seen for value in (*args, *kwargs.values())]
    assert 4242 in flat
    assert "a start time no later pid can wear" in flat


# --- AC7 / AC12: the partial the reattached path drops ------------------------
#
# Added at `test-break`. Every reattach test above ends its `.out` with a line
# that forces the stream buffer to flush — a stage report, a plain line, an
# unterminated remainder — so the buffer is always empty by the time settlement
# runs and the divergence below cannot show. A `content_block_delta` arriving
# LAST is the case that exposes it, and it is the normal shape of a run killed
# mid-sentence: the restart boundary this ticket exists for.


def _delta(text: str) -> str:
    """One partial-message `stream_event`, as the adapters emit them.

    Short on purpose: `_maybe_chunk_flush` only promotes a buffer to a row at
    CHUNK_FLUSH_CHARS, so anything under that stays buffered and reaches
    `run_log_lines` only through `finalize`'s opening flush.
    """
    return json.dumps({"type": "content_block_delta", "delta": {"text": text}})


TRAILING_PARTIAL = "the last thing the agent managed to say"


def test_an_unflushed_stream_partial_reaches_the_log_on_the_reattached_path(
    db_session: Session, adopted
):
    """AC7 ("partials reach run_log_lines identically across the restart
    boundary") and AC12 ("no duplication and no gap").

    RED at 39abcc99. `_finalize_log` builds a SECOND `RunLogStreamer` for this
    run and calls `_hydrate()`, which sets `_stream_buffer = ""`.
    `RunLogStreamer.finalize` opens with `_flush_stream_buffer(force=True)`, so
    the text the loop had buffered is flushed on the live path — where `cli.py`
    finalizes the same streamer the loop fed — and dropped here. The streamer
    `resupervise` already holds is threaded through `_supervise_until_gone` and
    `_drain_to_end` and then discarded at `_settle`.
    """
    _write_output(adopted, out=_report("pass") + _delta(TRAILING_PARTIAL) + "\n", rc="0")

    with mock.patch.object(run_resupervise, "liveness", _gone):
        resupervise(adopted.id, interval_seconds=0)

    db_session.expire_all()
    texts = _log_lines(db_session, adopted)
    assert any(TRAILING_PARTIAL in text for text in texts), (
        "the buffered partial was lost between the loop's streamer and the one "
        f"settlement built; rows were {texts}"
    )


def test_the_live_path_keeps_the_same_partial(db_session: Session, adopted):
    """The control for the test above, so its failure reads as a divergence
    between the two paths rather than as a property neither path has.

    This is the live shape: one streamer, fed by the loop and finalized by
    `cli.py`. GREEN at 39abcc99.
    """
    from loregarden.services.run_log_stream import RunLogStreamer

    streamer = RunLogStreamer(
        run_id=adopted.id,
        ticket_id=adopted.ticket_id,
        run_code=adopted.run_code,
        agent_id=adopted.agent_id,
        skill_name="",
    )
    streamer.append_stream_line(_delta(TRAILING_PARTIAL))
    streamer.finalize(status=RunStatus.SUCCEEDED, stderr="")

    db_session.expire_all()
    texts = _log_lines(db_session, adopted)
    assert any(TRAILING_PARTIAL in text for text in texts), texts


def test_one_run_gets_one_log_streamer_through_settlement(db_session: Session, adopted):
    """AC7/AC12, as the invariant rather than as the symptom.

    One run's log is mutable state — a buffer, a live line, a sequence cursor
    and an offset. Two instances of it means two answers, and `_hydrate()`
    reconstructs only the half that is durable. Pinning the count is what stops
    the next reader of this module from reaching for a fresh streamer again.

    This also closes the private seam that made it possible: `_hydrate` is
    reached into from another module, so a public resume is what the settlement
    path should be calling if it needs one at all.
    """
    built: list[str] = []
    real = run_resupervise.RunLogStreamer

    class _Counting(real):  # type: ignore[misc, valid-type]
        def __init__(self, **kwargs):
            built.append(kwargs["run_id"])
            super().__init__(**kwargs)

    _write_output(adopted, out="work happened\n" + _report("pass"), rc="0")

    with (
        mock.patch.object(run_resupervise, "liveness", _gone),
        mock.patch.object(run_resupervise, "RunLogStreamer", _Counting),
    ):
        resupervise(adopted.id, interval_seconds=0)

    assert built == [adopted.id], (
        f"{len(built)} streamers were built for one run; settlement should use "
        "the one the supervising loop already fed"
    )
