"""Research threads: read-only tools, claims checked against source, and a person's word standing."""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from loregarden.agents.tool_grants import claude_tool_flags, effective_allowlist
from loregarden.mcp.research_tools import HANDLERS
from loregarden.models.domain import BaxterChatSession, ChatSurface, Workspace
from loregarden.models.domain.chat_enums import ChatThreadMode
from loregarden.models.domain.enums import ChatAdvisoryCause
from loregarden.models.domain.survey_enums import ClaimRecorder, ClaimStatus, SurveySourceKind
from loregarden.models.domain.survey_research_tables import SurveyResearchCandidate
from loregarden.models.domain.survey_tables import SurveySource
from loregarden.services import baxter_chat_service
from loregarden.services.agent_turn_runner import AgentTurnResult
from loregarden.services.baxter_chat_research import research_agent
from loregarden.services.baxter_chat_service import (
    chat_session_snapshot,
    invoke_baxter_chat_model,
    set_chat_mode,
)
from loregarden.services.git_subprocess import run_git
from loregarden.services.reference_survey import research, research_repo
from loregarden.services.reference_survey.errors import SurveyError
from loregarden.services.reference_survey.research import (
    CandidateIn,
    ClaimIn,
    QuestionIn,
    RefuteIn,
    open_question,
    question_view,
    record_candidates,
    refute,
)
from sqlmodel import Session, select
from tests.survey_support import build_world, write_tree

RIGGER = "https://github.com/acme/rigger"
SKELETON = "generates a skeleton for an unrigged mesh"
SKINNING = "computes skin weights"
_ABOUT = json.dumps(
    {"language": "Python", "stargazers_count": 412, "pushed_at": "2026-10-30T12:00:00Z",
     "license": {"spdx_id": "MIT", "name": "MIT License"}}
)  # fmt: skip


@pytest.fixture(name="cache")
def cache_fixture(tmp_path: Path):
    """A clone of acme/rigger in a private reference cache, at one known commit."""
    root = tmp_path / "reference_repos"
    clone = write_tree(
        root / "github.com" / "acme" / "rigger",
        {"rig/auto_skeleton.py": "def build_skeleton(mesh):\n    return joints(mesh)\n",
         "README.md": "Auto-rigging and skin weights.\n"},
    )  # fmt: skip
    for args in (["init", "-q"], ["add", "."], ["-c", "user.email=t@t", "-c", "user.name=t",
                 "commit", "-qm", "init"]):  # fmt: skip
        run_git(args, cwd=clone, check=True, capture_output=True)
    head = run_git(["rev-parse", "HEAD"], cwd=clone, capture_output=True, text=True, check=True)
    with mock.patch("loregarden.services.reference_repo_service.cache_root", return_value=root):
        yield head.stdout.strip()


@pytest.fixture(name="ctx")
def ctx_fixture(isolated_db):
    session = Session(isolated_db)
    world = build_world(session)
    thread = BaxterChatSession(
        workspace_id=world.workspace_id, title="Rigging", mode=ChatThreadMode.RESEARCH
    )
    session.add(thread)
    session.commit()
    question = open_question(
        session, QuestionIn(thread_id=thread.id, question="Which repos auto-rig a mesh?",
                            capabilities=[SKELETON, SKINNING]),
    )  # fmt: skip
    with mock.patch.object(research, "gh_api_json", return_value=_ABOUT):
        yield session, world, thread, question
    session.close()


def _verified(commit: str, path: str = "rig/auto_skeleton.py", lines=(1, 2)) -> ClaimIn:
    return ClaimIn(capability=SKELETON, status=ClaimStatus.VERIFIED, evidence_path=path,
                   evidence_commit=commit, evidence_start=lines[0], evidence_end=lines[1],
                   note="build_skeleton places joints")  # fmt: skip


# --- the thread's tools -------------------------------------------------------


