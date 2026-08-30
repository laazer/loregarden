"""A transition gate must never report a pass over code it never read.

`--scope worktree` diffs against HEAD. Once a driver commits the ticket
worktree — which the external harness *must* do, because the worktree-retire
guard refuses to remove a tree holding uncommitted work — the diff is empty, the
gate matches zero files, prints nothing at all, and exits 0. The caller reads
that exit code as a pass. The code it skipped is an agent's freshly written
module: the least-reviewed code in the run.

These pin the four things that close that hole:

1. a clean-tree worktree run examines the branch diff instead of nothing, and
   fails loudly when it cannot work out what to examine at all;
2. every gate run says what it examined, with a file count, so "clean" and
   "read nothing" are never the same output;
3. a run over zero files does not claim success;
4. committing to satisfy the retire guard and gating the result both hold on
   one repository state, rather than defeating each other.

Black-box on the scripts by design: the fix may live in the shared scoping
helper or in each gate, and these should not care which.
"""

from __future__ import annotations

import importlib
import os
import re
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest
import yaml
from loregarden.models.domain import Worktree
from loregarden.services import worktree_lifecycle

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / ".lefthook" / "scripts"
_TS_PARSER = _ROOT / "client" / "node_modules" / "@typescript-eslint" / "typescript-estree"

#: `gate: … examined N file(s) …` — the count is what makes a vacuous run
#: distinguishable from a clean one. The prose around it is the gate's business.
EXAMINED_RE = re.compile(r"examined\s+(\d+)\s+file", re.IGNORECASE)

PY_ORGANIZATION_VIOLATION = """
def read(payload):
    return isinstance(payload, dict)
"""

PY_SILENT_EXCEPT_VIOLATION = """
def read(path):
    try:
        return path.read_text()
    except Exception:
        return None
"""

TS_VIOLATION = """export function describe(error: unknown): string {
  return error instanceof Error ? error.message : "unknown";
}
"""


def _git(repo: Path, *args: str) -> None:
    # GIT_DIR/GIT_WORK_TREE beat cwd, and a run nested in a worktree's hook
    # inherits them pointing at the real repository.
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=env)


