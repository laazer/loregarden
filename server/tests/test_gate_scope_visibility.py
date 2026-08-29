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

import os
import re
import subprocess
import sys
from pathlib import Path

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


@pytest.mark.xfail(
    strict=True,
    reason="gate mode confines discovered files to the Python source root, so a "
    "gate script an agent edits mid-stage is never graded — implement owns this",
)
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
    """The surface that matters for agents, and the one still open.

    A transition gate discovers its own file list from the diff, and
    `python_files_in_scope` then drops everything outside the detected source
    root — `server/` here. An agent that edits `.lefthook/scripts/*.py` during a
    stage therefore has that edit gated by nothing at all, which is precisely
    the vacuous-pass shape this ticket exists to close, one directory over.
    """
    _commit_agent_work(repo, ".lefthook/scripts/planted_check.py", body)

    result = _run_gate(gate, repo, "--base", "main")

    assert result.returncode == 1, _out(result)
    assert "planted_check.py" in _out(result)
