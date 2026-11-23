"""No agent this control plane spawns can take or bury an entry on the shared stash stack.

`run_8fa4e2` ran `git stash` with nothing to save, then `git stash pop`, which
applied and dropped the operator's own stash into a ticket worktree. The guard
refuses the command; these tests prove the refusal, where it is installed, and
that the bridge's policy gives the same answer.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest
from loregarden.agents import cli_adapters, shared_stash_guard
from loregarden.agents.cli_adapters import (
    STASH_GUARD_UNSUPPORTED,
    build_interactive_invocation,
    build_triage_invocation,
    chat_hook_settings,
    resolve_cli_invocation,
    stage_run_hook_settings,
)
from loregarden.agents.executors.tool_auto_approve import (
    denied_cli_tool_message,
    is_chat_auto_approved_cli_tool,
)
from loregarden.agents.shared_stash_guard import denial, stash_commands
from loregarden.mcp.caller import ORCHESTRATED_ENV
from loregarden.models.domain import CliAdapter
from loregarden.services.git_subprocess import scrubbed_git_env
from tests.repo_paths import REPO_ROOT

SHA = "ddd1db5c299c442c5b1ebd4684c4e18b1a5f3c20"

#: What run_8fa4e2 sent, verbatim but for the paths.
INCIDENT = (
    "cd /w/ticket/server/loremaker\n"
    "git stash\n"
    "poetry run python -m pytest tests/unit/test_workflow_api.py::test_x 2>&1 | tail -20\n"
    "git stash pop 2>&1 | tail -5"
)

TAKES_OR_MOVES = [
    "git stash",
    "git stash pop",
    "git stash -u",
    "git stash -q --keep-index",
    "git stash -- server/x.py",
    "git stash push -u -m tag",
    "git stash save wip",
    "git stash apply",
    "git stash apply stash@{0}",
    "git stash apply 0",
    "git stash apply 1234567",  # seven digits are an index to git, not a SHA
    "git stash apply --index",
    'git stash apply "$SHA"',  # nobody can read which commit this is
    "git stash pop stash@{2}",
    "git stash drop",
    "git stash drop stash@{0}",
    "git stash clear",
    "git stash branch rescue",
    "git stash import refs/x",
    "git stash $ACTION",
]

ALLOWED = [
    "git stash list",
    "git stash list --format='%H %gs'",
    "git stash list >/dev/null 2>&1",
    "git stash show -p stash@{0}",
    "git stash create",
    "git stash create wip",
    f"git stash store -m rescued {SHA}",
    f"git stash apply {SHA}",
    f"git stash apply --index {SHA}",
    f"git stash apply -q {SHA[:12]}",
    "git stash --help",
    "git status",
    "git diff -- server/stash.py",
    "git log --grep stash",
    "git checkout stash",
]


# --- the agent policy --------------------------------------------------------------


def test_the_incidents_own_command_is_refused_and_told_what_to_do_instead():
    reason = denial(INCIDENT, agent_run=True)
    assert "checkpoint log" in reason
    assert "temporary commit" in reason


@pytest.mark.parametrize("command", ["git stash", "git stash pop"])
def test_each_half_of_the_incident_is_refused_on_its_own(command):
    assert denial(command, agent_run=True)


@pytest.mark.parametrize("command", TAKES_OR_MOVES)
def test_an_agent_cannot_take_or_move_an_entry(command):
    assert denial(command, agent_run=True)


@pytest.mark.parametrize("command", ALLOWED)
def test_an_agent_can_look_and_can_apply_a_commit(command):
    assert denial(command, agent_run=True) == ""


@pytest.mark.parametrize(
    "command",
    [
        "git status\ngit stash pop",
        "git status;git stash pop",
        "git status&&git stash pop",
        "true || git stash pop",
        "git diff | tee d & git stash pop",
        "(cd server && git stash pop)",
        "{ git stash pop; }",
        "if git stash pop; then echo ok; fi",
        "! git stash pop",
        "git \\\n  stash \\\n  pop",
        "git 'stash' \"pop\"",
        "/usr/bin/git -C /w -c core.pager=cat --no-pager stash pop",
        "git --git-dir /w/.git --work-tree=/w stash pop",
        "FOO=1 env BAR=2 timeout 5 git stash pop",
        "echo x | xargs git stash pop",
        "bash -c 'git stash pop'",
        "zsh -lc 'cd /w && git stash pop'",
        "eval git stash pop",
        "eval 'git stash pop'",
        'echo "$( (cd server; ls); git stash pop )"',
        "out=$(git stash pop)",
        'echo "before $(git stash pop) after"',
        'echo "$(echo "$(git stash pop)")"',
        "echo `git stash pop`",
        "diff <(git stash pop) x",
        "bash <<'EOF'\ngit stash pop\nEOF",
        "bash <<< 'git stash pop'",
        "cat <<EOF\n$(git stash pop)\nEOF",
        'cat <<EOF\na "quoted\\" line\n`git stash pop`\nEOF',
        "cat <<'EOF'\ndon't\nEOF\ngit stash pop",
        "cat <<-'EOF'\n\tbody\n\tEOF\ngit stash pop",
        "git commit -m \"$(cat <<'EOF'\nfix(x): it's a) and b\nEOF\n)\" && git stash pop",
        "echo a#b && git stash pop",
        "git status # it's fine\ngit stash pop",
        "git stash list && git stash pop",
        "git stash>/dev/null",
        "git stash 2>/dev/null",
        "echo 'unterminated\ngit stash pop' && git stash",
        "git-stash pop",
    ],
)
def test_shell_syntax_hides_nothing(command):
    assert denial(command, agent_run=True)


@pytest.mark.parametrize(
    "command",
    [
        "git commit -m 'refuse git stash pop'",
        'git commit -m "refuse git stash pop"',
        "grep -rn 'git stash' server",
        "gh pr create --body-file - <<'EOF'\nRefuses `git stash pop`.\nEOF",
        "git commit -F - <<'MSG'\ngit stash\ngit stash pop\nMSG",
        "git commit -m \"$(cat <<'EOF'\nit's the end of git stash pop\nEOF\n)\"",
        "git status # then git stash pop",
        "echo '$(git stash pop)'",
        "cat > notes <<< 'git stash pop'",
    ],
)
def test_text_that_only_mentions_a_stash_passes(command):
    assert denial(command, agent_run=True) == ""


def test_a_command_nested_too_deeply_to_read_is_refused_not_a_crash():
    nested = "$(" * 5000 + "git stash pop" + ")" * 5000
    assert denial(nested, agent_run=True)
    assert denial(nested, agent_run=False)
    assert denial("$(" * 5000, agent_run=True) == ""


def test_each_stash_of_a_command_is_seen():
    assert sorted(stash_commands(INCIDENT)) == [[], ["pop"]]


# --- the session policy ------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        INCIDENT,
        "git stash pop",
        "git stash pop stash@{1}",
        "git stash apply",
        "git stash apply stash@{0}",
        "git stash drop",
        "git stash clear",
        "git stash branch rescue",
    ],
)
def test_a_persons_session_cannot_restore_by_position(command):
    reason = denial(command, agent_run=False)
    assert "git stash apply <its SHA>" in reason
    assert "stash@{n}" in reason


@pytest.mark.parametrize(
    "command",
    [
        "git stash push -u -m tag",
        "git stash list --format='%H %gd %gs'",
        f"git stash apply {SHA}",
        'git stash apply "$SHA"',
        "git stash apply $(git stash list --format=%H -1)",
        "git stash drop stash@{3}",
        'git stash drop "$REF"',
    ],
)
def test_a_persons_session_keeps_the_tagged_protocol(command):
    assert denial(command, agent_run=False) == ""


# --- the hook, run as the CLIs run it ------------------------------------------------


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=scrubbed_git_env()
    ).stdout.strip()


@pytest.fixture(name="repo")
def repo_fixture(tmp_path: Path) -> Path:
    """A throwaway repository with one commit and one worktree."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "seed").write_text("seed\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "seed")
    return repo


