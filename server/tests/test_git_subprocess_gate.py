"""The lint gate that keeps git subprocess calls routed through run_git.

The gate is the only thing enforcing the GIT_DIR invariant once the services are
fixed, so a gate that silently stops matching would be worse than no gate at
all — the codebase would look protected while drifting. These tests pin both
halves: what it must catch, and what it must leave alone.
"""

import importlib.util
from pathlib import Path

import pytest

_CHECKER_PATH = (
    Path(__file__).resolve().parents[2] / ".lefthook" / "scripts" / "py_git_subprocess_check.py"
)


def _load_checker():
    spec = importlib.util.spec_from_file_location("py_git_subprocess_check", _CHECKER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


def _write(tmp_path: Path, source: str, name: str = "service.py") -> Path:
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return path


FLAGGED = {
    "multiline": """
import subprocess

def f(repo):
    return subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo,
        capture_output=True,
    )
""",
    "single_line": """
import subprocess

def f(repo):
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo)
""",
    "popen": """
import subprocess

def f(repo):
    return subprocess.Popen(["git", "fetch"], cwd=repo)
""",
    "check_output": """
import subprocess

def f(repo):
    return subprocess.check_output(["git", "log"], cwd=repo)
""",
    "gh_shells_out_to_git": """
import subprocess

def f(repo):
    return subprocess.Popen(["gh", "pr", "view"], cwd=repo)
""",
    "shell_string": """
import subprocess

def f(repo):
    return subprocess.check_output("git rev-parse HEAD", shell=True, cwd=repo)
""",
    "bare_import": """
from subprocess import run

def f(repo):
    return run(["git", "fetch"], cwd=repo)
""",
    "absolute_path": """
import subprocess

def f(repo):
    return subprocess.run(["/usr/bin/git", "status"], cwd=repo)
""",
}

ALLOWED = {
    "routed_through_helper": """
from loregarden.services.git_subprocess import run_git

def f(repo):
    return run_git(["status"], cwd=repo, capture_output=True)
""",
    "explicit_env_is_deliberate": """
import subprocess

from loregarden.services.git_subprocess import scrubbed_git_env

def f(repo):
    return subprocess.run(["gh", "pr", "view"], cwd=repo, env=scrubbed_git_env())
""",
    "unguarded_command": """
import subprocess

def f(repo):
    return subprocess.run(["npm", "ci"], cwd=repo)
""",
    "not_statically_knowable": """
import subprocess

def f(repo, cmd):
    return subprocess.run(cmd, cwd=repo)
""",
    "no_args": """
import subprocess

def f():
    return subprocess.run()
""",
}


@pytest.mark.parametrize("name", sorted(FLAGGED))
def test_flags_unscrubbed_git_calls(tmp_path, name):
    path = _write(tmp_path, FLAGGED[name])
    assert checker.violations_in(path), f"{name} should be flagged"


@pytest.mark.parametrize("name", sorted(ALLOWED))
def test_leaves_correct_calls_alone(tmp_path, name):
    path = _write(tmp_path, ALLOWED[name])
    assert checker.violations_in(path) == [], f"{name} should not be flagged"


def test_main_exits_nonzero_on_violation(tmp_path, capsys):
    path = _write(tmp_path, FLAGGED["multiline"])
    assert checker.main([str(path)]) == 1
    out = capsys.readouterr().out
    assert "run_git" in out, "failure output should name the fix"


def test_main_exits_zero_when_clean(tmp_path):
    path = _write(tmp_path, ALLOWED["routed_through_helper"])
    assert checker.main([str(path)]) == 0


def test_tests_are_exempt(tmp_path):
    """Tests drive git directly on purpose — including the unscrubbed guard test."""
    path = _write(tmp_path, FLAGGED["single_line"], name="test_something.py")
    assert checker.main([str(path)]) == 0


def test_the_helper_itself_is_exempt():
    """git_subprocess.py must build a raw git argv — it is the chokepoint."""
    helper = Path(__file__).resolve().parents[1] / "loregarden" / "services" / "git_subprocess.py"
    assert helper.exists()
    assert checker.main([str(helper)]) == 0
    # And it does contain the raw call the gate would otherwise flag.
    assert '["git", *args]' in helper.read_text(encoding="utf-8")


def test_real_services_are_clean():
    """The invariant holds across the actual server tree, not just fixtures."""
    services = Path(__file__).resolve().parents[1] / "loregarden"
    offenders = [str(py) for py in services.rglob("*.py") if checker.violations_in(py)]
    assert offenders == []