def _scrubbed_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repository with a `main` base commit and a ticket branch checked out."""
    _git(tmp_path, "init", "-q", "-b", "main", ".")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    src = tmp_path / "src" / "pkg"
    src.mkdir(parents=True)
    (src / "base.py").write_text("x = 1\n")
    client = tmp_path / "client" / "src"
    client.mkdir(parents=True)
    (client / "base.ts").write_text("export const x = 1;\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    _git(tmp_path, "checkout", "-q", "-b", "ticket-branch")
    return tmp_path


PY_ORGANIZATION_GATE = [sys.executable, str(_SCRIPTS / "py_organization_check.py")]
PY_SILENT_EXCEPT_GATE = [sys.executable, str(_SCRIPTS / "py_silent_except_check.py")]
TS_ORGANIZATION_GATE = ["node", str(_SCRIPTS / "ts_organization_check.cjs")]

#: The three gates `agent_context/orchestration/*.yaml` runs at every stage
#: transition, each with `--repo <workspace> --scope worktree`.
TRANSITION_GATES = [
    pytest.param(
        PY_ORGANIZATION_GATE, "src/pkg/new_mod.py", PY_ORGANIZATION_VIOLATION, id="py-org"
    ),
    pytest.param(
        PY_SILENT_EXCEPT_GATE,
        "src/pkg/new_mod.py",
        PY_SILENT_EXCEPT_VIOLATION,
        id="py-silent-except",
    ),
    pytest.param(TS_ORGANIZATION_GATE, "client/src/new_mod.ts", TS_VIOLATION, id="ts-org"),
]

CLEAN_TRANSITION_GATES = [
    pytest.param(PY_ORGANIZATION_GATE, "src/pkg/new_mod.py", "y = 2\n", id="py-org"),
    pytest.param(PY_SILENT_EXCEPT_GATE, "src/pkg/new_mod.py", "y = 2\n", id="py-silent-except"),
    pytest.param(
        TS_ORGANIZATION_GATE, "client/src/new_mod.ts", "export const y = 2;\n", id="ts-org"
    ),
]


#: Scopes that read `--base`. `staged` never reaches `_validated_ref`, so a
#: base-ref guard has nothing to bite on there; the scopes below are the ones a
#: caller can actually steer with a ref, and each resolves it differently:
#: `worktree` through `git merge-base`, `branch` straight into `git diff`. A
#: suite pinned to one of them grades one of the two guards.
BASE_CONSUMING_SCOPES = ["worktree", "branch"]


def _require_ts_parser() -> None:
    """An absent parser must fail CI, not skip it.

    The `.cjs` gate needs `client/node_modules`, which the `server` CI job did
    not install — so all ten `[ts-org]` params skipped there and the gate's
    rewrite had no enforced coverage anywhere. A skip nobody reads is
    indistinguishable from a pass, which is the exact failure this whole file
    exists to close. The job now runs `npm ci`; if that ever stops happening,
    this fails instead of quietly reporting green.
    """
    if _TS_PARSER.is_dir():
        return
    message = f"the TS gate cannot parse without {_TS_PARSER}; run `npm ci` in client/"
    if os.environ.get("CI"):
        pytest.fail(message)
    pytest.skip(message)


def _run_gate(
    gate: list[str], repo: Path, *extra: str, scope: str = "worktree"
) -> subprocess.CompletedProcess:
    if gate is TS_ORGANIZATION_GATE:
        _require_ts_parser()
    return subprocess.run(
        [*gate, "--repo", str(repo), "--scope", scope, *extra],
        capture_output=True,
        text=True,
        env=_scrubbed_env(),
    )


def _assert_no_vacuous_pass(result: subprocess.CompletedProcess) -> None:
    """`examined 0 file(s)` and exit 0 together is the bug, in one assertion.

    Whatever a gate decides about a scope it could not resolve, it may not
    report reading nothing and succeeding at the same time — that pair is what
    a caller reading the exit code sees as a pass.
    """
    match = EXAMINED_RE.search(_out(result))
    examined = None if match is None else int(match.group(1))
    assert not (examined == 0 and result.returncode == 0), _out(result)


def _out(result: subprocess.CompletedProcess) -> str:
    """Both streams: the TS gate writes its findings to stderr, the Python ones to stdout."""
    return result.stdout + result.stderr


def _commit_agent_work(repo: Path, relpath: str, body: str) -> None:
    """What a driver that must commit before it can retire the worktree leaves."""
    target = repo / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "agent work")


# --------------------------------------------------------------------------- #
# AC1 — a clean tree examines the branch diff, or fails loudly
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(("gate", "relpath", "body"), TRANSITION_GATES)
def test_committed_work_is_still_examined_at_worktree_scope(
    repo: Path, gate: list[str], relpath: str, body: str
) -> None:
    """AC1: the stage committed, so `git diff HEAD` is empty — the branch is not."""
    _commit_agent_work(repo, relpath, body)
    assert not _porcelain(repo), "precondition: the tree is clean"

    result = _run_gate(gate, repo, "--base", "main")

    assert result.returncode == 1, _out(result)
    assert Path(relpath).name in _out(result)


@pytest.mark.parametrize(("gate", "relpath", "body"), CLEAN_TRANSITION_GATES)
def test_clean_tree_run_names_the_branch_scope_it_fell_back_to(
    repo: Path, gate: list[str], relpath: str, body: str
) -> None:
    """AC1 + AC2: the caller can tell the run read the branch, not the empty diff."""
    _commit_agent_work(repo, relpath, body)

    result = _run_gate(gate, repo, "--base", "main")

    assert result.returncode == 0, _out(result)
    match = EXAMINED_RE.search(_out(result))
    assert match is not None, f"no file count in: {_out(result)!r}"
    assert int(match.group(1)) == 1
    assert "branch" in _out(result).lower()


#: Files that are not this run's subject: a scratch note an agent left behind,
#: and an unrelated edit to a file that was already tracked. Neither says
#: anything about the commit the stage just made.
UNRELATED_UNTRACKED = "NOTES.txt"
UNRELATED_TRACKED = {
    "src/pkg/new_mod.py": ("src/pkg/base.py", "y = 2\n"),
    "client/src/new_mod.ts": ("client/src/base.ts", "export const z = 3;\n"),
}


@pytest.mark.parametrize(("gate", "relpath", "body"), TRANSITION_GATES)
def test_a_stray_untracked_file_does_not_hide_the_committed_change(
    repo: Path, gate: list[str], relpath: str, body: str
) -> None:
    """AC1: the branch diff is not an alternative to the worktree diff.

    Reading the branch only when the *whole* tree is clean reopens the hole this
    ticket exists to close: the gate fires after the driver commits, and
    anything the agent left behind in between — a scratch note, a log, a report
    — puts the committed violation back out of view. One `echo notes >
    NOTES.txt` was enough to turn exit 1 into `examined 0 file(s)`, exit 0.
    """
    _commit_agent_work(repo, relpath, body)
    (repo / UNRELATED_UNTRACKED).write_text("notes\n")

    result = _run_gate(gate, repo, "--base", "main")

    assert result.returncode == 1, _out(result)
    assert Path(relpath).name in _out(result)


@pytest.mark.parametrize(("gate", "relpath", "body"), TRANSITION_GATES)
def test_an_unrelated_tracked_edit_does_not_hide_the_committed_change(
    repo: Path, gate: list[str], relpath: str, body: str
) -> None:
    """AC1, the worse variant: a plausible count *and* a pass.

    Touching any already-tracked file gives the worktree diff something to
    report, so the run printed `examined 1 file(s)` and `check passed` — a
    count that looks like work was read, over a file that is not the one the
    stage committed.
    """
    _commit_agent_work(repo, relpath, body)
    unrelated_rel, appended = UNRELATED_TRACKED[relpath]
    unrelated = repo / unrelated_rel
    unrelated.write_text(unrelated.read_text() + appended)

    result = _run_gate(gate, repo, "--base", "main")

    assert result.returncode == 1, _out(result)
    assert Path(relpath).name in _out(result)


@pytest.mark.parametrize("scope", BASE_CONSUMING_SCOPES)
@pytest.mark.parametrize(("gate", "relpath", "body"), TRANSITION_GATES)
def test_a_base_ref_that_looks_like_an_option_is_refused(
    repo: Path, gate: list[str], relpath: str, body: str, tmp_path: Path, scope: str
) -> None:
    """A `--base` starting with `-` reached git as a flag.

    `--base --output=/tmp/x` interpolated straight into the argv, so git wrote a
    real file outside the repository and the run reported zero files, exit 0.

    Both scopes, because only one of them is defended twice. At `worktree` the
    ref goes to `git merge-base`, which already fails closed on an unusable
    revision, so the ref guard can be deleted there and nothing notices. At
    `branch` the ref is interpolated straight into `git diff <ref>...HEAD`, and
    the guard is the only thing standing between an agent-supplied string and
    git's argv — `branch` is an `OrganizationScope` member and a value of the
    `loregarden_check_organization` scope enum, so that path is reachable.
    """
    _commit_agent_work(repo, relpath, body)
    written = tmp_path / "escaped.txt"

    result = _run_gate(gate, repo, "--base", f"--output={written}", scope=scope)

    assert result.returncode != 0, _out(result)
    assert not written.exists(), "the base ref reached git as an option"
    _assert_no_vacuous_pass(result)


@pytest.mark.parametrize(("gate", "relpath", "body"), CLEAN_TRANSITION_GATES)
def test_an_explicit_file_list_names_the_scope_it_was_given(
    repo: Path, gate: list[str], relpath: str, body: str
) -> None:
    """AC2: the scope line must not claim the index when the run is not scoped to it."""
    target = repo / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body)
    _git(repo, "add", "-A")

    result = _run_gate(gate, repo, str(target))

    assert "staged changes" not in _out(result), _out(result)
    assert "worktree" in _out(result).lower(), _out(result)


@pytest.mark.parametrize(("gate", "relpath", "body"), TRANSITION_GATES)
def test_uncommitted_work_is_examined_at_worktree_scope(
    repo: Path, gate: list[str], relpath: str, body: str
) -> None:
    """AC1 must not cost the builtin driver's case: an untracked file still counts."""
    target = repo / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body)

    result = _run_gate(gate, repo, "--base", "main")

    assert result.returncode == 1, _out(result)
    assert Path(relpath).name in _out(result)


