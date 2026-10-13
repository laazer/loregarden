"""A no-checkout merge is signed, and says so, when the repository signs (782).

`git commit` honours `commit.gpgsign`; `git commit-tree` does not. The first
landings made by `merge_without_checkout` were unsigned in a repository
whose branch rules require signatures, and could not be merged.

The fix for that shipped as `720af81d`; the first two tests here are its
regression lock (AC1, AC2). The rest cover `MergeOutcome.signed`, which makes
the decision the fix makes visible to the caller instead of reporting a signed
and an unsigned landing as the same unqualified success (AC-A1…A3).

**The module no longer skips as a whole.** Only the two cases that need a real
signing key are skipped without `ssh-keygen`; everything about `signed` is
asserted on every host (AC-A5). A module-level `pytestmark` meant this file
could report green having asserted nothing at all.
"""

from __future__ import annotations

import shutil
import subprocess
import typing
from dataclasses import fields
from pathlib import Path
from unittest import mock

import pytest
from loregarden.services import git_merge_noco
from loregarden.services.git_merge_noco import (
    BranchTips,
    MergeOutcome,
    merge_preview,
    merge_tips,
    merge_without_checkout,
    read_branch_tips,
    rev,
    signs_commits,
)
from tests.worktree_helpers import commit_on, git, make_repo

needs_ssh_keygen = pytest.mark.skipif(
    shutil.which("ssh-keygen") is None, reason="needs ssh-keygen to make a signing key"
)


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


@pytest.fixture
def unsigned_repo(tmp_path: Path) -> Path:
    """A repo with signing explicitly off, and a landing waiting to be made.

    `integration/x` and `feature` both branch from `main`; `feature` carries one
    commit the target does not. Needs no key, so every assertion built on it
    runs on a host with no `ssh-keygen`.
    """
    repo = make_repo(tmp_path)
    git(repo, "config", "commit.gpgsign", "false")
    git(repo, "branch", "integration/x", "main")
    git(repo, "branch", "feature", "main")
    commit_on(repo, "feature", "f.txt", "feature work\n")
    return repo


def _signature_status(repo: Path, ref: str) -> str:
    # %G? is G (good), B (bad), U (untrusted-good), N (no signature). Valid
    # here, and only here, because `signing_repo` writes its own
    # gpg.ssh.allowedSignersFile — on a checkout without one it reads N for
    # every commit, signed or not (782).
    return git(repo, "log", "-1", "--format=%G?", ref).stdout.strip()


# --- AC1 / AC2: the shipped fix, regression-locked -------------------------


@needs_ssh_keygen
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


def test_a_landing_in_an_unsigned_repository_is_not_signed(unsigned_repo: Path):
    repo = unsigned_repo
    assert not signs_commits(repo)

    outcome = merge_without_checkout(
        repo, target="integration/x", source="feature", subject="Land feature"
    )

    assert outcome.ok
    assert _signature_status(repo, "integration/x") == "N"


# --- AC-A1: the field is tri-state, and defaults to "no commit written" ----


def test_signed_is_a_tristate_defaulting_to_no_commit_written():
    """`bool | None`, default `None` — a bare `bool` fails this (AC-A1).

    `None` has to be representable: collapsing "this call wrote no commit"
    into "the commit is unsigned" is the "it failed" / "there is nothing"
    collapse CLAUDE.md forbids, on the seam this bug lived on.
    """
    annotation = typing.get_type_hints(MergeOutcome)["signed"]
    admitted = typing.get_args(annotation) or (annotation,)

    assert type(None) in admitted, f"signed must admit None, not just {annotation!r}"
    assert bool in admitted, f"signed must admit bool, got {annotation!r}"
    signed_field = next(f for f in fields(MergeOutcome) if f.name == "signed")
    assert signed_field.default is None
    assert MergeOutcome(ok=True).signed is None


# --- AC-A2: a written commit reports the decision that wrote it ------------


