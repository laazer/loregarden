"""Releasing a directory: the reflexive base, the prune, and a spent ERROR.

lg-external-harness-1026, AC9/AC10. `test_directory_release.py` pins the design
— rename first, no pre-rename stat, an unguessable park name, a raise carrying
the original error and both paths — and `test_directory_release_races.py` covers
the races. Every case here survives both and breaks the invariant anyway.

1. **The base is relative to itself.** `Path(base).is_relative_to(base)` is
   `True`, so a containment check written exactly as AC9 words it accepts the
   base directory as a thing to release — and one call then renames every
   checkout on the machine to an unguessable name and deletes it. The
   sibling-prefix test cannot see this: `.worktrees` is not a sibling of
   `.worktrees`. Nor can it see the arguments swapped round, which accepts the
   base's parent and takes the repository with it.
2. **The prune runs between the rename and the delete**, and both of its
   failure modes are invisible to a suite that only ever runs a prune that
   works: a discarded non-zero exit (the `run_git(check=False)` result this repo
   forbids discarding), and a raise leaking out of a call whose caller is a
   sweep.
3. **"Additionally logs exactly one ERROR" has an *additionally*.** Exactly-one
   is pinned for the worst case; zero is pinned for nothing. A module that logs
   ERROR for every failed `rmtree`, including the ones it recovered from, reads
   identically — and spends on a recoverable condition the one record that means
   "the bytes are somewhere else now".
"""

from __future__ import annotations

import ast
import errno
import json
import logging
import os
import subprocess
from pathlib import Path
from unittest import mock

import pytest
from loregarden.services import directory_release
from loregarden.services.directory_release import (
    DirectoryReleaseFailed,
    ReleaseDisposition,
    release_directory,
)
from tests.worktree_helpers import git, make_repo


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    return make_repo(tmp_path)


@pytest.fixture(name="base")
def base_fixture(repo: Path) -> Path:
    base = repo.parent / ".worktrees"
    base.mkdir(exist_ok=True)
    return base


@pytest.fixture(name="park_root")
def park_root_fixture(repo: Path) -> Path:
    return repo.parent / ".worktrees-releasing"


@pytest.fixture(name="checkout")
def checkout_fixture(repo: Path, base: Path) -> Path:
    path = base / "tree-one"
    git(repo, "worktree", "add", "-q", "-b", "feature/one", str(path))
    (path / "work.txt").write_text("committed work\n")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "work")
    return path


# ---------------------------------------------------------------------------
# AC9 — containment is a strict descendant, not `is_relative_to`
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spelling", ["as-is", "trailing-dot", "through-a-parent"])
def test_the_base_itself_is_not_a_thing_that_can_be_released(
    repo: Path, base: Path, park_root: Path, checkout: Path, spelling: str
):
    """The one-line reading of AC9 that deletes every checkout on the box.

    `is_relative_to` is reflexive. A release whose target *is* the base passes
    the containment check as written, renames the base out from under every
    live agent in one syscall, and deletes it — and because the rename is
    atomic and the name is unguessable, there is nothing to recover by hand.

    Three spellings, because `resolve()` flattens all of them onto the base and
    a check that compares the strings it was handed would catch only the first.
    """
    target = {
        "as-is": base,
        "trailing-dot": base / ".",
        "through-a-parent": base / "tree-one" / "..",
    }[spelling]

    with pytest.raises(DirectoryReleaseFailed):
        release_directory(target, inside=base, park_root=park_root)

    assert base.is_dir(), "the base was released"
    assert checkout.is_dir(), "a release of the base took a live checkout with it"
    assert (checkout / "work.txt").read_text() == "committed work\n"


def test_an_ancestor_of_the_base_is_refused_as_firmly_as_a_sibling(
    repo: Path, base: Path, park_root: Path, checkout: Path
):
    """`<repo_parent>` holds the repository, the base and the park root.

    A containment check with its arguments the wrong way round —
    `base.is_relative_to(path)` instead of `path.is_relative_to(base)` — passes
    every test in the suite that only ever hands it a descendant, and accepts
    the whole workspace parent here.
    """
    with pytest.raises(DirectoryReleaseFailed):
        release_directory(base.parent, inside=base, park_root=park_root)

    assert base.parent.is_dir()
    assert repo.is_dir(), "the repository itself was released"


# ---------------------------------------------------------------------------
# AC9 — the prune runs between the rename and the delete, and it can fail
# ---------------------------------------------------------------------------


