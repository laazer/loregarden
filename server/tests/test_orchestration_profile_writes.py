"""Saving a profile must not destroy the file it saves into.

These files are hand-written and carry the reasoning behind their values —
why each gate is paired with a same-named fixer, why the agent-retry cap is 1
rather than 3. A PyYAML round-trip drops all of it, and the queue's automation
toggles PUT on every click, so this went from a rare loss to a routine one.
"""

from pathlib import Path

import pytest
from loregarden.models.domain import Workspace
from loregarden.services.orchestration_profile import (
    GatesConfig,
    GitAutomationConfig,
    update_gates_config,
    update_git_config,
)

PROFILE = """\
slug: proj
name: Proj Autopilot
driver: builtin_autopilot
gates:
  enabled: true
  # Each check is paired with a fixer of the same name below, so a failure the
  # fixer can resolve never reaches a human.
  commands:
    - ruff check .
    - oxlint .
  autofix_commands:
    - ruff check --fix .
  autofix_agent_fallback: true
  # A second full agent respawn rarely converts a still-failing lint gate and
  # just re-pays a whole CLI run's usage, so cap it at one.
  autofix_max_agent_attempts: 1
max_stages_per_run: 0
"""


@pytest.fixture(name="profile_dir")
def profile_dir_fixture(tmp_path, monkeypatch):
    root = tmp_path / "agent_context" / "orchestration"
    root.mkdir(parents=True)
    (root / "proj.yaml").write_text(PROFILE, encoding="utf-8")
    monkeypatch.setattr("loregarden.services.orchestration_profile.orchestration_dir", lambda: root)
    return root


@pytest.fixture(name="workspace")
def workspace_fixture():
    return Workspace(slug="proj", name="proj", repo_path=".")


def _text(profile_dir: Path) -> str:
    return (profile_dir / "proj.yaml").read_text(encoding="utf-8")


def test_saving_gates_keeps_the_comments(profile_dir, workspace):
    update_gates_config(
        workspace,
        GatesConfig(enabled=True, commands=["ruff check .", "oxlint ."], transition_script=""),
    )

    text = _text(profile_dir)
    assert "# Each check is paired with a fixer" in text
    assert "# A second full agent respawn rarely converts" in text


def test_saving_git_keeps_the_gates_comments(profile_dir, workspace):
    """The queue's toggles write a sibling block. Rewriting the document must
    not disturb the block next to the one being written."""
    update_git_config(workspace, GitAutomationConfig(commit=True, push=True))

    text = _text(profile_dir)
    assert "# Each check is paired with a fixer" in text
    assert "# A second full agent respawn rarely converts" in text


def test_saving_git_only_adds_the_git_block(profile_dir, workspace):
    before = _text(profile_dir)

    update_git_config(workspace, GitAutomationConfig(commit=True))

    after = _text(profile_dir)
    # Every original line survives verbatim: a save is an edit, not a reformat.
    for line in before.splitlines():
        assert line in after.splitlines(), f"lost line: {line!r}"
    assert "git:" in after


def test_saving_preserves_list_indentation(profile_dir, workspace):
    update_git_config(workspace, GitAutomationConfig(commit=True))

    # Reflowing `  - item` to `- item` across every list makes a one-key change
    # look like a rewrite in review.
    assert "    - ruff check ." in _text(profile_dir)


def test_values_still_round_trip(profile_dir, workspace):
    profile = update_git_config(
        workspace, GitAutomationConfig(commit=True, push=True, base_branch="develop")
    )

    assert profile.git.commit is True
    assert profile.git.base_branch == "develop"
    # And the untouched neighbour is unchanged, not reset to its default.
    assert profile.gates.autofix_max_agent_attempts == 1


def test_a_missing_profile_is_created_without_error(tmp_path, monkeypatch, workspace):
    root = tmp_path / "orchestration"
    monkeypatch.setattr("loregarden.services.orchestration_profile.orchestration_dir", lambda: root)

    profile = update_git_config(workspace, GitAutomationConfig(commit=True))

    assert (root / "proj.yaml").is_file()
    assert profile.git.commit is True
