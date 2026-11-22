"""A workspace whose workflow-enforcement module lacks the stage-report section
gets this control plane's copy, not an empty contract.

Loremaker's module had no `STAGE REPORT CONTRACT` section, so every stage there
parked at preflight before an agent ran. Nothing carries the module into other
workspaces, so loregarden's own copy is the fallback; a workspace's own section
still wins when it has one.
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest import mock

import pytest
from loregarden.agents import mcp_context
from loregarden.agents.mcp_context import (
    STAGE_REPORT_SECTION_TITLE,
    WORKFLOW_ENFORCEMENT_DOC_REL,
    load_stage_report_contract_doc,
    resolve_stage_report_contract,
)
from loregarden.models.domain import ContractAudience


def _module(agent_context: Path, body: str | None) -> Path:
    """A workflow-enforcement module, with the section when ``body`` is given."""
    path = agent_context / WORKFLOW_ENFORCEMENT_DOC_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    divider = "-" * 30
    section = f"{divider}\n{STAGE_REPORT_SECTION_TITLE}\n{divider}\n{body}\n" if body else ""
    path.write_text(f"intro\n\n{section}", encoding="utf-8")
    return path


@pytest.fixture(name="shared")
def shared_fixture(tmp_path: Path):
    """This control plane's agent_context, holding a contract."""
    root = tmp_path / "control-plane"
    _module(root, "The control plane's report format.")
    with (
        mock.patch.object(mcp_context.settings, "agent_context_dir", root),
        mock.patch.object(mcp_context, "_contract_fallback_warned", set()),
    ):
        yield root


def test_a_workspace_without_the_section_gets_the_control_planes(tmp_path, shared):
    workspace = tmp_path / "workspace"
    _module(workspace, None)

    contract = resolve_stage_report_contract(workspace, audience=ContractAudience.OTHER)

    assert contract.text == "The control plane's report format."
    assert contract.fallback is True
    assert contract.source == shared / WORKFLOW_ENFORCEMENT_DOC_REL


def test_a_workspace_with_no_module_at_all_gets_the_control_planes(tmp_path, shared):
    contract = resolve_stage_report_contract(tmp_path / "bare", audience=ContractAudience.OTHER)

    assert contract.text == "The control plane's report format."
    assert contract.fallback is True


def test_the_workspaces_own_section_wins(tmp_path, shared):
    workspace = tmp_path / "workspace"
    own = _module(workspace, "The workspace's own format.")

    contract = resolve_stage_report_contract(workspace, audience=ContractAudience.OTHER)

    assert contract.text == "The workspace's own format."
    assert contract.fallback is False
    assert contract.source == own


def test_neither_copy_is_empty_and_not_a_fallback(tmp_path):
    empty = tmp_path / "control-plane"
    empty.mkdir()
    with mock.patch.object(mcp_context.settings, "agent_context_dir", empty):
        contract = resolve_stage_report_contract(
            tmp_path / "workspace", audience=ContractAudience.OTHER
        )

    assert contract.text == ""
    assert contract.fallback is False
    assert contract.source is None


def test_the_control_plane_itself_is_never_its_own_fallback(shared):
    _module(shared, None)

    contract = resolve_stage_report_contract(shared, audience=ContractAudience.OTHER)

    assert contract.text == ""
    assert contract.fallback is False


def test_the_prompt_loader_warns_once_per_workspace(
    tmp_path, shared, caplog: pytest.LogCaptureFixture
):
    workspace = tmp_path / "workspace"
    _module(workspace, None)

    with caplog.at_level(logging.WARNING, logger=mcp_context.__name__):
        first = load_stage_report_contract_doc(workspace, audience=ContractAudience.OTHER)
        second = load_stage_report_contract_doc(workspace, audience=ContractAudience.OTHER)

    assert first == second == "The control plane's report format."
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert str(workspace) in warnings[0].getMessage()
