"""The one channel every gate reads a source file through.

`precommit_git_diff.read_source_text` is where "I could not examine this"
becomes `UnexaminableFileError` rather than an empty string a gate reports
clean. These exercise it directly, on a `tmp_path`, with no repository and no
gate subprocess in the way — the gate-level consequences of the same guard
live in `test_gate_scope_visibility.py`, which is the harness for scope and
cross-gate conformance.

Split out of that module when it crossed the 2500-line organization cap: these
tests share nothing with it but the loader below, and they are the half that
needs neither git nor a subprocess.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / ".lefthook" / "scripts"


def _load_script(module_name: str):
    """Import a gate script by name, with `.lefthook/scripts` on the path.

    Imported rather than exec'd from a spec so it lands in `sys.modules`: its
    dataclasses resolve their annotations through their own module, and a
    module that is not registered there raises while building them.
    """
    if str(_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS))
    return importlib.import_module(module_name)


def test_a_symlinked_source_is_refused_rather_than_followed(tmp_path):
    """A graded path that is not a regular file is unexaminable, not clean.

    `located_path` resolves only the parent on purpose, so a symlinked module
    keeps the relpath git used and stays gradeable. That leaves the file itself
    a symlink, and opening it follows the link: `src/x.py -> /dev/zero` never
    returns and allocates until the host gives out, which in orchestration is
    bounded only by the 300s gate timeout — a gate that stops being a gate. The
    check belongs at the read, where the target is known.
    """
    diff = _load_script("precommit_git_diff")
    os.symlink("/dev/zero", tmp_path / "leak.py")

    with pytest.raises(diff.UnexaminableFileError) as excinfo:
        diff.read_source_text(tmp_path / "leak.py", repo=tmp_path)

    assert "not a regular file" in str(excinfo.value)


def test_a_broken_symlink_names_the_file_instead_of_raising_oserror(tmp_path):
    """A dangling link must arrive through the one channel, not as a traceback.

    Before the check it raised a bare `FileNotFoundError` out of the middle of
    the walk, which blocks every stage transition without naming what is wrong —
    loud, but unactionable, and it bypasses the `UnexaminableError` channel that
    is supposed to be the single place a gate decides about a file it could not
    examine.
    """
    diff = _load_script("precommit_git_diff")
    os.symlink(tmp_path / "gone.py", tmp_path / "broken.py")

    with pytest.raises(diff.UnexaminableFileError) as excinfo:
        diff.read_source_text(tmp_path / "broken.py", repo=tmp_path)

    assert "broken.py" in str(excinfo.value)


def test_an_oversized_file_is_refused_rather_than_read_whole(tmp_path):
    """No hand-written module approaches the cap; a path that does is not source."""
    diff = _load_script("precommit_git_diff")
    big = tmp_path / "huge.py"
    big.write_bytes(b"# padding\n" * ((diff.MAX_SOURCE_BYTES // 10) + 1))

    with pytest.raises(diff.UnexaminableFileError) as excinfo:
        diff.read_source_text(big, repo=tmp_path)

    assert "grading limit" in str(excinfo.value)


def test_an_ordinary_source_file_still_reads(tmp_path):
    """The positive control: the guard must not refuse real source."""
    diff = _load_script("precommit_git_diff")
    ok = tmp_path / "ok.py"
    ok.write_text("import os\n\n\ndef f():\n    return os.sep\n", encoding="utf-8")

    assert diff.read_source_text(ok, repo=tmp_path).startswith("import os")


def test_read_source_text_has_no_default_repository(tmp_path):
    """AC1: `repo` is required and keyword-only, so nobody can opt out of the check.

    A default would let a caller that never passed one read exactly as before,
    and "nobody passed it" would be indistinguishable from "nothing to check" —
    the defect shape this family is made of. Positional passing is refused too:
    the second argument to a reader is not obviously a repository root, and a
    caller that guesses wrong silently widens the boundary.
    """
    diff = _load_script("precommit_git_diff")
    ok = tmp_path / "ok.py"
    ok.write_text("x = 1\n", encoding="utf-8")

    with pytest.raises(TypeError):
        diff.read_source_text(ok)
    with pytest.raises(TypeError):
        diff.read_source_text(ok, tmp_path)


def test_a_symlink_out_of_the_repo_names_the_path_and_its_target(tmp_path):
    """AC1: the message carries both halves, because either alone is unactionable.

    The path as git spells it is the only handle the reader has on the diff;
    the resolved target is the only thing that explains why a file that looks
    like source was refused. A refusal naming one of the two sends the reader
    looking for a file that reads fine.
    """
    diff = _load_script("precommit_git_diff")
    repo_root = tmp_path / "repo"
    (repo_root / "src").mkdir(parents=True)
    outside = tmp_path / "elsewhere" / "target.py"
    outside.parent.mkdir()
    outside.write_text("x = 1\n", encoding="utf-8")
    link = repo_root / "src" / "linked.py"
    link.symlink_to(outside)

    with pytest.raises(diff.UnexaminableFileError) as excinfo:
        diff.read_source_text(link, repo=repo_root)

    message = str(excinfo.value)
    assert str(link) in message, message
    assert str(outside.resolve()) in message, message
    assert "cannot be reported clean" in message, message


def test_a_symlink_inside_the_repo_still_reads(tmp_path):
    """AC1's positive control at the unit: the guard refuses targets, not links.

    Without this a guard that raises on every symlink satisfies every other
    assertion here. That is not a stricter gate, it is an outage: a workspace
    that links a module into its source root would be unable to transition a
    stage at all.
    """
    diff = _load_script("precommit_git_diff")
    repo_root = tmp_path / "repo"
    (repo_root / "src").mkdir(parents=True)
    real = repo_root / "vendored.py"
    real.write_text("import os\n", encoding="utf-8")
    link = repo_root / "src" / "linked.py"
    link.symlink_to(real)

    assert diff.read_source_text(link, repo=repo_root) == "import os\n"


def test_a_repository_reached_through_a_symlink_still_grades_its_own_files(tmp_path):
    """AC1: both sides are resolved, or a macOS checkout refuses everything it owns.

    The target is compared resolved. If the repository root it is compared
    against is not, a checkout reached through a symlinked prefix — `/var` ->
    `/private/var` on macOS, which is where `tmp_path` itself lives, and every
    agent worktree under a linked home — puts *every* file it owns outside its
    own root. The gate then refuses the whole tree and blocks every stage
    transition, and the same code passes wherever the prefix happens to be
    real. A platform-dependent path bug in 546 passed on macOS and failed only
    in CI; this is written so the mirror image of it cannot pass here either.
    """
    diff = _load_script("precommit_git_diff")
    real_root = tmp_path / "real_repo"
    (real_root / "src").mkdir(parents=True)
    (real_root / "src" / "mod.py").write_text("import os\n", encoding="utf-8")
    linked_root = tmp_path / "linked_repo"
    linked_root.symlink_to(real_root, target_is_directory=True)

    assert diff.read_source_text(linked_root / "src" / "mod.py", repo=linked_root) == "import os\n"


def test_a_repository_reached_through_a_symlink_still_catches_an_escape(tmp_path):
    """The other half of the macOS row: an unresolved root must not disable the check.

    `test_a_repository_reached_through_a_symlink_still_grades_its_own_files`
    only pins that a symlinked root does not refuse its own files, and an
    implementation that compares against an *unresolved* root satisfies it — by
    concluding the file is not in the repository at all and skipping the check.
    Passing and not-checking are the same observation there, which is the shape
    this whole file exists to keep apart.

    Same fixture, escape added. The refusal has to survive the symlinked
    prefix, or the guard is off for every macOS checkout and every agent
    worktree under a linked home — which is to say off in exactly the
    environments it was written for.
    """
    diff = _load_script("precommit_git_diff")
    real_root = tmp_path / "real_repo"
    (real_root / "src").mkdir(parents=True)
    linked_root = tmp_path / "linked_repo"
    linked_root.symlink_to(real_root, target_is_directory=True)
    outside = tmp_path / "elsewhere" / "target.py"
    outside.parent.mkdir()
    outside.write_text("x = 1\n", encoding="utf-8")
    (real_root / "src" / "linked.py").symlink_to(outside)

    with pytest.raises(diff.UnexaminableFileError) as excinfo:
        diff.read_source_text(linked_root / "src" / "linked.py", repo=linked_root)

    assert str(outside.resolve()) in str(excinfo.value)


def test_a_sibling_directory_sharing_the_repositorys_prefix_is_outside_it(tmp_path):
    """AC1: containment is a path-component question, not a string one.

    `str(target).startswith(str(repo))` is the cheapest way to write this check
    and it is wrong by one character: `/w/repo` is a prefix of
    `/w/repo-vendor/x.py`, so a link into the sibling directory beside a
    checkout — where a workspace's vendored or generated trees usually sit —
    reads as inside the repository and is graded clean. `relative_to` and
    `is_relative_to` both answer on components and get this right; nothing else
    in this file would notice the difference.
    """
    diff = _load_script("precommit_git_diff")
    repo_root = tmp_path / "repo"
    (repo_root / "src").mkdir(parents=True)
    sibling = tmp_path / "repo-vendor"
    sibling.mkdir()
    outside = sibling / "target.py"
    outside.write_text("x = 1\n", encoding="utf-8")
    link = repo_root / "src" / "linked.py"
    link.symlink_to(outside)

    with pytest.raises(diff.UnexaminableFileError) as excinfo:
        diff.read_source_text(link, repo=repo_root)

    assert str(outside.resolve()) in str(excinfo.value)


def test_a_path_the_caller_named_outside_the_repository_still_reads(tmp_path):
    """The unit half of the explicitly-listed decision; see the gate test of the
    same name for why it is this way round.

    Nothing here presents itself as a repository file: the caller handed over
    an absolute path that is plainly elsewhere. Refusing it would refuse every
    fixture `test_silent_except_gate.py` grades, and a gate that cannot be
    pointed at a file is not a stricter gate.
    """
    diff = _load_script("precommit_git_diff")
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    outside = tmp_path / "elsewhere.py"
    outside.write_text("x = 1\n", encoding="utf-8")

    assert diff.read_source_text(outside, repo=repo_root) == "x = 1\n"


def test_a_run_outside_any_checkout_grades_what_it_is_handed(tmp_path):
    """AC1: `repo=None` is "there is no boundary", not "refuse everything".

    `Invocation.repo` is `Optional[Path]` because `git_repo_root()` returns
    `None` outside a checkout, and `resolve_gate_scope` already has an answer
    for that case. `None` has to reach `read_source_text` explicitly — the
    keyword stays required, so no caller opts out by forgetting — and mean the
    only thing it can mean: there is no repository to be outside of.
    """
    diff = _load_script("precommit_git_diff")
    loose = tmp_path / "loose.py"
    loose.write_text("x = 1\n", encoding="utf-8")

    assert diff.read_source_text(loose, repo=None) == "x = 1\n"


def test_a_symlink_cycle_is_refused_through_the_one_channel(tmp_path):
    """ELOOP raises `RuntimeError`, which is not an `OSError`.

    `Path.resolve` raises `RuntimeError` on a symlink cycle. Catching only
    `OSError` let it escape the `UnexaminableError` channel entirely: the
    organization gate's DRY catalog walks the whole source root, so one committed
    cycle anywhere under it took the gate down with a traceback even when every
    listed file was clean. Never a false pass — but a repository's own contents
    blocking every stage transition, which is the other way a gate stops being
    useful. The TypeScript gate already handled it; this pins the Python side.
    """
    diff = _load_script("precommit_git_diff")
    os.symlink(tmp_path / "b.py", tmp_path / "a.py")
    os.symlink(tmp_path / "a.py", tmp_path / "b.py")

    with pytest.raises(diff.UnexaminableFileError) as excinfo:
        diff.read_source_text(tmp_path / "a.py", repo=tmp_path)

    assert "a.py" in str(excinfo.value)
