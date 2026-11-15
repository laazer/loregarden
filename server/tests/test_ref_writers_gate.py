"""The gate that keeps branch moves inside `integration_branch_moves`.

Pins what it must catch (every ref-write shape the services actually use),
what it must leave alone (a force flag on something that is not a ref write),
the waiver and its minimum reason, and that it is diff-scoped: a writer that
predates the change passes until someone edits the call.
"""

import importlib.util
import subprocess
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / ".lefthook" / "scripts"
_CHECKER_PATH = _SCRIPTS / "py_ref_writers_check.py"


def _load_checker():
    spec = importlib.util.spec_from_file_location("py_ref_writers_check", _CHECKER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


def _write(tmp_path: Path, source: str, name: str = "service.py") -> Path:
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return path


FLAGGED = {
    "update_ref_list": 'run_git(["update-ref", f"refs/heads/{b}", new, old], cwd=r)\n',
    "update_ref_delete": 'run_git(["update-ref", "-d", ref], cwd=r)\n',
    "update_ref_varargs": '_git(repo_root, "update-ref", f"refs/heads/{b}", head, expected)\n',
    "branch_force_delete": '_git(repo_root, "branch", "-D", branch)\n',
    # `-d` deletes too: it only asks git "merged into HEAD?" first.
    "branch_delete": '_git(repo_root, "branch", "-d", branch)\n',
    "branch_long_delete": 'run_git(["branch", "--delete", branch], cwd=r)\n',
    "branch_force_move": 'run_git(["branch", "-f", branch, "main"], cwd=r)\n',
    "branch_long_force": 'run_git(["branch", "--force", branch, sha], cwd=r)\n',
    "checkout_reset_branch": 'run_git(["checkout", "-B", branch, sha], cwd=r)\n',
    "switch_reset_branch": 'run_git(["switch", "-C", branch, sha], cwd=r)\n',
    "push_force": 'run_git(["push", "--force", "origin", b], cwd=r)\n',
    "push_force_with_lease_fstring": (
        'run_git(["push", f"--force-with-lease={ref}:{held}", "origin", f"{h}:{ref}"], cwd=r)\n'
    ),
    "push_short_force": 'run_git(["push", "-f", "origin", b], cwd=r)\n',
    "gh_delete_ref": (
        'subprocess.run(["gh", "api", "-X", "DELETE", f"repos/{{o}}/{{r}}/git/refs/heads/{b}"])\n'
    ),
    "shell_string": 'subprocess.run("git update-ref refs/heads/x HEAD", shell=True)\n',
    "multiline": 'run_git(\n    [\n        "update-ref",\n        ref,\n        sha,\n    ],\n    cwd=r,\n)\n',
}

CLEAN = {
    "worktree_remove_force": 'run_git(["worktree", "remove", "--force", str(tree)], cwd=r)\n',
    "plain_branch_list": 'run_git(["branch", "--list"], cwd=r)\n',
    "plain_push": 'run_git(["push", "origin", b], cwd=r)\n',
    "cli_force_flag": 'argv.append("--force")\n',
    "flag_vocabulary": '_FORCE = frozenset({"--force", "-f", "-D"})\n',
    "gh_delete_a_comment": (
        'subprocess.run(["gh", "api", "-X", "DELETE", f"repos/{{o}}/{{r}}/issues/comments/{c}"])\n'
    ),
    "gh_get_ref": 'subprocess.run(["gh", "api", f"repos/{{o}}/{{r}}/git/refs/heads/{b}"])\n',
    "argv_from_variable": "run_git(argv, cwd=r)\n",
    "fstring_led_by_placeholder": 'run_git(["push", f"{flag}", "origin"], cwd=r)\n',
}


@pytest.mark.parametrize("name", sorted(FLAGGED))
def test_flags_every_ref_write_shape(tmp_path, name):
    found = checker.violations_in(_write(tmp_path, FLAGGED[name]), repo=None)
    assert len(found) == 1, found
    assert "outside integration_branch_moves" in found[0][2]


@pytest.mark.parametrize("name", sorted(CLEAN))
def test_leaves_what_is_not_a_ref_write_alone(tmp_path, name):
    assert checker.violations_in(_write(tmp_path, CLEAN[name]), repo=None) == []


def test_a_waiver_with_a_reason_on_the_call_passes(tmp_path):
    source = (
        '_git(repo, "branch", "-D", scratch)'
        "  # py-org: allow-ref-write - scratch branch created above, holds nothing\n"
    )
    assert checker.violations_in(_write(tmp_path, source), repo=None) == []


def test_a_waiver_in_the_comment_block_above_passes(tmp_path):
    source = (
        "# py-org: allow-ref-write - chat branches are not integration branches;\n"
        "# the chat sweep owns them.\n"
        'run_git(["branch", "-D", branch], cwd=r)\n'
    )
    assert checker.violations_in(_write(tmp_path, source), repo=None) == []


@pytest.mark.parametrize("reason", ["", " - ok", " -   because"])
def test_a_waiver_without_a_real_reason_does_not(tmp_path, reason):
    source = f'run_git(["update-ref", ref, sha], cwd=r)  # py-org: allow-ref-write{reason}\n'
    found = checker.violations_in(_write(tmp_path, source), repo=None)
    assert len(found) == 1
    assert "no real reason" in found[0][2]


def test_the_owner_and_tests_are_exempt():
    owner = Path("server/loregarden/services/integration_branch_moves.py")
    assert checker._is_exempt(owner)
    assert checker._is_exempt(Path("server/tests/test_anything.py"))
    assert not checker._is_exempt(Path("server/loregarden/services/target_branch.py"))


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo_with_an_old_writer(tmp_path: Path) -> Path:
    """A committed module with a bare ref write in it, as every service has today."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "service.py").write_text(
        'def move(r, ref, sha):\n    run_git(["update-ref", ref, sha], cwd=r)\n\n\ndef other():\n    return 1\n',
        encoding="utf-8",
    )
    _git(repo, "add", "service.py")
    _git(repo, "commit", "-q", "-m", "seed")
    return repo


def test_a_writer_that_predates_the_change_passes_an_edit_elsewhere(repo_with_an_old_writer):
    repo = repo_with_an_old_writer
    module = repo / "service.py"
    module.write_text(module.read_text().replace("return 1", "return 2"), encoding="utf-8")
    _git(repo, "add", "service.py")

    assert checker.main(["--repo", str(repo), "--scope", "staged", str(module)]) == 0


def test_an_edit_to_the_writer_itself_makes_it_this_changes(repo_with_an_old_writer, capsys):
    repo = repo_with_an_old_writer
    module = repo / "service.py"
    module.write_text(module.read_text().replace("ref, sha]", "ref, sha, old]"), encoding="utf-8")
    _git(repo, "add", "service.py")

    assert checker.main(["--repo", str(repo), "--scope", "staged", str(module)]) == 1
    assert "service.py:2" in capsys.readouterr().out


def test_a_new_module_with_a_bare_writer_fails(repo_with_an_old_writer, capsys):
    repo = repo_with_an_old_writer
    module = _write(repo, FLAGGED["branch_force_delete"], name="sweeper.py")
    _git(repo, "add", "sweeper.py")

    assert checker.main(["--repo", str(repo), "--scope", "staged", str(module)]) == 1
    assert (
        "sweeper.py:1: `branch -D/-d/-f` outside integration_branch_moves"
        in capsys.readouterr().out
    )


def test_a_new_module_with_no_ref_write_passes(repo_with_an_old_writer):
    repo = repo_with_an_old_writer
    module = _write(repo, CLEAN["plain_push"], name="pusher.py")
    _git(repo, "add", "pusher.py")

    assert checker.main(["--repo", str(repo), "--scope", "staged", str(module)]) == 0


def test_it_runs_wherever_the_git_subprocess_gate_runs():
    """Wired beside py-git-subprocess in lefthook.yml, with the same glob."""
    lefthook = (Path(__file__).resolve().parents[2] / "lefthook.yml").read_text()
    assert "py_ref_writers_check.py {staged_files}" in lefthook
