"""Adversarial edges for lg-protocol-executors-1612 — leftover role CLI.

The happy-path contracts live in test_agent_does_not_declare_a_cli.py.
These pin the mutations that still pass those tests: a leftover `adapter`
on a role dict, an inherit blob, a drifted workspace pin vs a dispatch
snapshot, and an ALTER TABLE that is actually an empty UPDATE.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from unittest import mock

import pytest
from loregarden.agents.cli_adapters import CliInvocation, resolve_cli_invocation
from loregarden.agents.executors.dispatch_warnings import (
    maybe_warn_adapter_override,
    maybe_warn_weak_mcp_model,
)
from loregarden.models.domain import (
    AgentRun,
    BaxterChatSession,
    StudioAgentCreate,
    StudioAgentUpdate,
    Ticket,
    WorkflowStageDef,
    Workspace,
)
from loregarden.models.domain.enums import CliAdapter
from loregarden.services.baxter_chat_service import home_chat_adapter
from loregarden.services.cli_settings import adapter_model_pins_apply, adapter_override_warning
from loregarden.services.studio_service import StudioService, seed_builtin_agents
from pydantic import ValidationError
from sqlmodel import select
from tests.repo_paths import REPO_ROOT

REPO = REPO_ROOT


@pytest.fixture(autouse=True)
def _no_env_adapter_pin(monkeypatch):
    monkeypatch.delenv("LOREGARDEN_CLI_ADAPTER", raising=False)


def _workspace(**kwargs) -> Workspace:
    return Workspace(slug="ws", name="ws", **kwargs)


def _leftover_cursor(_agent_id: str | None = None) -> dict:
    return {"adapter": "cursor", "default_model": "opus"}


# --- R1 seed / views / PATCH / drop-column census -----------------------------


def test_r1_seed_does_not_write_a_cli():
    src = inspect.getsource(seed_builtin_agents)
    assert 'cfg.get("adapter"' not in src
    assert "adapter=" not in src


def test_r1_views_do_not_copy_or_warn_via_a_role_cli():
    from loregarden.services import studio_agent_views

    src = inspect.getsource(studio_agent_views)
    assert "adapter=agent.adapter" not in src
    assert 'snap.get("adapter"' not in src
    assert "cfg.get('adapter'" not in src
    assert 'cfg.get("adapter"' not in src
    assert "adapter=agent.adapter" not in src
    assert "analyze_tool_grants(" in src
    assert "adapter=" not in inspect.getsource(studio_agent_views.agent_view)


def test_r1_patch_body_that_sends_adapter_is_not_stored(db_session):
    service = StudioService(db_session)
    service.create_agent(StudioAgentCreate(slug="patched-role", name="Patched"))
    try:
        body = StudioAgentUpdate.model_validate({"name": "Patched 2", "adapter": "cursor"})
    except ValidationError:
        return
    dumped = body.model_dump(exclude_none=True)
    assert "adapter" not in dumped
    saved = service.update_agent("patched-role", body)
    assert "adapter" not in saved.model_dump()


def test_r1_drop_columns_issues_alter_table_not_rebuild_or_empty_update():
    from loregarden.db import migration_utils

    src = inspect.getsource(migration_utils.drop_columns_if_present)
    assert "ALTER TABLE" in src
    assert "DROP COLUMN" in src
    assert "table_columns" in src
    assert "_rebuild_table" not in src
    assert "UPDATE SET" not in src
    assert "SET adapter" not in src


def test_r1_a_migration_drops_studio_agents_adapter_via_the_helper():
    versions = REPO / "server" / "loregarden" / "db" / "versions"
    hits: list[str] = []
    for path in versions.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "drop_columns_if_present" not in text:
            continue
        if "studio_agents" in text and "adapter" in text:
            hits.append(path.name)
            assert "_rebuild_table" not in text
            assert "SET adapter" not in text
    assert hits, "no migration calls drop_columns_if_present('studio_agents', ['adapter'])"


# --- R2 leftover role CLI must not steer the pipeline -------------------------


def test_r2_leftover_role_cli_does_not_steer_unpinned_pipeline():
    """Inherit + leftover AGENTS adapter=cursor still resolves local, not cursor.

    # CHECKPOINT: ticket studio stays on the pipeline seam, so leftover claude
    on the role must not keep inherit sessions off process-default local.
    """
    from loregarden.services.ticket_studio_service import (
        _assert_adapter_can_read_extra_dirs,
    )

    with mock.patch(
        "loregarden.agents.registry.get_agent",
        side_effect=_leftover_cursor,
    ):
        with pytest.raises(ValueError, match="local"):
            _assert_adapter_can_read_extra_dirs(_workspace(cli_adapter="default"))


def test_r2_no_production_caller_passes_agent_adapter():
    import loregarden

    root = Path(loregarden.__file__).parent
    leftover: list[str] = []
    for path in root.rglob("*.py"):
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8")
        if "agent_adapter=" not in text:
            continue
        if "def resolve_effective_adapter" in text or "def resolve_chat_adapter" in text:
            continue
        leftover.append(rel)
    assert leftover == []


def test_r2_invocation_selects_workspace_without_a_declared_cli_param(tmp_path, monkeypatch):
    monkeypatch.setenv("LOREGARDEN_CURSOR_BIN", "cursor-agent")
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("stage task", encoding="utf-8")
    inv = resolve_cli_invocation(
        agent_id="planner",
        prompt="stage task",
        prompt_file=prompt_file,
        skill_name="plan",
        workspace_root=tmp_path,
        workspace=_workspace(cli_adapter="cursor"),
    )
    assert inv.adapter == "cursor"


def test_r2_usage_service_does_not_pass_a_shim_agent_adapter():
    src = (REPO / "server" / "loregarden" / "services" / "usage_service.py").read_text(
        encoding="utf-8"
    )
    assert "agent_adapter=" not in src


# --- R3 / R4 mutations --------------------------------------------------------


def test_r3_does_not_collapse_to_selected_is_claude():
    """981 undoes a claude-only check; cursor==cursor must stay True."""
    assert adapter_model_pins_apply(pin_adapter="local", selected_adapter="local") is True
    assert (
        adapter_model_pins_apply(pin_adapter=CliAdapter.DEFAULT, selected_adapter="claude") is False
    )


def test_r3_weak_mcp_warning_classifies_remaining_pins_as_claude():
    src = inspect.getsource(maybe_warn_weak_mcp_model)
    assert "CliAdapter.CLAUDE" in src
    assert 'agent.get("adapter"' not in src
    assert "agent.get('adapter'" not in src


def test_r4_override_warning_signature_has_no_declared_cli_params():
    params = inspect.signature(adapter_override_warning).parameters
    assert "agent_adapter" not in params
    assert "pin_source" not in params
    assert "model" not in params


def test_r4_stage_model_drop_outranks_the_role_default():
    streamer = mock.Mock()
    run = AgentRun(run_code="r-stage", ticket_id="t", workspace_id="w", agent_id="learning")
    stage = WorkflowStageDef(key="plan", name="Plan", model="haiku")

    maybe_warn_adapter_override(
        streamer=streamer,
        run=run,
        agent={"adapter": "claude", "default_model": "opus"},
        stage_def=stage,
        invocation=CliInvocation(argv=["cursor-agent"], adapter="cursor"),
    )

    streamer.append.assert_called_once()
    _level, message = streamer.append.call_args.args
    assert message == (
        "Agent 'learning' model pin 'haiku' does not apply to cursor and was dropped."
    )
    assert "opus" not in message
    assert "declares adapter" not in message


def test_r4_drop_on_local_names_local_not_a_declared_cli():
    message = adapter_override_warning(
        agent_id="static_qa",
        selected_adapter="local",
        dropped_model="sonnet",
    )
    assert message == (
        "Agent 'static_qa' model pin 'sonnet' does not apply to local and was dropped."
    )


# --- R5 leftover role CLI must not steer chat ---------------------------------


def test_r5_leftover_role_cli_does_not_steer_unpinned_home():
    """Surface default is CHAT_SURFACE_DEFAULT, not get_agent().adapter.

    # CHECKPOINT: leftover role CLI is cursor; unpinned Home must still be claude.
    """
    with mock.patch(
        "loregarden.services.baxter_chat_service.get_agent",
        side_effect=_leftover_cursor,
    ):
        selected = home_chat_adapter(
            _workspace(cli_adapter="default"),
            BaxterChatSession(workspace_id="w", runtime_json="{}"),
        )
    assert selected == "claude"


def test_r5_inherit_blob_does_not_take_workspace_cursor():
    selected = home_chat_adapter(
        _workspace(cli_adapter="cursor"),
        BaxterChatSession(workspace_id="w", runtime_json='{"cli_adapter": "default"}'),
    )
    assert selected == "claude"


def test_r5_surface_override_blob_still_pins_cursor():
    selected = home_chat_adapter(
        _workspace(cli_adapter="default"),
        BaxterChatSession(workspace_id="w", runtime_json='{"cli_adapter": "cursor"}'),
    )
    assert selected == "cursor"


def test_r5_chat_callers_do_not_call_the_pipeline_resolver():
    from loregarden.services import baxter_chat_service, branch_triage_chat_service

    for fn in (
        baxter_chat_service.home_chat_adapter,
        baxter_chat_service.invoke_baxter_chat_model,
        branch_triage_chat_service.branch_chat_snapshot,
        branch_triage_chat_service.invoke_branch_triage_model,
    ):
        src = inspect.getsource(fn)
        assert "resolve_chat_adapter(" in src, fn.__name__
        assert "CHAT_SURFACE_DEFAULT" in src, fn.__name__
        assert "override_json" in src, fn.__name__
        assert "resolve_effective_adapter(" not in src, fn.__name__


def test_r5_ticket_studio_stays_on_the_pipeline_resolver():
    from loregarden.services import ticket_studio_service

    src = inspect.getsource(ticket_studio_service)
    assert "resolve_effective_adapter(" in src
    assert "resolve_chat_adapter(" not in src


# --- R6 / R8 / R10 client source mutations ------------------------------------


def test_r6_source_label_includes_process_default():
    src = (REPO / "client" / "src" / "components" / "WorkspaceRuntimeFields.tsx").read_text(
        encoding="utf-8"
    )
    assert "process default" in src
    assert "each stage's agent" not in src
    assert 'cli_adapter_source === "agent"' not in src
    assert "cli_adapter_source === 'agent'" not in src


def test_r8_runtime_model_select_hardcodes_claude_not_a_role_cli():
    # The agent editor moved off `StudioPage` into its own components; read the
    # page *and* them, so the move cannot carry a role CLI back in unseen.
    paths = (
        REPO / "client" / "src" / "pages" / "StudioPage.tsx",
        REPO / "client" / "src" / "components" / "studio" / "AgentEditorTabs.tsx",
        REPO / "client" / "src" / "components" / "studio" / "agentDraft.ts",
        REPO / "client" / "src" / "components" / "studio" / "studioWorkflowHelpers.ts",
    )
    src = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    assert "modelOptionsForAdapter('claude'" in src or 'modelOptionsForAdapter("claude"' in src
    assert "agentDraft.adapter" not in src
    assert "selectedAgent?.adapter" not in src
    assert "selectedAgent.adapter" not in src
    assert "agent-provider" not in src
    assert "ADAPTERS" not in src
    assert "draft.adapter" not in src
    assert 'adapter: "claude"' not in src
    assert "adapter: 'claude'" not in src


def test_r10_stage_model_hardcodes_claude_not_stage_agent_adapter():
    card = (REPO / "client" / "src" / "components" / "studio" / "StudioStagesCard.tsx").read_text(
        encoding="utf-8"
    )
    helpers = (
        REPO / "client" / "src" / "components" / "studio" / "studioWorkflowHelpers.ts"
    ).read_text(encoding="utf-8")
    src = card + helpers
    assert "modelOptionsForAdapter('claude'" in src or 'modelOptionsForAdapter("claude"' in src
    assert "stageAgent?.adapter" not in src
    assert "stageAgent.adapter" not in src
    assert 'placeholder="Model id"' not in src


def test_r11_client_preview_profile_has_no_provider():
    api_dir = REPO / "client" / "src" / "api"
    # The Studio view types were lifted out of `types.ts` into `studioTypes.ts`,
    # which `types.ts` re-exports; read both so the move cannot hide a leftover.
    types = "\n".join(
        (api_dir / name).read_text(encoding="utf-8") for name in ("types.ts", "studioTypes.ts")
    )
    block = types.split("export interface StudioAgentPreviewProfile")[1].split("}")[0]
    assert "provider" not in block
    agent_block = types.split("export interface StudioAgent {")[1].split("}")[0]
    assert "adapter:" not in agent_block


# --- R7 snapshot vs leftover / drifted pin ------------------------------------


def test_r7_steer_prefers_dispatch_snapshot_over_leftover_role_and_drifted_workspace(
    db_session, monkeypatch
):
    monkeypatch.setattr(
        "loregarden.agents.registry.get_agent",
        _leftover_cursor,
    )
    from loregarden.models.domain.enums import RunStatus
    from loregarden.services.run_steering import steer_refusal

    ticket = db_session.exec(select(Ticket)).first()
    workspace = db_session.get(Workspace, ticket.workspace_id)
    workspace.cli_adapter = "cursor"
    db_session.add(workspace)
    run = AgentRun(
        run_code="run_snap_steer",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="planner",
        status=RunStatus.RUNNING,
        runtime_exit_action_snapshot_json=json.dumps({"adapter": "claude"}),
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)

    assert steer_refusal(run) == ""


def test_r7_reconstructed_invocation_prefers_dispatch_snapshot_when_workspace_drifted(
    db_session, monkeypatch
):
    monkeypatch.setattr(
        "loregarden.agents.registry.get_agent",
        _leftover_cursor,
    )
    from loregarden.services.run_resupervise import _reconstructed_invocation

    ticket = db_session.exec(select(Ticket)).first()
    workspace = db_session.get(Workspace, ticket.workspace_id)
    workspace.cli_adapter = "cursor"
    db_session.add(workspace)
    run = AgentRun(
        run_code="run_snap_recon",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="planner",
        model="opus",
        effort="high",
        runtime_exit_action_snapshot_json=json.dumps({"adapter": "claude"}),
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)

    inv = _reconstructed_invocation(run)
    assert inv.adapter == "claude"


def test_r7_steer_refusal_does_not_read_get_agent_adapter():
    from loregarden.services import run_steering

    src = inspect.getsource(run_steering.steer_refusal)
    assert 'get("adapter"' not in src
    assert "get_agent(" not in src


def test_r7_reconstructed_invocation_does_not_read_get_agent_adapter():
    from loregarden.services import run_resupervise

    src = inspect.getsource(run_resupervise._reconstructed_invocation)
    assert "get_agent(" not in src
    assert (
        'get("adapter"' not in src or "stored_run_snapshot" in src or "runtime_exit_action" in src
    )
