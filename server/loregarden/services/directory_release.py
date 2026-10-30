"""Releasing a directory: the rename IS the claim, and a failure keeps the resource.

Two defects lived in the old spelling (``worktree_service.cleanup_worktree``),
and this module is where both die.

**Check-then-act.** It asked ``worktree_path.exists()`` and then removed, so the
answer to the question is what licensed the act — and between the question and
the act anything can happen. :func:`release_directory` never asks: it
``os.replace``s the live path to an unguessable name under a sibling park root,
and *the rename's own failure is the check*. One syscall, no window, and the
loser of a race learns by failing rather than by having been told yes earlier.

**A failure reported as an outcome.** It answered ``False`` for "outside the
base", for "``worktree remove`` raised" and for "``rmtree`` raised" alike, and
a ``False`` read as "not removed" while the directory was in fact half gone.
Every failure here raises, and the raise carries the original error as
``__cause__``, what was not cleaned, and a :class:`ReleaseDisposition` saying
what is true of the bytes and of git's record of them at that moment — with a
park path only on the one arm where the bytes really are still parked.

:func:`directory_state` is the other half: ENOENT and ENOTDIR are the only
absences, and every other ``OSError`` is raised rather than answered. Measured
under ``server/.venv`` (3.11.15), ``Path.is_dir()`` and ``.exists()`` already
raise ``PermissionError`` on EACCES — ``pathlib._IGNORED_ERRNOS == (2, 20, 9,
62)`` — so what this buys is the *stated* contract and one place to convert it,
so a sweep loop is not ended by a stat nobody thought about.

This module is also the destination for the nine other ``shutil.rmtree`` sites
in the tree that this ticket deliberately does not move (AC31): whatever
removes a directory next has a rename-then-delete to reach for instead of
writing its own.
"""

from __future__ import annotations

import errno
import logging
import os
import shutil
import stat as stat_module
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from loregarden.services.git_subprocess import run_git

logger = logging.getLogger(__name__)

#: The two errnos that mean "there is nothing here", and the only two. ENOENT
#: is the path's own absence; ENOTDIR is a parent that is a file, so the child
#: cannot exist either. Anything else is the kernel declining to answer, which
#: is not an answer.
_ABSENT_ERRNOS = (errno.ENOENT, errno.ENOTDIR)


class DirectoryState(StrEnum):
    """Whether a directory is there. Deliberately not a bool.

    ``if not state:`` is the line that deleted on an unreadable stat, so the
    answer is not usable as a predicate. There is no third member: uncertainty
    is raised, not returned.
    """

    PRESENT = "present"
    ABSENT = "absent"


class ReleaseDisposition(StrEnum):
    """What is true of the bytes, and of git's record of them, at the raise.

    Named for the OBSERVABLE END STATE and never for how far the attempt got,
    so that no member is false about an arm it covers and no caller has to
    infer anything from a field being empty.

    ``INTACT``
        The directory is whole at ``uncleaned_path``, git's worktree
        registration still names that path, and ``parked_path`` is empty.
        Nothing was deleted and nothing was pruned: either the release was
        refused before the claiming rename, or the prune raised — in which
        case ``shutil.rmtree`` never ran — and the rename back succeeded.
    ``RESTORED``
        The directory is back at ``uncleaned_path`` and may be PARTLY DELETED,
        git's registration has been PRUNED, and ``parked_path`` is empty. The
        prune succeeded, the delete raised, and the rename back succeeded.
    ``STILL_PARKED``
        The bytes are not at ``uncleaned_path`` at all: the rename back failed,
        and ``parked_path`` is the only thing on the system that records where
        they went.
    """

    INTACT = "intact"
    RESTORED = "restored"
    STILL_PARKED = "still-parked"


class DirectoryReleaseFailed(OSError):
    """A release was attempted and the directory is still somewhere on disk.

    Carries what the exception type alone cannot: the original failure as
    ``__cause__``, the path that was not cleaned, and the parked name the
    rename produced — which, when a restore also failed, is the only thing
    that knows where the bytes went.

    ``disposition`` says which: ``parked_path`` is non-empty if and only if
    the disposition is ``STILL_PARKED``. There is no validator — the exception
    must construct on any combination, because a runtime check on an
    already-failing path turns a reporting bug into a second crash — so that
    invariant is this sentence plus a parametrized test over the real arms.

    ``refusal`` is the caller's own vocabulary, left for the seam to stamp:
    :class:`~loregarden.services.worktree_service.CleanupRefusal` cannot be
    imported here (``worktree_service`` imports this module), and a module
    whose job is to release a directory has no business knowing what a worktree
    is.
    """

    def __init__(
        self,
        message: str,
        *,
        disposition: ReleaseDisposition,
        refusal: object | None = None,
        uncleaned_path: str = "",
        parked_path: str = "",
    ) -> None:
        super().__init__(message)
        self.disposition = disposition
        self.refusal = refusal
        self.uncleaned_path = uncleaned_path
        self.parked_path = parked_path


