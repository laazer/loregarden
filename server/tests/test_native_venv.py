"""Starting the dev server rebuilds a venv whose Python is translated, and only then."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
from tests.repo_paths import REPO_ROOT

_LIB = REPO_ROOT / "scripts" / "lib" / "native-venv.sh"


def _script(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


def _python(venv: Path, machine: str) -> None:
    """Answers the two questions the script asks of a venv's Python."""
    _script(
        venv / "bin" / "python", f'case "$2" in *platform*) echo {machine};; *) echo 3.11;; esac'
    )
    (venv / "marker").write_text(machine, encoding="utf-8")


@dataclass
class Box:
    """A server directory, and a machine described by stub `sysctl` and `uv`."""

    root: Path

    @property
    def server(self) -> Path:
        return self.root / "server"

    @property
    def venv(self) -> Path:
        return self.server / ".venv"

    def uv(self, *, sync: int = 0, machine: str = "arm64") -> None:
        template = self.root / "template"
        _python(template, machine)
        _script(
            self.root / "bin" / "uv",
            f'echo "$*" >> {self.root}/uv.log\n'
            f'if [ "$1" = venv ]; then cp -R {template} "$4"; else exit {sync}; fi',
        )

    def run(self, *, apple: bool = True) -> subprocess.CompletedProcess[str]:
        _script(self.root / "bin" / "sysctl", "echo 1" if apple else "exit 1")
        return subprocess.run(
            ["bash", "-euo", "pipefail", "-c", 'source "$0"; ensure_native_venv "$1"; echo started']
            + [str(_LIB), str(self.server)],
            capture_output=True,
            text=True,
            env={**os.environ, "PATH": f"{self.root}/bin:{os.environ['PATH']}"},
        )

    def calls(self) -> list[str]:
        log = self.root / "uv.log"
        return log.read_text(encoding="utf-8").splitlines() if log.exists() else []

    def machine(self) -> str:
        return (self.venv / "marker").read_text(encoding="utf-8")


@pytest.fixture
def box(tmp_path: Path) -> Box:
    made = Box(tmp_path)
    made.server.mkdir()
    made.uv()
    return made


def test_a_translated_venv_is_rebuilt_on_the_same_python_version(box: Box):
    _python(box.venv, "x86_64")
    done = box.run()
    assert done.returncode == 0, done.stderr
    assert box.machine() == "arm64"
    assert box.calls() == [
        f"venv --python cpython-3.11-macos-aarch64 {box.venv}",
        "sync",
    ]
    assert not (box.server / ".venv.translated").exists()


@pytest.mark.parametrize("failure", ["sync", "machine"])
def test_a_rebuild_that_fails_puts_the_old_venv_back_and_the_server_still_starts(box: Box, failure):
    _python(box.venv, "x86_64")
    box.uv(sync=1) if failure == "sync" else box.uv(machine="x86_64")
    (box.venv / "marker").write_text("original", encoding="utf-8")
    done = box.run()
    assert (done.returncode, done.stdout) == (0, "started\n")
    assert box.machine() == "original"
    assert "keeping the translated one" in done.stderr
    assert not (box.server / ".venv.translated").exists()


def test_a_native_venv_is_left_alone(box: Box):
    _python(box.venv, "arm64")
    assert box.run().returncode == 0
    assert box.calls() == []


def test_an_intel_mac_or_linux_is_left_alone(box: Box):
    _python(box.venv, "x86_64")
    assert box.run(apple=False).returncode == 0
    assert box.machine() == "x86_64"
    assert box.calls() == []


def test_no_venv_is_not_one_to_rebuild(box: Box):
    assert box.run().returncode == 0
    assert box.calls() == []


def test_the_dev_server_asks_before_it_syncs():
    script = (REPO_ROOT / "scripts" / "dev-server.sh").read_text(encoding="utf-8")
    call = script.index('ensure_native_venv "$ROOT/server"')
    assert call < script.index("\nuv sync\n") < script.index("loregarden.dev_supervisor \\")
