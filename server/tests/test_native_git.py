"""Which git runs: a native one on Apple silicon when PATH's is translated.

One rule, two implementations: `services/native_git.py` for the server's
`run_git`, `.lefthook/scripts/native-git.sh` for the hooks and every test run
they start. Stub gits stand in for real ones; which of them counts as native is
decided by the test (the probes are patched), so the rule runs on Linux CI too.
"""

from __future__ import annotations

import logging
import os
import struct
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest
from loregarden.services import git_subprocess, native_git
from loregarden.services.native_git import GitChoice, has_arm64_slice, resolve_git
from tests.repo_paths import REPO_ROOT

NATIVE_GIT_SH = REPO_ROOT / ".lefthook" / "scripts" / "native-git.sh"
HOOK_ENV = REPO_ROOT / ".lefthook" / "scripts" / "hook-noninteractive.sh"

#: Stub directories whose name contains this are "Intel": no arm64 slice.
INTEL = "x86stub"


def _stub_git(directory: Path, version: str, calls: Path | None = None) -> Path:
    """A `git` that answers `--version` and `--exec-path`, and logs each call."""
    directory.mkdir(parents=True, exist_ok=True)
    git = directory / "git"
    log = f'echo "$*" >> "{calls}"\n' if calls else ""
    git.write_text(
        "#!/bin/sh\n"
        f"{log}"
        'case "$1" in\n'
        f'  --version) echo "git version {version}" ;;\n'
        f'  --exec-path) echo "{directory}/libexec" ;;\n'
        "esac\n"
    )
    git.chmod(0o755)
    return git


def _native_by_name(path: Path) -> bool:
    return INTEL not in str(path)


class Machine:
    """A PATH git and a candidate list, judged by both implementations."""

    def __init__(self, tmp_path: Path, path_git: str | None, candidates: list[tuple[str, str]]):
        self.tmp_path = tmp_path
        # PATH's git: an Intel one when given a version, else whatever a test links in.
        self.bin = tmp_path / (f"pathbin-{INTEL}" if path_git else "pathbin")
        self.bin.mkdir()
        if path_git is not None:
            _stub_git(self.bin, path_git)
        self.candidates = tuple(
            _stub_git(tmp_path / name / "bin", version) if version else tmp_path / name / "git"
            for name, version in candidates
        )
        # The stubs run on /bin/sh; nothing else from PATH is needed but `file`.
        self.path = f"{self.bin}:/usr/bin:/bin"

    def python(self, *, apple_silicon: bool = True, env: dict[str, str] | None = None) -> GitChoice:
        with mock.patch.object(native_git, "has_arm64_slice", side_effect=_native_by_name):
            return resolve_git(
                {"PATH": self.path, **(env or {})},
                candidates=self.candidates,
                on_apple_silicon=apple_silicon,
            )

    def shell(self, *, apple_silicon: bool = True, env: dict[str, str] | None = None):
        script = f"""
source "{NATIVE_GIT_SH}"
_native_git_apple_silicon() {{ {"return 0" if apple_silicon else "return 1"}; }}
_native_git_has_arm64() {{ case "$1" in *{INTEL}*) return 1 ;; esac; return 0; }}
loregarden_resolve_native_git
"""
        return subprocess.run(
            ["bash", "-c", script],
            env={
                "PATH": self.path,
                "LOREGARDEN_NATIVE_GIT_CANDIDATES": ":".join(map(str, self.candidates)),
                **(env or {}),
            },
            capture_output=True,
            text=True,
            check=True,
        )


def _agree(machine: Machine, **kwargs) -> str:
    """Both implementations' choice, asserted equal: an absolute path, or "" for PATH's git."""
    py = machine.python(**kwargs)
    sh = machine.shell(**kwargs).stdout.strip()
    assert (py.binary if py.binary != "git" else "") == sh, (py, sh)
    return sh