@needs_ssh_keygen
def test_a_landing_in_a_signing_repository_reports_signed_true(signing_repo: Path):
    repo = signing_repo
    git(repo, "branch", "integration/x", "main")
    git(repo, "branch", "feature", "main")
    commit_on(repo, "feature", "f.txt", "feature work\n")

    outcome = merge_without_checkout(
        repo, target="integration/x", source="feature", subject="Land feature"
    )

    assert outcome.ok and not outcome.already_contained
    assert outcome.signed is True


def test_a_landing_in_an_unsigned_repository_reports_signed_false(unsigned_repo: Path):
    """The case AC-A5 requires to run with no `ssh-keygen` on the host."""
    outcome = merge_without_checkout(
        unsigned_repo, target="integration/x", source="feature", subject="Land feature"
    )

    assert outcome.ok and not outcome.already_contained
    assert outcome.signed is False, "a written commit must report the decision that wrote it"


def test_the_reported_flag_is_the_decision_not_a_re_read_of_the_commit(unsigned_repo: Path):
    """`signed` follows `commit.gpgsign`, which is what `signs_commits` reads.

    Guards R3: re-reading the commit back to verify would be a second git call
    on the landing path, and would answer `N` for a signed commit on a checkout
    with no `gpg.ssh.allowedSignersFile`.
    """
    repo = unsigned_repo
    outcome = merge_without_checkout(
        repo, target="integration/x", source="feature", subject="Land feature"
    )

    assert outcome.signed is signs_commits(repo)


# --- AC-A3: an outcome that wrote no commit reports None, in all four shapes


def test_an_already_contained_landing_wrote_no_commit_and_reports_none(unsigned_repo: Path):
    repo = unsigned_repo
    merge_without_checkout(repo, target="integration/x", source="feature", subject="Land feature")

    again = merge_without_checkout(
        repo, target="integration/x", source="feature", subject="Land feature"
    )

    assert again.ok and again.already_contained
    assert again.signed is None, "a short-circuit wrote no commit; that is not 'unsigned'"


def test_a_conflicted_landing_wrote_no_commit_and_reports_none(unsigned_repo: Path):
    repo = unsigned_repo
    commit_on(repo, "feature", "shared.txt", "feature\n")
    commit_on(repo, "integration/x", "shared.txt", "target\n")

    outcome = merge_without_checkout(
        repo, target="integration/x", source="feature", subject="Land feature"
    )

    assert not outcome.ok and outcome.conflicted_files == ("shared.txt",)
    assert outcome.signed is None


def test_a_merge_tree_failure_wrote_no_commit_and_reports_none(unsigned_repo: Path):
    """git could not merge at all: the names it is handed resolve to nothing.

    Reached through the real code path by giving `merge_tips` tips whose
    branches do not exist — both shas are real and divergent, so neither the
    missing-branch guards nor the ancestry shortcut fires, and `merge-tree`
    itself fails.
    """
    repo = unsigned_repo
    tips = BranchTips(
        shas={"ghost-target": rev(repo, "integration/x"), "ghost-source": rev(repo, "feature")}
    )

    outcome = merge_tips(
        repo, tips, target="ghost-target", source="ghost-source", subject="Land feature"
    )

    assert not outcome.ok and not outcome.conflicted_files
    assert outcome.detail, "git's own words are kept"
    assert outcome.signed is None


@pytest.mark.parametrize("shape", ["clean", "already_contained", "conflict"])
def test_a_preview_writes_nothing_so_it_reports_none(unsigned_repo: Path, shape: str):
    """Every `merge_preview` result, which is all `integration_branch_health` sees."""
    repo = unsigned_repo
    target = rev(repo, "integration/x")
    source = rev(repo, "feature")
    if shape == "already_contained":
        source = target
    if shape == "conflict":
        commit_on(repo, "feature", "shared.txt", "feature\n")
        commit_on(repo, "integration/x", "shared.txt", "target\n")
        target = rev(repo, "integration/x")
        source = rev(repo, "feature")

    outcome = merge_preview(repo, target, source)

    assert outcome.signed is None


# --- AC-A3, the two shapes that write a commit object and keep nothing ------
#
# The three shapes above never reach `commit-tree`. These two do, so they are
# the only ones where a `signed` set from the decision rather than from the ref
# move reads as a state — "the landing is unsigned" for a landing that does not
# exist. Both run on a host with no `ssh-keygen` (AC-A5).


