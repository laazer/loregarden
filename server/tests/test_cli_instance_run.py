"""`loregarden instance run` registers a startup task's process for as long as it runs.

The registry is the suite's isolated one (`isolated_instance_registry` in conftest).
"""

import json
import os
import sys
from unittest import mock

import pytest
from loregarden.cli import errors
from loregarden.cli.main import main
from loregarden.services import local_instances
from loregarden.services.local_instances import get_registry

# Run by the wrapped command: prints what the registry says about it, from the
# command's side, while it is running.
_REPORT_RECORD = """
import json, os, pathlib, sys
path = pathlib.Path(os.environ["LORE_EDEN_INSTANCES_DIR"]) / "records" / "loregarden-client.json"
record = json.loads(path.read_text()) if path.exists() else None
print(json.dumps({"record": record, "parent": os.getppid()}))
sys.exit(3)
"""


def _run(*extra: str, command: list[str]) -> int:
    with pytest.raises(SystemExit) as exited:
        main(
            [
                "instance",
                "run",
                "--name",
                "client",
                "--kind",
                "client",
                "--port",
                "5173",
                *extra,
                "--",
                *command,
            ]
        )
    return exited.value.code


def test_the_record_exists_while_the_command_runs_and_is_removed_after(capfd):
    code = _run(
        "--health-path",
        "/",
        "--label",
        "worktree=/checkout",
        command=[sys.executable, "-c", _REPORT_RECORD],
    )

    assert code == 3  # the command's own status, passed through
    seen = json.loads(capfd.readouterr().out)
    record = seen["record"]
    assert record["id"] == "loregarden-client"
    assert record["kind"] == "client"
    assert record["role"] == "main"
    assert record["url"] == "http://127.0.0.1:5173"
    assert record["health_path"] == "/"
    assert record["labels"] == {"worktree": "/checkout"}
    # The wrapper's pid: alive exactly as long as the command it runs.
    assert record["pid"] == seen["parent"] == os.getpid()
    assert get_registry().get("loregarden-client") is None


def test_a_registry_it_cannot_write_is_reported_and_the_command_still_runs(capfd):
    with mock.patch.object(
        local_instances, "get_registry", side_effect=PermissionError("read-only home")
    ):
        code = _run(command=[sys.executable, "-c", "raise SystemExit(5)"])

    assert code == 5
    assert "loregarden/client" in capfd.readouterr().err


def test_a_command_is_required(capsys):
    assert (
        main(["instance", "run", "--name", "client", "--kind", "client", "--port", "5173"])
        == errors.EXIT_USAGE
    )


def test_a_label_must_be_key_value(capsys):
    argv = [
        "instance",
        "run",
        "--name",
        "c",
        "--kind",
        "client",
        "--port",
        "1",
        "--label",
        "oops",
        "--",
        "true",
    ]
    assert main(argv) == errors.EXIT_USAGE
    assert "key=value" in capsys.readouterr().err
