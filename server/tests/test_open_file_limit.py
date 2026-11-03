"""The server lifts its soft open-file limit, so the suites it spawns can run.

Started from a GUI terminal it inherited launchd's 256, and land verification's
`pytest -n auto` died in setup on "Too many open files" in tests that pass
from any shell.
"""

import resource
import subprocess
import sys
from unittest import mock

from loregarden.core import open_file_limit
from loregarden.core.open_file_limit import TARGET_OPEN_FILES, raise_open_file_limit

_PROBE = (
    "import resource, subprocess, sys;"
    "from loregarden.core.open_file_limit import raise_open_file_limit;"
    "raise_open_file_limit();"
    # What a spawned suite sees: a child's limit, not this process's.
    "print(subprocess.run([sys.executable, '-c',"
    " 'import resource; print(resource.getrlimit(resource.RLIMIT_NOFILE)[0])'],"
    " capture_output=True, text=True).stdout.strip())"
)


def test_a_child_of_a_server_started_at_256_inherits_the_raised_limit():
    hard = resource.getrlimit(resource.RLIMIT_NOFILE)[1]
    expected = TARGET_OPEN_FILES if hard == resource.RLIM_INFINITY else min(TARGET_OPEN_FILES, hard)
    done = subprocess.run(
        ["sh", "-c", f'ulimit -Sn 256 && exec "{sys.executable}" -c "{_PROBE}"'],
        capture_output=True,
        text=True,
        check=False,
    )

    assert done.returncode == 0, done.stderr
    assert int(done.stdout.strip()) == expected


def test_a_limit_already_high_enough_is_left_alone():
    with (
        mock.patch.object(
            open_file_limit.resource, "getrlimit", return_value=(10**6, resource.RLIM_INFINITY)
        ),
        mock.patch.object(open_file_limit.resource, "setrlimit") as setrlimit,
    ):
        assert raise_open_file_limit() == 10**6
    setrlimit.assert_not_called()


def test_it_never_asks_past_the_hard_limit():
    with (
        mock.patch.object(open_file_limit.resource, "getrlimit", return_value=(256, 4096)),
        mock.patch.object(open_file_limit.resource, "setrlimit") as setrlimit,
    ):
        assert raise_open_file_limit() == 4096
    setrlimit.assert_called_once_with(resource.RLIMIT_NOFILE, (4096, 4096))


def test_a_refused_raise_is_logged_and_reports_the_old_limit():
    with (
        mock.patch.object(open_file_limit.resource, "getrlimit", return_value=(256, 4096)),
        mock.patch.object(open_file_limit.resource, "setrlimit", side_effect=ValueError("no")),
        mock.patch.object(open_file_limit.logger, "warning") as warning,
    ):
        assert raise_open_file_limit() == 256
    warning.assert_called_once()


def test_the_server_raises_it_at_boot(isolated_db):
    """At boot, before any loop can spawn a suite."""
    from fastapi.testclient import TestClient
    from loregarden import main as main_module
    from loregarden.main import app

    with (
        mock.patch.object(main_module, "raise_open_file_limit") as raised,
        mock.patch.object(main_module, "_recover_previous_boot"),
        mock.patch.object(main_module, "start_reconcile_loop", return_value=None),
    ):
        with TestClient(app):
            pass
    raised.assert_called_once_with()
