"""The shared git-subprocess chokepoint scrubs inherited repo bindings."""

import ast
import os
import subprocess
from pathlib import Path
from unittest import mock

import loregarden
import pytest
from loregarden.agents.cli_adapters import CliInvocation, invocation_env
from loregarden.models.domain import Ticket, WorkItemType
from loregarden.services.git_branch import ensure_ticket_branch
from loregarden.services.git_subprocess import (
    GH_AUTH_REQUIRED_EXIT,
    GH_BINARY_ENV,
    GH_USER_ENV,
    GIT_LOCATION_ENV_VARS,
    GhAccountUnavailable,
    gh_token_for_user,
    run_gh,
    run_git,
    scrubbed_git_env,
)


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=path, check=True, capture_output=True
    )
    (path / "README.md").write_text("# test\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)


def _two_repos(tmp_path: Path) -> tuple[Path, Path]:
    """A repo to operate on, and an unrelated one to leak a GIT_DIR from."""
    target = tmp_path / "target"
    intruder = tmp_path / "intruder"
    for repo in (target, intruder):
        repo.mkdir()
        _init_repo(repo)
    return target, intruder


def test_scrubbed_git_env_drops_repo_bindings():
    env = scrubbed_git_env({"GIT_DIR": "/elsewhere/.git", "PATH": "/usr/bin", "HOME": "/home/x"})
    assert "GIT_DIR" not in env
    # Everything unrelated survives — the child still needs a usable environment.
    assert env["PATH"] == "/usr/bin"
    assert env["HOME"] == "/home/x"


def test_scrubbed_git_env_drops_every_location_var():
    polluted = dict.fromkeys(GIT_LOCATION_ENV_VARS, "/elsewhere")
    assert scrubbed_git_env(polluted) == {}


def test_scrubbed_git_env_defaults_to_ambient_environment(monkeypatch):
    monkeypatch.setenv("GIT_DIR", "/elsewhere/.git")
    assert "GIT_DIR" not in scrubbed_git_env()
    # The caller's own environment is left intact.
    assert os.environ["GIT_DIR"] == "/elsewhere/.git"


def test_inherited_git_dir_would_hijack_a_plain_subprocess(tmp_path, monkeypatch):
    """Guard the premise: without scrubbing, GIT_DIR really does beat cwd.

    If this ever stops holding, the tests below pass for the wrong reason.
    """
    target, intruder = _two_repos(tmp_path)
    monkeypatch.setenv("GIT_DIR", str(intruder / ".git"))

    proc = subprocess.run(
        ["git", "rev-parse", "--absolute-git-dir"],
        cwd=target,
        capture_output=True,
        text=True,
    )
    assert Path(proc.stdout.strip()).resolve() == (intruder / ".git").resolve()


def test_run_git_resolves_through_cwd_despite_inherited_git_dir(tmp_path, monkeypatch):
    target, intruder = _two_repos(tmp_path)
    monkeypatch.setenv("GIT_DIR", str(intruder / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(intruder))

    proc = run_git(
        ["rev-parse", "--absolute-git-dir"],
        cwd=target,
        capture_output=True,
        text=True,
    )
    assert Path(proc.stdout.strip()).resolve() == (target / ".git").resolve()


def test_run_git_honours_dash_c_despite_inherited_git_dir(tmp_path, monkeypatch):
    """The `_git(cwd, *args)` service helpers select the repo with `-C`, not cwd."""
    target, intruder = _two_repos(tmp_path)
    monkeypatch.setenv("GIT_DIR", str(intruder / ".git"))

    proc = run_git(
        ["-C", str(target), "rev-parse", "--absolute-git-dir"],
        capture_output=True,
        text=True,
    )
    assert Path(proc.stdout.strip()).resolve() == (target / ".git").resolve()


def test_run_git_writes_to_the_repo_at_cwd(tmp_path, monkeypatch):
    """A mutating command lands in the target repo, not the leaked one."""
    target, intruder = _two_repos(tmp_path)
    monkeypatch.setenv("GIT_DIR", str(intruder / ".git"))

    (target / "new.txt").write_text("x\n", encoding="utf-8")
    run_git(["add", "."], cwd=target, check=True, capture_output=True)
    run_git(["commit", "-m", "scoped"], cwd=target, check=True, capture_output=True)

    def _subject(repo: Path) -> str:
        return subprocess.run(
            ["git", "log", "-1", "--format=%s"],
            cwd=repo,
            env={k: v for k, v in os.environ.items() if k not in GIT_LOCATION_ENV_VARS},
            capture_output=True,
            text=True,
        ).stdout.strip()

    assert _subject(target) == "scoped"
    assert _subject(intruder) == "init"


def test_service_layer_branch_checkout_survives_leaked_git_dir(tmp_path, monkeypatch):
    """End-to-end: a real service call routed through the helper stays scoped."""
    target, intruder = _two_repos(tmp_path)
    monkeypatch.setenv("GIT_DIR", str(intruder / ".git"))

    ticket = Ticket(
        external_id="99-scoped",
        work_item_type=WorkItemType.TASK,
        title="x",
        workspace_id="w",
    )
    branch = ensure_ticket_branch(target, ticket)
    assert branch == "loregarden/99-scoped"

    def _current_branch(repo: Path) -> str:
        return subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo,
            env={k: v for k, v in os.environ.items() if k not in GIT_LOCATION_ENV_VARS},
            capture_output=True,
            text=True,
        ).stdout.strip()

    assert _current_branch(target) == "loregarden/99-scoped"
    # The leaked repo was never touched.
    assert _current_branch(intruder) == "main"


# -- gh ---------------------------------------------------------------------


def _spawned_gh(monkeypatch, binary: str | None) -> list[str]:
    if binary is None:
        monkeypatch.delenv(GH_BINARY_ENV, raising=False)
    else:
        monkeypatch.setenv(GH_BINARY_ENV, binary)
    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    with mock.patch(
        "loregarden.services.git_subprocess.subprocess.run", return_value=completed
    ) as spawn:
        run_gh(["pr", "list"], cwd=Path("."))
    return spawn.call_args.args[0]


def test_run_gh_spawns_gh_from_path_by_default(monkeypatch):
    assert _spawned_gh(monkeypatch, None) == ["gh", "pr", "list"]


def test_run_gh_spawns_the_binary_the_override_names(monkeypatch):
    assert _spawned_gh(monkeypatch, "/opt/tools/gh") == ["/opt/tools/gh", "pr", "list"]


def test_run_gh_scrubs_repo_bindings_and_passes_its_timeout(monkeypatch):
    monkeypatch.setenv("GIT_DIR", "/elsewhere/.git")
    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    with mock.patch(
        "loregarden.services.git_subprocess.subprocess.run", return_value=completed
    ) as spawn:
        run_gh(["pr", "view"], cwd=Path("."), gh_token="t0k", timeout=10)
    kwargs = spawn.call_args.kwargs
    assert "GIT_DIR" not in kwargs["env"]
    assert kwargs["env"]["GH_TOKEN"] == "t0k"
    assert kwargs["timeout"] == 10


def _gh_argv_sites(source: str) -> list[int]:
    """Lines of list/tuple literals whose first element is the string "gh"."""
    # A "gh" element is spelled with either quote; a module with neither cannot
    # hold one, and parsing all ~430 modules was most of this test's time.
    if '"gh"' not in source and "'gh'" not in source:
        return []
    return [
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.List | ast.Tuple)  # py-org: allow-isinstance — AST node type
        and node.elts
        and isinstance(node.elts[0], ast.Constant)  # py-org: allow-isinstance — AST node type
        and node.elts[0].value == "gh"
    ]