def test_a_research_turn_is_offered_no_tool_that_writes():
    agent = research_agent({"slug": "triage", "mcp_tools": ["loregarden_update_ticket"]})
    allowed = effective_allowlist(
        agent["tool_grants"], mcp_tools=agent["mcp_tools"], mcp_enabled=True,
        surface=ChatSurface.HOME, interactive=True,
    )  # fmt: skip
    assert {"Write", "Edit", "Bash", "NotebookEdit", "Task"}.isdisjoint(allowed)
    assert {"Read", "Grep", "WebSearch", "WebFetch"} <= set(allowed)
    assert "mcp__loregarden__loregarden_research_record" in allowed
    assert "mcp__loregarden__loregarden_update_ticket" not in allowed
    flags = claude_tool_flags(
        agent["tool_grants"], mcp_tools=agent["mcp_tools"], mcp_enabled=True,
        surface=ChatSurface.HOME, interactive=True,
    )  # fmt: skip
    disallowed = flags[flags.index("--disallowedTools") + 1].split(",")
    assert {"Write", "Edit", "Bash"} <= set(disallowed)


def _turn(session, world, thread, adapter="claude"):
    workspace = session.get(Workspace, world.workspace_id)
    captured = {}

    def fake_run(request):
        captured["request"] = request
        return AgentTurnResult(reply="done", strategy="permission_bridge", adapter=adapter)

    with (
        mock.patch.object(baxter_chat_service, "run_agent_turn", side_effect=fake_run),
        mock.patch.object(baxter_chat_service, "resolve_effective_adapter", return_value=adapter),
    ):
        outcome = invoke_baxter_chat_model(
            session, workspace, content="find auto-riggers", chat_session=thread
        )
    return outcome, captured.get("request")


def test_a_research_turn_runs_narrowed_with_its_posture_and_publishes_nothing(ctx, monkeypatch):
    session, world, thread, question = ctx
    monkeypatch.delenv(baxter_chat_service.BAXTER_CHAT_CLI_PROFILE.stub_env, raising=False)
    refute(session, RefuteIn(url="https://github.com/x/anycreature", capability=SKELETON,
                             note="anyCreature can't build skeletons"))  # fmt: skip

    outcome, request = _turn(session, world, thread)

    assert outcome.acting is False
    assert request.agent["tool_grants"].disallowed_tools
    assert "loregarden_research_record" in request.agent["mcp_tools"]
    assert "## Research thread" in request.prompt
    assert question.id in request.prompt
    assert "anyCreature can't build skeletons" in request.prompt
    assert "Git is in scope" not in request.prompt


def test_research_refuses_an_adapter_that_cannot_be_held_read_only(ctx, monkeypatch):
    session, world, thread, _ = ctx
    monkeypatch.delenv(baxter_chat_service.BAXTER_CHAT_CLI_PROFILE.stub_env, raising=False)
    with pytest.raises(ValueError, match="Claude Code only"):
        _turn(session, world, thread, adapter="cursor")


def test_the_snapshot_names_the_mode_and_switching_back_restores_general(ctx, monkeypatch):
    session, _, thread, _ = ctx
    monkeypatch.setenv("LOREGARDEN_CLI_ADAPTER", "cursor")
    assert chat_session_snapshot(session, thread)["chat_mode"]["cause"] == (
        ChatAdvisoryCause.ADAPTER_CANNOT_RESEARCH.value
    )
    monkeypatch.setenv("LOREGARDEN_CLI_ADAPTER", "claude")
    snapshot = chat_session_snapshot(session, thread)
    assert snapshot["mode"] == "research"
    assert snapshot["chat_mode"]["cause"] == ChatAdvisoryCause.RESEARCH_THREAD.value
    general = chat_session_snapshot(session, set_chat_mode(session, thread, ChatThreadMode.GENERAL))
    assert general["mode"] == "general"
    assert general["chat_mode"]["cause"] != ChatAdvisoryCause.RESEARCH_THREAD.value


