"""Runnable reproductions for defects that are open right now.

Why this module exists (674). Seven gate tickets were worked in one session and
the same arc repeated on every one: the description was directionally right, the
defect was often already fixed or smaller than stated, and the real finding only
appeared when the gate was actually exercised. The measured cause is that a
ticket records a claim and nothing re-tests the claim before someone acts on it.

The clearest case: 591 and 592 were filed at 04:29 on 2026-08-30 by a review of
577, and fixed by 577's *own* later rounds in commit 60d74df two days later.
Nothing closed them, so they sat in `backlog` until someone re-derived the answer
by hand five days on. The reviews had already done the work — their bodies say
"Exercised (scratch repo, committed src/loop_a.py -> loop_b.py)" — but as prose,
which only a careful human re-run can cash in.

So: the reproduction lives here, as a test, and it asserts the **correct**
behaviour. While the defect is open the test fails, `xfail` absorbs that, and the
suite stays green. The moment anyone fixes the defect — including the parent
branch's own next round — pytest reports XPASS, `strict=True` turns that into a
failure, and whoever fixed it must close the ticket and drop the marker.

That is the whole mechanism. No scheduled sweep, no new tooling: a fix that
lands anywhere cannot quietly leave its ticket open, because the existing suite
goes red.

Rules for this module:

- Assert the behaviour the gate *should* have, never the defect. A test that
  asserts the bug is present passes forever and has to be deleted by hand.
- `strict=True` always. A non-strict xfail that starts passing is silent, which
  is the failure mode being fixed.
- `reason` names the ticket. That is the link back, and it is what the person
  seeing XPASS needs.
- When a repro starts passing: close the ticket, delete the test from here, and
  move it into the suite that covers that surface permanently.
- **Build the fixture in a pytest fixture, never inside the test body.** `xfail`
  absorbs failures in the call phase, so a repro whose setup is broken looks
  exactly like a healthy open defect. That is not hypothetical: the first
  version of this module built its repository inside the test, with a `git init`
  whose `cwd` did not exist yet. Both repros failed at setup, both reported
  XFAIL, and the module proved nothing while looking green. A setup failure in a
  fixture is reported as an ERROR, which `xfail` does not swallow.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / ".lefthook" / "scripts"
PY_ORGANIZATION_GATE = [sys.executable, str(_SCRIPTS / "py_organization_check.py")]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=_scrubbed())


def _scrubbed() -> dict:
    import os

    env = dict(os.environ)
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(name, None)
    return env


def _repo(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    _git(tmp_path, "init", "-q", "-b", "main", ".")
    _git(tmp_path, "config", "user.email", "t@example.invalid")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "src" / "pkg" / "base.py").write_text("x = 1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    _git(tmp_path, "checkout", "-q", "-b", "ticket-branch")
    return tmp_path


def _run(gate: list[str], repo: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [*gate, "--repo", str(repo), *extra],
        capture_output=True,
        text=True,
        timeout=60,
        env=_scrubbed(),
    )


def _out(result: subprocess.CompletedProcess) -> str:
    return f"{result.stdout}\n{result.stderr}"


@pytest.fixture
def repo_with_symlinked_parent(tmp_path: Path) -> Path:
    """587's repository. A fixture, so a broken setup ERRORs instead of XFAILing."""
    outside = tmp_path / "outside" / "dir"
    outside.mkdir(parents=True)
    (outside / "x.py").write_text("import subprocess\n")
    repo = _repo(tmp_path / "repo")
    (repo / "src" / "dir").symlink_to(outside)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "directory symlink")
    return repo


@pytest.mark.xfail(
    strict=True,
    reason=(
        "lg-improved-memory-587 — open. `located_path` resolves the parent, so when the "
        "parent is itself a link out of the repository the located path is already "
        "outside, the escape guard never fires, and the foreign file is graded."
    ),
)
def test_a_source_under_a_symlinked_parent_directory_is_refused(
    repo_with_symlinked_parent: Path,
) -> None:
    """587: the guard compares located-inside against resolved-outside.

    Verified live on 2026-09-05: `exit=0`, `Python organization checks passed.`
    over a file that lives outside the repository entirely.
    """
    repo = repo_with_symlinked_parent

    result = _run(PY_ORGANIZATION_GATE, repo, str(repo / "src" / "dir" / "x.py"))

    assert result.returncode != 0, _out(result)
    assert "outside the repository" in _out(result), _out(result)


@pytest.fixture
def repo_with_ignored_link(tmp_path: Path) -> Path:
    """593's repository. Same reason as above: setup belongs in a fixture."""
    repo = _repo(tmp_path / "repo")
    (repo / ".gitignore").write_text(".env.py\n")
    (repo / ".env.py").write_text(
        'AWS_SECRET_KEY = "SENTINEL"\n\n\ndef leak(payload):\n    return isinstance(payload, dict)\n'
    )
    (repo / "src" / "pkg" / "envleak.py").symlink_to(repo / ".env.py")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "link to an ignored file")
    return repo


@pytest.mark.xfail(
    strict=True,
    reason=(
        "lg-improved-memory-593 — open. The boundary is the repository *directory*, not "
        "its tracked content, so a committed link to a gitignored file is graded and its "
        "structure printed."
    ),
)
def test_a_link_to_an_untracked_file_is_not_graded(repo_with_ignored_link: Path) -> None:
    """593: an untracked, gitignored file reaches stdout through an in-repo path.

    Verified live on 2026-09-05: a link to a gitignored `.env.py` holding a
    secret produced `envleak.py:5: isinstance(..., dict)` on stdout — the line
    numbers and structure of a file git is deliberately not tracking, in the CI
    log and the stage transcript.
    """
    repo = repo_with_ignored_link

    result = _run(PY_ORGANIZATION_GATE, repo, "--scope", "worktree", "--base", "main")

    # Not "did it fail" — it fails today, by reporting the contents. The claim is
    # that the untracked file's structure must not be read out at all.
    assert "isinstance" not in _out(result), _out(result)