class DirectoryOutsideBase(DirectoryReleaseFailed):
    """The path is not a strict descendant of the directory it must stay inside.

    A subclass rather than an unrelated type on purpose: ``retire_worktree`` is
    the one place that catches a release failure (AC14), and a second exception
    type is a second handler someone forgets — after which an uncaught raise
    ends a sweep.
    """


def directory_state(path: Path | str) -> DirectoryState:
    """Whether `path` is a directory, raising when the kernel will not say.

    ENOENT and ENOTDIR answer ABSENT. Every other ``OSError`` — EACCES, EIO,
    ELOOP, ENAMETOOLONG — is raised with its errno intact, because "I will not
    tell you" is one step short of a delete and every bool-shaped helper in
    this tree used to turn it into "there is nothing here".
    """
    try:
        info = os.stat(path)
    except OSError as exc:
        if exc.errno in _ABSENT_ERRNOS:
            return DirectoryState.ABSENT
        raise
    return DirectoryState.PRESENT if stat_module.S_ISDIR(info.st_mode) else DirectoryState.ABSENT


def _resolved_without_probing_it(path: Path) -> Path:
    """Where `path` really points, without stat-ing `path` itself.

    ``Path.resolve()`` would be the obvious call and it ``stat``s its own
    target, which is precisely the pre-rename probe AC9 forbids: asking adds
    nothing the rename will not answer, and licenses the act on a reply that
    may already be stale. So the *parent* is resolved — a different path, and
    one the release never touches — and ``..`` is flattened textually.

    The final component is never followed. Following it and judging
    containment on the destination releases the *destination*: an inside->
    inside link (``<base>/link`` -> ``<base>/real``) passes containment on
    ``<base>/real``, so the rename and the ``rmtree`` delete another row's
    checkout — its uncommitted work included — while the link itself stays on
    disk and the caller is told the release succeeded. A checkout is a
    directory git created and never a link, so a link here is not the thing
    this gate was asked to release, in either direction. It is refused.
    """
    parent = path.parent.resolve()
    candidate = Path(os.path.normpath(parent / path.name))
    # `os.path.islink`, not `Path.is_symlink()`: the latter is implemented
    # with `os.stat` in 3.11 and so probes the live path after all.
    if os.path.islink(candidate):
        raise DirectoryOutsideBase(
            f"{candidate} is a symlink, not a checkout; nothing was attempted",
            disposition=ReleaseDisposition.INTACT,
            uncleaned_path=str(candidate),
        )
    return candidate


def _contained(target: Path, base: Path) -> bool:
    """Is `target` a STRICT descendant of `base`, both already resolved?

    ``Path.is_relative_to`` is reflexive, so the plain reading of it accepts
    the base itself — and one call would then rename every live agent's
    checkout out from under them in a single syscall and delete it. The base
    and its ancestors are refused as firmly as a sibling.
    """
    return target.is_relative_to(base) and target != base


def _prune_metadata(parked: Path) -> None:
    """Drop git's record of the worktree that used to live at the released path.

    Run from the *parked* copy, which still carries the ``.git`` file pointing
    at its admin directory, so no separate repository argument is needed and
    nothing has to be read off the live path before the rename. A directory
    that was never a registered worktree has no ``.git`` file and nothing to
    prune.

    A non-zero exit is not discarded: without the prune git still holds
    metadata for the original path and the next ``worktree add`` there is
    refused as "already registered", which is exactly the condition this step
    exists to prevent. It is raised as an ``OSError`` so the caller's recovery
    — restore, then report — runs for a failed prune as it does for a failed
    delete.
    """
    if not (parked / ".git").exists():
        return
    pruned = run_git(
        ["worktree", "prune"],
        cwd=str(parked),
        check=False,
        capture_output=True,
        text=True,
    )
    if pruned.returncode != 0:
        detail = ((pruned.stderr or pruned.stdout) or "git worktree prune failed").strip()
        raise OSError(errno.EIO, f"git worktree prune failed: {detail}")


