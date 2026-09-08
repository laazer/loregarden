"""Recovering what a run read from the CLI transcript.

The transcript shapes below are copied from real runs rather than invented. That
matters more than usual here: this signal was declared missing once, on the
strength of grepping 60 runs for the Claude schema while the recent ones were
written in the cursor-agent schema. A fixture that only models the schema the
author had in mind would reproduce exactly that mistake.
"""

from __future__ import annotations

import json

import pytest
from loregarden.agents.executors.read_paths import (
    MAX_READ_PATHS,
    extract_read_paths,
    record_read_paths,
)
from sqlmodel import Session
from tests.factories import make_agent_run, make_workspace_ticket


def _claude(name: str, **args) -> str:
    """One assistant event carrying a tool_use block, as Claude writes it."""
    return json.dumps(
        {
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": name, "input": args}]},
        }
    )


def _cursor(call: str, **args) -> str:
    """One tool_call event, as cursor-agent writes it — note the nesting."""
    return json.dumps(
        {"type": "tool_call", "subtype": "started", "tool_call": {call: {"args": args}}}
    )


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    (tmp_path / "server" / "loregarden").mkdir(parents=True)
    return tmp_path


def test_claude_read_calls_are_recovered(repo):
    stdout = "\n".join(
        [
            _claude("Read", file_path=str(repo / "server/loregarden/a.py")),
            _claude("Read", file_path=str(repo / "server/loregarden/b.py")),
        ]
    )
    assert extract_read_paths(stdout, repo) == [
        "server/loregarden/a.py",
        "server/loregarden/b.py",
    ]


def test_cursor_read_calls_are_recovered(repo):
    """The schema that was missed. `readToolCall` nested under `tool_call`, and
    no `tool_use` marker anywhere in the line."""
    stdout = _cursor("readToolCall", path=str(repo / "server/loregarden/a.py"))
    assert extract_read_paths(stdout, repo) == ["server/loregarden/a.py"]


def test_both_schemas_in_one_transcript(repo):
    stdout = "\n".join(
        [
            _claude("Read", file_path=str(repo / "server/loregarden/a.py")),
            _cursor("readToolCall", path=str(repo / "server/loregarden/b.py")),
        ]
    )
    assert extract_read_paths(stdout, repo) == [
        "server/loregarden/a.py",
        "server/loregarden/b.py",
    ]


def test_a_parent_tool_use_id_is_not_a_tool_use_block(repo):
    """Every cursor event carries `parent_tool_use_id`, whose substring made a
    first measurement count all 74 cursor runs as Claude-schema. Matching on the
    block's own type is what keeps that from recurring."""
    stdout = json.dumps(
        {"type": "user", "parent_tool_use_id": None, "message": {"content": "hello"}}
    )
    assert extract_read_paths(stdout, repo) == []


def test_paths_outside_the_repo_are_dropped(repo):
    """A run reads its own scratch, skill files and `~/.cursor` config. Keeping
    those would make every rework-diff intersection true and the signal useless."""
    stdout = "\n".join(
        [
            _claude("Read", file_path=str(repo / "server/loregarden/a.py")),
            _claude("Read", file_path="/Users/someone/.cursor/mcp.json"),
            _claude("Read", file_path="/Users/someone/.claude/skills/x/SKILL.md"),
        ]
    )
    assert extract_read_paths(stdout, repo) == ["server/loregarden/a.py"]


def test_search_scopes_are_not_recorded_as_reads(repo):
    """Grep and Glob name a scope, not a file whose contents were opened.
    Deliberate, and documented on CLAUDE_READ_TOOLS, so a later reader does not
    take the absence for an oversight."""
    stdout = "\n".join(
        [
            _claude("Grep", path=str(repo / "server")),
            _claude("Glob", path=str(repo / "server")),
            _cursor("grepToolCall", path=str(repo / "server")),
        ]
    )
    assert extract_read_paths(stdout, repo) == []