def _run_hook(argv: list[str], payload: str, **env: str) -> subprocess.CompletedProcess[str]:
    clean = {k: v for k, v in scrubbed_git_env().items() if k != ORCHESTRATED_ENV}
    return subprocess.run(
        argv, input=payload, capture_output=True, text=True, timeout=60, env={**clean, **env}
    )


def _bash(command: str, cwd: Path) -> str:
    return json.dumps({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)})


_GUARD = REPO_ROOT / ".claude" / "hooks" / "shared_stash_guard.py"


def _system_pythons() -> list[str]:
    """What a hook's `python3` may be: this interpreter, and macOS's own 3.9 where it exists."""
    system = "/usr/bin/python3"
    return [sys.executable, *([system] if Path(system).exists() else [])]


@pytest.mark.parametrize("python", _system_pythons())
def test_the_hook_refuses_the_incident_for_a_marked_run(python, repo):
    done = _run_hook([python, str(_GUARD)], _bash(INCIDENT, repo), **{ORCHESTRATED_ENV: "1"})
    verdict = json.loads(done.stdout)["hookSpecificOutput"]
    assert verdict["hookEventName"] == "PreToolUse"
    assert verdict["permissionDecision"] == "deny"
    assert "agent runs" in verdict["permissionDecisionReason"]