def test_a_question_belongs_to_a_research_thread(ctx):
    session, world, _, _ = ctx
    general = BaxterChatSession(workspace_id=world.workspace_id, title="General")
    session.add(general)
    session.commit()
    with pytest.raises(SurveyError, match="research thread"):
        open_question(session, QuestionIn(thread_id=general.id, question="Which repos rig?",
                                          capabilities=[SKELETON]))  # fmt: skip


# --- claims -------------------------------------------------------------------


def test_a_verified_claim_needs_a_real_file_at_the_commit_research_read(ctx, cache):
    session, _, _, question = ctx
    result = record_candidates(session, question.id, [
        CandidateIn(url=RIGGER + ".git", name="Rigger", claims=[
            _verified(cache),
            ClaimIn(capability=SKINNING, status=ClaimStatus.VERIFIED,
                    evidence_path="rig/weights.py", evidence_commit=cache,
                    evidence_start=1, evidence_end=1),
        ]),
        CandidateIn(url="https://github.com/acme/unread", name="Unread",
                    claims=[_verified("abcdef1234")]),
    ])  # fmt: skip

    assert result.recorded == [RIGGER, "https://github.com/acme/unread"]
    assert result.refused == [
        f"{RIGGER} · {SKINNING}: rig/weights.py does not exist at {cache[:12]}",
        "https://github.com/acme/unread · "
        f"{SKELETON}: https://github.com/acme/unread was never read; read it with "
        "loregarden_research_repo first",
    ]
    view = question_view(session, question.id)
    rigger = view.candidates[0]
    assert rigger.claims[SKELETON].status is ClaimStatus.VERIFIED
    assert rigger.claims[SKINNING] is None
    assert (rigger.licence_name, rigger.stars, rigger.language) == ("MIT", 412, "Python")


def test_a_verified_claim_keeps_the_lines_the_server_read_and_links_them(ctx, cache):
    session, _, _, question = ctx
    claim = _verified(cache)
    claim.evidence_url = "https://example.com/made-up"
    record_candidates(
        session, question.id, [CandidateIn(url=RIGGER, name="Rigger", claims=[claim])]
    )
    shown = question_view(session, question.id).candidates[0].claims[SKELETON]
    assert shown.evidence_excerpt == "def build_skeleton(mesh):\n    return joints(mesh)"
    assert shown.evidence_url == f"{RIGGER}/blob/{cache}/rig/auto_skeleton.py#L1-L2"
    assert (shown.evidence_start, shown.evidence_end) == (1, 2)


@pytest.mark.parametrize(
    ("lines", "why"),
    [((0, 0), "cites the lines that do it"), ((2, 1), "cites the lines that do it"),
     ((1, 61), "at most 60 lines"), ((1, 3), "has 2 lines at")],
)  # fmt: skip
def test_a_verified_claim_must_cite_lines_that_are_in_the_file(ctx, cache, lines, why):
    session, _, _, question = ctx
    result = record_candidates(session, question.id, [
        CandidateIn(url=RIGGER, name="Rigger", claims=[_verified(cache, lines=lines)]),
    ])  # fmt: skip
    assert len(result.refused) == 1
    assert why in result.refused[0]


def test_a_refutation_from_source_cites_lines_held_to_the_same_check(ctx, cache):
    session, _, _, question = ctx
    refuted = ClaimIn(capability=SKINNING, status=ClaimStatus.REFUTED, evidence_commit=cache,
                      evidence_path="rig/auto_skeleton.py", evidence_start=2, evidence_end=2,
                      note="returns joints only; no weights")  # fmt: skip
    beyond = refuted.model_copy(update={"capability": SKELETON, "evidence_end": 9})
    result = record_candidates(session, question.id, [
        CandidateIn(url=RIGGER, name="Rigger", claims=[refuted, beyond]),
    ])  # fmt: skip
    assert result.refused == [
        f"{RIGGER} · {SKELETON}: rig/auto_skeleton.py has 2 lines at {cache[:12]}; lines 2-9 are not in it"
    ]
    shown = question_view(session, question.id).candidates[0].claims[SKINNING]
    assert (shown.status, shown.evidence_excerpt) == (
        ClaimStatus.REFUTED,
        "    return joints(mesh)",
    )
    assert shown.evidence_url.endswith("#L2-L2")


