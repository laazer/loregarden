"""The lint gate that refuses exception handlers which catch everything and say nothing.

The gate has to stay narrow in both directions. Too loose and a `pass` slips
through, so a caller reports success it never had. Too strict and it flags the
handlers that already surface the failure — recording it on a result, handing it
back to the caller — and the next agent learns to waive it by reflex. These
tests pin both halves.
"""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

_CHECKER_PATH = (
    Path(__file__).resolve().parents[2] / ".lefthook" / "scripts" / "py_silent_except_check.py"
)


def _load_checker():
    spec = importlib.util.spec_from_file_location("py_silent_except_check", _CHECKER_PATH)
    module = importlib.util.module_from_spec(spec)
    # Registered before exec: @dataclass resolves annotations through
    # sys.modules[cls.__module__], which is None for an unregistered module.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


def _write(tmp_path: Path, source: str, name: str = "service.py") -> Path:
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return path


FLAGGED = {
    "pass": """
def f():
    try:
        work()
    except Exception:
        pass
""",
    "bare_except": """
def f():
    try:
        work()
    except:
        return None
""",
    "base_exception": """
def f():
    try:
        work()
    except BaseException:
        pass
""",
    "return_false": """
def f():
    try:
        return work()
    except Exception:
        return False
""",
    "assign_none_discards_exc": """
def f():
    try:
        value = work()
    except Exception:
        value = None
    return value
""",
    "return_empty_container": """
def f():
    try:
        return work()
    except Exception:
        return []
""",
    "continue_in_loop": """
def f(items):
    for item in items:
        try:
            work(item)
        except Exception:
            continue
""",
    "tuple_containing_exception": """
def f():
    try:
        work()
    except (ValueError, Exception):
        return None
""",
    "suppress_exception": """
from contextlib import suppress

def f():
    with suppress(Exception):
        work()
""",
    "ellipsis_body": """
def f():
    try:
        work()
    except Exception:
        ...
""",
}

ALLOWED = {
    "logs_then_recovers": """
import logging

logger = logging.getLogger(__name__)

def f():
    try:
        return work()
    except Exception as exc:
        logger.warning("work failed: %s", exc)
        return None
""",
    "reraises": """
def f():
    try:
        return work()
    except Exception:
        raise
""",
    "records_on_result": """
def f(failures):
    try:
        return work()
    except Exception as exc:
        failures.append(str(exc))
        return None
""",
    "returns_value_built_from_exc": """
def f():
    try:
        return work()
    except Exception as exc:
        return f"unavailable: {exc}"
""",
    "narrow_exception_is_expected": """
def f():
    try:
        return work()
    except FileNotFoundError:
        return None
""",
    "narrow_suppress": """
from contextlib import suppress

def f():
    with suppress(FileNotFoundError):
        work()
""",
    "waived": """
def f():
    try:
        cleanup()
    except Exception:  # py-silent: allow - cleanup on an already-failing path
        pass
""",
}


@pytest.mark.parametrize("name", sorted(FLAGGED))
def test_flags_silent_broad_catches(tmp_path, name):
    path = _write(tmp_path, FLAGGED[name])
    assert checker.violations_in(path, repo=None), f"{name} should be flagged"


@pytest.mark.parametrize("name", sorted(ALLOWED))
def test_leaves_visible_failures_alone(tmp_path, name):
    path = _write(tmp_path, ALLOWED[name])
    assert checker.violations_in(path, repo=None) == [], f"{name} should not be flagged"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", ".")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    return tmp_path


def _git(repo: Path, *args: str) -> None:
    # Scrub GIT_DIR/GIT_WORK_TREE: they beat cwd, and a test run from a worktree
    # hook inherits them pointing at the real repo.
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=_SCRUBBED_ENV)


_SCRUBBED_ENV = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}


def _run_gate(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_CHECKER_PATH), *args],
        cwd=repo,
        capture_output=True,
        text=True,
        env=_SCRUBBED_ENV,
    )