def test_a_prune_git_refuses_is_not_discarded(
    repo: Path, base: Path, park_root: Path, checkout: Path
):
    """A `run_git(..., check=False)` result nobody reads is this repo's named
    anti-pattern, and here it hides the exact condition the prune exists to
    prevent: git still holding metadata for a path that is now empty, so the
    next `worktree add` there is refused as "already registered".

    Either answer is defensible — raise, or carry it — and what is not
    defensible is a release that reports success having silently left git's
    metadata stale. So the assertion is on the pair: if the call returns
    normally, the path must really be reusable.
    """
    failed = mock.Mock(returncode=1, stdout="", stderr="fatal: could not prune")
    real_run_git = directory_release.run_git

    def prune_fails(args, **kwargs):
        if list(args[:2]) == ["worktree", "prune"]:
            return failed
        return real_run_git(args, **kwargs)

    with mock.patch.object(directory_release, "run_git", side_effect=prune_fails):
        try:
            release_directory(checkout, inside=base, park_root=park_root)
        except DirectoryReleaseFailed:
            return  # carried as a failure: the caller keeps ownership

    readded = git(repo, "worktree", "add", "-q", "-b", "feature/again", str(checkout))
    assert readded.returncode == 0, (
        "the release reported success while git still held the path's metadata"
    )


def test_a_prune_that_raises_does_not_escape_as_itself(
    repo: Path, base: Path, park_root: Path, checkout: Path
):
    """`retire_worktree` catches `DirectoryReleaseFailed` and nothing else
    (AC14), and a sweep is the caller. A bare `OSError` out of the prune ends
    the pass, and every worktree after this one goes unvisited — the
    crash-in-a-loop failure AC11 forbids, arriving from the one step in
    `release_directory` that is neither the rename nor the delete.
    """
    real_run_git = directory_release.run_git

    def prune_raises(args, **kwargs):
        if list(args[:2]) == ["worktree", "prune"]:
            raise OSError(errno.ENOMEM, "cannot fork")
        return real_run_git(args, **kwargs)

    with mock.patch.object(directory_release, "run_git", side_effect=prune_raises):
        with pytest.raises(DirectoryReleaseFailed):
            release_directory(checkout, inside=base, park_root=park_root)


# ---------------------------------------------------------------------------
# AC10 — "additionally logs exactly one ERROR" has an *additionally*
# ---------------------------------------------------------------------------


def test_a_release_that_worked_logs_no_error_at_all(
    repo: Path, base: Path, park_root: Path, checkout: Path, caplog
):
    with caplog.at_level(logging.ERROR):
        release_directory(checkout, inside=base, park_root=park_root)

    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR] == []


def test_a_failure_that_put_the_tree_back_does_not_log_the_park_path_at_error(
    repo: Path, base: Path, park_root: Path, checkout: Path, caplog
):
    """The ERROR record means one thing: the checkout is not where its row says
    it is, and only this string knows where it went. A module that logs it for
    every failed `rmtree` — including the ones it recovered from — spends that
    record on a condition with nothing to recover, and the record stops being
    read before the day it matters.

    The restored case still raises, and the raise reports `RESTORED` — the end
    state, with no park path, because there is nothing parked. That is where a
    recovered failure is reported (lg-external-harness-1413 AC-M: the
    assertion below used to require the park path here, and the spec no longer
    wants it).
    """
    parked: list[str] = []
    real_replace = os.replace

    def record(src, dst, **kwargs):
        if not parked:
            parked.append(str(dst))
        return real_replace(src, dst, **kwargs)

    with caplog.at_level(logging.ERROR):
        with mock.patch("os.replace", side_effect=record):
            with mock.patch("shutil.rmtree", side_effect=OSError(errno.EIO, "I/O error")):
                with pytest.raises(DirectoryReleaseFailed) as caught:
                    release_directory(checkout, inside=base, park_root=park_root)

    assert checkout.is_dir(), "fixture: this is the case where the restore worked"
    assert caught.value.disposition is ReleaseDisposition.RESTORED
    assert caught.value.parked_path == ""
    spurious = [
        r.getMessage()
        for r in caplog.records
        if r.levelno >= logging.ERROR and parked[0] in r.getMessage()
    ]
    assert spurious == [], (
        f"the park path was logged at ERROR after a successful restore: {spurious}"
    )


# ---------------------------------------------------------------------------
# lg-external-harness-1413 — the disposition, attacked
#
# `test_directory_release.py` pins every criterion AC1.1-AC1.6, AC-M and
# AC5.1-AC5.3 ask for. Each case below survives that suite and breaks the
# invariant anyway. The mutant each one kills is named in its docstring, and
# each was measured SURVIVING the shipped suite before it was written.
# ---------------------------------------------------------------------------


