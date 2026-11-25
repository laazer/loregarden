"""The doctor reports a refused or failed dev-server reload, from the server's checkout."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from unittest import mock

import pytest
from loregarden.dev_supervisor import STATUS_FILE, ReloadState, Status
from loregarden.models.domain import DoctorCheck, DoctorStatus
from loregarden.services import doctor, doctor_dev_server
from loregarden.services.doctor_dev_server import (
    STATE_DIR,
    check_dev_server_reload,
    check_native_python,
    is_translated,
)

#: A pid no process holds: above the kernel's default pid_max on macOS and Linux.
DEAD_PID = 4_194_304 + 7


def _write(root: Path, state: ReloadState, *, pid: int) -> None:
    state_dir = root / STATE_DIR
    state_dir.mkdir(parents=True)
    status = Status(
        state=state,
        trigger=1,
        detail="the new build does not import",
        log_file=str(state_dir / "server.log"),
        log_tail=["Traceback (most recent call last):", "RuntimeError: lore-eden disagrees"],
        supervisor_pid=pid,
    )
    (state_dir / STATUS_FILE).write_text(json.dumps(asdict(status)), encoding="utf-8")


def _check(root: Path):
    with mock.patch("loregarden.services.doctor_dev_server.settings", mock.Mock(repo_root=root)):
        return check_dev_server_reload(mock.Mock(), mock.Mock(), Path("/elsewhere"))


@pytest.mark.parametrize("state", [ReloadState.REFUSED, ReloadState.FAILED])
def test_a_refused_or_failed_reload_fails_with_its_traceback(tmp_path: Path, state: ReloadState):
    _write(tmp_path, state, pid=os.getpid())

    finding = _check(tmp_path)

    assert finding.check is DoctorCheck.DEV_SERVER_RELOAD
    assert finding.status is DoctorStatus.FAIL
    assert "RuntimeError: lore-eden disagrees" in finding.finding
    assert "server.log" in finding.remediation


@pytest.mark.parametrize("state", [ReloadState.OK, ReloadState.STARTING])
def test_a_serving_supervisor_passes(tmp_path: Path, state: ReloadState):
    _write(tmp_path, state, pid=os.getpid())

    assert _check(tmp_path).status is DoctorStatus.PASS


def test_a_status_left_by_a_supervisor_that_is_gone_is_not_applicable(tmp_path: Path):
    _write(tmp_path, ReloadState.FAILED, pid=DEAD_PID)

    assert _check(tmp_path).status is DoctorStatus.PASS


def test_no_status_is_not_applicable(tmp_path: Path):
    assert _check(tmp_path).status is DoctorStatus.PASS


def test_an_unreadable_status_warns_rather_than_passing(tmp_path: Path):
    (tmp_path / STATE_DIR).mkdir(parents=True)
    (tmp_path / STATE_DIR / STATUS_FILE).write_text("{not json", encoding="utf-8")

    assert _check(tmp_path).status is DoctorStatus.WARN


def test_the_check_is_registered_but_does_not_park_dispatch():
    assert doctor.CHECKS[DoctorCheck.DEV_SERVER_RELOAD] is check_dev_server_reload
    assert DoctorCheck.DEV_SERVER_RELOAD not in doctor.DISPATCH_PREFLIGHT_CHECKS


def _native_finding(tmp_path: Path, *, translated: bool):
    with mock.patch.object(doctor_dev_server, "is_translated", return_value=translated):
        return check_native_python(mock.Mock(), mock.Mock(), tmp_path)


def test_a_translated_python_is_reported_with_the_restart_that_fixes_it(tmp_path):
    finding = _native_finding(tmp_path, translated=True)
    assert finding.check is DoctorCheck.NATIVE_PYTHON
    assert finding.status is DoctorStatus.WARN
    assert "scripts/dev-server.sh" in finding.remediation


def test_a_native_python_passes(tmp_path):
    assert _native_finding(tmp_path, translated=False).status is DoctorStatus.PASS


def test_it_agrees_with_an_intel_python_on_apple_silicon():
    """Whatever Python this suite runs on: Intel here means translated only on an arm64 Mac."""
    asked = (
        subprocess.run(
            ["sysctl", "-n", "hw.optional.arm64"], capture_output=True, text=True, check=False
        )
        if sys.platform == "darwin"
        else None
    )
    apple_silicon = asked is not None and asked.stdout.strip() == "1"
    assert is_translated() is (apple_silicon and platform.machine() == "x86_64")


def test_the_doctor_runs_it_and_no_dispatch_waits_on_it():
    assert doctor.CHECKS[DoctorCheck.NATIVE_PYTHON] is check_native_python
    assert DoctorCheck.NATIVE_PYTHON not in doctor.DISPATCH_PREFLIGHT_CHECKS
