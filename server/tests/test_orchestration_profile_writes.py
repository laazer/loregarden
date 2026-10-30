"""Saving a profile must not destroy the file it saves into.

These files are hand-written and carry the reasoning behind their values —
why each gate is paired with a same-named fixer, why the agent-retry cap is 1
rather than 3. A PyYAML round-trip drops all of it, and the queue's automation
toggles PUT on every click, so this went from a rare loss to a routine one.
"""

from pathlib import Path

import pytest
from loregarden.models.domain import Workspace
from loregarden.models.domain.enums_capacity import DockerFootprint
from loregarden.models.domain.schemas import GitAutomationView
from loregarden.services.orchestration_profile import (
    GatesConfig,
    GitAutomationConfig,
    resolve_orchestration_profile,
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


def test_a_save_is_what_the_next_read_returns(profile_dir, workspace):
    """Reads are cached per version of the file; a save is a new version."""
    assert resolve_orchestration_profile(workspace).git.base_branch != "develop"

    update_git_config(workspace, GitAutomationConfig(base_branch="develop"))

    assert resolve_orchestration_profile(workspace).git.base_branch == "develop"


def test_an_edit_in_place_of_the_same_size_is_read_back(profile_dir, workspace):
    """A hand edit keeps the inode and here keeps the size; the mtime still moves."""
    path = profile_dir / "proj.yaml"
    assert resolve_orchestration_profile(workspace).gates.commands[0] == "ruff check ."

    path.write_text(PROFILE.replace("ruff check .", "ruff check /"), encoding="utf-8")

    assert resolve_orchestration_profile(workspace).gates.commands[0] == "ruff check /"


def test_a_caller_mutating_its_profile_does_not_reach_the_next(profile_dir, workspace):
    resolve_orchestration_profile(workspace).gates.commands.append("rm -rf /")

    assert "rm -rf /" not in resolve_orchestration_profile(workspace).gates.commands


GIT_CONFIGURED_PROFILE = """\
slug: proj
name: Proj Autopilot
gates:
  enabled: true
git:
  commit: true
  push: true
  # The suite that must pass against the merge a land would publish.
  land_verify_commands:
    - uv run --project server pytest -q
  land_verify_capacity_footprint: stack
  tree_open_pr: true
  tree_auto_merge: true
  prune_landed_chat_branches: true
"""


@pytest.fixture(name="git_profile_dir")
def git_profile_dir_fixture(tmp_path, monkeypatch):
    root = tmp_path / "agent_context" / "orchestration"
    root.mkdir(parents=True)
    (root / "proj.yaml").write_text(GIT_CONFIGURED_PROFILE, encoding="utf-8")
    monkeypatch.setattr("loregarden.services.orchestration_profile.orchestration_dir", lambda: root)
    return root


def test_a_toggle_save_does_not_disable_the_land_guard(git_profile_dir, workspace):
    """The queue's automation panel PUTs its whole `GitAutomationView` on every
    toggle, and that view carries no `land_verify_*` field — so `model_validate`
    defaults them and, without the preserve list, one click on "push" wrote an
    empty command list over a configured one and silently turned the land guard
    off for that workspace (lg-milestone-that-1238, AC20).

    This is the endpoint's own path: a view that names neither field, validated
    into a config exactly as `update_workspace_git_automation` does.
    """
    view = GitAutomationView(commit=True, push=True, auto_merge=True)
    assert "land_verify_commands" not in view.model_dump()

    profile = update_git_config(
        workspace, GitAutomationConfig.model_validate(view.model_dump(mode="json"))
    )

    assert profile.git.land_verify_commands == ["uv run --project server pytest -q"]
    assert profile.git.land_verify_capacity_footprint is DockerFootprint.STACK
    # The toggle the panel did send still lands.
    assert profile.git.auto_merge is True
    # And the comment above the preserved list is still in the file.
    assert "# The suite that must pass" in _text(git_profile_dir)


def test_the_other_fields_the_toggle_view_omits_also_survive(git_profile_dir, workspace):
    """Same defect, three fields that had it before the land guard existed."""
    profile = update_git_config(
        workspace,
        GitAutomationConfig.model_validate(GitAutomationView(commit=True).model_dump(mode="json")),
    )

    assert profile.git.tree_open_pr is True
    assert profile.git.tree_auto_merge is True
    assert profile.git.prune_landed_chat_branches is True


def test_a_caller_that_owns_the_whole_block_can_still_clear_it(git_profile_dir, workspace):
    """Preserving is for the editor that cannot see the field, not a rule that
    the field is unwritable: an explicit empty `preserve` writes what it sends."""
    profile = update_git_config(
        workspace,
        GitAutomationConfig(commit=True, land_verify_commands=[]),
        preserve=frozenset(),
    )

    assert profile.git.land_verify_commands == []