def test_an_intel_path_git_gives_way_to_the_first_native_candidate_new_enough(tmp_path):
    machine = Machine(
        tmp_path,
        path_git="2.51.1",
        candidates=[("missing", ""), ("old", "2.37.9"), ("clt", "2.50.1"), ("xcode", "2.50.1")],
    )
    assert _agree(machine) == str(machine.candidates[2])


def test_git_3_counts_as_new_enough(tmp_path):
    machine = Machine(tmp_path, path_git="2.51.1", candidates=[("next", "3.0.0")])
    assert _agree(machine) == str(machine.candidates[0])


def test_a_native_path_git_is_kept(tmp_path):
    machine = Machine(tmp_path, path_git=None, candidates=[("clt", "2.50.1")])
    _stub_git(tmp_path / "nix" / "bin", "2.47.0")
    (machine.bin / "git").symlink_to(tmp_path / "nix" / "bin" / "git")
    assert _agree(machine) == ""


def test_an_inherited_shim_on_path_is_looked_past(tmp_path):
    """A parent hook's shim, without the GIT_EXEC_PATH it came with, is not PATH's git."""
    machine = Machine(tmp_path, path_git="2.51.1", candidates=[("clt", "2.50.1")])
    stale = tmp_path / "T" / "loregarden-native-git-999"
    stale.mkdir(parents=True)
    (stale / "git").symlink_to(machine.candidates[0])
    machine.path = f"{stale}:{machine.path}"
    assert _agree(machine) == str(machine.candidates[0])


def test_a_native_path_git_too_old_for_merge_tree_is_passed_over(tmp_path):
    machine = Machine(tmp_path, path_git=None, candidates=[("clt", "2.50.1")])
    _stub_git(tmp_path / "old-native" / "bin", "2.30.0")
    (machine.bin / "git").symlink_to(tmp_path / "old-native" / "bin" / "git")
    assert _agree(machine) == str(machine.candidates[0])


def test_nothing_changes_off_apple_silicon(tmp_path):
    machine = Machine(tmp_path, path_git="2.51.1", candidates=[("clt", "2.50.1")])
    assert _agree(machine, apple_silicon=False) == ""
    assert machine.python(apple_silicon=False).native


def test_an_explicit_git_wins_everywhere(tmp_path):
    machine = Machine(tmp_path, path_git="2.51.1", candidates=[("clt", "2.50.1")])
    env = {native_git.GIT_BINARY_ENV: "/somewhere/git"}
    assert _agree(machine, env=env) == "/somewhere/git"
    assert _agree(machine, env=env, apple_silicon=False) == "/somewhere/git"


def test_with_nothing_native_both_fall_back_and_say_so(tmp_path):
    machine = Machine(tmp_path, path_git="2.51.1", candidates=[("old", "2.20.0"), ("gone", "")])
    assert _agree(machine) == ""
    assert machine.python().native is False
    warned = machine.shell().stderr
    assert "xcode-select --install" in warned
    assert "LOREGARDEN_GIT_BIN" in warned


def test_the_xcrun_shim_on_path_is_passed_over(tmp_path):
    machine = Machine(tmp_path, path_git=None, candidates=[("clt", "2.50.1")])
    shim = _stub_git(tmp_path / "shim" / "bin", "2.50.1")
    (machine.bin / "git").symlink_to(shim)
    with mock.patch.object(native_git, "XCRUN_GIT_SHIM", machine.bin / "git"):
        assert machine.python().binary == str(machine.candidates[0])
    # The shell names /usr/bin/git, where the shim really is.
    assert "/usr/bin/git" in NATIVE_GIT_SH.read_text()


def test_git_choice_warns_once_when_it_falls_back(caplog):
    fallback = GitChoice(binary="git", native=False, reason="nothing native")
    native_git.git_choice.cache_clear()
    try:
        with (
            mock.patch.object(native_git, "resolve_git", return_value=fallback),
            caplog.at_level(logging.WARNING, logger=native_git.__name__),
        ):
            assert native_git.git_binary() == "git"
            assert native_git.git_binary() == "git"
        assert [r.levelno for r in caplog.records] == [logging.WARNING]
    finally:
        native_git.git_choice.cache_clear()