def test_duplicate_reads_collapse(repo):
    same = str(repo / "server/loregarden/a.py")
    stdout = "\n".join([_claude("Read", file_path=same)] * 4)
    assert extract_read_paths(stdout, repo) == ["server/loregarden/a.py"]


def test_a_truncated_final_line_does_not_lose_the_rest(repo):
    """A killed run's transcript ends mid-line. Losing every read because the
    last line is half-written would be worse than losing that line."""
    stdout = _claude("Read", file_path=str(repo / "server/loregarden/a.py")) + '\n{"type":"assi'
    assert extract_read_paths(stdout, repo) == ["server/loregarden/a.py"]


def test_non_json_noise_is_skipped(repo):
    stdout = "\n".join(
        ["warning: something", _claude("Read", file_path=str(repo / "server/loregarden/a.py")), ""]
    )
    assert extract_read_paths(stdout, repo) == ["server/loregarden/a.py"]


def test_the_stored_list_is_bounded(repo):
    stdout = "\n".join(
        _claude("Read", file_path=str(repo / f"server/loregarden/f{i}.py"))
        for i in range(MAX_READ_PATHS + 25)
    )
    assert len(extract_read_paths(stdout, repo)) == MAX_READ_PATHS


def test_reading_nothing_is_recorded_as_an_answer(db_session: Session, repo):
    """675's distinction, applied before it can be lost: `[]` with a stamp means
    the run read nothing in the repo. NULL means nobody looked. A column that
    conflates them took 1086 rows to unpick last time."""
    ticket = make_workspace_ticket(db_session, "rp-empty")
    run = make_agent_run(db_session, workspace_id=ticket.workspace_id, ticket_id=ticket.id)

    record_read_paths(db_session, run, "no tool calls here", repo)

    db_session.refresh(run)
    assert run.read_paths_json == "[]"
    assert run.read_paths_recorded_at is not None


def test_recording_stores_the_paths_and_the_stamp(db_session: Session, repo):
    ticket = make_workspace_ticket(db_session, "rp-full")
    run = make_agent_run(db_session, workspace_id=ticket.workspace_id, ticket_id=ticket.id)

    record_read_paths(
        db_session, run, _claude("Read", file_path=str(repo / "server/loregarden/a.py")), repo
    )

    db_session.refresh(run)
    assert json.loads(run.read_paths_json) == ["server/loregarden/a.py"]
    assert run.read_paths_recorded_at is not None


def test_a_run_that_was_never_examined_keeps_a_null_stamp(db_session: Session):
    """The default, and the reason the stamp is a separate column."""
    ticket = make_workspace_ticket(db_session, "rp-untouched")
    run = make_agent_run(db_session, workspace_id=ticket.workspace_id, ticket_id=ticket.id)
    db_session.refresh(run)
    assert run.read_paths_json == "[]"
    assert run.read_paths_recorded_at is None


def test_a_symlinked_directory_inside_the_repo_still_counts(repo, tmp_path):
    """Found by running the parser over real transcripts, not by review.

    `agent_context` is a symlink into iCloud in some workspaces. Resolving the
    read path followed it out of the tree, so three runs with six `readToolCall`
    events each yielded nothing at all — a silent, total loss of signal for
    exactly the files an agent reads most.
    """
    outside = tmp_path / "elsewhere"
    (outside / "agents").mkdir(parents=True)
    (outside / "agents" / "role.md").write_text("x", encoding="utf-8")
    (repo / "agent_context").symlink_to(outside)

    stdout = _cursor("readToolCall", path=str(repo / "agent_context/agents/role.md"))
    assert extract_read_paths(stdout, repo) == ["agent_context/agents/role.md"]


def test_a_path_climbing_out_of_the_repo_is_still_refused(repo):
    """The lexical match must not become a way in: `..` is collapsed first, so a
    path that leaves the tree fails both comparisons."""
    stdout = _claude("Read", file_path=str(repo / "server/../../outside/secrets.env"))
    assert extract_read_paths(stdout, repo) == []