def release_directory(path: Path | str, *, inside: Path | str, park_root: Path | str) -> None:
    """Remove `path` by renaming it out of the way and then deleting it.

    `inside` is the directory `path` must be a strict descendant of, compared
    segment-aware on resolved paths — which is what makes a sibling sharing a
    string prefix (``.worktrees-old/x`` against ``.worktrees``) a refusal where
    the ``str.startswith`` guard this replaces accepted it. A symlink is
    refused outright, whichever side of the base it points at, because what a
    release acts on must be the directory it was named and not whatever that
    name currently dereferences to.

    `park_root` must be a sibling of `inside` under the same parent, so the
    rename is same-device: a cross-device ``os.replace`` answers EXDEV, which
    is not a rename and not atomic, and degrading it into copy-then-delete
    would put the window straight back. EXDEV is reported rather than worked
    around.

    Returns ``None`` on success and raises :class:`DirectoryReleaseFailed`
    otherwise. There is no arm that returns normally with the bytes still on
    disk somewhere.
    """
    target = _resolved_without_probing_it(Path(path))
    base = Path(inside).resolve()
    if not _contained(target, base):
        raise DirectoryOutsideBase(
            f"{path} is not inside {inside}, so nothing was attempted",
            disposition=ReleaseDisposition.INTACT,
            uncleaned_path=str(target),
        )

    try:
        Path(park_root).mkdir(parents=True, exist_ok=True)
        parked = Path(park_root).resolve() / uuid4().hex
    except OSError as exc:
        raise DirectoryReleaseFailed(
            f"{target} was not released: its park root {park_root} could not be made",
            disposition=ReleaseDisposition.INTACT,
            uncleaned_path=str(target),
        ) from exc

    # The claim. No `exists()`, no `stat()`, no `worktree remove` — one syscall
    # that either moves the directory or says why it could not.
    try:
        os.replace(target, parked)
    except OSError as exc:
        raise DirectoryReleaseFailed(
            f"{target} was not released: the rename that claims it failed",
            disposition=ReleaseDisposition.INTACT,
            uncleaned_path=str(target),
        ) from exc

    # The prune and the delete are attempted SEPARATELY because they do not
    # fail into the same end state. A prune that raises means `shutil.rmtree`
    # never ran, so the tree is whole and still registered; a delete that
    # raises means the registration is already gone and the bytes may be part
    # way through being removed. One handler could only tell one of those two
    # stories, and it would be false about the other.
    try:
        _prune_metadata(parked)
    except OSError as exc:
        disposition = _restore(parked, target, on_success=ReleaseDisposition.INTACT)
        raise DirectoryReleaseFailed(
            _failure_message(target, parked, disposition),
            disposition=disposition,
            uncleaned_path=str(target),
            parked_path=str(parked) if disposition is ReleaseDisposition.STILL_PARKED else "",
        ) from exc

    try:
        shutil.rmtree(parked)
    except OSError as exc:
        disposition = _restore(parked, target, on_success=ReleaseDisposition.RESTORED)
        raise DirectoryReleaseFailed(
            _failure_message(target, parked, disposition),
            disposition=disposition,
            uncleaned_path=str(target),
            parked_path=str(parked) if disposition is ReleaseDisposition.STILL_PARKED else "",
        ) from exc


def _failure_message(target: Path, parked: Path, disposition: ReleaseDisposition) -> str:
    """What to say about a release that failed after the claiming rename.

    Only the ``STILL_PARKED`` sentence names the park root: on the other two
    the rename back succeeded, so that name points at nothing and reading it
    sends whoever is holding the failure to a path that is not there.
    """
    if disposition is ReleaseDisposition.STILL_PARKED:
        return f"{target} was not deleted and is not back either; it is parked at {parked}"
    if disposition is ReleaseDisposition.RESTORED:
        return f"{target} was not deleted; it was put back where it was, possibly partly removed"
    return f"{target} was not deleted; nothing was removed and it is where it was"


def _restore(parked: Path, target: Path, *, on_success: ReleaseDisposition) -> ReleaseDisposition:
    """Put the tree back where its row says it is, or say loudly where it is not.

    A tree left under the park root is a tree no row points at and no sweeper
    revisits — the orphan this whole ticket exists to stop creating, minted by
    the recovery path. Moved back rather than copied back, so a failure cannot
    leave a second checkout of the same branch behind.

    The one ERROR record in this module, and it means one thing: the checkout
    is not where its row says it is and only this string knows where it went.
    A failure the restore recovered from is reported by the raise, which says
    so in its own words — spending this record on it would retire the record
    before the day it matters.

    Returns `on_success` when the tree is back where its row says it is, and
    ``STILL_PARKED`` when it is not. The caller reports the end state from this
    answer and from which syscall raised, never by stat-ing the park path: that
    would be a check-then-act on a path another process may be moving.
    """
    try:
        os.replace(parked, target)
    except OSError:
        logger.error(
            "Could not put %s back; its contents are parked at %s and nothing else records "
            "that path — move it back by hand before the next sweep",
            target,
            parked,
            exc_info=True,
        )
        return ReleaseDisposition.STILL_PARKED
    return on_success
