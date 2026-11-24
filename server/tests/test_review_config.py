"""Which launch targets a ticket is offered, from the files it changes."""

from __future__ import annotations

from pathlib import Path

import pytest
from loregarden.models.domain import Workspace
from loregarden.services.review_config import (
    LaunchMatch,
    LaunchOffer,
    ReviewConfig,
    config_status,
    launch_offer,
    readable_config,
)

GAME = {"name": "game", "paths": ["scenes/*", "project.godot"], "command": ["godot"]}
BACKEND = {"name": "backend tests", "kind": "check", "paths": ["backend/*"], "command": ["pytest"]}
TARGETS = ReviewConfig.model_validate({"targets": [GAME, BACKEND]})
WITH_DEFAULT = ReviewConfig.model_validate({"launch": {"command": ["run"]}, "targets": [BACKEND]})


@pytest.mark.parametrize(
    ("files", "targets"),
    [
        (["backend/services/deep/codex.py"], ["backend tests"]),
        (["scenes/a/b.tscn", "README.md"], ["game"]),
        (["project.godot"], ["game"]),
        (["backend/a.py", "scenes/a.tscn"], ["game", "backend tests"]),
    ],
)
def test_a_ticket_is_offered_the_targets_its_files_fall_under(files, targets):
    assert launch_offer(TARGETS, files) == LaunchOffer(match=LaunchMatch.MATCHED, targets=targets)


@pytest.mark.parametrize("files", [["README.md"], ["other/backend/a.py"], ["Backend/a.py"]])
def test_files_no_target_covers_offer_nothing(files):
    assert launch_offer(TARGETS, files) == LaunchOffer(match=LaunchMatch.NO_MATCH)


@pytest.mark.parametrize("files", [None, []])
def test_unreadable_or_empty_changes_offer_every_target(files):
    assert launch_offer(WITH_DEFAULT, files) == LaunchOffer(
        match=LaunchMatch.UNKNOWN, targets=["backend tests", "build"]
    )


def test_launch_is_offered_only_for_changes_no_target_claims():
    assert launch_offer(WITH_DEFAULT, ["README.md"]).targets == ["build"]
    assert launch_offer(WITH_DEFAULT, ["backend/a.py"]).targets == ["backend tests"]


def test_a_workspace_with_one_launch_offers_it_for_everything():
    config = ReviewConfig.model_validate({"launch": {"command": ["run"]}})

    assert launch_offer(config, ["anything.py"]) == LaunchOffer(
        match=LaunchMatch.MATCHED, targets=["build"]
    )


def test_no_config_offers_nothing_and_says_so():
    assert launch_offer(None, ["a"]) == LaunchOffer(match=LaunchMatch.UNCONFIGURED)


def test_a_broken_file_does_not_raise_into_the_queue_and_its_status_names_it(
    tmp_path: Path, caplog
):
    (tmp_path / ".loregarden").mkdir()
    (tmp_path / ".loregarden" / "review.yaml").write_text("targets: 3\n")
    workspace = Workspace(slug="game", name="game", repo_path=str(tmp_path))

    assert readable_config(workspace) is None
    assert "review config for game" in caplog.text
    assert "not a valid review config" in config_status(workspace).error


def test_status_lists_the_targets_with_what_kind_each_is(tmp_path: Path):
    (tmp_path / ".loregarden").mkdir()
    (tmp_path / ".loregarden" / "review.yaml").write_text(WITH_DEFAULT.model_dump_json())
    workspace = Workspace(slug="game", name="game", repo_path=str(tmp_path))

    status = config_status(workspace)

    assert [(t.name, t.kind.value) for t in status.targets] == [
        ("backend tests", "check"),
        ("build", "app"),
    ]