@pytest.mark.parametrize(("gate", "relpath", "body"), TRANSITION_GATES)
def test_unresolvable_base_fails_loudly_rather_than_passing_silently(
    repo: Path, gate: list[str], relpath: str, body: str
) -> None:
    """AC1: `git diff nope...HEAD` exits 128 and yields nothing.

    Today that empty output is indistinguishable from a clean diff, so the gate
    exits 0. A scope it could not resolve is not a scope it examined.
    """
    _commit_agent_work(repo, relpath, body)

    result = _run_gate(gate, repo, "--base", "no-such-base-ref")

    assert result.returncode != 0
    assert "no-such-base-ref" in _out(result)
    _assert_no_vacuous_pass(result)


@pytest.mark.parametrize(("gate", "relpath", "body"), TRANSITION_GATES)
def test_unresolvable_base_at_branch_scope_never_reports_zero_files_and_success(
    repo: Path, gate: list[str], relpath: str, body: str
) -> None:
    """The same hole one scope over, where only one guard covers it.

    `worktree` sends an unknown ref to `git merge-base`, which returns None and
    the resolver turns into a described degradation or a raise. `branch` sends
    it to `git diff nope...HEAD`, which exits 128 with an empty stdout — byte
    identical to a clean diff. If the diff helper ever goes back to returning
    that empty string instead of raising, the run reports `examined 0 file(s)`
    and exits 0 while the worktree-scope test above still passes, because
    merge-base masks it there.
    """
    _commit_agent_work(repo, relpath, body)

    result = _run_gate(gate, repo, "--base", "no-such-base-ref", scope="branch")

    assert result.returncode != 0, _out(result)
    assert "no-such-base-ref" in _out(result)
    _assert_no_vacuous_pass(result)


@pytest.mark.parametrize(("gate", "relpath", "body"), TRANSITION_GATES)
def test_an_unresolvable_base_with_a_stray_file_still_fails_loudly(
    repo: Path, gate: list[str], relpath: str, body: str
) -> None:
    """The production default, and the shape the "nothing examined" guard missed.

    `default.yaml`, `blobert.yaml` and `loregarden.yaml` pass no `--base`, so a
    workspace whose trunk is not `main` degrades to worktree-vs-HEAD on every
    run. Guarding that fallback on the *raw path list* meant one stray file
    nobody grades — a scratch note, a log — was enough to make it look like a
    scope with content, and all three gates printed `examined 0 file(s)` and
    exited 0 over committed violations they never read.
    """
    _commit_agent_work(repo, relpath, body)
    (repo / "scratch-note.txt").write_text("left behind by an agent\n")

    result = _run_gate(gate, repo, "--base", "no-such-base-ref")

    assert result.returncode != 0, _out(result)
    assert "no-such-base-ref" in _out(result)
    _assert_no_vacuous_pass(result)


@pytest.mark.parametrize(("gate", "relpath", "body"), TRANSITION_GATES)
def test_an_ambient_git_dir_does_not_redirect_the_gate(
    repo: Path, gate: list[str], relpath: str, body: str, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """GIT_DIR beats `cwd`, so an inherited one aims every gate at another repo.

    The control plane runs these gates as subprocesses, and anything nested
    inside a git hook — or a worktree push — carries GIT_DIR. Pointed at an
    unrelated repository the gate resolves that repo's scope instead, examines
    nothing of the workspace it was handed, and exits 0.
    """
    other = tmp_path_factory.mktemp("other-repo")
    _git(other, "init", "-q", "-b", "main", ".")
    _git(other, "config", "user.email", "t@example.com")
    _git(other, "config", "user.name", "t")
    (other / "unrelated.txt").write_text("x\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-qm", "base")
    _commit_agent_work(repo, relpath, body)

    if gate is TS_ORGANIZATION_GATE:
        _require_ts_parser()
    result = subprocess.run(
        [*gate, "--repo", str(repo), "--scope", "worktree", "--base", "main"],
        capture_output=True,
        text=True,
        env={**_scrubbed_env(), "GIT_DIR": str(other / ".git"), "GIT_WORK_TREE": str(other)},
    )

    assert result.returncode == 1, _out(result)
    assert Path(relpath).name in _out(result)
    _assert_no_vacuous_pass(result)


# --------------------------------------------------------------------------- #
# AC2 — every gate says what it examined
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(("gate", "relpath", "body"), CLEAN_TRANSITION_GATES)
def test_passing_run_reports_the_file_count_it_examined(
    repo: Path, gate: list[str], relpath: str, body: str
) -> None:
    """AC2: a pass over real files carries the count that proves it read them."""
    target = repo / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body)

    result = _run_gate(gate, repo, "--base", "main")

    assert result.returncode == 0, _out(result)
    match = EXAMINED_RE.search(_out(result))
    assert match is not None, f"no file count in: {_out(result)!r}"
    assert int(match.group(1)) == 1


@pytest.mark.parametrize(("gate", "relpath", "body"), TRANSITION_GATES)
def test_branch_scope_examines_the_branch_diff_and_says_so(
    repo: Path, gate: list[str], relpath: str, body: str
) -> None:
    """AC2 at `branch` scope, so the happy path there is graded too.

    Without this, every `branch`-scope assertion in the file is about a failure,
    and a resolver that raised on *every* branch run would still look correct.
    """
    _commit_agent_work(repo, relpath, body)

    result = _run_gate(gate, repo, "--base", "main", scope="branch")

    assert result.returncode == 1, _out(result)
    assert Path(relpath).name in _out(result)
    match = EXAMINED_RE.search(_out(result))
    assert match is not None, f"no file count in: {_out(result)!r}"
    assert int(match.group(1)) == 1
    assert "main...HEAD" in _out(result), _out(result)


# --------------------------------------------------------------------------- #
# AC3 — zero files is never a success
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(("gate", "relpath", "body"), CLEAN_TRANSITION_GATES)
def test_zero_file_run_reports_zero_and_never_claims_a_pass(
    repo: Path, gate: list[str], relpath: str, body: str
) -> None:
    """AC3: nothing committed, nothing edited — the gate graded nothing.

    That is a legitimate state (a review stage changes no code), so it does not
    block the transition. It must not be reported in the same words as a run
    that read files and found them clean.
    """
    result = _run_gate(gate, repo, "--base", "main")

    match = EXAMINED_RE.search(_out(result))
    assert match is not None, f"no file count in: {_out(result)!r}"
    assert int(match.group(1)) == 0
    assert "passed" not in _out(result).lower()


# --------------------------------------------------------------------------- #
# AC4 — the retire guard and the gate hold at once
# --------------------------------------------------------------------------- #


def _porcelain(repo: Path) -> str:
    env = _scrubbed_env()
    proc = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
    )
    return proc.stdout.strip()


