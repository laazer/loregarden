"""The managed control-plane section installed into a workspace's AGENTS.md.

An agent opened in blobert or lore-eden reads that repo's AGENTS.md. Until this
block is there, nothing tells it the ticket it was handed lives in a database it
can reach — so it greps for a ticket file, finds none, and infers requirements
from the code. These pin the parts that make the block safe to install into a
repository loregarden does not own, and honest about the tools it names.
"""

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

from loregarden.mcp.tool_ids import McpTool

_ROOT = Path(__file__).resolve().parents[2]
_INSTALLER = _ROOT / "scripts" / "install_workspace_agents_doc.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


installer = _load("install_workspace_agents_doc", _INSTALLER)

EXISTING = "# Workspace\n\nIts own notes.\n"


def _install(target: Path, *, check: bool = False, slug: str | None = None) -> int:
    argv = ["--agents-file", str(target), "--loregarden-root", str(_ROOT)]
    if slug:
        argv += ["--workspace-slug", slug]
    if check:
        argv.append("--check")
    saved = sys.argv
    sys.argv = ["install_workspace_agents_doc.py", *argv]
    try:
        return installer.main()
    finally:
        sys.argv = saved


def test_creates_the_file_when_the_workspace_has_none(tmp_path: Path):
    """lore-eden has no AGENTS.md at all, and is still a workspace agents work in."""
    target = tmp_path / "AGENTS.md"
    assert _install(target) == 0
    text = target.read_text()
    assert installer.BEGIN_MARKER in text
    assert installer.END_MARKER in text


def test_leaves_the_workspaces_own_content_alone(tmp_path: Path):
    target = tmp_path / "AGENTS.md"
    target.write_text(EXISTING)
    assert _install(target) == 0
    text = target.read_text()
    assert text.startswith(EXISTING.rstrip("\n"))
    assert text.index(EXISTING.rstrip("\n")) < text.index(installer.BEGIN_MARKER)


def test_is_idempotent(tmp_path: Path):
    target = tmp_path / "AGENTS.md"
    target.write_text(EXISTING)
    assert _install(target) == 0
    once = target.read_text()
    assert _install(target) == 0
    assert target.read_text() == once


def test_refresh_replaces_rather_than_appends(tmp_path: Path):
    """A stale block must not survive beside the new one, contradicting it."""
    target = tmp_path / "AGENTS.md"
    target.write_text(EXISTING)
    assert _install(target) == 0
    stale = target.read_text().replace("loregarden's database", "a file in this repo")
    target.write_text(stale)

    assert _install(target) == 0
    text = target.read_text()
    assert text.count(installer.BEGIN_MARKER) == 1
    assert "a file in this repo" not in text


def test_check_mode_reports_without_writing(tmp_path: Path):
    target = tmp_path / "AGENTS.md"
    target.write_text(EXISTING)

    assert _install(target, check=True) == 1  # missing
    assert target.read_text() == EXISTING

    assert _install(target) == 0
    current = target.read_text()
    assert _install(target, check=True) == 0  # current

    target.write_text(current.replace("Loregarden control plane", "Old heading", 1))
    outdated = target.read_text()
    assert _install(target, check=True) == 1  # outdated
    assert target.read_text() == outdated


def test_check_mode_does_not_create_the_file(tmp_path: Path):
    target = tmp_path / "AGENTS.md"
    assert _install(target, check=True) == 1
    assert not target.exists()


def test_slug_reaches_the_cli_examples(tmp_path: Path):
    target = tmp_path / "AGENTS.md"
    assert _install(target, slug="blobert") == 0
    assert "workspace_slug=blobert" in target.read_text()


def test_without_a_slug_the_block_carries_a_placeholder_not_a_guess(tmp_path: Path):
    """A wrong slug is a command that fails; a placeholder is one you have to fill in."""
    target = tmp_path / "AGENTS.md"
    assert _install(target) == 0
    text = target.read_text()
    assert f"workspace_slug={installer.SLUG_PLACEHOLDER}" in text
    assert tmp_path.name not in text


def test_a_refresh_keeps_the_slug_it_was_installed_with(tmp_path: Path):
    """Nothing else records it, so a slug-less refresh would silently downgrade
    working commands back to a placeholder."""
    target = tmp_path / "AGENTS.md"
    assert _install(target, slug="blobert") == 0
    assert _install(target) == 0
    assert "workspace_slug=blobert" in target.read_text()


def test_check_does_not_call_a_current_block_outdated_over_the_slug(tmp_path: Path):
    target = tmp_path / "AGENTS.md"
    assert _install(target, slug="blobert") == 0
    assert _install(target, check=True) == 0


def test_an_explicit_slug_overrides_the_recorded_one(tmp_path: Path):
    target = tmp_path / "AGENTS.md"
    assert _install(target, slug="blobert") == 0
    assert _install(target, slug="lore-eden") == 0
    text = target.read_text()
    assert "workspace_slug=lore-eden" in text
    assert "blobert" not in text


def test_paths_point_into_the_loregarden_checkout(tmp_path: Path):
    """The workspace has no copy of these; a relative path would resolve to nothing."""
    target = tmp_path / "AGENTS.md"
    assert _install(target) == 0
    text = target.read_text()
    for path in (
        _ROOT / "scripts" / "loregarden-cli.sh",
        _ROOT / "agent_context" / "agents" / "common_assets" / "loregarden_mcp_v1.md",
    ):
        assert path.exists(), path
        assert str(path) in text


def test_every_tool_the_block_names_exists(tmp_path: Path):
    """The block is documentation an agent will act on. A renamed tool must fail here."""
    target = tmp_path / "AGENTS.md"
    assert _install(target) == 0
    named = set(re.findall(r"`(loregarden_[a-z_]+)`", target.read_text()))
    assert named, "the block should name tools"
    assert named <= {tool.value for tool in McpTool}


def test_wrapper_refuses_a_non_repository(tmp_path: Path):
    result = subprocess.run(
        [str(_ROOT / "scripts" / "install-workspace-docs.sh"), str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "not a git repository" in result.stderr
    assert not (tmp_path / "AGENTS.md").exists()


def test_wrapper_refuses_a_slug_for_several_workspaces(tmp_path: Path):
    result = subprocess.run(
        [
            str(_ROOT / "scripts" / "install-workspace-docs.sh"),
            "--slug",
            "blobert",
            str(tmp_path / "a"),
            str(tmp_path / "b"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "one workspace" in result.stderr