def test_the_claim_card_shows_what_stands_and_counts_what_came_before(ctx, cache):
    session, _, _, question = ctx
    empty = research.claim_card(session, RIGGER + ".git", SKELETON)
    assert (empty.url, empty.name, empty.claim, empty.earlier) == (RIGGER, "", None, 0)
    record_candidates(session, question.id, [
        CandidateIn(url=RIGGER, name="Rigger", claims=[_verified(cache)]),
    ])  # fmt: skip
    refute(session, RefuteIn(url=RIGGER, capability=SKELETON, note="it only retargets"))
    card = research.claim_card(session, RIGGER, SKELETON)
    assert card.name == "Rigger"
    assert (card.claim.status, card.claim.note) == (ClaimStatus.REFUTED, "it only retargets")
    assert card.earlier == 1


def test_a_stale_commit_and_a_capability_nobody_asked_for_are_refused(ctx, cache):
    session, _, _, question = ctx
    result = record_candidates(session, question.id, [
        CandidateIn(url=RIGGER, name="Rigger", claims=[
            _verified("1234567890"),
            ClaimIn(capability="exports FBX", status=ClaimStatus.DOCS_ONLY),
        ]),
    ])  # fmt: skip
    assert "is not the commit research read" in result.refused[0]
    assert "not one of the question's capabilities" in result.refused[1]
    assert result.warnings == [f"{RIGGER}: nothing claimed for {SKELETON}; {SKINNING}"]


def test_a_persons_refutation_stands_over_a_later_agent_claim(ctx, cache):
    session, _, _, question = ctx
    refute(session, RefuteIn(url=RIGGER, capability=SKELETON, note="it only retargets"))
    result = record_candidates(session, question.id, [
        CandidateIn(url=RIGGER, name="Rigger", claims=[
            _verified(cache), ClaimIn(capability=SKINNING, status=ClaimStatus.DOCS_ONLY),
        ]),
    ])  # fmt: skip

    assert result.refused == []
    assert "a person refuted this" in result.warnings[0]
    shown = question_view(session, question.id).candidates[0].claims[SKELETON]
    assert (shown.status, shown.recorded_by) == (ClaimStatus.REFUTED, ClaimRecorder.OPERATOR)


def test_a_new_agent_claim_replaces_the_agents_earlier_one_and_reruns_mark_new(ctx, cache):
    session, _, _, question = ctx
    docs = ClaimIn(capability=SKELETON, status=ClaimStatus.DOCS_ONLY)
    record_candidates(session, question.id, [CandidateIn(url=RIGGER, name="Rigger", claims=[docs])])
    record_candidates(session, question.id, [
        CandidateIn(url=RIGGER, name="Rigger", claims=[_verified(cache)]),
        CandidateIn(url="https://github.com/acme/other", name="Other"),
    ])  # fmt: skip
    view = question_view(session, question.id)
    assert view.run_count == 2
    assert [(c.name, c.new) for c in view.candidates] == [("Rigger", False), ("Other", True)]
    assert view.candidates[0].claims[SKELETON].status is ClaimStatus.VERIFIED


def test_a_candidate_already_in_the_library_links_to_its_source(ctx):
    session, world, _, question = ctx
    source = SurveySource(workspace_id=world.workspace_id, kind=SurveySourceKind.REPO,
                          url=RIGGER, name="rigger", question="q")  # fmt: skip
    session.add(source)
    session.commit()
    record_candidates(session, question.id, [CandidateIn(url=RIGGER + "/", name="Rigger")])
    candidate = session.exec(select(SurveyResearchCandidate)).one()
    assert candidate.source_id == source.id


def test_unreadable_metadata_is_a_warning_and_kept_on_the_row(ctx):
    session, _, _, question = ctx
    failing = mock.patch.object(
        research, "gh_api_json", side_effect=research.UpstreamUnavailable("gh api: rate limited")
    )
    with failing:
        result = record_candidates(session, question.id, [CandidateIn(url=RIGGER, name="Rigger")])
    assert result.warnings[0] == f"{RIGGER}: facts not read (gh api: rate limited)"
    assert question_view(session, question.id).candidates[0].metadata_error == (
        "gh api: rate limited"
    )