def _worktree_row(repo: Path) -> Worktree:
    return Worktree(
        workspace_id="ws",
        agent_run_id="run",
        worktree_path=str(repo),
        branch="ticket-branch",
        parent_branch="main",
    )


def test_retire_guard_and_transition_gate_hold_on_the_same_tree(repo: Path) -> None:
    """AC4: the two safeguards must both bind on one repository state.

    Before: the agent's violation is uncommitted. The gate catches it, and the
    retire guard refuses to remove the tree — both hold, and this is the state
    the builtin driver gates in.

    After: the harness commits, because it cannot retire the worktree otherwise.
    The retire guard is now satisfied *and* the gate must still catch the same
    violation. Today it does not: the commit that unblocks retirement is exactly
    what empties the diff the gate reads.
    """
    relpath = "src/pkg/new_mod.py"
    target = repo / relpath
    target.write_text(PY_ORGANIZATION_VIOLATION)
    row = _worktree_row(repo)

    assert worktree_lifecycle._is_dirty(repo) is True
    uncommitted = _run_gate(PY_ORGANIZATION_GATE, repo, "--base", "main")
    assert uncommitted.returncode == 1, _out(uncommitted)

    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "agent work")

    # Ticket 489's guard: nothing uncommitted, and the branch carries commits
    # `main` does not, so retiring the checkout would preserve the work.
    assert worktree_lifecycle._is_dirty(repo) is False
    assert worktree_lifecycle._has_preserved_commits(repo, row) is True

    committed = _run_gate(PY_ORGANIZATION_GATE, repo, "--base", "main")
    assert committed.returncode == 1, _out(committed)
    assert "new_mod.py" in _out(committed)


# --------------------------------------------------------------------------- #
# The gates' own source is graded by something
# --------------------------------------------------------------------------- #

_LEFTHOOK_YML = _ROOT / "lefthook.yml"

#: The two pre-commit commands that run the gates this file is about. They are
#: the ones whose rules must also bind on the scripts that implement them.
SELF_GRADING_COMMANDS = ["py-organization", "py-silent-except"]


