"""The loregarden Claude Code plugin (`claude-plugin/`) is complete and its hooks dispatch.

The plugin reaches the hooks and skills through symlinks into `.claude/hooks`
and `agent_context/skills`, which an install copies as real files. A symlink
that stops resolving, or a hooks.json naming a hook the dispatcher does not
know, would ship a plugin that installs cleanly and then does nothing.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_PLUGIN = _ROOT / "claude-plugin"
_HOOKS = _ROOT / ".claude" / "hooks"
_spec = importlib.util.spec_from_file_location("plugin_dispatch", _HOOKS / "plugin_dispatch.py")
dispatch = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dispatch)

_COMMAND = re.compile(r'"\$\{CLAUDE_PLUGIN_ROOT\}/hooks/scripts/plugin_dispatch\.py" (\w+)$')


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_the_marketplace_lists_the_plugin_by_its_manifest_name():
    market = _json(_ROOT / ".claude-plugin" / "marketplace.json")
    manifest = _json(_PLUGIN / ".claude-plugin" / "plugin.json")
    (entry,) = market["plugins"]
    assert entry["name"] == manifest["name"] == "loregarden"
    assert (_ROOT / entry["source"]).resolve() == _PLUGIN.resolve()


def test_the_mcp_server_url_defaults_to_the_local_server_and_honors_the_override():
    server = _json(_PLUGIN / ".mcp.json")["mcpServers"]["loregarden"]
    assert server["url"] == "${LOREGARDEN_MCP_URL:-http://127.0.0.1:8000/mcp}"


def test_every_shipped_skill_resolves_to_a_skill_file():
    skills = sorted((_PLUGIN / "skills").iterdir())
    assert skills, "the plugin ships no skills"
    for skill in skills:
        assert skill.is_symlink(), f"{skill.name}: copy, not link; it will drift from agent_context"
        assert (skill / "SKILL.md").is_file(), f"{skill.name} does not resolve"


def test_every_hook_command_names_a_dispatchable_hook_that_exists():
    assert (_PLUGIN / "hooks" / "scripts").resolve() == _HOOKS.resolve()
    named = set()
    for group in _json(_PLUGIN / "hooks" / "hooks.json")["hooks"]["PreToolUse"]:
        for hook in group["hooks"]:
            found = _COMMAND.search(hook["command"])
            assert found, hook["command"]
            named.add(found.group(1))
    assert named == dispatch.PLUGIN_HOOKS
    for name in named:
        assert (_HOOKS / f"{name}.py").is_file()


def test_the_hooks_module_exists_and_its_state_is_namespaced_by_the_plugin_name():
    """The engine keys `$.state` by plugin name; a contract under any other name is never read."""
    hooks = _json(_PLUGIN / "hooks" / "hooks.json")
    manifest = _json(_PLUGIN / ".claude-plugin" / "plugin.json")
    for module in hooks["modules"]:
        assert (_PLUGIN / "hooks" / module).is_file(), module
    contract = (_PLUGIN / manifest["types"]).read_text(encoding="utf-8")
    assert re.search(rf"^\s+{manifest['name']}: \{{", contract, re.MULTILINE)
    garden = (_PLUGIN / "hooks" / "garden.tsx").read_text(encoding="utf-8")
    assert set(re.findall(r"plugin: '([\w-]+)'", garden)) == {manifest["name"]}


def _run(hook: str, project_dir: Path, command: str = "git -c commit.gpgsign=false commit -m x"):
    payload = json.dumps({"cwd": str(project_dir), "tool_input": {"command": command}})
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}
    env["CLAUDE_PROJECT_DIR"] = str(project_dir)
    return subprocess.run(
        [sys.executable, str(_HOOKS / "plugin_dispatch.py"), hook],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


@pytest.fixture(name="signing_repo")
def signing_repo_fixture(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}
    for args in (["init", "-q"], ["config", "commit.gpgsign", "true"]):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=env)
    return repo


def test_the_dispatcher_runs_the_hook_and_passes_its_verdict_through(signing_repo):
    result = _run("git_signing_guard", signing_repo)
    assert result.returncode == 0, result.stderr
    verdict = json.loads(result.stdout)["hookSpecificOutput"]
    assert verdict["permissionDecision"] == "deny"


def test_the_dispatcher_steps_aside_where_the_project_ships_its_own_copy(signing_repo):
    own = signing_repo / ".claude" / "hooks"
    own.mkdir(parents=True)
    (own / "git_signing_guard.py").write_text("", encoding="utf-8")
    result = _run("git_signing_guard", signing_repo)
    assert (result.returncode, result.stdout) == (0, "")


def test_an_unknown_hook_is_a_visible_error_not_a_silent_pass(signing_repo):
    result = _run("not_a_hook", signing_repo)
    assert result.returncode == 1
    assert "not_a_hook" in result.stderr
