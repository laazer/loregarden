"""`loregarden local_main` finds the main server through the instance registry.

The registry is the suite's isolated one (`isolated_instance_registry` in conftest), so
these register into a temp directory, never the developer's `~/.lore-eden/instances`.
"""

import json
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from lore_eden.instances import InstanceKind, InstanceRole, register_self
from loregarden.cli import errors
from loregarden.cli.main import main
from loregarden.services.local_instances import PROJECT, get_registry


class _Health(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's dispatch name
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - base signature
        """Keep the request log out of the test output."""


def _register(port: int, role: InstanceRole = InstanceRole.MAIN):
    return register_self(
        get_registry(),
        project=PROJECT,
        name="main" if role == InstanceRole.MAIN else "feat-x",
        kind=InstanceKind.SERVER,
        host="127.0.0.1",
        port=port,
        role=role,
        labels={"worktree": "/checkout"},
    )


def _closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_reports_the_registered_main_server_and_its_health(capsys):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Health)
    Thread(target=server.serve_forever, daemon=True).start()
    handle = _register(server.server_address[1])
    try:
        assert main(["local_main"]) == errors.EXIT_OK
    finally:
        handle.release()
        server.shutdown()
        server.server_close()

    payload = json.loads(capsys.readouterr().out)
    assert payload["instance"]["url"] == f"http://127.0.0.1:{server.server_address[1]}"
    assert payload["instance"]["role"] == "main"
    assert payload["instance"]["labels"]["worktree"] == "/checkout"
    assert payload["health"]["ok"] is True


def test_a_registered_main_that_does_not_answer_is_reported_unhealthy(capsys):
    handle = _register(_closed_port())
    try:
        assert main(["local_main"]) == errors.EXIT_OK
    finally:
        handle.release()

    payload = json.loads(capsys.readouterr().out)
    assert payload["health"]["ok"] is False
    assert payload["health"]["error"]


def test_no_main_server_is_a_failure_not_an_empty_answer(capsys):
    """A branch server alone is not main; the caller must not get a URL for it."""
    handle = _register(_closed_port(), role=InstanceRole.BRANCH)
    try:
        assert main(["local_main"]) == errors.EXIT_ERROR
    finally:
        handle.release()

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "LocalMainNotRunningError" in captured.err