# --- the tools ----------------------------------------------------------------


def test_the_repo_tool_reads_and_reports_the_commit_it_read(ctx, cache):
    session, *_ = ctx
    read = json.loads(HANDLERS["loregarden_research_repo"](
        session, {"action": "read", "url": RIGGER, "path": "rig/auto_skeleton.py"}
    ))  # fmt: skip
    assert read["commit"] == cache
    assert read["text"].startswith("1: def build_skeleton")
    assert read["blob_url"] == f"{RIGGER}/blob/{cache}/rig/auto_skeleton.py"
    grep = json.loads(HANDLERS["loregarden_research_repo"](
        session, {"action": "grep", "url": RIGGER, "pattern": "skeleton"}
    ))  # fmt: skip
    assert grep["matches"] == ["rig/auto_skeleton.py:1:def build_skeleton(mesh):"]
    with pytest.raises(SurveyError, match="outside the repository"):
        research_repo.read(RIGGER, "../../etc/passwd")
    refused = json.loads(HANDLERS["loregarden_research_repo"](
        session, {"action": "read", "url": RIGGER, "path": "../../etc/passwd"}
    ))  # fmt: skip
    assert refused == {"url": RIGGER, "refused": "../../etc/passwd is outside the repository"}


def test_the_record_tool_opens_records_and_refutes(ctx, cache):
    session, _, thread, _ = ctx
    tool = HANDLERS["loregarden_research_record"]
    opened = json.loads(tool(session, {"action": "question", "thread_id": thread.id,
                                       "question": "Which repos compute weights?",
                                       "capabilities": [SKINNING]}))  # fmt: skip
    recorded = json.loads(tool(session, {
        "action": "candidates", "question_id": opened["question_id"],
        "candidates": [{"url": RIGGER, "name": "Rigger", "claims": [
            {"capability": SKINNING, "status": "docs_only", "evidence_url": RIGGER}]}],
    }))  # fmt: skip
    assert (recorded["recorded"], recorded["refused"]) == ([RIGGER], [])
    refuted = json.loads(tool(session, {"action": "refute", "url": RIGGER, "capability": SKINNING,
                                        "note": "operator: weights are manual"}))  # fmt: skip
    assert refuted["status"] == "refuted"


# --- the API ------------------------------------------------------------------


def test_the_api_serves_the_table_switches_mode_and_records_a_refutation(client: TestClient):
    workspace = client.get("/api/workspaces").json()[0]["slug"]
    thread = client.post(f"/api/workspaces/{workspace}/baxter-chat/sessions", json={}).json()
    switched = client.put(
        f"/api/workspaces/{workspace}/baxter-chat/sessions/{thread['id']}/mode",
        json={"mode": "research"},
    )
    assert switched.status_code == 200, switched.text
    assert switched.json()["mode"] == "research"
    assert client.get("/api/reference-research/questions/nope").status_code == 404

    refuted = client.post(
        "/api/reference-research/claims/refute",
        json={"url": RIGGER, "capability": SKELETON, "note": "it cannot"},
    )
    assert refuted.status_code == 200, refuted.text
    assert refuted.json()["recorded_by"] == "operator"
    card = client.get(
        "/api/reference-research/claims", params={"url": RIGGER, "capability": SKELETON}
    )
    assert card.status_code == 200, card.text
    assert (card.json()["claim"]["status"], card.json()["earlier"]) == ("refuted", 0)
    assert (
        client.get(
            "/api/reference-research/claims", params={"url": "ftp://x", "capability": "y"}
        ).status_code
        == 400
    )
    bad = client.post(
        "/api/reference-research/claims/refute",
        json={"url": "ftp://nope", "capability": SKELETON, "note": "it cannot"},
    )
    assert bad.status_code == 400
