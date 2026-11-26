"""A push whose suite cannot start is refused before it queues for capacity.

A push from a worktree without client/node_modules waited 1h26m in the capacity
line, ran 13 minutes of pytest, failed on the tests that need it, then waited
39m39s more for client-tests to run 0s and say so (2026-11-23). The check is
`.lefthook/scripts/push-prerequisites.sh`; that the hook runs it before anything
that reserves, and stops there, is `test_lefthook_hook_order.py`.

These run the real scripts in a scratch repository, with the selector and node
stubbed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from tests.repo_paths import REPO_ROOT
from tests.worktree_helpers import git, make_repo

SCRIPTS = REPO_ROOT / ".lefthook" / "scripts"
COPIED = (
    "push-prerequisites.sh",
    "test-prerequisites.sh",
    "push-base.sh",
    "hook-noninteractive.sh",
    "native-git.sh",
    "ensure-node.sh",
    "server_python.sh",
)
INSTALL = "cd client && npm ci"
NEEDS_CLIENT = "from tests.toolchain import require_client_modules\n"
PLAIN = "def test_it(): pass\n"


class Checkout:
    def __init__(self, root: Path, bin_dir: Path) -> None:
        self.root = root
        self.bin_dir = bin_dir

    def script(self, path: Path, body: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)

    def selects(self, *tests: tuple[str, str], exit_code: int = 0) -> None:
        """Make the selector print these tests (name, source) and exit."""
        listed = []
        for name, source in tests:
            path = self.root / "server" / "tests" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source, encoding="utf-8")
            listed.append(f"server/tests/{name}")
        body = f"import sys\nprint({chr(10).join(listed)!r})\nsys.exit({exit_code})\n"
        selector = self.root / ".lefthook" / "scripts" / "select_pytest_targets.py"
        selector.write_text(body, encoding="utf-8")

    def install_client(self) -> None:
        self.script(self.root / "client" / "node_modules" / ".bin" / "jest", "#!/bin/sh\n")

    def check(self, suite: str, **env: str) -> subprocess.CompletedProcess[str]:
        inherited = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("GIT_", "LOREGARDEN_")) and k != "HOME"
        }
        path = f"{self.bin_dir}{os.pathsep}{inherited['PATH']}"
        return subprocess.run(
            ["bash", str(self.root / ".lefthook/scripts/push-prerequisites.sh"), suite],
            # HOME has no nvm, so ensure-node sees only the stub.
            env=inherited | {"PATH": path, "HOME": str(self.bin_dir)} | env,
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
        )


@pytest.fixture(name="checkout")
def checkout_fixture(tmp_path: Path) -> Checkout:
    """A repository with a base to diff against, a server venv, and no client install."""
    root = make_repo(tmp_path)
    git(root, "update-ref", "refs/remotes/origin/main", "main")
    scripts = root / ".lefthook" / "scripts"
    scripts.mkdir(parents=True)
    for name in COPIED:
        shutil.copy(SCRIPTS / name, scripts / name)
    checkout = Checkout(root, tmp_path / "bin")
    checkout.script(checkout.bin_dir / "node", "#!/bin/sh\necho v20.20.2\n")
    checkout.script(root / "server/.venv/bin/python", f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    (root / "client").mkdir()
    checkout.selects(("test_plain.py", PLAIN))
    return checkout


def test_a_client_push_without_node_modules_is_refused_with_the_command(checkout):
    result = checkout.check("client-tests")

    assert result.returncode == 1
    assert INSTALL in result.stderr


def test_a_half_finished_install_is_refused(checkout):
    (checkout.root / "client" / "node_modules" / "left-pad").mkdir(parents=True)

    assert checkout.check("client-tests").returncode == 1


def test_an_installed_client_passes(checkout):
    checkout.install_client()

    assert checkout.check("client-tests").returncode == 0


def test_a_linked_node_modules_passes(checkout, tmp_path):
    """A ticket worktree links the parent checkout's own."""
    checkout.script(tmp_path / "parent" / "node_modules" / ".bin" / "jest", "#!/bin/sh\n")
    (checkout.root / "client/node_modules").symlink_to(tmp_path / "parent/node_modules")

    assert checkout.check("client-tests").returncode == 0


def test_a_node_too_old_for_the_client_tools_is_refused(checkout):
    checkout.install_client()
    checkout.script(checkout.bin_dir / "node", "#!/bin/sh\necho v14.0.0\n")

    result = checkout.check("client-tests")

    assert result.returncode == 1
    assert "node >=20" in result.stderr


def test_server_tests_that_never_touch_the_client_do_not_ask_for_it(checkout):
    assert checkout.check("server-tests").returncode == 0