def _expand_braces(pattern: str) -> list[str]:
    """`{a,b}/x` -> `a/x`, `b/x` — lefthook's own glob syntax, as used above."""
    match = re.search(r"\{([^{}]*)\}", pattern)
    if match is None:
        return [pattern]
    expanded: list[str] = []
    for option in match.group(1).split(","):
        head, tail = pattern[: match.start()], pattern[match.end() :]
        expanded.extend(_expand_braces(head + option + tail))
    return expanded


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Doublestar semantics: `**/` spans directories, `*` and `?` do not."""
    parts: list[str] = []
    index = 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            parts.append("(?:[^/]+/)*")
            index += 3
        elif pattern[index] == "*":
            parts.append("[^/]*")
            index += 1
        elif pattern[index] == "?":
            parts.append("[^/]")
            index += 1
        else:
            parts.append(re.escape(pattern[index]))
            index += 1
    return re.compile("^" + "".join(parts) + "$")


def _glob_matches(pattern: str, relpath: str) -> bool:
    return any(_glob_to_regex(option).match(relpath) for option in _expand_braces(pattern))


def _gate_script_relpaths() -> list[str]:
    return sorted(f".lefthook/scripts/{path.name}" for path in _SCRIPTS.glob("*.py"))


def test_the_glob_translator_reproduces_the_globs_lefthook_already_has() -> None:
    """The matcher below is only evidence if it agrees with the known cases."""
    assert _glob_matches("server/**/*.py", "server/src/loregarden/api/runs.py")
    assert _glob_matches("server/**/*.py", "server/conftest.py")
    assert not _glob_matches("server/**/*.py", "client/src/app.ts")
    assert not _glob_matches("server/**/*.py", ".lefthook/scripts/py_string_vocab.py")
    assert _glob_matches("client/**/*.{ts,tsx}", "client/src/state/toastStore.ts")
    assert not _glob_matches("client/**/*.{ts,tsx}", "client/src/state/toastStore.js")
    assert not _glob_matches(".lefthook/scripts/*.py", ".lefthook/scripts/sub/deep.py")


@pytest.mark.parametrize("command", SELF_GRADING_COMMANDS)
def test_the_gate_scripts_are_themselves_covered_by_the_precommit_glob(command: str) -> None:
    """The scripts enforcing the repo's standards were exempt from them.

    The pre-commit glob is `server/**` and gate mode confines itself to the
    detected Python source root, so `.lefthook/scripts/**` is read by no gate on
    either surface: a violation planted in a gate script goes undetected. These
    are the most-trusted 1,800 lines in the repo and the least-graded.
    """
    hooks = yaml.safe_load(_LEFTHOOK_YML.read_text(encoding="utf-8"))
    glob = hooks["pre-commit"]["commands"][command]["glob"]
    uncovered = [rel for rel in _gate_script_relpaths() if not _glob_matches(glob, rel)]

    assert _gate_script_relpaths(), "precondition: there are gate scripts to grade"
    assert not uncovered, f"{command} glob {glob!r} does not reach: {uncovered}"


@pytest.mark.parametrize(
    ("gate", "body"),
    [
        pytest.param(PY_ORGANIZATION_GATE, PY_ORGANIZATION_VIOLATION, id="py-org"),
        pytest.param(PY_SILENT_EXCEPT_GATE, PY_SILENT_EXCEPT_VIOLATION, id="py-silent-except"),
    ],
)
def test_a_violation_planted_in_a_gate_script_is_caught(
    repo: Path, gate: list[str], body: str
) -> None:
    """The glob is only half of it — the gate must grade the path once it gets it.

    Pre-commit hands an explicit file list, which the selector deliberately does
    not re-confine to the source root, so this path already holds. It is pinned
    so that a future tightening of the selector cannot quietly re-exempt the
    scripts the test above just wired in.
    """
    relpath = ".lefthook/scripts/planted_check.py"
    target = repo / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body)
    _git(repo, "add", "-A")

    result = _run_gate(gate, repo, str(target), scope="staged")

    assert result.returncode == 1, _out(result)
    assert "planted_check.py" in _out(result)


@pytest.mark.parametrize(
    ("gate", "body"),
    [
        pytest.param(PY_ORGANIZATION_GATE, PY_ORGANIZATION_VIOLATION, id="py-org"),
        pytest.param(PY_SILENT_EXCEPT_GATE, PY_SILENT_EXCEPT_VIOLATION, id="py-silent-except"),
    ],
)
def test_a_gate_script_edited_during_a_stage_is_graded_at_worktree_scope(
    repo: Path, gate: list[str], body: str
) -> None:
    """The surface that matters for agents.

    A transition gate discovers its own file list from the diff, and
    `python_files_in_scope` drops everything outside the detected source root —
    `server/` here. An agent that edits `.lefthook/scripts/*.py` during a stage
    therefore had that edit gated by nothing at all, which is precisely the
    vacuous-pass shape this ticket exists to close, one directory over. The
    selector now exempts the gate scripts from that confinement.
    """
    _commit_agent_work(repo, ".lefthook/scripts/planted_check.py", body)

    result = _run_gate(gate, repo, "--base", "main")

    assert result.returncode == 1, _out(result)
    assert "planted_check.py" in _out(result)


# --------------------------------------------------------------------------- #
# A workspace file, or the environment, must not be able to empty the diff
# --------------------------------------------------------------------------- #


def _load_script(module_name: str):
    """Import a gate script by name, with `.lefthook/scripts` on the path.

    Imported rather than exec'd from a spec so it lands in `sys.modules`: its
    dataclasses resolve their annotations through their own module, and a
    module that is not registered there raises while building them.
    """
    if str(_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS))
    return importlib.import_module(module_name)


@pytest.mark.parametrize("attribute", ["-diff", "binary"])
@pytest.mark.parametrize(("gate", "relpath", "body"), TRANSITION_GATES)
def test_a_gitattributes_entry_cannot_empty_the_diff(
    repo: Path, gate: list[str], relpath: str, body: str, attribute: str
) -> None:
    """One committed workspace file disarmed every gate.

    A repo-root `.gitattributes` marking sources `-diff` (or `binary`) leaves
    `git diff -U0` with no hunks while `--name-only` still lists the file. The
    graded-file guard is satisfied — `examined 1 file(s)` — every touched-line
    set is empty, and the gate exits 0 over a real violation. `--numstat`
    already reports these as `-\t-\tpath`; discarding that marker is what made
    the suppression invisible. A file git will not diff by line is a file that
    must be graded whole, not one reported as graded and clean.
    """
    _commit_agent_work(repo, relpath, body)
    (repo / ".gitattributes").write_text(f"* {attribute}\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "attributes")

    result = _run_gate(gate, repo, "--base", "main")

    assert result.returncode == 1, _out(result)
    assert Path(relpath).name in _out(result)


@pytest.mark.parametrize(
    "scrub",
    [
        pytest.param(lambda: _load_script("precommit_git_diff"), id="gate-script"),
        pytest.param(
            lambda: importlib.import_module("loregarden.services.git_subprocess"), id="service"
        ),
    ],
)
def test_the_git_env_scrub_drops_injected_config(scrub) -> None:
    """`GIT_CONFIG_KEY_n` reaches the `.gitattributes` hole through the environment.

    `GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.attributesFile` makes git read
    attributes from any path the caller names, so the suppression above needs no
    committed file. `GIT_ALTERNATE_OBJECT_DIRECTORIES` is the same class of
    binding as `GIT_OBJECT_DIRECTORY`, which was already scrubbed.
    """
    module = scrub()
    injected = {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.attributesFile",
        "GIT_CONFIG_VALUE_0": "/tmp/attrs",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES": "/tmp/objects",
    }
    with mock.patch.dict(os.environ, injected):
        scrubbed = module.scrubbed_git_env()

    assert not set(injected) & set(scrubbed), sorted(set(injected) & set(scrubbed))


# --------------------------------------------------------------------------- #
# Disjoint history: no fork point is not the same as no such ref
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(("gate", "relpath", "body"), TRANSITION_GATES)
def test_disjoint_history_grades_the_branch_rather_than_blocking_every_transition(
    repo: Path, gate: list[str], relpath: str, body: str
) -> None:
    """An orphan branch is not an unresolvable trunk.

    `git merge-base` exits non-zero for two different answers: the ref does not
    exist, and the ref exists but shares no commit with HEAD. Collapsing both to
    "did not resolve" made every stage transition in such a workspace fail
    permanently, under a message that named trunk detection instead of the real
    cause. There is no fork point to diff from, but nothing is shared either —
    so the whole branch is the change, and the gate grades it.
    """
    _git(repo, "checkout", "-q", "--orphan", "orphan-branch")
    _git(repo, "rm", "-rq", "--cached", ".")
    for tracked in ("src/pkg/base.py", "client/src/base.ts"):
        (repo / tracked).unlink()
    _commit_agent_work(repo, relpath, body)

    result = _run_gate(gate, repo, "--base", "main")

    assert result.returncode == 1, _out(result)
    assert Path(relpath).name in _out(result)
    assert "did not resolve" not in _out(result), _out(result)


# --------------------------------------------------------------------------- #
# The scope argument itself
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(("gate", "relpath", "body"), TRANSITION_GATES)
def test_an_unrecognised_scope_is_refused_rather_than_coerced_to_the_index(
    repo: Path, gate: list[str], relpath: str, body: str
) -> None:
    """A typo'd `--scope` silently became `staged` — the same vacuous pass, one argument over.

    At a stage transition the index is empty, so `--scope wortree` examined zero
    files and exited 0 over a committed violation, in all three gates.
    """
    _commit_agent_work(repo, relpath, body)

    result = _run_gate(gate, repo, "--base", "main", scope="wortree")

    assert result.returncode != 0, _out(result)
    assert "wortree" in _out(result), _out(result)
    _assert_no_vacuous_pass(result)


@pytest.mark.parametrize(("gate", "relpath", "body"), TRANSITION_GATES)
def test_an_explicitly_named_untracked_file_is_graded_whole(
    repo: Path, gate: list[str], relpath: str, body: str
) -> None:
    """The `.cjs` mirror decided "no untracked files" instead of deriving it.

    In explicit-file mode `resolveGateScope` hardcoded `includesUntracked:false`
    where the Python resolver derives it from the scope, so a named untracked
    file was diffed against HEAD, produced no added lines, and passed — while
    Python, on identical inputs, graded the whole file and exited 1.
    """
    target = repo / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body)

    result = _run_gate(gate, repo, "--base", "main", str(target))

    assert result.returncode == 1, _out(result)
    assert Path(relpath).name in _out(result)


@pytest.mark.parametrize(
    "gate", [PY_ORGANIZATION_GATE, PY_SILENT_EXCEPT_GATE], ids=["py-org", "py-silent-except"]
)
def test_a_symlinked_source_file_is_still_graded(
    repo: Path, gate: list[str], tmp_path: Path
) -> None:
    """`resolve()` followed a symlinked module out of the source root.

    The scope filter resolved each candidate before asking whether it sat under
    the repo's Python root, so a file linked into `src/` resolved to wherever it
    really lives, failed the test, and was dropped: `examined 0 file(s)`, exit 0,
    no degraded warning. Resolving the file's *directory* still normalises the
    symlinked prefix a checkout can sit behind without following the file.
    """
    real = tmp_path.parent / "linked_module.py"
    real.write_text(PY_ORGANIZATION_VIOLATION + PY_SILENT_EXCEPT_VIOLATION)
    (repo / "src" / "pkg" / "linked.py").symlink_to(real)

    result = _run_gate(gate, repo, "--base", "main")

    assert result.returncode == 1, _out(result)
    assert "linked.py" in _out(result)


# --------------------------------------------------------------------------- #
# The paths git prints, and the files behind them
# --------------------------------------------------------------------------- #


def _non_ascii_sibling(relpath: str) -> str:
    """`src/pkg/new_mod.py` -> `src/pkg/bäd.py`, keeping the gate's own language."""
    path = Path(relpath)
    return (path.parent / f"bäd{path.suffix}").as_posix()