def test_no_module_builds_a_gh_argv_outside_run_gh():
    """`run_gh` is the one seam: its binary override is how tests keep the real `gh` out."""
    package = Path(loregarden.__file__).parent
    offenders = {
        str(path.relative_to(package)): lines
        for path in sorted(package.rglob("*.py"))
        if (lines := _gh_argv_sites(path.read_text(encoding="utf-8")))
    }
    assert offenders == {}


def test_the_argv_scan_sees_a_gh_argv():
    """The scan above proves nothing if it cannot see the shape it bans."""
    assert _gh_argv_sites('subprocess.run(["gh", "pr", "list"])\n') == [1]
    assert _gh_argv_sites('subprocess.run(["other", "status"])\n') == []


# -- which gh account -------------------------------------------------------


def _gh_answering(token_lookup: subprocess.CompletedProcess):
    """A `subprocess.run` that answers `gh auth token` with `token_lookup`, else succeeds."""
    calls: list[tuple[list[str], dict[str, str]]] = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs["env"]))
        if argv[1:3] == ["auth", "token"]:
            return token_lookup
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="ok", stderr="")

    return run, calls


def _token(value: str) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=f"{value}\n", stderr="")


def test_run_gh_acts_as_the_configured_account_not_the_active_one(monkeypatch):
    monkeypatch.setenv(GH_USER_ENV, "laazer")
    run, calls = _gh_answering(_token("laazer-token"))
    with mock.patch("loregarden.services.git_subprocess.subprocess.run", side_effect=run):
        result = run_gh(["pr", "create"], cwd=Path("."))

    assert result.returncode == 0
    lookup, command = calls
    assert lookup[0][1:] == ["auth", "token", "--user", "laazer"]
    assert "GH_TOKEN" not in lookup[1]
    assert command[0][1:] == ["pr", "create"]
    assert command[1]["GH_TOKEN"] == "laazer-token"


