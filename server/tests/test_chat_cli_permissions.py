"""Interactive chat may write files and run ordinary git without the inbox."""

from loregarden.agents.executors.tool_auto_approve import (
    is_auto_approved_cli_tool,
    is_chat_auto_approved_cli_tool,
)


def test_stage_policy_still_gates_writes():
    assert is_auto_approved_cli_tool("Write") is False
    assert is_auto_approved_cli_tool("Bash") is False


def test_chat_auto_approves_workspace_file_tools():
    for tool in ("Read", "Write", "Edit", "MultiEdit", "Glob", "Grep", "LS"):
        assert is_chat_auto_approved_cli_tool(tool, {}) is True, tool


def test_chat_auto_approves_ordinary_git():
    allowed = [
        "git status",
        "git diff --stat",
        "git add -A && git commit -m 'fix the flaky test'",
        "git push -u origin HEAD",
        "git checkout -b feature/chat-writes",
        "cd server && git log -1 --oneline",
        "/usr/bin/git -C /tmp/repo status",
    ]
    for command in allowed:
        assert is_chat_auto_approved_cli_tool("Bash", {"command": command}) is True, command


def test_chat_still_gates_destructive_git_and_arbitrary_shell():
    gated = [
        "git push --force origin HEAD",
        "git push --force-with-lease",
        "git push -f",
        "git reset --hard HEAD~1",
        "git clean -fd",
        "git checkout -f main",
        "git branch -D leftover",
        "git stash drop",
        "ls",
        "pytest server/tests",
        "rm -rf dist",
        "git status && rm -rf /tmp/scratch",
    ]
    for command in gated:
        assert is_chat_auto_approved_cli_tool("Bash", {"command": command}) is False, command
