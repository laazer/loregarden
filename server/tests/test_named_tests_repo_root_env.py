"""The rerun subprocess must see the ticket's own checkout, not the server's.

`named_tests._run_batches` shells out through `run_verify_command`, which
merges `scrubbed_git_env()` (the full ambient environment, minus git's
bindings) under whatever `extra_env` it is given. The server process itself
often carries `LOREGARDEN_REPO_ROOT` (set by whoever launched it), and
`server/tests/repo_paths.find_repo_root` honours that override before it ever
looks at `cwd`. Left unset in `extra_env`, the ambient value wins and a rerun
inside a ticket worktree resolves fixtures and other repo-root-relative paths
against the wrong checkout (lg-hive-workplace-900).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from loregarden.models.domain import GateOutcome
from loregarden.services.land_verification import VerifyOutcome
from loregarden.services.named_tests import NamedTestRun, _run_batches
from loregarden.services.orchestration_profile import CriterionTestCommand


def test_the_rerun_env_pins_repo_root_to_the_ticket_checkout(tmp_path: Path) -> None:
    root = tmp_path / "ticket-checkout"
    root.mkdir()
    command = CriterionTestCommand(prefix="server/", command="pytest {tests}", strip_prefix=False)
    batches = [("crit", command, ["server/tests/test_x.py::test_y"])]

    with patch("loregarden.services.named_tests.run_verify_command") as run_verify:
        run_verify.return_value = VerifyOutcome(ok=True, outcome=GateOutcome.PASSED)
        _run_batches(batches, root, NamedTestRun())

    assert run_verify.call_count == 1
    _, kwargs = run_verify.call_args
    assert kwargs["extra_env"]["LOREGARDEN_REPO_ROOT"] == str(root)