def test_run_git_spawns_the_chosen_git(tmp_path):
    calls = tmp_path / "calls"
    git = _stub_git(tmp_path / "native" / "bin", "2.50.1", calls)
    with mock.patch.object(git_subprocess, "git_binary", return_value=str(git)):
        git_subprocess.run_git(["--version"], capture_output=True, text=True, check=True)
    assert calls.read_text() == "--version\n"


def _mach_o(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


@pytest.mark.parametrize(
    ("header", "native"),
    [
        (struct.pack("<Ii", 0xFEEDFACF, 0x0100000C) + b"\0" * 24, True),
        (struct.pack("<Ii", 0xFEEDFACF, 0x01000007) + b"\0" * 24, False),
        (
            struct.pack(">II", 0xCAFEBABE, 2)
            + struct.pack(">iiIII", 0x01000007, 3, 0, 0, 0)
            + struct.pack(">iiIII", 0x0100000C, 0, 0, 0, 0),
            True,
        ),
        (struct.pack(">II", 0xCAFEBABE, 1) + struct.pack(">iiIII", 0x01000007, 3, 0, 0, 0), False),
        (b"#!/bin/sh\n", False),
        (b"\xca\xfe", False),
    ],
    ids=["thin-arm64", "thin-x86_64", "universal", "fat-x86_64-only", "script", "truncated"],
)
def test_the_mach_o_header_decides_native(tmp_path, header: bytes, native: bool):
    assert has_arm64_slice(_mach_o(tmp_path / "bin", header)) is native


class TestApplying:
    """`loregarden_use_native_git`: the shim on PATH, its helpers, and its cache."""

    def _run(self, tmp_path: Path, machine: Machine, body: str, *, darwin: bool = True):
        script = f"""
set -euo pipefail
source "{NATIVE_GIT_SH}"
_native_git_darwin() {{ {"return 0" if darwin else "return 1"}; }}
_native_git_apple_silicon() {{ _native_git_darwin; }}
_native_git_has_arm64() {{ case "$1" in *{INTEL}*) return 1 ;; esac; return 0; }}
{body}
"""
        return subprocess.run(
            ["bash", "-c", script],
            env={
                "PATH": machine.path,
                "TMPDIR": str(tmp_path / "tmp"),
                "LOREGARDEN_NATIVE_GIT_CANDIDATES": ":".join(map(str, machine.candidates)),
            },
            capture_output=True,
            text=True,
            check=True,
        )

    def test_the_native_git_goes_first_with_its_own_helpers(self, tmp_path):
        """Under the hooks' `set -e`, with a candidate missing, as /opt/homebrew is here."""
        (tmp_path / "tmp").mkdir()
        machine = Machine(tmp_path, path_git="2.51.1", candidates=[("brew", ""), ("clt", "2.50.1")])
        out = self._run(
            tmp_path,
            machine,
            'loregarden_use_native_git; readlink "$(command -v git)"; '
            'echo "$GIT_EXEC_PATH"; echo "$LOREGARDEN_GIT_BIN"',
        ).stdout.splitlines()
        clt = machine.candidates[1]
        assert out == [str(clt), f"{clt.parent}/libexec", str(clt)]

    def test_a_second_hook_reads_the_cache_and_asks_no_git(self, tmp_path):
        (tmp_path / "tmp").mkdir()
        calls = tmp_path / "calls"
        machine = Machine(tmp_path, path_git="2.51.1", candidates=[])
        clt = _stub_git(tmp_path / "clt" / "bin", "2.50.1", calls)
        machine.candidates = (clt,)
        self._run(tmp_path, machine, "loregarden_use_native_git")
        asked = calls.read_text()
        out = self._run(tmp_path, machine, 'loregarden_use_native_git; echo "$GIT_EXEC_PATH"')
        assert calls.read_text() == asked
        assert out.stdout.strip() == f"{clt.parent}/libexec"

    def test_sourcing_twice_leaves_one_shim_on_path(self, tmp_path):
        (tmp_path / "tmp").mkdir()
        machine = Machine(tmp_path, path_git="2.51.1", candidates=[("clt", "2.50.1")])
        out = self._run(
            tmp_path,
            machine,
            "loregarden_use_native_git; unset LOREGARDEN_GIT_BIN; loregarden_use_native_git; "
            'echo "$PATH"',
        )
        assert out.stdout.count("loregarden-native-git-") == 1

    def test_off_darwin_path_is_untouched(self, tmp_path):
        (tmp_path / "tmp").mkdir()
        machine = Machine(tmp_path, path_git="2.51.1", candidates=[("clt", "2.50.1")])
        out = self._run(tmp_path, machine, 'loregarden_use_native_git; echo "$PATH"', darwin=False)
        assert out.stdout.strip() == machine.path
        assert list((tmp_path / "tmp").iterdir()) == []

    def test_a_fallback_warns_once_per_process_tree_even_from_cache(self, tmp_path):
        (tmp_path / "tmp").mkdir()
        machine = Machine(tmp_path, path_git="2.51.1", candidates=[("old", "2.20.0")])
        first = self._run(tmp_path, machine, "loregarden_use_native_git; loregarden_use_native_git")
        assert first.stderr.count("xcode-select --install") == 1
        cached = self._run(tmp_path, machine, 'loregarden_use_native_git; echo "$PATH"')
        assert cached.stderr.count("xcode-select --install") == 1
        assert cached.stdout.strip() == machine.path


def _real_apple_silicon_with_native_git() -> bool:
    return native_git.apple_silicon() and any(
        native_git.has_arm64_slice(c) for c in native_git.NATIVE_GIT_CANDIDATES if c.exists()
    )


@pytest.mark.skipif(
    not _real_apple_silicon_with_native_git(), reason="needs Apple silicon with a native git"
)
def test_on_this_mac_the_hooks_and_the_server_pick_the_same_native_git(tmp_path):
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("LOREGARDEN_GIT", "LOREGARDEN_NATIVE", "GIT_EXEC"))
    }
    env["TMPDIR"] = str(tmp_path)
    hooks = subprocess.run(
        # Under the hooks' own `set -euo pipefail`: a step that exits non-zero once
        # killed every hook, with /opt/homebrew/bin/git missing.
        [
            "bash",
            "-c",
            f'set -euo pipefail; source "{HOOK_ENV}"; echo "${{LOREGARDEN_GIT_BIN:-git}}"',
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    server = resolve_git(env).binary
    assert hooks == server
    assert sys.platform == "darwin"


@pytest.mark.skipif(
    not _real_apple_silicon_with_native_git(), reason="needs Apple silicon with a native git"
)
def test_on_this_mac_the_shimmed_git_finds_its_own_helpers(tmp_path):
    """Apple's git run through a symlink derives git-core from the link, not the target.

    A symlink at ~/.local/bin/git once looked in ~/.local/libexec/git-core and
    `git submodule` failed. The shim is a symlink (a `sh` wrapper doubled the
    cost of every git), so the exported GIT_EXEC_PATH is what keeps helpers
    found: assert it names a real git-core, and that a helper runs.
    """
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("LOREGARDEN_GIT", "LOREGARDEN_NATIVE", "GIT_"))
    }
    env["TMPDIR"] = str(tmp_path / "tmp")
    (tmp_path / "tmp").mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    out = subprocess.run(
        [
            "bash",
            "-c",
            f'set -euo pipefail; source "{HOOK_ENV}"; cd "{repo}"; git init -q; '
            "command -v git; git --exec-path; git submodule status",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    shim_git, exec_path = Path(out[0]), Path(out[1])
    assert shim_git.is_symlink(), "the hooks should run git through the shim"
    # The target's own git-core, not one that only happens to exist: this Mac
    # has Xcode.app too, whose git-core is where the unexported fallback looks.
    assert exec_path == shim_git.resolve().parent.parent / "libexec" / "git-core"
    assert (exec_path / "git-submodule").is_file(), exec_path