_CONSTRUCTED = ("DirectoryReleaseFailed", "DirectoryOutsideBase")


class _ConstructionSites(ast.NodeVisitor):
    """Every call that CONSTRUCTS a release failure, and the keywords it names."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.sites: list[tuple[str, int, set[str]]] = []

    def visit_Call(self, node: ast.Call) -> None:
        name = getattr(node.func, "id", "") or getattr(node.func, "attr", "")
        if name in _CONSTRUCTED:
            self.sites.append((self.path.name, node.lineno, {kw.arg for kw in node.keywords}))
        self.generic_visit(node)


def _production_construction_sites() -> list[tuple[str, int, set[str]]]:
    package = Path(directory_release.__file__).parents[1]
    found: list[tuple[str, int, set[str]]] = []
    for source in sorted(package.rglob("*.py")):
        visitor = _ConstructionSites(source)
        visitor.visit(ast.parse(source.read_text()))
        found.extend(visitor.sites)
    return found


def test_every_production_construction_site_names_a_disposition():
    """AC-M, pinned mechanically instead of by a grep somebody remembers to run.

    AC-M asks the implementer to re-grep and report the count. A grep is not a
    test: a site on a path no test drives ships a `TypeError` that first fires
    in production, on an already-failing cleanup, and turns a reportable
    release failure into a crash inside the handler that was reporting it.
    Site 7 — `worktree_service.py` — is in a different module from the other
    six and is exactly the one a grep of `directory_release.py` alone misses.

    Read off the AST rather than by substring, so a keyword inside a docstring
    or a comment cannot satisfy it.
    """
    sites = _production_construction_sites()
    missing = [(f, line, sorted(kw)) for f, line, kw in sites if "disposition" not in kw]

    assert missing == [], f"construction sites with no disposition: {missing}"
    assert len(sites) == 7, (
        "AC-M counts 7 production construction sites (4 pre-rename refusals, the new prune "
        f"handler, the new delete handler, and worktree_service's outside-base). Found: {sites}"
    )


def test_the_disposition_parameter_carries_no_default():
    """AC1.2 — "no default" is a property of the SIGNATURE, not of a call.

    `test_the_disposition_is_required_and_has_no_default` constructs with no
    disposition and expects `TypeError`, which a `disposition:
    ReleaseDisposition = ReleaseDisposition.INTACT` would also... not raise, so
    that test does hold. What it cannot see is the near-miss: a default added
    later as a kindness to one caller silently re-opens the inference this
    change deletes, and every arm that forgets to decide then reports `INTACT`
    — the one member that claims nothing was touched.
    """
    module = ast.parse(Path(directory_release.__file__).read_text())
    initialisers = [
        child
        for node in ast.walk(module)
        if type(node).__name__ == "ClassDef" and node.name == "DirectoryReleaseFailed"
        for child in node.body
        if type(child).__name__ == "FunctionDef" and child.name == "__init__"
    ]
    assert len(initialisers) == 1
    args = initialisers[0].args

    assert "disposition" not in [a.arg for a in args.args], "it must be keyword-only"
    kwonly = [a.arg for a in args.kwonlyargs]
    assert "disposition" in kwonly
    assert args.kw_defaults[kwonly.index("disposition")] is None, (
        "a default lets a construction site skip the decision and report INTACT by omission"
    )


def test_the_disposition_is_a_string_enum_and_its_value_is_the_wire_vocabulary():
    """AC1.1 — `StrEnum`, not `Enum`, and the members' values are load-bearing.

    `test_release_disposition_has_exactly_three_members` asserts the three
    name-to-value pairs, which a plain `Enum` satisfies exactly. The difference
    only shows up where the value leaves Python: `json.dumps` refuses a plain
    `Enum`, and an f-string renders it `ReleaseDisposition.INTACT`. The sibling
    ticket that puts this vocabulary in the removal payload would then ship
    either a crash or the repr, and the member values this test pins would have
    reached the API without anyone choosing them.
    """
    assert issubclass(ReleaseDisposition, str)

    for member in ReleaseDisposition:
        assert str(member) == member.value
        assert f"{member}" == member.value
        assert json.loads(json.dumps({"disposition": member})) == {"disposition": member.value}
        assert ReleaseDisposition(member.value) is member


def _prune_exits_non_zero(real_run_git, on_call):
    def run(args, **kwargs):
        if list(args[:2]) == ["worktree", "prune"]:
            on_call()
            return subprocess.CompletedProcess(
                args=["git", *args], returncode=1, stdout="", stderr="fatal: could not prune\n"
            )
        return real_run_git(args, **kwargs)

    return run


@pytest.mark.parametrize("failing_syscall", ["the delete", "the prune"])
def test_the_park_path_is_never_read_off_the_disk_once_the_handler_is_entered(
    repo: Path, base: Path, park_root: Path, checkout: Path, failing_syscall: str
):
    """AC1.6, with the window opened where the deciding actually starts.

    The shipped oracle opens its window at the RESTORING `os.replace` and
    watches four probes (`os.stat`, `os.lstat`, `os.path.exists`,
    `Path.exists`). Two forbidden implementations slip through it, and both
    were measured surviving it with zero failures:

    * a probe taken BEFORE the restore — `was_there = parked.exists()` at the
      top of the handler, carried into the decision. The window is not open
      yet, so the oracle never sees it.
    * a probe through a call it does not watch — `any(parked.iterdir())`, which
      reaches `os.scandir`, or `os.access(parked, os.F_OK)`. Both answer the
      same question `parked.exists()` does and neither is instrumented.

    Constraint 2 is wider than the shipped oracle: *nothing anywhere* may stat,
    `exists()` or `is_dir()` the park path to decide the disposition. So the
    window here opens the moment the failing syscall raises — the first
    instant at which any reading of the park path could only be a decision
    about the end state — and the probe set is widened to the directory-reading
    calls as well. `_prune_metadata` probing `<parked>/.git` happens before the
    prune fails, and so is outside the window in both arms, which is correct:
    that read is the module's own business and decides nothing.
    """
    deciding = {"yes": False}
    probes: list[str] = []
    real = {
        "stat": os.stat,
        "lstat": os.lstat,
        "scandir": os.scandir,
        "listdir": os.listdir,
        "access": os.access,
        "exists": os.path.exists,
        "path_exists": Path.exists,
    }

    def probe(name):
        def wrapper(subject, *rest, **kwargs):
            if deciding["yes"] and str(subject).startswith(str(park_root)):
                probes.append(f"{name}({subject})")
            return real[name](subject, *rest, **kwargs)

        return wrapper

    def opens_the_window():
        deciding["yes"] = True

    def rmtree_raises(path, *rest, **kwargs):
        opens_the_window()
        raise OSError(errno.EIO, "I/O error")

    arming = {
        "the delete": lambda: mock.patch("shutil.rmtree", side_effect=rmtree_raises),
        "the prune": lambda: mock.patch.object(
            directory_release,
            "run_git",
            side_effect=_prune_exits_non_zero(directory_release.run_git, opens_the_window),
        ),
    }[failing_syscall]()

    with (
        arming,
        mock.patch("os.stat", side_effect=probe("stat")),
        mock.patch("os.lstat", side_effect=probe("lstat")),
        mock.patch("os.scandir", side_effect=probe("scandir")),
        mock.patch("os.listdir", side_effect=probe("listdir")),
        mock.patch("os.access", side_effect=probe("access")),
        mock.patch("os.path.exists", side_effect=probe("exists")),
        mock.patch.object(Path, "exists", probe("path_exists")),
    ):
        with pytest.raises(DirectoryReleaseFailed) as caught:
            release_directory(checkout, inside=base, park_root=park_root)

    assert deciding["yes"], "fixture: this arm must actually reach the failing syscall"
    assert caught.value.disposition is not ReleaseDisposition.STILL_PARKED, (
        "fixture: the restore was left alone, so it must have worked"
    )
    assert probes == [], f"the park path was read off the disk to decide the disposition: {probes}"


@pytest.mark.parametrize("failing_syscall", ["the delete", "the prune"])
def test_a_recovered_failure_leaves_nothing_at_all_under_the_park_root(
    repo: Path, base: Path, park_root: Path, checkout: Path, failing_syscall: str
):
    """A `RESTORED` or `INTACT` answer claims there is nothing parked. Check it.

    Every assertion in the shipped suite about these two arms reads the
    EXCEPTION — an empty `parked_path`, the park path absent from the message,
    from the row and from the payload — or reads the original path and finds
    the bytes there. None of them looks under the park root. So a `_restore`
    written as a COPY back rather than a move passes the whole suite while
    minting precisely the orphan this module exists to stop creating: a second
    checkout of the same branch, under a name no row points at and no sweeper
    revisits, with an exception that says `parked_path=""` so nothing will ever
    go looking. `_restore`'s own docstring promises the move; nothing tested it.
    """
    arming = {
        "the delete": lambda: mock.patch(
            "shutil.rmtree", side_effect=OSError(errno.EIO, "I/O error")
        ),
        "the prune": lambda: mock.patch.object(
            directory_release,
            "run_git",
            side_effect=_prune_exits_non_zero(directory_release.run_git, lambda: None),
        ),
    }[failing_syscall]()

    with arming:
        with pytest.raises(DirectoryReleaseFailed) as caught:
            release_directory(checkout, inside=base, park_root=park_root)

    assert caught.value.disposition is not ReleaseDisposition.STILL_PARKED
    assert caught.value.parked_path == ""
    assert checkout.is_dir(), "fixture: this is the arm whose restore worked"
    leftovers = sorted(p.name for p in park_root.iterdir()) if park_root.is_dir() else []
    assert leftovers == [], (
        f"an answer carrying no park path left {leftovers} under {park_root}: an orphan "
        "no row points at, which is the state this module exists to stop creating"
    )


def test_a_delete_that_got_part_way_before_raising_still_reports_restored(
    repo: Path, base: Path, park_root: Path, checkout: Path
):
    """`RESTORED` says the bytes are back and POSSIBLY PARTLY DELETED. Make them so.

    Every arm in the shipped suite makes `shutil.rmtree` raise before it
    touches anything, so the parked tree is pristine when `_restore` runs and
    the member's own wording — "possibly partly deleted" — is never exercised.
    That is the one shape EIO actually takes in production: `rmtree` walks the
    tree, unlinks what it can, and raises on the entry it cannot.

    The mutant this kills refuses to restore a tree the delete already damaged
    — the plausible-looking "don't move a half-deleted tree back over a good
    path" guard — which would answer `STILL_PARKED` for the commonest failure
    there is, and leave the row pointing at nothing.
    """
    survivors: dict[str, object] = {}

    def partly_delete_then_raise(path, *rest, **kwargs):
        victim = Path(path) / "work.txt"
        victim.unlink()
        survivors["gone"] = victim.name
        raise OSError(errno.EIO, "I/O error")

    with mock.patch("shutil.rmtree", side_effect=partly_delete_then_raise):
        with pytest.raises(DirectoryReleaseFailed) as caught:
            release_directory(checkout, inside=base, park_root=park_root)

    assert survivors["gone"] == "work.txt", "fixture: the delete really got part way"
    assert caught.value.disposition is ReleaseDisposition.RESTORED
    assert caught.value.parked_path == ""

    # Back at the path the row names, damage and all — which is what the member
    # claims, and is strictly better than being under an unguessable name.
    assert checkout.is_dir()
    assert not (checkout / "work.txt").exists(), "fixture: the partial delete really happened"
    assert (checkout / ".git").exists(), "what the delete did not reach came back"
    assert sorted(p.name for p in park_root.iterdir()) == []


@pytest.mark.parametrize(
    ("failing_syscall", "restore"), [("the delete", "fails"), ("the prune", "fails")]
)
def test_a_failed_restore_logs_exactly_one_error_whichever_syscall_raised(
    repo: Path, base: Path, park_root: Path, checkout: Path, caplog, failing_syscall, restore
):
    """Exactly-one is pinned for the delete arm only. The split adds a second handler.

    AC5.2 counts the ERROR records for a failed `rmtree` whose restore also
    failed. Nothing counts them for the prune arm, which is a handler that did
    not exist before this change — so a `logger.error` added there, reporting a
    condition `_restore` already reported, doubles the record that is supposed
    to mean one thing, and no test in the suite notices. Constraint 5 says
    `_restore`'s is the only ERROR in the module; this asserts the COUNT on
    both arms, never the wording.
    """
    real_replace = os.replace
    parked: list[str] = []

    def replace_once_then_fail(src, dst, **kwargs):
        if not parked:
            parked.append(str(dst))
            return real_replace(src, dst, **kwargs)
        raise OSError(errno.EIO, "the restore failed too")

    arming = {
        "the delete": lambda: mock.patch(
            "shutil.rmtree", side_effect=OSError(errno.EIO, "I/O error")
        ),
        "the prune": lambda: mock.patch.object(
            directory_release,
            "run_git",
            side_effect=_prune_exits_non_zero(directory_release.run_git, lambda: None),
        ),
    }[failing_syscall]()

    with caplog.at_level(logging.ERROR):
        with arming:
            with mock.patch("os.replace", side_effect=replace_once_then_fail):
                with pytest.raises(DirectoryReleaseFailed) as caught:
                    release_directory(checkout, inside=base, park_root=park_root)

    assert caught.value.disposition is ReleaseDisposition.STILL_PARKED
    assert caught.value.parked_path == parked[0]
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1, [r.getMessage() for r in errors]
    assert parked[0] in errors[0].getMessage()