def test_an_agent_is_refused_where_no_other_worktree_exists(repo):
    done = _run_hook(
        [sys.executable, str(_GUARD), "--agent-run"], _bash("git stash push -m x", repo)
    )
    assert '"deny"' in done.stdout


def test_a_persons_session_is_refused_only_where_worktrees_share_the_stack(repo, tmp_path):
    argv = [sys.executable, str(_GUARD)]
    assert _run_hook(argv, _bash("git stash pop", repo)).stdout == ""
    _git(repo, "worktree", "add", "-q", str(tmp_path / "second"))
    assert '"deny"' in _run_hook(argv, _bash("git stash pop", repo)).stdout
    assert _run_hook(argv, _bash("git stash push -u -m tag", repo)).stdout == ""


def test_a_repository_git_cannot_read_counts_as_shared(tmp_path):
    """A broken probe must never open the guard."""
    assert shared_stash_guard.stack_is_shared(str(tmp_path / "missing"))
    assert shared_stash_guard.stack_is_shared(str(tmp_path))


def test_an_unreadable_input_is_reported_and_does_not_wedge_the_shell():
    done = _run_hook([sys.executable, str(_GUARD), "--agent-run"], "not json")
    assert done.returncode == 1
    assert done.stdout == ""
    assert "unreadable hook input" in done.stderr


def test_the_incident_takes_the_operators_stash_when_nothing_refuses_it(repo, tmp_path):
    """What the guard is for: the pop in a clean worktree drops another worktree's entry."""
    (repo / "seed").write_text("the operator's work\n")
    _git(repo, "stash", "push", "-q", "-m", "operator")
    ticket = tmp_path / "ticket"
    _git(repo, "worktree", "add", "-q", str(ticket))

    sequence = "git stash\ngit stash pop"
    done = _run_hook([sys.executable, str(_GUARD), "--agent-run"], _bash(sequence, ticket))
    if '"deny"' not in done.stdout:
        subprocess.run(["sh", "-c", sequence], cwd=ticket, env=scrubbed_git_env(), check=False)

    assert "operator" in _git(repo, "stash", "list")
    assert (ticket / "seed").read_text() == "seed\n"


# --- where it is installed ----------------------------------------------------------


def _installed_guards(settings_json: str) -> list[str]:
    return [
        hook["command"]
        for entry in json.loads(settings_json)["hooks"]["PreToolUse"]
        if entry["matcher"] == "Bash"
        for hook in entry["hooks"]
        if "shared_stash_guard" in hook["command"]
    ]


@pytest.mark.parametrize(
    "settings_json",
    [
        stage_run_hook_settings(stage_report=True),
        stage_run_hook_settings(stage_report=False),
        chat_hook_settings(),
    ],
)
def test_the_command_claude_is_given_denies(settings_json, tmp_path):
    (command,) = _installed_guards(settings_json)
    # A form only the agent policy refuses, so the installed command must claim it.
    done = _run_hook(shlex.split(command), _bash("git stash push -m x", tmp_path))
    assert '"deny"' in done.stdout


def _running(adapter: str):
    """The suite forces the `local` adapter; these tests are about the others."""
    return mock.patch.dict(os.environ, {"LOREGARDEN_CLI_ADAPTER": adapter})


def _stage(tmp_path: Path, adapter: str):
    with _running(adapter):
        return resolve_cli_invocation(
            agent_id="qa",
            adapter=adapter,
            prompt="p",
            prompt_file=_prompt(tmp_path),
            skill_name="",
            workspace_root=tmp_path,
            run_id="r",
        )


def _triage(tmp_path: Path, adapter: str):
    with _running(adapter):
        return build_triage_invocation(
            agent_id="triage",
            adapter=adapter,
            prompt="p",
            prompt_file=_prompt(tmp_path),
            skill_name="",
            workspace_root=tmp_path,
        )


def _settings_of(argv: list[str]) -> str:
    return argv[argv.index("--settings") + 1]


def _prompt(tmp_path: Path) -> Path:
    path = tmp_path / "prompt.md"
    path.write_text("hi", encoding="utf-8")
    return path


