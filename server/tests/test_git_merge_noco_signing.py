"""A no-checkout merge is signed when the repository signs its commits (782).

`git commit` honours `commit.gpgsign`; `git commit-tree` does not. The first
landings made by `merge_without_checkout` were unsigned in a repository
whose branch rules require signatures, and could not be merged.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from loregarden.services.git_merge_noco import merge_without_checkout, signs_commits
from tests.worktree_helpers import commit_on, git, make_repo

pytestmark = pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="needs ssh-keygen")


@pytest.fixture
def signing_repo(tmp_path: Path) -> Path:
    """A repo configured to SSH-sign, with a throwaway key it trusts."""
    repo = make_repo(tmp_path)
    key = tmp_path / "signing_key"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
    allowed = tmp_path / "allowed_signers"
    allowed.write_text(f"test@example.com {key.with_suffix('.pub').read_text()}")
    git(repo, "config", "gpg.format", "ssh")
    git(repo, "config", "user.signingkey", str(key.with_suffix(".pub")))
    git(repo, "config", "gpg.ssh.allowedSignersFile", str(allowed))
    git(repo, "config", "commit.gpgsign", "true")
    return repo


def _signature_status(repo: Path, ref: str) -> str:
    # %G? is G (good), B (bad), U (untrusted-good), N (no signature) ...
    return git(repo, "log", "-1", "--format=%G?", ref).stdout.strip()


def test_a_landing_in_a_signing_repository_is_signed(signing_repo: Path):
    repo = signing_repo
    git(repo, "branch", "integration/x", "main")
    git(repo, "branch", "feature", "main")
    commit_on(repo, "feature", "f.txt", "feature work\n")
    assert signs_commits(repo)

    outcome = merge_without_checkout(
        repo, target="integration/x", source="feature", subject="Land feature"
    )

    assert outcome.ok and not outcome.already_contained
    assert _signature_status(repo, "integration/x") in ("G", "U"), (
        f"landing commit is not signed: %G?={_signature_status(repo, 'integration/x')!r}"
    )


def test_a_landing_in_an_unsigned_repository_is_not_signed(tmp_path: Path):
    repo = make_repo(tmp_path)
    git(repo, "config", "commit.gpgsign", "false")
    git(repo, "branch", "integration/x", "main")
    git(repo, "branch", "feature", "main")
    commit_on(repo, "feature", "f.txt", "feature work\n")
    assert not signs_commits(repo)

    outcome = merge_without_checkout(
        repo, target="integration/x", source="feature", subject="Land feature"
    )

    assert outcome.ok
    assert _signature_status(repo, "integration/x") == "N"