@pytest.mark.parametrize(("gate", "relpath", "body"), TRANSITION_GATES)
def test_a_non_ascii_path_is_decoded_rather_than_consumed_quoted(
    repo: Path, gate: list[str], relpath: str, body: str
) -> None:
    """`core.quotePath` is git's default, and the quoted literal is not a path.

    `git diff --name-only` prints `src/pkg/bäd.py` as `"src/pkg/b\\303\\244d.py"`.
    Used as a relpath, its suffix is `.py"`, so the language filter dropped it:
    `examined 0 file(s)`, exit 0, over a committed violation.
    """
    target = _non_ascii_sibling(relpath)
    _commit_agent_work(repo, target, body)

    result = _run_gate(gate, repo, "--base", "main")

    assert result.returncode == 1, _out(result)
    assert Path(target).name in _out(result), _out(result)
    _assert_no_vacuous_pass(result)


@pytest.mark.parametrize(("gate", "relpath", "body"), TRANSITION_GATES)
def test_the_examined_count_includes_the_non_ascii_paths(
    repo: Path, gate: list[str], relpath: str, body: str
) -> None:
    """AC2: the count has to be the number of files the gate read.

    A diff of one non-ASCII and one plain file announced `examined 1 file(s)` —
    the quoted path was filtered out before the count was taken, so the number
    itself under-reported, and the file it named was never graded.
    """
    _commit_agent_work(repo, relpath, body)
    _commit_agent_work(repo, _non_ascii_sibling(relpath), body)

    result = _run_gate(gate, repo, "--base", "main")

    match = EXAMINED_RE.search(_out(result))
    assert match is not None, _out(result)
    assert int(match.group(1)) == 2, _out(result)
    assert result.returncode == 1, _out(result)


@pytest.mark.parametrize(("gate", "relpath", "body"), TRANSITION_GATES)
def test_a_path_git_lists_but_the_worktree_lacks_is_not_reported_clean(
    repo: Path, gate: list[str], relpath: str, body: str
) -> None:
    """A file the run could not read is unexaminable, not clean.

    `skip-worktree` plus `rm` is one route to it (a cone sparse-checkout is
    another): the path stays in the diff, so it is counted and graded, but the
    read came back empty-handed and every gate treated that exactly like a file
    that parsed clean — `examined 1 file(s)`, `checks passed.`, exit 0, over the
    violation still sitting in the commit.
    """
    _commit_agent_work(repo, relpath, body)
    _git(repo, "update-index", "--skip-worktree", relpath)
    (repo / relpath).unlink()

    result = _run_gate(gate, repo, "--base", "main")

    assert result.returncode == 1, _out(result)
    assert "could not read it" in _out(result), _out(result)
    assert "passed" not in _out(result), _out(result)


# --------------------------------------------------------------------------- #
# A diff that will not say which lines changed
# --------------------------------------------------------------------------- #


def _suppress_via_diff_driver(repo: Path, pattern: str) -> None:
    """A `diff=<driver>` whose command prints nothing.

    `--name-only` still lists the file and `--numstat` still reports real
    counts, so the `-\t-` marker `-diff`/`binary` produces never fires — but
    `git diff -U0` emits no hunk at all.
    """
    _git(repo, "config", "diff.nodiff.command", "/usr/bin/true")
    (repo / ".gitattributes").write_text(f"{pattern} diff=nodiff\n")


def _suppress_via_clean_filter(repo: Path, pattern: str) -> None:
    """A `filter=` whose clean step empties the blob.

    The committed blob has no content, so `--numstat` reports `0\t0` and the
    diff carries no hunk — while the file on disk, the one the gate reads, is
    full of code. Counts alone cannot tell this from a mode-only `chmod`.
    """
    _git(repo, "config", "filter.dropall.clean", "/usr/bin/true")
    (repo / ".gitattributes").write_text(f"{pattern} filter=dropall\n")


SUPPRESSION_PATTERNS = {
    "src/pkg/new_mod.py": "*.py",
    "client/src/new_mod.ts": "*.ts",
}


#: Each suppression crossed with whether the violating file *arrives* on the
#: branch or is *edited* there. Both arrivals, because the two halves of the fix
#: cover different ones and running only `added` hid that: an added file is
#: graded whole for being added, so the "git emitted no hunk for this path" rule
#: could be deleted outright and every added-file case still passed. Only an
#: edit to a file that already existed puts that rule under load — and an agent
#: editing an existing module is the commoner shape.
#:
#: `clean-filter` has no `modified` row because there is nothing to construct:
#: the clean step empties the blob, so an edit to an already-emptied file leaves
#: the index unchanged and git records no commit at all.
SUPPRESSION_MATRIX = [
    pytest.param(setup, added, id=f"{setup_id}-{'added' if added else 'modified'}")
    for setup, setup_id in [
        (
            lambda repo, pattern: (repo / ".gitattributes").write_text(f"{pattern} -diff\n"),
            "attr-nodiff",
        ),
        (
            lambda repo, pattern: (repo / ".gitattributes").write_text(f"{pattern} binary\n"),
            "attr-binary",
        ),
        (_suppress_via_diff_driver, "diff-driver"),
        (_suppress_via_clean_filter, "clean-filter"),
    ]
    for added in (True, False)
    if not (setup_id == "clean-filter" and not added)
]


