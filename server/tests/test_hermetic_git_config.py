"""The suite's git sees a CI runner's config, not the developer's (see conftest)."""

import subprocess


def test_a_commit_without_its_own_identity_fails_as_it_does_in_ci(tmp_path):
    """A test that leaned on the developer's global user.email passed on push and failed in CI."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    commit = subprocess.run(
        ["git", "commit", "-q", "--allow-empty", "-m", "root"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert commit.returncode == 128
    assert "auto-detection is disabled" in commit.stderr


def test_no_global_default_branch_reaches_a_new_repository(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    head = subprocess.run(
        ["git", "symbolic-ref", "HEAD"], cwd=tmp_path, capture_output=True, text=True, check=True
    )
    assert head.stdout.strip() == "refs/heads/master"
