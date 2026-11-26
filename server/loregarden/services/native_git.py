"""Which git binary to run: a native one on Apple silicon when PATH's runs under Rosetta.

On an Apple-silicon Mac whose PATH leads to an Intel git (`/usr/local/bin/git`
from an Intel Homebrew), every git the server and its tests spawn pays Rosetta:
measured 2026-11-25 at 98ms per `git rev-parse` against 50ms for the native one
in the Command Line Tools, and half a test run's worker time was spent waiting
on child processes, much of it git starting or exiting.

The rule, in order (`.lefthook/scripts/native-git.sh` is the same rule for the
hooks, and `test_native_git.py` holds the two to each other):

1. `LOREGARDEN_GIT_BIN` names one: run that, unchecked. It is a person's choice.
2. Not Apple silicon (Linux, CI, an Intel Mac): PATH's `git`. Nothing to fix.
3. PATH's git is native and new enough: keep it (a person's choice again).
   `/usr/bin/git` does not count: it is the `xcrun` shim, slower than either.
   Nor does a hooks' shim directory inherited on PATH; it is looked past.
4. The first of `NATIVE_GIT_CANDIDATES` that has an arm64 slice and is at least
   `MIN_GIT_VERSION`.
5. PATH's `git`, translated, with a warning that says what to install.
   Never silently: the doctor's `native_git` check reports the same.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import functools
import logging
import os
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

from pydantic import BaseModel

logger = logging.getLogger(__name__)

#: Overrides the whole rule, like the agent CLIs' `LOREGARDEN_*_BIN`. The hooks
#: export it once resolved, so the Python they start does not resolve again.
GIT_BINARY_ENV = "LOREGARDEN_GIT_BIN"

#: `git merge-tree --write-tree` (landing, merge queue, branch replay) arrived in 2.38.
MIN_GIT_VERSION = (2, 38)

#: Native gits worth preferring, best first: an arm64 Homebrew's, then Apple's.
#: Apple's are universal binaries; run by absolute path they need no `xcrun`.
NATIVE_GIT_CANDIDATES = (
    Path("/opt/homebrew/bin/git"),
    Path("/Library/Developer/CommandLineTools/usr/bin/git"),
    Path("/Applications/Xcode.app/Contents/Developer/usr/bin/git"),
)

#: The `xcrun` shim: native, but it looks up a developer dir on every call (142ms).
XCRUN_GIT_SHIM = Path("/usr/bin/git")

#: What to do when nothing native qualifies; also the doctor's remediation.
NATIVE_GIT_REMEDY = (
    "Install Apple's Command Line Tools (`xcode-select --install`), whose git is native, "
    "or point LOREGARDEN_GIT_BIN at a native git >= 2.38."
)

#: Seconds for `git --version`; a git that cannot answer that is not a candidate.
_VERSION_TIMEOUT_SECONDS = 10

_CPU_TYPE_ARM64 = 0x0100000C
_MH_MAGIC_64 = 0xFEEDFACF
_FAT_MAGIC = 0xCAFEBABE
_FAT_MAGIC_64 = 0xCAFEBABF

_VERSION_RE = re.compile(r"git version (\d+)\.(\d+)")


class GitChoice(BaseModel):
    """The git to spawn, and why, for `run_git` and the doctor."""

    #: An absolute path, or `git` to look it up on PATH.
    binary: str
    #: False only when the machine is Apple silicon and this git runs translated.
    native: bool
    reason: str


def apple_silicon() -> bool:
    """Whether the hardware is arm64, asked of the kernel, so a translated process gets it right."""
    if sys.platform != "darwin":
        return False
    libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    value = ctypes.c_int(0)
    size = ctypes.c_size_t(ctypes.sizeof(value))
    # ENOENT is an Intel Mac.
    found = libc.sysctlbyname(
        b"hw.optional.arm64", ctypes.byref(value), ctypes.byref(size), None, 0
    )
    return found == 0 and value.value == 1


def has_arm64_slice(path: Path) -> bool:
    """Whether the Mach-O at `path` (symlinks followed) can run natively on Apple silicon."""
    try:
        with open(path, "rb") as handle:
            header = handle.read(8)
            if len(header) < 8:
                return False
            (magic,) = struct.unpack(">I", header[:4])
            if magic in (_FAT_MAGIC, _FAT_MAGIC_64):
                (count,) = struct.unpack(">I", header[4:8])
                entry = 32 if magic == _FAT_MAGIC_64 else 20
                table = handle.read(entry * count)
                return any(
                    struct.unpack(">I", table[i * entry : i * entry + 4])[0] == _CPU_TYPE_ARM64
                    for i in range(min(count, len(table) // entry))
                )
            (thin_magic, cputype) = struct.unpack("<Ii", header)
            return thin_magic == _MH_MAGIC_64 and cputype == _CPU_TYPE_ARM64
    except OSError:  # silent-ok: an unreadable candidate is not a candidate; the rule moves on
        return False


def git_version(binary: str) -> tuple[int, int] | None:
    """`(major, minor)` from `binary --version`, or None when it does not answer."""
    try:
        result = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=_VERSION_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("could not ask %s for its version: %s", binary, exc)
        return None
    match = _VERSION_RE.search(result.stdout)
    return (int(match.group(1)), int(match.group(2))) if match else None


#: The hooks' shim directories (`native-git.sh`): `<TMPDIR>/loregarden-native-git-<uid>`.
_SHIM_DIR_PREFIX = "loregarden-native-git-"


def _without_shims(path: str | None) -> str | None:
    """`path` minus the hooks' shim directories.

    A shim is a symlink to Apple's git, which finds its helpers from the path it
    was run by; it works only beside the GIT_EXEC_PATH exported with it, so an
    inherited one is never evidence that PATH's own git is native.
    """
    if path is None:
        return None
    return os.pathsep.join(
        entry
        for entry in path.split(os.pathsep)
        if not Path(entry).name.startswith(_SHIM_DIR_PREFIX)
    )


def _qualifies(path: Path) -> bool:
    if not (path.is_file() and os.access(path, os.X_OK) and has_arm64_slice(path)):
        return False
    version = git_version(str(path))
    return version is not None and version >= MIN_GIT_VERSION


def resolve_git(
    env: dict[str, str] | None = None,
    *,
    candidates: tuple[Path, ...] = NATIVE_GIT_CANDIDATES,
    on_apple_silicon: bool | None = None,
) -> GitChoice:
    """The rule in the module docstring. `candidates`/`on_apple_silicon` exist for its tests."""
    env = dict(os.environ if env is None else env)
    explicit = (env.get(GIT_BINARY_ENV) or "").strip()
    if explicit:
        return GitChoice(binary=explicit, native=True, reason=f"set by {GIT_BINARY_ENV}")
    if not (apple_silicon() if on_apple_silicon is None else on_apple_silicon):
        return GitChoice(binary="git", native=True, reason="not Apple silicon; PATH's git")
    on_path = shutil.which("git", path=_without_shims(env.get("PATH")))
    path_git = Path(on_path) if on_path else None
    if path_git is not None and path_git != XCRUN_GIT_SHIM and _qualifies(path_git):
        return GitChoice(binary="git", native=True, reason=f"PATH's git ({path_git}) is native")
    for candidate in candidates:
        if _qualifies(candidate):
            return GitChoice(
                binary=str(candidate),
                native=True,
                reason=f"PATH's git ({path_git}) is not native; {candidate} is",
            )
    return GitChoice(
        binary="git",
        native=path_git == XCRUN_GIT_SHIM,
        reason=f"no native git >= {MIN_GIT_VERSION[0]}.{MIN_GIT_VERSION[1]} found; "
        f"PATH's git ({path_git}) runs translated",
    )


@functools.cache
def git_choice() -> GitChoice:
    """`resolve_git()` once per process, warning when it fell back to a translated git."""
    choice = resolve_git()
    if not choice.native:
        logger.warning("%s. %s", choice.reason, NATIVE_GIT_REMEDY)
    return choice


def git_binary() -> str:
    """The git `run_git` spawns."""
    return git_choice().binary
