"""Contracts for lg-protocol-executors-1612 — a role does not declare a CLI.

Ticket ACs R1–R7, R12 (server). Client surfaces are in the Jest files.
Every test that walks the last pipeline tier delenv's LOREGARDEN_CLI_ADAPTER so
conftest.force_local_cli_adapter cannot make inherit untestable.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from unittest import mock

import pytest
from loregarden.agents.cli_adapters import (
    resolve_cli_invocation,
    resolve_terminal_handoff_invocation,
)
from loregarden.agents.registry import AGENTS
from loregarden.agents.tool_grants import analyze_tool_grants
from loregarden.models.domain import (
    AgentRun,
    BaxterChatSession,
    StudioAgent,
    StudioAgentCreate,
    StudioAgentPreviewProfile,
    StudioAgentPreviewRequest,
    StudioAgentUpdate,
    StudioAgentView,
    StudioGeneratedAgent,
    Ticket,
    Workspace,
)
from loregarden.models.domain.enums import CliAdapter, ToolGrantWarningCode
from loregarden.services.baxter_chat_service import home_chat_adapter
from loregarden.services.cli_settings import (
    CLAUDE_MODEL_OPTIONS,
    adapter_model_pins_apply,
    adapter_pin_source,
    resolve_chat_adapter,
    resolve_effective_adapter,
    resolve_runtime_effective,
)
from loregarden.services.studio_agent_prompt import _studio_agent_dict
from loregarden.services.studio_generation import (
    AGENT_GENERATE_JSON_SCHEMA,
    parse_agent_generate_payload,
)
from loregarden.services.studio_service import (
    _AGENT_SNAPSHOT_FIELDS,
    _AGENT_UPDATE_COLUMNS,
    StudioService,
)
from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy import inspect as sa_inspect
from sqlmodel import select


@pytest.fixture(autouse=True)
def _no_env_adapter_pin(monkeypatch):
    monkeypatch.delenv("LOREGARDEN_CLI_ADAPTER", raising=False)


def _workspace(**kwargs) -> Workspace:
    return Workspace(slug="ws", name="ws", **kwargs)


# --- R1 Role has no CLI -------------------------------------------------------


def test_r1_no_agents_dict_declares_a_cli():
    for agent_id, cfg in AGENTS.items():
        assert "adapter" not in cfg, f"{agent_id} still declares a CLI"


def test_r1_studio_agent_model_has_no_adapter_column():
    assert "adapter" not in StudioAgent.model_fields


def test_r1_schemas_have_no_adapter_or_preview_provider():
    for model in (
        StudioAgentCreate,
        StudioAgentUpdate,
        StudioAgentView,
        StudioAgentPreviewRequest,
        StudioGeneratedAgent,
    ):
        assert "adapter" not in model.model_fields, model.__name__
    assert "provider" not in StudioAgentPreviewProfile.model_fields


def test_r1_create_body_that_sends_adapter_is_not_stored(db_session):
    """Extra-ignore or reject — either way the row does not store a CLI."""
    try:
        body = StudioAgentCreate.model_validate(
            {"slug": "no-cli", "name": "No CLI", "adapter": "cursor"}
        )
    except ValidationError:
        return
    assert "adapter" not in body.model_dump()
    saved = StudioService(db_session).create_agent(body)
    assert not hasattr(saved, "adapter")
    dumped = saved.model_dump()
    assert "adapter" not in dumped


def test_r1_snapshot_and_update_tuples_omit_adapter():
    assert "adapter" not in _AGENT_SNAPSHOT_FIELDS
    assert "adapter" not in {src for src, _dst in _AGENT_UPDATE_COLUMNS}


def test_r1_studio_agent_dict_does_not_copy_adapter(db_session):
    slug = "dict-role"
    StudioService(db_session).create_agent(StudioAgentCreate(slug=slug, name="Dict Role"))
    row = db_session.exec(select(StudioAgent).where(StudioAgent.slug == slug)).one()
    copied = _studio_agent_dict(row)
    assert "adapter" not in copied


def test_r1_generate_schema_and_parser_do_not_author_a_cli():
    assert "adapter" not in AGENT_GENERATE_JSON_SCHEMA
    generated = parse_agent_generate_payload(
        '{"name": "Localization Reviewer", "adapter": "cursor", "role_body": "x"}'
    )
    assert generated is not None
    assert "adapter" not in generated.model_dump()


def test_r1_drop_columns_if_present_alters_and_is_a_noop_when_absent():
    from loregarden.db import migration_utils

    assert hasattr(migration_utils, "drop_columns_if_present")
    drop = migration_utils.drop_columns_if_present
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE studio_agents (id INTEGER, adapter VARCHAR, name VARCHAR)"))
        with mock.patch.object(migration_utils, "_rebuild_table") as rebuild:
            drop(conn, "studio_agents", ["adapter"])
            rebuild.assert_not_called()
            drop(conn, "studio_agents", ["adapter"])
            rebuild.assert_not_called()
        cols = migration_utils.table_columns(conn, "studio_agents")
        assert "adapter" not in cols
        assert cols == {"id", "name"}


def test_r1_live_studio_agents_table_has_no_adapter_column(db_session):
    cols = {c["name"] for c in sa_inspect(db_session.get_bind()).get_columns("studio_agents")}
    assert "adapter" not in cols


def test_r1_agent_view_json_has_no_adapter(client):
    body = client.get("/api/studio/agents/planner").json()
    assert "adapter" not in body


# --- R2 Pipeline harness env → ticket → workspace → process -------------------


def test_r2_resolve_effective_adapter_has_no_agent_fallback_or_default():
    params = inspect.signature(resolve_effective_adapter).parameters
    assert "agent_adapter" not in params
    assert "fallback" not in params
    assert "default" not in params


def test_r2_env_wins_over_ticket_and_workspace():
    ws = _workspace(cli_adapter="claude")
    with mock.patch.dict("os.environ", {"LOREGARDEN_CLI_ADAPTER": "cursor"}):
        assert resolve_effective_adapter(workspace=ws, ticket_adapter="claude") == "cursor"


def test_r2_ticket_wins_when_not_default():
    ws = _workspace(cli_adapter="cursor")
    assert resolve_effective_adapter(workspace=ws, ticket_adapter="claude") == "claude"


def test_r2_workspace_wins_when_ticket_is_default():
    ws = _workspace(cli_adapter="cursor")
    assert resolve_effective_adapter(workspace=ws, ticket_adapter="default") == "cursor"
    assert resolve_effective_adapter(workspace=ws, ticket_adapter="") == "cursor"


def test_r2_last_tier_is_settings_local():
    from loregarden.config import Settings

    assert Settings.model_fields["cli_adapter"].default == "local"
    ws = _workspace(cli_adapter="default")
    assert resolve_effective_adapter(workspace=ws, ticket_adapter="default") == "local"
    assert resolve_effective_adapter(workspace=None, ticket_adapter="default") == "local"


def test_r2_does_not_read_an_agent_dict():
    src = inspect.getsource(resolve_effective_adapter)
    assert "agent.get" not in src
    assert "agent_adapter" not in src


def test_r2_cli_invocation_and_terminal_handoff_drop_declared_cli_adapter():
    assert "adapter" not in inspect.signature(resolve_cli_invocation).parameters
    assert "adapter" not in inspect.signature(resolve_terminal_handoff_invocation).parameters
    assert "agent_adapter=" not in inspect.getsource(resolve_cli_invocation)
    assert "agent_adapter=" not in inspect.getsource(resolve_terminal_handoff_invocation)


# --- R3 pins_apply ------------------------------------------------------------


def test_r3_signature_is_pin_adapter():
    params = inspect.signature(adapter_model_pins_apply).parameters
    assert "pin_adapter" in params
    assert "agent_adapter" not in params


def test_r3_empty_and_default_are_false():
    assert adapter_model_pins_apply(pin_adapter="", selected_adapter="cursor") is False
    assert adapter_model_pins_apply(pin_adapter="default", selected_adapter="claude") is False
    assert adapter_model_pins_apply(pin_adapter="claude", selected_adapter="cursor") is False
    assert adapter_model_pins_apply(pin_adapter="claude", selected_adapter="claude") is True


def test_r3_does_not_infer_family_from_the_model_id():
    src = inspect.getsource(adapter_model_pins_apply)
    assert "sonnet" not in src
    assert "opus" not in src
    assert "claude-opus" not in src


def test_r3_callers_pass_claude_for_remaining_pins():
    """Remaining default_model / stage model / claude_model pins are Claude-authored."""
    from loregarden.agents.cli_adapters import resolve_cli_invocation as resolve_inv

    src = inspect.getsource(resolve_inv)
    assert "CliAdapter.CLAUDE" in src or "pin_adapter=CliAdapter.CLAUDE" in src.replace(" ", "")


# --- R5 Chat seam -------------------------------------------------------------


def test_r5_chat_surface_default_is_claude_next_to_the_resolver():
    from loregarden.services import cli_settings

    assert cli_settings.CHAT_SURFACE_DEFAULT == CliAdapter.CLAUDE
    params = inspect.signature(resolve_chat_adapter).parameters
    assert "surface_default" in params
    assert "agent_adapter" not in params


def test_r5_chat_does_not_inherit_workspace_cli_adapter():
    selected = resolve_chat_adapter(surface_default=CliAdapter.CLAUDE, override_json="{}")
    assert selected == "claude"
    assert "workspace" not in inspect.signature(resolve_chat_adapter).parameters


def test_r5_unpinned_home_selects_claude_not_workspace_or_local():
    ws = _workspace(cli_adapter="cursor")
    session = BaxterChatSession(workspace_id="w", runtime_json="{}")
    assert home_chat_adapter(ws, session) == "claude"


def test_r5_home_and_branch_triage_call_the_chat_seam_not_the_pipeline():
    from loregarden.services import baxter_chat_service, branch_triage_chat_service

    for module in (baxter_chat_service, branch_triage_chat_service):
        src = inspect.getsource(module)
        assert "resolve_chat_adapter(" in src
        assert "CHAT_SURFACE_DEFAULT" in src


def test_r5_triage_passes_surface_default_not_agent_adapter():
    from loregarden.services import triage_run_service, triage_service

    for module in (triage_service, triage_run_service):
        src = inspect.getsource(module)
        assert "CHAT_SURFACE_DEFAULT" in src
        assert 'agent.get("adapter"' not in src
        assert "agent.get('adapter'" not in src


def test_r5_no_leftover_agent_get_adapter_in_the_package():
    """Chat and pipeline paths must not read a role CLI. stored_run_snapshot
    adapter (selected harness) is out of scope — R7 leaves it alone."""
    import loregarden

    root = Path(loregarden.__file__).parent
    needles = (
        'agent.get("adapter"',
        "agent.get('adapter'",
        'cfg.get("adapter"',
        "cfg.get('adapter'",
        'snap.get("adapter"',
        "snap.get('adapter'",
        'request.agent.get("adapter"',
        "request.agent.get('adapter'",
    )
    allowed = {"services/exit_action_approvals.py"}
    hits: list[str] = []
    for path in root.rglob("*.py"):
        rel = path.relative_to(root).as_posix()
        if rel in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        if any(needle in text for needle in needles):
            hits.append(rel)
    assert hits == []


def test_r7_pipeline_callers_stop_passing_a_declared_cli():
    import loregarden

    root = Path(loregarden.__file__).parent
    callers = (
        "agents/executors/cli.py",
        "services/run_capacity.py",
        "services/agent_turn_runner.py",
        "services/ticket_studio_service.py",
    )
    leftover = []
    for rel in callers:
        text = (root / rel).read_text(encoding="utf-8")
        if 'agent.get("adapter"' in text or "agent.get('adapter'" in text:
            leftover.append(rel)
        if "agent_adapter=" in text:
            leftover.append(rel)
    assert leftover == []


# --- R6 inherit = local (process default) -------------------------------------


def test_r6_pin_source_last_tier_is_process():
    assert adapter_pin_source(workspace=_workspace(cli_adapter="default")) == "process"
    assert adapter_pin_source(workspace=_workspace(cli_adapter="")) == "process"
    assert adapter_pin_source(workspace=None) == "process"


def test_r6_runtime_effective_names_local_process_not_agent():
    effective = resolve_runtime_effective(_workspace(cli_adapter="default"))
    assert effective["cli_adapter"] == "local"
    assert effective["cli_adapter_source"] == "process"
    src = inspect.getsource(resolve_runtime_effective)
    assert '"agent"' not in src or "cli_adapter_source" in src
    assert 'cli_adapter_source": "agent"' not in inspect.getsource(resolve_runtime_effective)


def test_r6_claude_model_options_empty_label_is_unchanged():
    """Workspace/ticket Claude pickers still mean a Claude profile. R8 forbids reuse."""
    empty = next(opt for opt in CLAUDE_MODEL_OPTIONS if opt["id"] == "")
    assert empty["label"] == "Default (Claude Code profile)"


# --- R7 restore / steering / leftover readers ---------------------------------


def test_r7_restoring_v1_snapshot_with_adapter_does_not_write_a_cli(db_session):
    import json

    from loregarden.models.domain import StudioAgentVersion

    service = StudioService(db_session)
    service.create_agent(StudioAgentCreate(slug="restored-role", name="Restored"))
    agent = db_session.exec(select(StudioAgent).where(StudioAgent.slug == "restored-role")).one()
    row = db_session.exec(
        select(StudioAgentVersion).where(
            StudioAgentVersion.agent_id == agent.id, StudioAgentVersion.version == agent.version
        )
    ).one()
    snap = json.loads(row.snapshot_json)
    snap["adapter"] = "cursor"
    row.snapshot_json = json.dumps(snap)
    db_session.add(row)
    db_session.commit()

    restored = service.restore_agent_version("restored-role", agent.version)
    assert "adapter" not in restored.model_dump()
    row_after = db_session.exec(
        select(StudioAgent).where(StudioAgent.slug == "restored-role")
    ).one()
    assert not hasattr(row_after, "adapter")
    assert resolve_effective_adapter(workspace=_workspace(cli_adapter="default")) == "local"


def test_r7_no_agent_run_adapter_column():
    assert "adapter" not in AgentRun.model_fields


def test_r7_reconstructed_invocation_does_not_default_missing_registry_key_to_local(
    db_session,
):
    from loregarden.services.run_resupervise import _reconstructed_invocation

    ticket = db_session.exec(select(Ticket)).first()
    workspace = db_session.get(Workspace, ticket.workspace_id)
    workspace.cli_adapter = "cursor"
    db_session.add(workspace)
    db_session.commit()
    run = AgentRun(
        run_code="run_recon",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="planner",
        model="gpt-5",
        effort="high",
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)

    inv = _reconstructed_invocation(run)
    assert inv.adapter == "cursor"
    assert inv.adapter != "local"


# --- R12 grants ---------------------------------------------------------------


def test_r12_analyze_tool_grants_has_no_adapter_argument():
    params = inspect.signature(analyze_tool_grants).parameters
    assert "adapter" not in params


def test_r12_adapter_ignores_grants_enum_is_gone():
    assert getattr(ToolGrantWarningCode, "ADAPTER_IGNORES_GRANTS", None) is None


def test_r12_dispatch_warnings_has_no_runtime_grants_warning():
    from loregarden.agents.executors import dispatch_warnings

    src = inspect.getsource(dispatch_warnings)
    assert "ADAPTER_IGNORES_GRANTS" not in src
    assert "adapter_ignores_grants" not in src
