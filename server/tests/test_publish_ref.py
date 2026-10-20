"""An integration branch has one name, and the remote's copy of it is never the branch.

`publish_tree` pushes a squash cut from the base. While it pushed that squash
*as* ``integration/<root>``, a conflict preview against the remote-tracking
ref compared a ticket branch with a commit sharing none of its history, and
reported add/add conflicts in files the branch already held. On 2026-10-20 a
hand-resolved merge of that ref into lg-external-harness-1026's branch broke
its next two checkout merges.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from loregarden.models.domain import Worktree
from loregarden.services.conflict_detector import ConflictDetectorService
from loregarden.services.publish_ref import (
    branch_for_pr_head,
    comparison_ref,
    publish_ref_for,
)
from tests.worktree_helpers import commit_on, git, make_repo

INTEGRATION = "integration/lg-root-1"
TICKET = "loregarden/lg-child-2"


@pytest.mark.parametrize("branch", [INTEGRATION, TICKET, "main", "chat/is-the-thing-on-1a2b3c4d"])
def test_a_pr_head_maps_back_to_the_branch_it_publishes(branch):
    assert branch_for_pr_head(publish_ref_for(branch)) == branch
    assert branch_for_pr_head(branch) == branch, "a head pushed with its history is its own"


def test_an_integration_branch_is_compared_locally_and_any_other_on_the_remote():
    assert comparison_ref(INTEGRATION) == INTEGRATION
    assert comparison_ref("main") == "origin/main"
    assert comparison_ref(TICKET) == f"origin/{TICKET}"


@pytest.fixture(name="stale_squash_checkout")
def stale_squash_checkout_fixture(tmp_path) -> Path:
    """A ticket checkout on top of the real integration branch, beside a remote
    that still holds the old squash of it under the branch's own name.

    The ticket edits ``shared.txt``, which the integration branch added, so a
    merge of the squash is add/add in it while a merge of the branch is a no-op.
    """
    repo = make_repo(tmp_path)
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-q", "origin", "main")

    git(repo, "branch", INTEGRATION, "main")
    commit_on(repo, INTEGRATION, "shared.txt", "the tree's version\n")
    tree = git(repo, "rev-parse", f"{INTEGRATION}^{{tree}}").stdout.strip()
    squash = git(repo, "commit-tree", tree, "-p", "main", "-m", "publish").stdout.strip()
    git(repo, "push", "-q", "origin", f"{squash}:refs/heads/{INTEGRATION}")
    git(repo, "fetch", "-q", "origin")

    checkout = tmp_path / "ticket"
    git(repo, "worktree", "add", "-q", "-b", TICKET, str(checkout), INTEGRATION)
    (checkout / "shared.txt").write_text("the ticket's revision\n")
    git(checkout, "commit", "-qam", "ticket work")

    # The premise the test depends on: the squash really does conflict.
    probe = subprocess.run(
        ["git", "merge-tree", "--write-tree", "--name-only", TICKET, f"origin/{INTEGRATION}"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert probe.returncode == 1 and "shared.txt" in probe.stdout, "fixture: squash must conflict"
    return checkout


@pytest.mark.asyncio
async def test_a_stale_squash_on_the_remote_is_not_reported_as_a_conflict(
    stale_squash_checkout,
):
    worktree = Worktree(
        id="wt-1",
        workspace_id="ws-1",
        agent_run_id="run-1",
        branch_name=TICKET,
        worktree_path=str(stale_squash_checkout),
    )
    service = ConflictDetectorService(MagicMock(), repo_path=str(stale_squash_checkout))

    preview = await service.get_conflict_preview(worktree, INTEGRATION)

    assert preview["checked"] is True
    assert (preview["has_conflicts"], preview["conflicting_files"]) == (False, [])
    status = git(stale_squash_checkout, "status", "--porcelain").stdout
    assert status == "", "the probe leaves the checkout as it found it"