def test_the_configured_token_is_looked_up_once(monkeypatch):
    monkeypatch.setenv(GH_USER_ENV, "laazer")
    run, calls = _gh_answering(_token("laazer-token"))
    with mock.patch("loregarden.services.git_subprocess.subprocess.run", side_effect=run):
        run_gh(["pr", "view"], cwd=Path("."))
        run_gh(["pr", "merge"], cwd=Path("."))

    assert [argv[1:3] for argv, _ in calls] == [["auth", "token"], ["pr", "view"], ["pr", "merge"]]


def test_an_explicit_token_beats_the_configured_account(monkeypatch):
    monkeypatch.setenv(GH_USER_ENV, "laazer")
    run, calls = _gh_answering(_token("laazer-token"))
    with mock.patch("loregarden.services.git_subprocess.subprocess.run", side_effect=run):
        run_gh(["pr", "list"], cwd=Path("."), gh_token="explicit")

    assert [(argv[1:], env["GH_TOKEN"]) for argv, env in calls] == [(["pr", "list"], "explicit")]


def test_an_unset_account_leaves_gh_on_its_active_one(monkeypatch):
    monkeypatch.delenv("GH_TOKEN", raising=False)
    run, calls = _gh_answering(_token("never-asked"))
    with mock.patch("loregarden.services.git_subprocess.subprocess.run", side_effect=run):
        run_gh(["pr", "list"], cwd=Path("."))

    assert [(argv[1:], "GH_TOKEN" in env) for argv, env in calls] == [(["pr", "list"], False)]


def test_an_unresolvable_account_fails_the_call_instead_of_using_the_active_one(monkeypatch):
    monkeypatch.setenv(GH_USER_ENV, "laazer")
    missing = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="no account found for laazer"
    )
    run, calls = _gh_answering(missing)
    with mock.patch("loregarden.services.git_subprocess.subprocess.run", side_effect=run):
        result = run_gh(["pr", "merge"], cwd=Path("."))

    assert [argv[1:3] for argv, _ in calls] == [["auth", "token"]]
    assert result.returncode == GH_AUTH_REQUIRED_EXIT
    assert "laazer" in result.stderr and GH_USER_ENV in result.stderr


def test_a_failed_lookup_is_not_cached(monkeypatch):
    monkeypatch.setenv(GH_USER_ENV, "laazer")
    missing = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="no")
    with mock.patch("loregarden.services.git_subprocess.subprocess.run", return_value=missing):
        with pytest.raises(GhAccountUnavailable):
            gh_token_for_user("laazer")
    with mock.patch(
        "loregarden.services.git_subprocess.subprocess.run", return_value=_token("fixed")
    ):
        assert gh_token_for_user("laazer") == "fixed"


def test_agents_get_the_configured_accounts_token(monkeypatch):
    monkeypatch.setenv(GH_USER_ENV, "laazer")
    invocation = CliInvocation(argv=["claude"])
    with mock.patch(
        "loregarden.services.git_subprocess.subprocess.run", return_value=_token("laazer-token")
    ):
        assert invocation_env(invocation)["GH_TOKEN"] == "laazer-token"


def test_an_agent_still_spawns_when_the_account_cannot_be_resolved(monkeypatch):
    monkeypatch.setenv(GH_USER_ENV, "laazer")
    monkeypatch.delenv("GH_TOKEN", raising=False)
    invocation = CliInvocation(argv=["claude"])
    missing = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="no")
    with mock.patch("loregarden.services.git_subprocess.subprocess.run", return_value=missing):
        assert "GH_TOKEN" not in invocation_env(invocation)