def test_a_refused_ref_update_wrote_nothing_the_branch_kept_and_reports_none(
    unsigned_repo: Path,
):
    """The compare-and-swap lost: `commit-tree` ran, `update-ref` refused.

    A commit object exists and no branch points at it, which is the one shape
    where "was the commit signed?" has an answer that must still not be
    reported: this call moved no ref, so it landed nothing. Reached the way the
    race itself reaches it — tips read a moment before the target moved.
    """
    repo = unsigned_repo
    stale = read_branch_tips(repo, "integration/x", "feature")
    commit_on(repo, "integration/x", "someone_else.txt", "moved under us\n")

    outcome = merge_tips(repo, stale, target="integration/x", source="feature", subject="Land")

    assert not outcome.ok, "the target moved since the reading; the merge must be refused"
    assert outcome.detail, "git's own words about the refusal are kept"
    assert outcome.signed is None, (
        "the ref did not move, so this call landed nothing — `signed` is not a state yet"
    )


def test_a_commit_tree_failure_wrote_no_commit_and_reports_none(tmp_path: Path):
    """Signing is on and the key is missing, so `commit-tree -S` itself fails.

    A repository configured to sign on a host that cannot — the operational
    failure the `-S` flag introduced. `signs_commits` answers True, so an
    implementation taking `signed` from that decision at the wrong moment
    reports a signed landing for a commit git refused to write.
    """
    repo = make_repo(tmp_path)
    git(repo, "branch", "integration/x", "main")
    git(repo, "branch", "feature", "main")
    commit_on(repo, "feature", "f.txt", "feature work\n")
    # Configured last: `git commit` honours gpgsign too, so the branch has to
    # exist before the host loses its ability to sign.
    git(repo, "config", "commit.gpgsign", "true")
    git(repo, "config", "gpg.format", "ssh")
    git(repo, "config", "user.signingkey", str(tmp_path / "no_such_key.pub"))
    assert signs_commits(repo), "the decision is to sign; the host just cannot"

    outcome = merge_without_checkout(
        repo, target="integration/x", source="feature", subject="Land feature"
    )

    assert not outcome.ok and not outcome.conflicted_files
    assert outcome.detail, "git's own words about the failed signature are kept"
    assert outcome.signed is None, "no commit was written; 'signed' would be a claim about nothing"
    assert rev(repo, "integration/x") == rev(repo, "main"), "the target must not have moved"


# --- AC-A2: the flag is the decision, and costs no second git call ----------


def test_the_flag_costs_no_second_git_call_and_nothing_reads_the_commit_back(
    unsigned_repo: Path,
):
    """`signed` reuses the `signs_commits` reading; it does not verify the commit.

    R3, and AC-A2's "no second git invocation". A re-read would be a git call
    on every landing, and on a checkout with no `gpg.ssh.allowedSignersFile` it
    answers `N` for a commit that is signed — so it would report the shipped fix
    as broken. Observing the invocations is the only way to see the difference:
    for a repository that signs, the decision and the re-read agree.
    """
    repo = unsigned_repo
    calls: list[tuple[str, ...]] = []
    real = git_merge_noco._git

    def record(repo_root: Path, *args: str):
        calls.append(tuple(args))
        return real(repo_root, *args)

    with mock.patch.object(git_merge_noco, "_git", record):
        outcome = merge_without_checkout(
            repo, target="integration/x", source="feature", subject="Land feature"
        )

    assert outcome.ok and outcome.signed is False
    subcommands = [c[0] for c in calls]
    assert subcommands[-1] == "update-ref", (
        f"the ref move is the last thing the landing does, not {subcommands[-1]!r}: {subcommands}"
    )
    assert sum(1 for c in calls if "commit.gpgsign" in c) == 1, (
        f"`commit.gpgsign` is read once, for both the -S flag and the field: {calls}"
    )
    reads_the_commit_back = [c for c in calls if outcome.sha in c and c[0] != "update-ref"]
    assert not reads_the_commit_back, (
        f"the merge commit is read back to derive `signed`: {reads_the_commit_back}"
    )