@pytest.mark.parametrize(("suppress", "added"), SUPPRESSION_MATRIX)
@pytest.mark.parametrize(("gate", "relpath", "body"), TRANSITION_GATES)
def test_a_suppressed_diff_is_graded_whole_rather_than_passed(
    repo: Path, gate: list[str], relpath: str, body: str, suppress, added: bool
) -> None:
    """ "Which lines changed?" answered with silence is not "none of them".

    Round 4 keyed on the `-\t-` marker git writes for `-diff`/`binary`, which
    covers two of these four and none of the others: a `diff=<driver>` that
    prints nothing reports real counts (`10\t0`) and emits no hunk, and a
    `filter=` that cleans to empty reports `0\t0` with the code still on disk.
    Each printed `examined 1 file(s)`, `checks passed.`, exit 0 over a
    committed violation. The marker is one spelling; asking the diff whether it
    emitted a hunk for the path is the question, and it has an answer for the
    spelling nobody has found yet.
    """
    if not added:
        # Already on `main`, clean, so the branch *edits* it rather than adding it.
        _git(repo, "checkout", "-q", "main")
        _commit_agent_work(repo, relpath, "# placeholder\n")
        _git(repo, "checkout", "-q", "ticket-branch")
        _git(repo, "merge", "-q", "main")
    suppress(repo, SUPPRESSION_PATTERNS[relpath])
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "attributes")
    _commit_agent_work(repo, relpath, body)

    result = _run_gate(gate, repo, "--base", "main")

    assert result.returncode == 1, _out(result)
    assert Path(relpath).name in _out(result), _out(result)


@pytest.mark.parametrize(("gate", "relpath", "body"), TRANSITION_GATES)
def test_a_mode_only_change_is_not_graded_whole(
    repo: Path, gate: list[str], relpath: str, body: str
) -> None:
    """The discriminator, from the other side — and the reason it is a discriminator.

    `chmod +x` reports `0\t0` in `--numstat` and emits no hunk either, so it is
    indistinguishable from a suppressed diff by "was there a hunk?" alone. A
    rule that graded every hunkless path whole would grade this file's existing
    contents and block the transition over a violation the branch never
    touched. Non-zero counts, and "is it an addition", are what keep it out.

    The violation is on `main`, so a run that reports it has over-reached.
    """
    _git(repo, "checkout", "-q", "main")
    _commit_agent_work(repo, relpath, body)
    _git(repo, "checkout", "-q", "-b", "chmod-branch")
    (repo / relpath).chmod(0o755)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "chmod")

    result = _run_gate(gate, repo, "--base", "main")

    assert result.returncode == 0, _out(result)
    match = EXAMINED_RE.search(_out(result))
    assert match is not None and int(match.group(1)) == 1, _out(result)


# --------------------------------------------------------------------------- #
# Failures that were loud but bypassed the one channel
# --------------------------------------------------------------------------- #

PY_GATES = [
    pytest.param(PY_ORGANIZATION_GATE, id="py-org"),
    pytest.param(PY_SILENT_EXCEPT_GATE, id="py-silent-except"),
]


@pytest.mark.parametrize("gate", PY_GATES)
def test_a_nul_byte_names_the_file_rather_than_crashing_the_gate(
    repo: Path, gate: list[str]
) -> None:
    """A `.py` a gate cannot parse must leave through the diagnosis, not a traceback.

    Which exception carries it is the interpreter's business and it has
    changed: CPython <= 3.10 raises `ValueError` for a NUL byte, which every
    gate's `except SyntaxError` missed, so the run died mid-walk with a stack
    trace instead of naming the file. 3.11 raises `SyntaxError`. Pinning the
    outcome rather than the exception is what makes this hold on both — see
    `test_a_parse_failure_that_is_not_a_syntax_error_is_unexaminable` for the
    branch this interpreter does not reach.
    """
    _commit_agent_work(repo, "src/pkg/new_mod.py", "x = 1\n\x00\n")

    result = _run_gate(gate, repo, "--base", "main")

    assert result.returncode != 0, _out(result)
    assert "Traceback" not in _out(result), _out(result)
    assert "new_mod.py" in _out(result), _out(result)
    assert "passed" not in _out(result), _out(result)


def test_a_parse_failure_that_is_not_a_syntax_error_is_unexaminable() -> None:
    """The `ValueError` branch, which this interpreter does not raise.

    `ast.parse` is not required to fail with `SyntaxError`, and on the
    interpreters where it does not, `except SyntaxError` let the failure out
    past the one channel every gate handles. Driven directly because a
    black-box repository cannot produce it here.
    """
    module = _load_script("precommit_git_diff")
    with mock.patch.object(module.ast, "parse", side_effect=ValueError("nope")):
        with pytest.raises(module.UnexaminableFileError) as caught:
            module.parse_python_source("x = 1\n", Path("src/pkg/new_mod.py"))
    assert "cannot be reported clean" in str(caught.value)


@pytest.mark.parametrize(("gate", "relpath", "body"), TRANSITION_GATES)
def test_non_utf8_content_is_reported_rather_than_crashing_the_gate(
    repo: Path, gate: list[str], relpath: str, body: str
) -> None:
    """A latin-1 byte anywhere in the diff took the whole run down.

    The diff carries the *content* of every file it touches, so one undecodable
    byte made `subprocess.run(text=True)` raise `UnicodeDecodeError` before any
    gate could say which file it was. The file that holds the bytes still fails
    — by name, through the same channel as every other unreadable file.
    """
    target = repo / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes("# caf\xe9\n".encode("latin-1") + body.encode())
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "agent work")

    result = _run_gate(gate, repo, "--base", "main")

    assert result.returncode != 0, _out(result)
    assert "Traceback" not in _out(result), _out(result)
    assert Path(relpath).name in _out(result), _out(result)


