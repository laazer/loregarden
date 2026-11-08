"""A review, verify or gate pass must show, from its own tool calls, that it read the diff."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from loregarden.agents.executors.shell_reads import shell_reads
from loregarden.models.domain import (
    AgentRun,
    Artifact,
    ArtifactKind,
    BlockKind,
    DomainEvent,
    EventType,
    OrchestrationRun,
    OrchestrationRunStatus,
    OrchestratorDecision,
    ParallelAgentSpec,
    ReadEvidenceStatus,
    RunStatus,
    StageStatus,
    Ticket,
    TicketState,
    WorkflowInstance,
    WorkflowStageDef,
    WorkflowTemplate,
    Workspace,
)
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.parallel_stage import member_result_from_run
from loregarden.services.read_evidence import judge as judge_transcript
from loregarden.services.workflow_state import initial_stages_json, parse_stage_map
from sqlmodel import Session, select

REPO = "/repo/.claude/worktrees/feature-x"
CHANGED = ("client/src/App.tsx", "server/loregarden/services/a.py", "server/tests/test_a.py")


# --------------------------------------------------------------------------- #
# Transcripts
# --------------------------------------------------------------------------- #


def _claude(*tools: tuple[str, dict]) -> str:
    """A Claude stream-json transcript whose assistant turns call these tools."""
    return "\n".join(
        json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "tool_use", "name": name, "input": args}]},
            }
        )
        for name, args in tools
    )


def _cursor(*calls: tuple[str, dict]) -> str:
    """A Cursor transcript: each call is written twice, started and completed."""
    lines = []
    for name, args in calls:
        for subtype in ("started", "completed"):
            lines.append(
                json.dumps(
                    {"type": "tool_call", "subtype": subtype, "tool_call": {name: {"args": args}}}
                )
            )
    return "\n".join(lines)


def _bash(command: str) -> tuple[str, dict]:
    return ("Bash", {"command": command})


def judge(stdout: str, repo: str, changed: tuple[str, ...]):
    judged = judge_transcript(stdout, repo, changed)
    assert judged is not None, "the fixture transcript parsed as no events"
    return judged


def _status(stdout: str, changed: tuple[str, ...] = CHANGED) -> ReadEvidenceStatus:
    return judge(stdout, REPO, changed)[0]


# --------------------------------------------------------------------------- #
# What a shell command read
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "command",
    [
        "git diff main...HEAD",
        "git --no-pager -C server diff origin/main",
        "git show HEAD",
        "git log -p -3",
        "git diff main -- ':!client/package-lock.json'",
    ],
)
def test_a_patch_with_no_selecting_pathspec_reads_the_whole_diff(command):
    assert shell_reads(command).whole_diff is True


@pytest.mark.parametrize(
    "command",
    ["git diff --stat main", "git diff --name-only main", "git log --oneline -5", "git status"],
)
def test_a_summary_reads_no_diff(command):
    reads = shell_reads(command)
    assert reads.whole_diff is False
    assert reads.named == frozenset()


def test_a_pathspec_after_double_dash_is_recorded_under_the_cwd():
    reads = shell_reads("cd server && git diff main -- loregarden/services 'tests/*.py'")
    assert reads.pathspecs == {"server/loregarden/services", "server/tests/*.py"}
    assert reads.whole_diff is False


def test_a_reading_command_names_its_path_arguments_under_the_cwd():
    reads = shell_reads("cd server; sed -n 1,80p loregarden/services/a.py | head -5")
    assert reads.named == {"server/loregarden/services/a.py"}


@pytest.mark.parametrize(
    ("command", "path"),
    [
        ("git blame -L 1,20 server/a.py", "server/a.py"),
        ("git show origin/main:server/a.py", "server/a.py"),
        ("git -C server show HEAD:loregarden/a.py", "loregarden/a.py"),
    ],
)
def test_a_git_read_names_its_file(command, path):
    assert shell_reads(command).named == {path}


@pytest.mark.parametrize("command", ["ls server/a.py", "wc -l server/a.py", "rm -f x.py"])
def test_a_command_that_does_not_read_contents_names_nothing(command):
    assert shell_reads(command).named == frozenset()


def test_an_unbalanced_quote_still_yields_the_paths():
    reads = shell_reads("grep -n 'unterminated server/a.py")
    assert "server/a.py" in reads.named


# --------------------------------------------------------------------------- #
# Judging a transcript against the diff
# --------------------------------------------------------------------------- #


def test_a_read_of_every_changed_file_is_ok():
    stdout = _claude(
        ("Read", {"file_path": f"{REPO}/client/src/App.tsx"}),
        _bash("cd server && sed -n 1,40p loregarden/services/a.py tests/test_a.py"),
    )
    status, read, tools, whole = judge(stdout, REPO, CHANGED)
    assert status is ReadEvidenceStatus.OK
    assert read == CHANGED
    assert (tools, whole) == (2, False)


def test_the_whole_patch_is_ok_without_naming_a_file():
    assert _status(_claude(_bash("git diff origin/main...HEAD"))) is ReadEvidenceStatus.OK


def test_a_glob_or_directory_pathspec_selects_the_files_under_it():
    stdout = _claude(_bash("git diff main -- 'server/*' client/src"))
    assert _status(stdout) is ReadEvidenceStatus.OK


def test_some_of_the_files_is_insufficient():
    stdout = _claude(_bash("git diff main -- server/loregarden/services/a.py"))
    status, read, _, _ = judge(stdout, REPO, CHANGED)
    assert status is ReadEvidenceStatus.INSUFFICIENT
    assert read == ("server/loregarden/services/a.py",)


def test_tool_calls_that_read_none_of_the_diff_read_nothing():
    stdout = _claude(
        ("Read", {"file_path": f"{REPO}/AGENTS.md"}),
        _bash("git diff --stat main"),
        _bash("ls server/loregarden/services"),
    )
    status, read, tools, _ = judge(stdout, REPO, CHANGED)
    assert status is ReadEvidenceStatus.NONE
    assert (read, tools) == ((), 3)


def test_a_transcript_with_no_tool_calls_is_no_tools():
    stdout = json.dumps({"type": "assistant", "message": {"content": [{"type": "text"}]}})
    assert _status(stdout) is ReadEvidenceStatus.NO_TOOLS


def test_cursor_reads_count_and_each_call_is_counted_once():
    stdout = _cursor(
        ("readToolCall", {"path": f"{REPO}/client/src/App.tsx"}),
        ("shellToolCall", {"command": "git diff main -- server"}),
    )
    status, _, tools, _ = judge(stdout, REPO, CHANGED)
    assert status is ReadEvidenceStatus.OK
    assert tools == 2


def test_a_path_in_another_worktree_of_the_repo_still_matches():
    stdout = _claude(("Read", {"file_path": "/elsewhere/.worktrees/t-9/server/tests/test_a.py"}))
    assert judge(stdout, REPO, CHANGED)[1] == ("server/tests/test_a.py",)


# --------------------------------------------------------------------------- #
# The verdict, on a real run
# --------------------------------------------------------------------------- #


def _report(status: str = "pass", **extra) -> str:
    payload = json.dumps({"status": status, "confidence": 0.9, **extra})
    return f"\n<<<LOREGARDEN_STAGE_REPORT>>>\n{payload}\n<<<END_STAGE_REPORT>>>\n"


def _stage(key: str, order: int, *, agent_id: str = "", **extra) -> WorkflowStageDef:
    return WorkflowStageDef(
        key=key,
        name=key,
        agent_id=agent_id,
        order=order,
        exit_actions_enabled=False,
        exit_actions=[],
        **extra,
    )


@pytest.fixture
def reviewing(db_session: Session, tmp_path):
    """A ticket at `review` (agent or parallel), with a captured three-file diff."""

    def build(*, agent_id: str = "architecture_reviewer", parallel: bool = False):
        review = _stage(
            "review",
            2,
            agent_id=agent_id,
            **(
                {
                    "stage_type": "parallel",
                    "parallel_agents": [ParallelAgentSpec(agent_id=agent_id)],
                }
                if parallel
                else {}
            ),
        )
        stages = [
            _stage("implement", 1, agent_id="backend_implementer"),
            review,
            _stage("done", 3, terminal=True),
        ]
        template = WorkflowTemplate(
            slug=f"re-{uuid4().hex[:8]}",
            name="Read evidence",
            stages_json=json.dumps([s.model_dump(mode="json") for s in stages]),
            transitions_json=json.dumps(
                [
                    {"from": "implement", "to": "review", "when": "pass"},
                    {"from": "review", "to": "done", "when": "pass"},
                    {"from": "review", "to": "implement", "when": "reject"},
                ]
            ),
        )
        db_session.add(template)
        db_session.commit()
        workspace = Workspace(
            slug=f"re-{uuid4().hex[:8]}",
            name="Read evidence",
            repo_path=str(tmp_path),
            workflow_template_id=template.id,
        )
        db_session.add(workspace)
        db_session.commit()
        ticket = Ticket(
            external_id=f"re-{uuid4().hex[:8]}",
            workspace_id=workspace.id,
            title="Read evidence",
            state=TicketState.IN_PROGRESS,
            workflow_stage_key="review",
            workflow_stage_status=StageStatus.RUNNING,
        )
        db_session.add(ticket)
        db_session.commit()
        db_session.add(
            WorkflowInstance(
                ticket_id=ticket.id,
                template_id=template.id,
                current_stage_key="review",
                stages_json=initial_stages_json(stages),
            )
        )
        db_session.add(
            Artifact(
                ticket_id=ticket.id,
                kind=ArtifactKind.DIFF,
                title="git diff",
                content_json=json.dumps({"sections": [{"path": path} for path in CHANGED]}),
            )
        )
        orch = OrchestrationRun(
            run_code=f"orch_{uuid4().hex[:8]}",
            ticket_id=ticket.id,
            workspace_id=workspace.id,
            current_stage_key="review",
            status=OrchestrationRunStatus.RUNNING,
        )
        db_session.add(orch)
        db_session.commit()
        db_session.refresh(ticket)
        return ticket, orch

    return build


def _run(session: Session, ticket: Ticket, orch: OrchestrationRun, agent_id: str, **fields):
    run = AgentRun(
        run_code=f"r_{uuid4().hex[:8]}",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id=agent_id,
        stage_key="review",
        status=fields.pop("status", RunStatus.RUNNING),
        start_repo_path=REPO,
        orchestration_run_id=orch.id,
        **fields,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _finish(session: Session, ticket: Ticket, orch, stdout: str, agent_id="architecture_reviewer"):
    run = _run(session, ticket, orch, agent_id)
    OrchestrationService(session).complete_run(run, status=RunStatus.SUCCEEDED, stdout=stdout)
    session.refresh(ticket)
    return run


def _all_decisions(session: Session, ticket: Ticket) -> list[dict]:
    events = session.exec(
        select(DomainEvent).where(
            DomainEvent.ticket_id == ticket.id,
            DomainEvent.type == EventType.ORCHESTRATOR_DECISION,
        )
    ).all()
    return [json.loads(event.payload_json) for event in events]


def _decisions(session: Session, ticket: Ticket) -> list[dict]:
    return [p for p in _all_decisions(session, ticket) if "read_evidence" in p]


def _review_status(session: Session, ticket: Ticket) -> StageStatus:
    instance, stages = OrchestrationService(session)._resolve_stages(ticket)
    return parse_stage_map(instance, stages)["review"]


UNREAD = _claude(("Read", {"file_path": f"{REPO}/AGENTS.md"}), _bash("git status"))
READ_ONE = _claude(_bash("git diff main -- server/loregarden/services/a.py"))


def test_a_pass_that_read_nothing_is_refused_and_the_history_names_the_run(db_session, reviewing):
    ticket, orch = reviewing()
    run = _finish(db_session, ticket, orch, UNREAD + _report())

    assert _review_status(db_session, ticket) is not StageStatus.DONE
    assert ticket.workflow_stage_key == "review"  # sent back to its own stage
    [decision] = _decisions(db_session, ticket)
    assert decision["decision"] == OrchestratorDecision.REFUSED_UNREAD_PASS
    assert decision["read_evidence"] == ReadEvidenceStatus.NONE
    assert run.run_code in decision["reason"]
    assert "read nothing" in decision["reason"]


def test_a_pass_that_read_some_of_the_diff_stands_and_the_history_says_how_much(
    db_session, reviewing
):
    ticket, orch = reviewing()
    _finish(db_session, ticket, orch, READ_ONE + _report())

    assert _review_status(db_session, ticket) is StageStatus.DONE
    [decision] = _decisions(db_session, ticket)
    assert decision["decision"] == OrchestratorDecision.READ_EVIDENCE_CHECKED
    assert decision["read_evidence"] == ReadEvidenceStatus.INSUFFICIENT
    assert decision["files_read"] == ["server/loregarden/services/a.py"]


def test_a_pass_with_no_tool_calls_blocks_as_the_harness(db_session, reviewing):
    ticket, orch = reviewing()
    no_tools = json.dumps({"type": "assistant", "message": {"content": [{"type": "text"}]}})
    _finish(db_session, ticket, orch, no_tools + _report())

    # Classified as the harness, which spends its one repair turn on the stage.
    assert _review_status(db_session, ticket) is not StageStatus.DONE
    [decision] = _decisions(db_session, ticket)
    assert decision["read_evidence"] == ReadEvidenceStatus.NO_TOOLS
    kinds = [
        payload["block_kind"]
        for payload in _all_decisions(db_session, ticket)
        if payload["decision"] == OrchestratorDecision.CLASSIFIED_BLOCK
    ]
    assert kinds == [BlockKind.HARNESS]


def test_the_agent_cannot_waive_its_own_read_nothing(db_session, reviewing):
    ticket, orch = reviewing()
    _finish(db_session, ticket, orch, UNREAD + _report())
    waiver = {"waivers": [{"name": "read:nothing", "reason": "I read it in my head"}]}
    _finish(db_session, ticket, orch, UNREAD + _report(**waiver))

    assert _review_status(db_session, ticket) is not StageStatus.DONE


def test_a_rerun_that_reads_the_diff_passes(db_session, reviewing):
    ticket, orch = reviewing()
    _finish(db_session, ticket, orch, UNREAD + _report())
    _finish(db_session, ticket, orch, READ_ONE + _report())

    assert _review_status(db_session, ticket) is StageStatus.DONE


def test_visual_qa_is_not_judged(db_session, reviewing):
    ticket, orch = reviewing(agent_id="visual_qa")
    _finish(db_session, ticket, orch, UNREAD + _report(), agent_id="visual_qa")

    assert _review_status(db_session, ticket) is StageStatus.DONE
    assert _decisions(db_session, ticket) == []


def test_a_run_with_no_transcript_is_not_judged(db_session, reviewing):
    ticket, orch = reviewing()
    _finish(db_session, ticket, orch, "Plain text from a harness." + _report())

    assert _review_status(db_session, ticket) is StageStatus.DONE
    assert _decisions(db_session, ticket) == []
    assert judge_transcript("no events here", REPO, CHANGED) is None


def test_a_parallel_lens_that_read_nothing_does_not_pass(db_session, reviewing):
    ticket, orch = reviewing(parallel=True)
    run = _run(
        db_session,
        ticket,
        orch,
        "architecture_reviewer",
        status=RunStatus.SUCCEEDED,
        stdout=UNREAD + _report(),
    )
    result = member_result_from_run(run)
    assert result.failure
    assert result.transient is False
    assert result.report.reroute_to_stage == "review"


def test_a_parallel_lens_with_no_tool_calls_is_a_harness_failure(db_session, reviewing):
    ticket, orch = reviewing(parallel=True)
    no_tools = json.dumps({"type": "assistant", "message": {"content": [{"type": "text"}]}})
    run = _run(
        db_session,
        ticket,
        orch,
        "architecture_reviewer",
        status=RunStatus.SUCCEEDED,
        stdout=no_tools + _report(),
    )
    result = member_result_from_run(run)
    assert result.failure
    assert result.transient is True
    assert result.report.blocked_kind is BlockKind.HARNESS
    # No reroute target, or the stage would read it as a rejection and rerun.
    assert not result.report.reroute_to_stage
