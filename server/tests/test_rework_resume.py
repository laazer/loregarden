"""A rework round resumes the session that did the work, with the finding."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from unittest import mock

import pytest
from loregarden.agents import cli_adapters
from loregarden.agents.cli_adapters import (
    SESSION_RESUME_UNSUPPORTED,
    CliInvocation,
    claude_resume_invocation,
    session_resume_unsupported,
)
from loregarden.agents.executors import cli as cli_module
from loregarden.agents.executors import stage_turn
from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.agents.executors.rework_round import (
    FRESH_STEM_SUFFIX,
    prepare_round,
    run_round,
)
from loregarden.models.domain import AgentRun, CliAdapter, RunStatus, Ticket
from loregarden.models.domain.enums import ReworkArtifactKind, utcnow
from loregarden.services.rework_feedback import (
    record_rework_feedback,
    rework_feedback_artifact_title,
)
from loregarden.services.rework_resume import (
    ResumePlan,
    plan_rework_resume,
    rework_round_tokens,
    session_id_of,
)
from loregarden.services.run_log_stream import RunLogStreamer
from loregarden.testing.factories import make_artifact
from sqlmodel import Session
from tests.factories import make_workspace_ticket

REPORT = (
    "<<<LOREGARDEN_STAGE_REPORT>>>\n"
    '{"status": "pass", "confidence": 0.9, "reroute_to_stage": null, "reroute_context": ""}\n'
    "<<<END_STAGE_REPORT>>>"
)
SESSION = "f2d4d949-df34-4910-80ad-2167c60d7528"
FINDING = "The null case in parse_header still raises; handle it and add a test."


def _stream(final_text: str = REPORT, *, session_id: str = SESSION) -> str:
    return "\n".join(
        [
            json.dumps({"type": "system", "subtype": "init", "session_id": session_id}),
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "result": final_text,
                    "session_id": session_id,
                }
            ),
        ]
    )


@pytest.fixture
def ticket(db_session: Session) -> Ticket:
    return make_workspace_ticket(db_session, "rework-resume")


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    return tmp_path


def _run(
    session: Session,
    ticket: Ticket,
    *,
    repo_root: Path,
    code: str,
    agent_id: str = "backend_implementer",
    stdout: str = "",
    minutes_ago: int = 0,
    stage_key: str = "implement",
    **fields,
) -> AgentRun:
    run = AgentRun(
        run_code=code,
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id=agent_id,
        stage_key=stage_key,
        status=fields.pop("status", RunStatus.SUCCEEDED),
        stdout=stdout,
        start_repo_path=str(repo_root),
        created_at=utcnow() - timedelta(minutes=minutes_ago),
        **fields,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _reject(session: Session, ticket: Ticket, *, by: AgentRun | None = None) -> None:
    record_rework_feedback(
        session,
        ticket,
        target_stage="implement",
        from_stage="review",
        context=FINDING,
        run_id=by.id if by is not None else None,
    )


def _reviewer(session: Session, ticket: Ticket, repo_root: Path) -> AgentRun:
    return _run(
        session,
        ticket,
        repo_root=repo_root,
        code="run_review",
        agent_id="reviewer",
        minutes_ago=5,
        stage_key="review",
    )


def _plan(session, ticket, run, repo_root, adapter=CliAdapter.CURSOR):
    return plan_rework_resume(session, ticket, run, adapter=adapter, repo_root=repo_root)


# ---- each adapter declares resume support ----------------------------------


def test_every_executing_adapter_declares_whether_it_resumes():
    executing = {adapter for adapter in CliAdapter if adapter is not CliAdapter.DEFAULT}
    assert set(SESSION_RESUME_UNSUPPORTED) == executing
    assert {a for a, why in SESSION_RESUME_UNSUPPORTED.items() if not why} == {
        CliAdapter.CLAUDE,
        CliAdapter.CURSOR,
    }


def test_an_undeclared_adapter_cannot_resume():
    assert session_resume_unsupported("default")
    assert session_resume_unsupported("something-new")


# ---- the decision ------------------------------------------------------------


def test_a_rework_round_resumes_the_earlier_runs_session(db_session, ticket, repo_root):
    prior = _run(
        db_session, ticket, repo_root=repo_root, code="run_1", stdout=_stream(), minutes_ago=10
    )
    _reject(db_session, ticket, by=_reviewer(db_session, ticket, repo_root))
    run = _run(db_session, ticket, repo_root=repo_root, code="run_2")

    plan = _plan(db_session, ticket, run, repo_root)

    assert plan.resumes and plan.session_id == SESSION and plan.prior_run_id == prior.id
    assert FINDING in plan.follow_up and "STAGE_REPORT" in plan.follow_up
    db_session.refresh(run)
    assert run.resumed_from_run_id == prior.id
    assert run.rework_feedback_id == plan.feedback_id
    assert run.resume_skip_reason == ""


def test_a_run_with_no_finding_since_the_last_run_is_not_a_rework_round(
    db_session, ticket, repo_root
):
    _reject(db_session, ticket)
    _run(db_session, ticket, repo_root=repo_root, code="run_1", stdout=_stream(), minutes_ago=0)
    run = _run(db_session, ticket, repo_root=repo_root, code="run_2")

    assert _plan(db_session, ticket, run, repo_root) is None
    db_session.refresh(run)
    assert (run.rework_feedback_id, run.resumed_from_run_id, run.resume_skip_reason) == ("", "", "")


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("no_prior", "no earlier run at this stage"),
        ("other_agent", "was agent frontend_implementer"),
        ("codex", "codex sessions are not resumed"),
        ("no_session", "recorded no CLI session"),
        ("other_checkout", "a CLI finds its session by directory"),
    ],
)
def test_a_round_that_cannot_resume_runs_fresh_and_says_why(
    db_session, ticket, repo_root, case, expected
):
    adapter = CliAdapter.CODEX if case == "codex" else CliAdapter.CURSOR
    if case != "no_prior":
        _run(
            db_session,
            ticket,
            repo_root=repo_root / "elsewhere" if case == "other_checkout" else repo_root,
            code="run_1",
            agent_id="frontend_implementer" if case == "other_agent" else "backend_implementer",
            stdout="" if case == "no_session" else _stream(),
            minutes_ago=10,
        )
    _reject(db_session, ticket)
    run = _run(db_session, ticket, repo_root=repo_root, code="run_2")

    plan = _plan(db_session, ticket, run, repo_root, adapter=adapter)

    assert not plan.resumes and expected in plan.skip_reason
    db_session.refresh(run)
    assert run.resumed_from_run_id == "" and expected in run.resume_skip_reason
    assert run.rework_feedback_id == plan.feedback_id


def test_the_same_finding_delivered_twice_resumes_once(db_session, ticket, repo_root):
    _run(db_session, ticket, repo_root=repo_root, code="run_1", stdout=_stream(), minutes_ago=10)
    reviewer = _reviewer(db_session, ticket, repo_root)
    _reject(db_session, ticket, by=reviewer)
    first = _run(
        db_session, ticket, repo_root=repo_root, code="run_2", stdout=_stream(), minutes_ago=2
    )
    assert _plan(db_session, ticket, first, repo_root).resumes

    _reject(db_session, ticket, by=reviewer)  # the same rejecting run, recorded again
    second = _run(db_session, ticket, repo_root=repo_root, code="run_3")
    plan = _plan(db_session, ticket, second, repo_root)

    assert not plan.resumes and "already resumed a session once, in run run_2" in plan.skip_reason


def test_a_new_rejecting_run_may_resume_again(db_session, ticket, repo_root):
    _run(db_session, ticket, repo_root=repo_root, code="run_1", stdout=_stream(), minutes_ago=10)
    _reject(db_session, ticket, by=_reviewer(db_session, ticket, repo_root))
    first = _run(
        db_session, ticket, repo_root=repo_root, code="run_2", stdout=_stream(), minutes_ago=2
    )
    assert _plan(db_session, ticket, first, repo_root).resumes

    second_review = _run(
        db_session,
        ticket,
        repo_root=repo_root,
        code="run_review2",
        agent_id="reviewer",
        minutes_ago=1,
        stage_key="review",
    )
    _reject(db_session, ticket, by=second_review)
    second = _run(db_session, ticket, repo_root=repo_root, code="run_3")

    assert _plan(db_session, ticket, second, repo_root).resumes


def test_a_finding_with_no_rejecting_run_is_keyed_by_itself(db_session, ticket, repo_root):
    _run(db_session, ticket, repo_root=repo_root, code="run_1", stdout=_stream(), minutes_ago=10)
    make_artifact(
        db_session,
        ticket_id=ticket.id,
        kind=ReworkArtifactKind.FEEDBACK,
        title=rework_feedback_artifact_title("implement"),
        content={"from_stage": "gate", "target_stage": "implement", "context": FINDING},
        created_at=utcnow() - timedelta(minutes=5),
    )
    first = _run(
        db_session, ticket, repo_root=repo_root, code="run_2", stdout=_stream(), minutes_ago=2
    )
    assert _plan(db_session, ticket, first, repo_root).resumes

    _reject(db_session, ticket)  # a different finding, also with no rejecting run
    second = _run(db_session, ticket, repo_root=repo_root, code="run_3")
    assert _plan(db_session, ticket, second, repo_root).resumes


def test_an_old_runs_session_is_read_from_its_output():
    run = AgentRun(run_code="r", workspace_id="w", agent_id="a", stdout=_stream())
    assert session_id_of(run) == SESSION
    run.cli_session_id = "recorded"
    assert session_id_of(run) == "recorded"


# ---- the invocations -------------------------------------------------------------


def test_a_claude_print_resume_keeps_the_stage_prompt_and_swaps_the_user_prompt(tmp_path):
    prompt_file = tmp_path / "prompt.md"
    fresh = cli_adapters._claude_print_invocation(prompt_file=prompt_file, workspace_root=tmp_path)

    resumed = claude_resume_invocation(fresh, session_id=SESSION, prompt="the finding")

    assert resumed.argv[1:4] == ["-p", "--resume", SESSION]
    assert str(prompt_file) in resumed.argv
    assert "the finding" in resumed.argv
    assert cli_adapters.DEFAULT_CLAUDE_USER_PROMPT not in resumed.argv
    assert sorted(set(resumed.argv) - set(fresh.argv)) == sorted(
        {"--resume", SESSION, "the finding"}
    )
    assert resumed.resume_session_id == SESSION


def test_a_claude_interactive_resume_adds_only_the_flag(tmp_path):
    fresh = CliInvocation(
        argv=["claude", "--input-format", "stream-json"], interactive=True, adapter="claude"
    )
    resumed = claude_resume_invocation(fresh, session_id=SESSION, prompt="ignored")
    assert resumed.argv == [*fresh.argv, "--resume", SESSION]


def test_a_command_that_cannot_carry_a_resume_runs_fresh(db_session, ticket, repo_root):
    run = _run(db_session, ticket, repo_root=repo_root, code="run_2", resumed_from_run_id="x")
    plan = ResumePlan(feedback_id="f", prior_run_id="x", session_id=SESSION, follow_up="go")
    override = CliInvocation(argv=["my-claude-wrapper"], adapter="claude")

    final, first = prepare_round(db_session, run, plan, override)

    assert first is override and not final.resumes
    db_session.refresh(run)
    assert run.resumed_from_run_id == "" and "cannot carry a resume" in run.resume_skip_reason


# ---- spawning the round ----------------------------------------------------------


def _cursor(tmp_path: Path) -> CliInvocation:
    with mock.patch.dict("os.environ", {"LOREGARDEN_CURSOR_OUTPUT_FORMAT": "stream-json"}):
        return cli_adapters._cursor_print_invocation(
            prompt="the stage prompt", workspace_root=tmp_path, orchestrated=True
        )


def test_a_resume_that_never_starts_falls_back_to_a_fresh_run(db_session, ticket, repo_root):
    run = _run(db_session, ticket, repo_root=repo_root, code="run_2", resumed_from_run_id="p")
    plan = ResumePlan(feedback_id="f", prior_run_id="p", session_id=SESSION, follow_up="go")
    fresh = _cursor(repo_root)
    _, first = prepare_round(db_session, run, plan, fresh)
    spawned = []

    def spawn(invocation, prompt, stem):
        spawned.append((invocation, prompt, stem))
        if len(spawned) == 1:
            return "", "Error: no chat found with id", RunStatus.FAILED
        return _stream(session_id="new-session"), "", RunStatus.SUCCEEDED

    result = run_round(
        db_session,
        run,
        plan=plan,
        invocation=fresh,
        first=first,
        prompt="the stage prompt",
        spawn=spawn,
        streamer=mock.MagicMock(spec=RunLogStreamer),
    )

    assert [stem for _, _, stem in spawned] == ["run_2", f"run_2{FRESH_STEM_SUFFIX}"]
    assert "--resume" in spawned[0][0].argv and spawned[1][0] is fresh
    assert result.status is RunStatus.SUCCEEDED and result.invocation is fresh
    db_session.refresh(run)
    assert run.resumed_from_run_id == ""
    assert "did not start a turn: Error: no chat found" in run.resume_skip_reason
    assert run.cli_session_id == "new-session"


def test_a_resumed_turn_that_started_and_failed_is_not_run_again(db_session, ticket, repo_root):
    run = _run(db_session, ticket, repo_root=repo_root, code="run_2", resumed_from_run_id="p")
    plan = ResumePlan(feedback_id="f", prior_run_id="p", session_id=SESSION, follow_up="go")
    fresh = _cursor(repo_root)
    _, first = prepare_round(db_session, run, plan, fresh)
    spawn = mock.MagicMock(return_value=(_stream("gave up"), "boom", RunStatus.FAILED))

    result = run_round(
        db_session,
        run,
        plan=plan,
        invocation=fresh,
        first=first,
        prompt="p",
        spawn=spawn,
        streamer=mock.MagicMock(spec=RunLogStreamer),
    )

    assert spawn.call_count == 1 and result.status is RunStatus.FAILED
    db_session.refresh(run)
    assert run.resumed_from_run_id == "p" and run.cli_session_id == SESSION


# ---- wired into the executor ------------------------------------------------------


def test_the_executor_resumes_a_rework_round_with_the_finding(db_session, ticket, repo_root):
    """End to end through `CliAgentExecutor.execute`: the cursor run that did the
    work is continued, the finding is the turn's prompt, and the run says so."""
    prior = _run(
        db_session, ticket, repo_root=repo_root, code="run_1", stdout=_stream(), minutes_ago=10
    )
    _reject(db_session, ticket, by=_reviewer(db_session, ticket, repo_root))
    run = _run(db_session, ticket, repo_root=repo_root, code="run_2", status=RunStatus.RUNNING)

    executor = CliAgentExecutor(db_session)
    real_prepare = executor._prepare_tree

    def prepare_tree(*args, **kwargs):
        _, stopped = real_prepare(*args, **kwargs)
        return repo_root, stopped

    with (
        mock.patch.object(executor, "_prepare_tree", side_effect=prepare_tree),
        mock.patch.object(cli_module, "resolve_cli_invocation", return_value=_cursor(repo_root)),
        mock.patch.object(
            stage_turn, "run_print_mode", return_value=(_stream(), "", RunStatus.SUCCEEDED)
        ) as spawned,
    ):
        completed = executor.execute(run, ticket)

    invocation = spawned.call_args.kwargs["invocation"]
    assert invocation.argv[invocation.argv.index("--resume") + 1] == SESSION
    assert FINDING in invocation.stdin_prompt
    assert completed.resumed_from_run_id == prior.id
    assert completed.cli_session_id == SESSION
    assert "--resume" in completed.command


