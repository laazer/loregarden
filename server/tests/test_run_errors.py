import subprocess

from loregarden.services.run_errors import agent_timeout_message, normalize_timeout_stderr


def test_agent_timeout_message():
    assert agent_timeout_message(600) == "Agent timed out after 600s"


def test_normalize_legacy_timeout_stderr():
    legacy = "Agent timed out after 600s: Command '['claude']' timed out after 1 seconds"
    assert normalize_timeout_stderr(legacy) == "Agent timed out after 600s"


def test_normalize_preserves_other_errors():
    msg = "Permission denied via Loregarden inbox"
    assert normalize_timeout_stderr(msg, timeout_seconds=600) == msg


def test_cli_timeout_handler_does_not_append_subprocess_detail():
    timeout = 600
    exc = subprocess.TimeoutExpired(["claude"], 1)
    msg = agent_timeout_message(timeout)
    assert str(exc) not in msg
    assert msg == "Agent timed out after 600s"