@pytest.mark.parametrize(("gate", "relpath", "body"), TRANSITION_GATES)
def test_a_submodule_bump_is_named_rather_than_silently_skipped(
    repo: Path, gate: list[str], relpath: str, body: str, tmp_path_factory
) -> None:
    """A gate cannot grade another repository — but it must not pretend it did.

    The parent diff lists only the gitlink, every language filter drops it (it
    is a directory), and the run printed `examined 0 file(s)` and exited 0 over
    a change nobody read. Failing on every pointer move would block transitions
    in any workspace that uses submodules for reasons unrelated to code
    quality, so the deliberate choice is to report: silent exit 0 is the one
    option that is wrong.
    """
    other = tmp_path_factory.mktemp("submodule")
    _git(other, "init", "-q", "-b", "main", ".")
    _git(other, "config", "user.email", "t@example.com")
    _git(other, "config", "user.name", "t")
    (other / "a.py").write_text("y = 1\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-qm", "s1")
    _git(
        repo,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        "-q",
        str(other),
        "vendor/sub",
    )
    _git(repo, "commit", "-qm", "add submodule")

    result = _run_gate(gate, repo, "--base", "main")

    assert "submodule" in _out(result), _out(result)
    assert "vendor/sub" in _out(result), _out(result)


# --------------------------------------------------------------------------- #
# Conformance: the `.cjs` is a hand-mirror of the `.py`, and drift is the bug
# --------------------------------------------------------------------------- #

#: Three of the nine instances of this defect found on this ticket were the
#: `.cjs` drifting from the `.py` — a fix landing in one and not the other, or
#: landing differently. Nothing shares code across the language boundary and
#: nothing can, so the mirror is held by this table instead: one repository
#: state, all three gates, and an assertion that they agree on *both* halves of
#: what a gate says — the verdict and the count. A gate that reads a different
#: number of files from its siblings under the same conditions has drifted,
#: whether or not its verdict happens to match today.
CONFORMANCE_PLANT = {
    "src/pkg/new_mod.py": PY_ORGANIZATION_VIOLATION + PY_SILENT_EXCEPT_VIOLATION,
    "client/src/new_mod.ts": TS_VIOLATION,
}


def _conformance_repo(repo: Path) -> None:
    for relpath, body in CONFORMANCE_PLANT.items():
        target = repo / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "agent work")


CONFORMANCE_MUTATIONS = [
    pytest.param(lambda repo: None, id="plain-commit"),
    pytest.param(lambda repo: (repo / "NOTES.txt").write_text("notes\n"), id="stray-untracked"),
    pytest.param(lambda repo: _suppress_via_diff_driver(repo, "*"), id="diff-driver"),
    pytest.param(lambda repo: _suppress_via_clean_filter(repo, "*"), id="clean-filter"),
    pytest.param(lambda repo: (repo / ".gitattributes").write_text("* -diff\n"), id="attr-nodiff"),
]


@pytest.mark.parametrize("mutate", CONFORMANCE_MUTATIONS)
def test_all_three_gates_agree_on_verdict_and_count(repo: Path, mutate) -> None:
    """The conformance table: one repo state, three gates, identical answers.

    Each scenario plants exactly one violating source file per language, so
    every gate has exactly one file of its own to grade and every gate should
    report `examined 1 file(s)` and exit 1. A `.cjs` that misses a fix the `.py`
    got — or vice versa — changes one of those two numbers, and this fails with
    the drift named, instead of the drift surviving to become the next instance.
    """
    _require_ts_parser()
    mutate(repo)
    _git(repo, "add", "-A")
    if _porcelain(repo).strip():
        _git(repo, "commit", "-qm", "setup")
    _conformance_repo(repo)

    verdicts = {}
    for gate, gate_id in (
        (PY_ORGANIZATION_GATE, "py-org"),
        (PY_SILENT_EXCEPT_GATE, "py-silent-except"),
        (TS_ORGANIZATION_GATE, "ts-org"),
    ):
        result = _run_gate(gate, repo, "--base", "main")
        match = EXAMINED_RE.search(_out(result))
        verdicts[gate_id] = (result.returncode, None if match is None else int(match.group(1)))

    # Identical verdict *and* identical count. The fixture plants exactly one
    # violating source file per language on top of one base file per language,
    # so the file counts are symmetric by construction and a difference is
    # drift, not a language difference.
    assert len(set(verdicts.values())) == 1, verdicts
    returncode, examined = next(iter(set(verdicts.values())))
    assert returncode == 1, verdicts
    assert examined is not None and examined >= 1, verdicts


def test_a_symlinked_source_is_refused_rather_than_followed(tmp_path):
    """A graded path that is not a regular file is unexaminable, not clean.

    `located_path` resolves only the parent on purpose, so a symlinked module
    keeps the relpath git used and stays gradeable. That leaves the file itself
    a symlink, and opening it follows the link: `src/x.py -> /dev/zero` never
    returns and allocates until the host gives out, which in orchestration is
    bounded only by the 300s gate timeout — a gate that stops being a gate. The
    check belongs at the read, where the target is known.
    """
    diff = _load_script("precommit_git_diff")
    os.symlink("/dev/zero", tmp_path / "leak.py")

    with pytest.raises(diff.UnexaminableFileError) as excinfo:
        diff.read_source_text(tmp_path / "leak.py")

    assert "not a regular file" in str(excinfo.value)


def test_a_broken_symlink_names_the_file_instead_of_raising_oserror(tmp_path):
    """A dangling link must arrive through the one channel, not as a traceback.

    Before the check it raised a bare `FileNotFoundError` out of the middle of
    the walk, which blocks every stage transition without naming what is wrong —
    loud, but unactionable, and it bypasses the `UnexaminableError` channel that
    is supposed to be the single place a gate decides about a file it could not
    examine.
    """
    diff = _load_script("precommit_git_diff")
    os.symlink(tmp_path / "gone.py", tmp_path / "broken.py")

    with pytest.raises(diff.UnexaminableFileError) as excinfo:
        diff.read_source_text(tmp_path / "broken.py")

    assert "broken.py" in str(excinfo.value)


def test_an_oversized_file_is_refused_rather_than_read_whole(tmp_path):
    """No hand-written module approaches the cap; a path that does is not source."""
    diff = _load_script("precommit_git_diff")
    big = tmp_path / "huge.py"
    big.write_bytes(b"# padding\n" * ((diff.MAX_SOURCE_BYTES // 10) + 1))

    with pytest.raises(diff.UnexaminableFileError) as excinfo:
        diff.read_source_text(big)

    assert "grading limit" in str(excinfo.value)


def test_an_ordinary_source_file_still_reads(tmp_path):
    """The positive control: the guard must not refuse real source."""
    diff = _load_script("precommit_git_diff")
    ok = tmp_path / "ok.py"
    ok.write_text("import os\n\n\ndef f():\n    return os.sep\n", encoding="utf-8")

    assert diff.read_source_text(ok).startswith("import os")