def test_the_executor_runs_a_local_rework_round_fresh_and_records_why(db_session, ticket):
    """The test environment's `local` adapter cannot resume: the round still runs,
    and the run records that it was a rework round and why it ran fresh."""
    _run(
        db_session,
        ticket,
        repo_root=Path("/nowhere"),
        code="run_1",
        stdout=_stream(),
        minutes_ago=10,
    )
    _reject(db_session, ticket)
    run = _run(
        db_session, ticket, repo_root=Path("/nowhere"), code="run_2", status=RunStatus.RUNNING
    )

    with mock.patch.object(
        stage_turn, "run_print_mode", return_value=(_stream(), "", RunStatus.SUCCEEDED)
    ):
        completed = CliAgentExecutor(db_session).execute(run, ticket)

    assert completed.rework_feedback_id
    assert completed.resumed_from_run_id == ""
    assert "the local runner has no CLI session" in completed.resume_skip_reason


# ---- tokens, resumed against fresh ----------------------------------------------


def test_rework_round_tokens_compares_resumed_and_fresh_from_the_rows(
    db_session, ticket, repo_root
):
    def round_run(code, *, resumed, tokens):
        _run(
            db_session,
            ticket,
            repo_root=repo_root,
            code=code,
            rework_feedback_id="f",
            resumed_from_run_id="p" if resumed else "",
            input_tokens=tokens[0] if tokens else None,
            cache_read_tokens=tokens[1] if tokens else None,
            output_tokens=tokens[2] if tokens else None,
        )

    round_run("r1", resumed=True, tokens=(100, 900, 50))
    round_run("r2", resumed=True, tokens=(300, 700, 150))
    round_run("r3", resumed=False, tokens=(1000, 5000, 400))
    round_run("r4", resumed=False, tokens=None)
    _run(db_session, ticket, repo_root=repo_root, code="not_rework", input_tokens=10**9)

    rows = {(row.stage_key, row.resumed): row for row in rework_round_tokens(db_session)}

    resumed, fresh = rows[("implement", True)], rows[("implement", False)]
    assert (resumed.rounds, resumed.measured, resumed.unmeasured) == (2, 2, 0)
    assert resumed.mean_input_tokens == 1000 and resumed.mean_output_tokens == 100
    assert (fresh.rounds, fresh.measured, fresh.unmeasured) == (2, 1, 1)
    assert fresh.mean_input_tokens == 6000
    assert len(rows) == 2