@pytest.mark.parametrize("bypass", ["0", "1"])
def test_every_claude_stage_run_installs_it(bypass, tmp_path):
    """The incident ran in print mode under bypassPermissions: only a hook could stop it."""
    with mock.patch.dict(os.environ, {"LOREGARDEN_ALLOW_PERMISSION_BYPASS": bypass}):
        invocation = _stage(tmp_path, "claude")
    assert invocation.interactive is (bypass == "0")
    assert _installed_guards(_settings_of(invocation.argv))


def test_every_claude_chat_turn_installs_it(tmp_path):
    bridged = build_interactive_invocation(
        adapter="claude",
        prompt_file=_prompt(tmp_path),
        workspace_root=tmp_path,
        orchestrated=False,
    )
    assert _installed_guards(_settings_of(bridged.argv))
    assert _installed_guards(_settings_of(_triage(tmp_path, "claude").argv))


@pytest.mark.parametrize("build", [_stage, _triage])
def test_every_cursor_agent_loads_a_plugin_whose_hook_denies(build, tmp_path):
    argv = build(tmp_path, "cursor").argv
    plugin = Path(argv[argv.index("--plugin-dir") + 1])
    assert json.loads((plugin / ".cursor-plugin" / "plugin.json").read_text())["name"]
    config = json.loads((plugin / "hooks" / "hooks.json").read_text())
    assert config["version"] == 1
    (hook,) = config["hooks"]["beforeShellExecution"]

    # cursor substitutes the plugin root into the command and runs it in a shell.
    command = hook["command"].replace("${CURSOR_PLUGIN_ROOT}", str(plugin))
    payload = {"hook_event_name": "beforeShellExecution", "cwd": str(tmp_path)}
    done = _run_hook(["sh", "-c", command], json.dumps({**payload, "command": INCIDENT}))
    verdict = json.loads(done.stdout)
    assert verdict["permission"] == "deny"
    assert "checkpoint log" in verdict["user_message"]
    assert verdict["agent_message"] == verdict["user_message"]
    allowed = _run_hook(["sh", "-c", command], json.dumps({**payload, "command": "git stash list"}))
    assert (allowed.returncode, allowed.stdout) == (0, "")


def test_every_adapter_says_whether_a_hook_reaches_its_shell():
    assert set(STASH_GUARD_UNSUPPORTED) == set(CliAdapter) - {CliAdapter.DEFAULT}


@pytest.mark.parametrize("adapter", [a for a, why in STASH_GUARD_UNSUPPORTED.items() if why])
@pytest.mark.parametrize("build", [_stage, _triage])
def test_an_adapter_no_hook_reaches_is_named_when_it_runs(build, adapter, tmp_path):
    with mock.patch.object(cli_adapters.logger, "warning") as warning:
        build(tmp_path, adapter.value)
    assert STASH_GUARD_UNSUPPORTED[adapter] in warning.call_args.args


def test_this_repository_and_the_plugin_run_it():
    project = json.loads((REPO_ROOT / ".claude" / "settings.json").read_text())
    assert _installed_guards(json.dumps(project))
    plugin = json.loads((REPO_ROOT / "claude-plugin" / "hooks" / "hooks.json").read_text())
    (command,) = _installed_guards(json.dumps(plugin))
    assert re.search(r"plugin_dispatch\.py\" shared_stash_guard$", command)
    assert _GUARD.resolve() == Path(shared_stash_guard.__file__).resolve()


def test_the_plugin_runs_it_in_a_repository_without_its_own_copy(repo, tmp_path):
    _git(repo, "worktree", "add", "-q", str(tmp_path / "second"))
    dispatch = REPO_ROOT / ".claude" / "hooks" / "plugin_dispatch.py"
    done = _run_hook(
        [shutil.which("python3") or sys.executable, str(dispatch), "shared_stash_guard"],
        _bash("git stash pop", repo),
        CLAUDE_PROJECT_DIR=str(repo),
    )
    assert '"deny"' in done.stdout, done.stderr


# --- the permission bridge and the chat policy agree with it ---------------------------


@pytest.mark.parametrize("command", [INCIDENT, *TAKES_OR_MOVES])
def test_the_bridge_denies_and_chat_never_approves_what_the_guard_refuses(command):
    assert "git stash" in denied_cli_tool_message("Bash", {"command": command})
    assert is_chat_auto_approved_cli_tool("Bash", {"command": command}) is False


@pytest.mark.parametrize(
    "command",
    ["git stash list", "git stash show -p stash@{0}", "git stash create", f"git stash apply {SHA}"],
)
def test_chat_still_approves_what_the_guard_allows(command):
    assert denied_cli_tool_message("Bash", {"command": command}) == ""
    assert is_chat_auto_approved_cli_tool("Bash", {"command": command}) is True