@pytest.mark.parametrize("source", [NEEDS_CLIENT, "from tests.toolchain import NPM_CI\n"])
def test_a_selected_test_that_needs_the_client_toolchain_is_refused(checkout, source):
    checkout.selects(("test_plain.py", PLAIN), ("test_gate.py", source))

    result = checkout.check("server-tests")

    assert result.returncode == 1
    assert INSTALL in result.stderr


def test_the_same_selection_passes_once_installed(checkout):
    checkout.selects(("test_gate.py", NEEDS_CLIENT))
    checkout.install_client()

    assert checkout.check("server-tests").returncode == 0


def test_a_run_that_will_be_the_full_suite_asks_for_the_client(checkout):
    """Selector refuses to narrow, narrows to nothing, names a test that is not
    there, is told to run everything, or has nothing to diff against."""
    assert checkout.check("server-tests", LOREGARDEN_FULL_TESTS="1").returncode == 1

    checkout.selects(("test_plain.py", PLAIN), exit_code=2)
    assert checkout.check("server-tests").returncode == 1

    checkout.selects()
    assert checkout.check("server-tests").returncode == 1

    checkout.selects(("test_plain.py", PLAIN))
    (checkout.root / "server/tests/test_plain.py").unlink()
    assert checkout.check("server-tests").returncode == 1


def test_with_no_base_the_server_suite_is_the_full_one(checkout):
    git(checkout.root, "update-ref", "-d", "refs/remotes/origin/main")

    assert checkout.check("server-tests").returncode == 1


def test_a_server_push_with_no_python_to_run_is_refused(checkout):
    checkout.install_client()  # so nothing but the missing python can refuse it
    shutil.rmtree(checkout.root / "server" / ".venv")
    # Everything the script needs but uv.
    tools = checkout.bin_dir.parent / "tools"
    tools.mkdir()
    for name in ("bash", "git", "dirname", "sed", "grep"):
        (tools / name).symlink_to(str(shutil.which(name)))

    result = checkout.check("server-tests", PATH=str(tools))

    assert result.returncode == 1
    assert "uv sync --extra dev" in result.stderr


def test_an_install_already_running_is_named_rather_than_started_twice(checkout):
    lock = checkout.root / ".git" / "worktree-bootstrap.lock"

    assert "worktree-bootstrap.log" not in checkout.check("client-tests").stderr
    lock.mkdir()
    assert "worktree-bootstrap.log" in checkout.check("client-tests").stderr


def _push_base(checkout: Checkout, **env: str) -> str:
    inherited = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "LOREGARDEN_"))}
    script = checkout.root / ".lefthook/scripts/push-base.sh"
    return subprocess.run(
        ["bash", "-c", f'source "{script}"; push_base "{checkout.root}"'],
        env=inherited | env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def test_the_base_is_the_override_then_the_pushed_branch_then_main(checkout, tmp_path):
    main = git(checkout.root, "rev-parse", "main").stdout.strip()
    assert _push_base(checkout) == main

    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(remote))
    git(checkout.root, "remote", "add", "origin", str(remote))
    git(checkout.root, "checkout", "-q", "-b", "work")
    git(checkout.root, "commit", "-q", "--allow-empty", "-m", "pushed")
    git(checkout.root, "push", "-q", "-u", "origin", "work")
    pushed = git(checkout.root, "rev-parse", "HEAD").stdout.strip()
    git(checkout.root, "commit", "-q", "--allow-empty", "-m", "not pushed")

    assert _push_base(checkout) == pushed != main
    assert _push_base(checkout, LOREGARDEN_TESTS_BASE="some-ref") == "some-ref"


def test_an_unknown_suite_is_an_error_not_a_pass(checkout):
    assert checkout.check("clinet-tests").returncode == 2


def test_the_suites_ask_the_same_question_when_run_without_the_hook():
    """`task ci` and land verification run them directly."""
    for name, asks in (
        ("server-tests.sh", "resolve_server_runner"),
        ("client-tests.sh", "require_client_modules"),
    ):
        lines = (SCRIPTS / name).read_text(encoding="utf-8").splitlines()
        assert asks in lines, f"{name} no longer calls {asks}"


def test_every_test_that_needs_the_client_is_recognised():
    """The check greps for the helpers; a test that names node_modules through
    tests/toolchain.py under another name would be missed."""
    toolchain = (REPO_ROOT / "server/tests/toolchain.py").read_text(encoding="utf-8")
    script = (SCRIPTS / "push-prerequisites.sh").read_text(encoding="utf-8")

    for helper in ("def require_client_modules", "NPM_CI ="):
        assert helper in toolchain
        assert helper.removeprefix("def ").removesuffix(" =") in script