def _commit_base(repo: Path) -> Path:
    src = repo / "src" / "pkg"
    src.mkdir(parents=True)
    (src / "mod.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    return src


# --------------------------------------------------------------------------- #
# invocation modes
# --------------------------------------------------------------------------- #


def test_bare_file_list_is_precommit_mode():
    inv = checker.parse_argv(["src/pkg/mod.py"])
    assert inv.diff_scope == "staged"
    assert inv.label == "pre-commit"
    assert [p.name for p in inv.files] == ["mod.py"]


def test_repo_and_scope_flags_are_gate_mode():
    inv = checker.parse_argv(["--repo", "/tmp/ws", "--scope", "worktree"])
    assert inv.diff_scope == "worktree"
    assert inv.label == "gate"
    assert inv.repo == Path("/tmp/ws").resolve()
    assert inv.files == []


def test_unknown_scope_is_carried_through_to_be_refused_not_coerced():
    """See the organization gate's twin: a coerced scope read the empty index."""
    assert checker.parse_argv(["--scope", "nonsense"]).diff_scope == "nonsense"


# --------------------------------------------------------------------------- #
# pre-commit mode: staged files, scoped to the index
# --------------------------------------------------------------------------- #


def test_precommit_flags_a_staged_handler(repo: Path):
    src = _commit_base(repo)
    (src / "mod.py").write_text(FLAGGED["pass"], encoding="utf-8")
    _git(repo, "add", "-A")

    result = _run_gate(repo, "src/pkg/mod.py")
    assert result.returncode == 1
    assert "src/pkg/mod.py:5" in result.stdout, "output should point at the handler"
    assert "py-silent: allow" in result.stdout, "output should name the escape hatch"


def test_precommit_leaves_untouched_debt_alone(repo: Path):
    """Editing a file that already swallows an exception must not block the commit.

    The rule is "don't add another", not "fix the tree on sight" — a gate that
    blocks on debt the author never wrote teaches waiving by reflex.
    """
    src = repo / "src" / "pkg"
    src.mkdir(parents=True)
    (src / "mod.py").write_text(FLAGGED["pass"], encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "inherited debt")

    (src / "mod.py").write_text(FLAGGED["pass"] + "\n\ndef g():\n    return 1\n", encoding="utf-8")
    _git(repo, "add", "-A")

    result = _run_gate(repo, "src/pkg/mod.py")
    assert result.returncode == 0, result.stdout


def test_precommit_passes_on_a_visible_failure(repo: Path):
    src = _commit_base(repo)
    (src / "mod.py").write_text(ALLOWED["logs_then_recovers"], encoding="utf-8")
    _git(repo, "add", "-A")

    assert _run_gate(repo, "src/pkg/mod.py").returncode == 0


# --------------------------------------------------------------------------- #
# gate mode: what an agent actually leaves behind
# --------------------------------------------------------------------------- #


def test_worktree_scope_reads_uncommitted_edits(repo: Path):
    src = _commit_base(repo)
    (src / "mod.py").write_text(FLAGGED["pass"], encoding="utf-8")

    result = _run_gate(repo, "--repo", str(repo), "--scope", "worktree")
    assert result.returncode == 1
    assert "mod.py" in result.stdout
    assert result.stdout.startswith("gate:")


def test_worktree_scope_reads_untracked_files(repo: Path):
    """A file an agent just created is not in `git diff` at all, and is the
    least-reviewed code in the run."""
    src = _commit_base(repo)
    (src / "brand_new.py").write_text(FLAGGED["return_false"], encoding="utf-8")

    result = _run_gate(repo, "--repo", str(repo), "--scope", "worktree")
    assert result.returncode == 1
    assert "brand_new.py" in result.stdout


def test_worktree_scope_ignores_files_outside_the_source_root(repo: Path):
    """Mirrors the lefthook glob: build tooling is not application code."""
    _commit_base(repo)
    tooling = repo / "tools"
    tooling.mkdir()
    (tooling / "build_helper.py").write_text(FLAGGED["pass"], encoding="utf-8")

    assert _run_gate(repo, "--repo", str(repo), "--scope", "worktree").returncode == 0


def test_worktree_scope_ignores_test_files(repo: Path):
    src = _commit_base(repo)
    (src / "test_thing.py").write_text(FLAGGED["pass"], encoding="utf-8")

    assert _run_gate(repo, "--repo", str(repo), "--scope", "worktree").returncode == 0


def test_clean_worktree_passes(repo: Path):
    src = _commit_base(repo)
    (src / "mod.py").write_text(ALLOWED["reraises"], encoding="utf-8")

    assert _run_gate(repo, "--repo", str(repo), "--scope", "worktree").returncode == 0


def test_repo_with_no_python_changes_passes(repo: Path):
    """Every workspace runs this gate, including ones with no Python at all."""
    (repo / "README.md").write_text("hi\n", encoding="utf-8")

    assert _run_gate(repo, "--repo", str(repo), "--scope", "worktree").returncode == 0


def test_tests_are_exempt(tmp_path):
    """Tests build deliberately broken states and assert on failure paths."""
    path = _write(tmp_path, FLAGGED["pass"], name="test_something.py")
    assert checker.main([str(path)]) == 0


def test_a_file_this_gate_cannot_parse_is_not_reported_clean(tmp_path):
    """A "ruff will speak up" assumption was about a *different* run.

    Returning no violations for an unparseable file made "I could not examine
    it" and "I examined it and it is clean" the same answer, and the gate exited
    0 either way. It is unexaminable, and this run has to say so.
    """
    path = _write(tmp_path, "def f(:\n")
    with pytest.raises(checker.UnexaminableFileError):
        checker.violations_in(path, repo=None)
    assert checker.main([str(path)]) == 1


# --------------------------------------------------------------------------- #
# wiring: a gate nobody invokes is a gate that stopped running
# --------------------------------------------------------------------------- #

_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_lefthook_runs_the_gate_pre_commit():
    lefthook = (_REPO_ROOT / "lefthook.yml").read_text(encoding="utf-8")
    assert "py_silent_except_check.py {staged_files}" in lefthook


def test_every_orchestration_profile_runs_the_gate():
    """It rides with the organization gates, `default.yaml` included — a
    workspace with no profile of its own still gets the rule."""
    profiles = sorted((_REPO_ROOT / "agent_context" / "orchestration").glob("*.yaml"))
    assert profiles, "no orchestration profiles found"
    for profile in profiles:
        text = profile.read_text(encoding="utf-8")
        if "py_organization_check.py" not in text:
            continue
        assert "py_silent_except_check.py --repo" in text, profile.name
        assert "--scope worktree" in text, profile.name


def test_workspace_hook_installer_ships_the_gate():
    """Other workspaces get it pre-commit too, from this checkout."""
    installer_path = _REPO_ROOT / "scripts" / "install_workspace_hooks.py"
    spec = importlib.util.spec_from_file_location("install_workspace_hooks", installer_path)
    installer = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = installer
    spec.loader.exec_module(installer)

    block = "\n".join(installer.render_block(_REPO_ROOT, ""))
    assert "py_silent_except_check.py {staged_files}" in block
    assert "loregarden-py-silent-except" in installer.MANAGED_COMMAND_NAMES


def test_real_services_are_clean():
    """The invariant holds across the actual server tree, not just fixtures."""
    services = Path(__file__).resolve().parents[1] / "loregarden"
    offenders = [
        f"{py}:{lineno}"
        for py in services.rglob("*.py")
        for lineno, _ in checker.violations_in(py, repo=None)
    ]
    assert offenders == []
