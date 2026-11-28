"""The gate that validates `ceiling:` markers: a knowingly-insufficient code site.

Spec `6d686e57` R6 (AC11) and R7.4. A ceiling is NOT a waiver. A waiver asserts
a rule should not apply; a ceiling asserts the code is knowingly insufficient,
names the quantity at which it stops holding, and names the upgrade path. That
quantity is what makes the member closable later, which is the whole reason the
class is enumerable at all — "this is simplified" can only accumulate.

Required shape, on the line or in the comment block directly above it:

    ceiling: <quantity at which this stops holding> — upgrade: <the path>

These tests pin both directions. Too loose and `# ceiling: this is simplified`
passes, which is the shape the marker exists to refuse. Too strict and a real
bound stated in words is rejected and the next author reaches for a waiver.

The gate is NOT a detector of unmarked shortcuts (ticket *Out of scope*): it
validates markers that exist.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest
from tests.repo_paths import REPO_ROOT

_ROOT = REPO_ROOT
_SCRIPTS = _ROOT / ".lefthook" / "scripts"
_CHECKER_PATH = _SCRIPTS / "py_ceiling_marker_check.py"
_DISPATCHER = _SCRIPTS / "workspace-gates.sh"
_LEFTHOOK = _ROOT / "lefthook.yml"

_SCRUBBED_ENV = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}


def _load_checker():
    spec = importlib.util.spec_from_file_location("py_ceiling_marker_check", _CHECKER_PATH)
    module = importlib.util.module_from_spec(spec)
    # Registered before exec, as the silent-except gate's loader does: @dataclass
    # resolves annotations through sys.modules[cls.__module__].
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


def _write(tmp_path: Path, source: str, name: str = "service.py") -> Path:
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return path


def _violations(tmp_path: Path, source: str) -> list:
    return checker.violations_in(_write(tmp_path, source), repo=None)


# --------------------------------------------------------------------------- #
# R6.1, R6.2 — a well-formed ceiling passes
# --------------------------------------------------------------------------- #

ACCEPTED = {
    # R6.1 verbatim. The named-bound set must therefore recognise "one process"
    # as a spelling of `single process`; see the test-design finding recorded on
    # this ticket. A bound stated in words, not digits, is the point of the set.
    "named_bound_one_process": """
def counter():
    # ceiling: one process — upgrade: move the counter into the artifacts table
    return _count
""",
    # R6.2 verbatim: a digit-bearing quantity.
    "numeric_quantity": """
def join_all(tickets):
    # ceiling: 500 tickets — upgrade: paginate the join
    return [t for t in tickets]
""",
    "named_bound_global_lock": """
def acquire():
    # ceiling: global lock — upgrade: a per-ticket advisory lock keyed on ticket_id
    return _LOCK.acquire()
""",
    "named_bound_quadratic": """
def pairs(items):
    # ceiling: O(n^2) — upgrade: index the left side and probe it once per right row
    return [(a, b) for a in items for b in items]
""",
    "named_bound_n_log_n": """
def ranked(items):
    # ceiling: O(n log n) — upgrade: bucket by kind so the sort is per group
    return sorted(items)
""",
    "named_bound_single_host": """
def lease():
    # ceiling: single host — upgrade: move the lease into the docker capacity ledger
    return _lease
""",
    # The marker may sit on the code line itself, not only above it.
    "trailing_on_the_code_line": """
def scan(paths):
    return [p for p in paths]  # ceiling: 2000 files — upgrade: shard the scan by directory
""",
    # ASCII `--` stands in for the em dash: an author on a keyboard without one
    # must not be pushed into a waiver.
    "ascii_double_hyphen_separator": """
def join_all(tickets):
    # ceiling: 500 tickets -- upgrade: paginate the join
    return list(tickets)
""",
    # R6.6 first half: the comment block directly above the code line.
    "multi_line_comment_block_directly_above": """
def join_all(tickets):
    # The join loads every row because the reader needs them all at once.
    # ceiling: 500 tickets — upgrade: paginate the join and merge per page
    return list(tickets)
""",
    # A file with no marker at all is not this gate's business: it is not a
    # detector of unmarked shortcuts (Out of scope).
    "no_marker_at_all": """
def anything(x):
    return x * 2
""",
}


@pytest.mark.parametrize("name", sorted(ACCEPTED))
def test_a_well_formed_ceiling_passes(tmp_path, name):
    assert _violations(tmp_path, ACCEPTED[name]) == [], f"{name} should be accepted"


# --------------------------------------------------------------------------- #
# R6.3, R6.4, R6.5, R6.6 — the refusals
# --------------------------------------------------------------------------- #


def test_a_ceiling_with_no_quantity_is_refused(tmp_path):
    """R6.3. "this is simplified" is exactly the shape the marker replaces."""
    source = """
def thing(x):
    # ceiling: this is simplified — upgrade: do it properly later
    return x
"""
    violations = _violations(tmp_path, source)
    assert violations, "a ceiling with no quantity must be refused"
    assert "quantity" in " ".join(message for _, message in violations).lower()


def test_a_ceiling_with_no_upgrade_clause_is_refused(tmp_path):
    """R6.4. A ceiling with no upgrade path records a dead end, not a debt."""
    source = """
def join_all(tickets):
    # ceiling: 500 tickets
    return list(tickets)
"""
    violations = _violations(tmp_path, source)
    assert violations, "a ceiling with no upgrade clause must be refused"
    assert "upgrade:" in " ".join(message for _, message in violations)


def test_a_thin_upgrade_clause_is_refused(tmp_path):
    """R6.4's floor: an upgrade path under MIN_ANSWER is a placeholder."""
    source = """
def join_all(tickets):
    # ceiling: 500 tickets — upgrade: tbd
    return list(tickets)
"""
    assert _violations(tmp_path, source), "a placeholder upgrade path must be refused"


@pytest.mark.parametrize(
    "waiver",
    [
        "silent-ok: best effort",
        "py-silent: allow - best effort",
        "py-org: allow-string",
        "ux-ok: decorative",
        "theme-ok: third-party widget",
        "motion-ok: ambient loop",
        "type-ok: library option takes pixels",
    ],
)
def test_a_waiver_is_not_a_ceiling(tmp_path, waiver):
    """R6.5. AC11 turns on a ceiling NOT being a waiver; the gate says so."""
    source = f"""
def thing(x):
    # ceiling: {waiver} — upgrade: rewrite the reader so the branch cannot be reached
    return x
"""
    violations = _violations(tmp_path, source)
    assert violations, f"a {waiver!r} ceiling must be refused"
    assert "waiver" in " ".join(message for _, message in violations).lower()


def test_an_unattached_marker_is_refused(tmp_path):
    """R6.6 second half: two blank lines mean the marker names no code site.

    An unattached ceiling is unresolvable as `path/to/file.py:<line>`, which is
    the form the enumeration requires of a CODE_SITE member (R2.2).
    """
    source = """
def join_all(tickets):
    # ceiling: 500 tickets — upgrade: paginate the join and merge per page


    return list(tickets)
"""
    violations = _violations(tmp_path, source)
    assert violations, "a marker detached from its code line must be refused"
    message = " ".join(text for _, text in violations).lower()
    assert "attach" in message or "unattached" in message or "code line" in message


def test_the_violation_points_at_the_marker_line(tmp_path):
    """A gate that names the wrong line costs the reader the diagnosis."""
    source = """
def join_all(tickets):
    # ceiling: this is simplified — upgrade: do it properly later
    return list(tickets)
"""
    violations = _violations(tmp_path, source)
    assert [lineno for lineno, _ in violations] == [3]


# --------------------------------------------------------------------------- #
# Invocation modes — the two every gate in GATES accepts
# --------------------------------------------------------------------------- #


def test_bare_file_list_is_precommit_mode():
    inv = checker.parse_gate_argv(["src/pkg/mod.py"])
    assert inv.diff_scope == "staged"
    assert inv.label == "pre-commit"
    assert [p.name for p in inv.files] == ["mod.py"]


def test_repo_and_scope_flags_are_gate_mode():
    inv = checker.parse_gate_argv(["--repo", "/tmp/ws", "--scope", "worktree"])
    assert inv.diff_scope == "worktree"
    assert inv.label == "gate"
    assert inv.repo == Path("/tmp/ws").resolve()


# --------------------------------------------------------------------------- #
# R6.9 — diff scoping, exercised against a real repository
# --------------------------------------------------------------------------- #


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=_SCRUBBED_ENV)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", ".")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    return tmp_path


def _run_gate(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_CHECKER_PATH), *args],
        cwd=repo,
        capture_output=True,
        text=True,
        env=_SCRUBBED_ENV,
    )


_BAD = """def join_all(tickets):
    # ceiling: this is simplified — upgrade: do it properly later
    return list(tickets)
"""

_GOOD = """def join_all(tickets):
    # ceiling: 500 tickets — upgrade: paginate the join and merge per page
    return list(tickets)
"""


def test_precommit_flags_a_staged_marker(repo: Path):
    src = repo / "src" / "pkg"
    src.mkdir(parents=True)
    (src / "mod.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")

    (src / "mod.py").write_text(_BAD, encoding="utf-8")
    _git(repo, "add", "-A")

    result = _run_gate(repo, "src/pkg/mod.py")
    assert result.returncode == 1
    assert "src/pkg/mod.py:2" in result.stdout
    assert "ceiling:" in result.stdout


def test_inherited_non_conforming_markers_do_not_fail_an_unrelated_commit(repo: Path):
    """R6.9. The rule is "don't add another", not "fix the tree on sight".

    A gate that blocks on debt the author never wrote teaches waiving by reflex,
    which is precisely what AC11 says a ceiling must not become.
    """
    src = repo / "src" / "pkg"
    src.mkdir(parents=True)
    (src / "inherited.py").write_text(_BAD, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "inherited debt")

    (src / "other.py").write_text(_GOOD, encoding="utf-8")
    _git(repo, "add", "-A")

    result = _run_gate(repo, "src/pkg/other.py", "src/pkg/inherited.py")
    assert result.returncode == 0, result.stdout


def test_worktree_scope_sees_an_uncommitted_marker(repo: Path):
    """Orchestration gates run `--scope worktree`: an agent's edits are uncommitted."""
    src = repo / "src" / "pkg"
    src.mkdir(parents=True)
    (src / "mod.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    (src / "mod.py").write_text(_BAD, encoding="utf-8")

    result = _run_gate(repo, "--repo", str(repo), "--scope", "worktree")
    assert result.returncode == 1, result.stdout
    assert "ceiling:" in result.stdout


# --------------------------------------------------------------------------- #
# R6.7, R6.8 — registered in BOTH places, or it runs in one of three
# --------------------------------------------------------------------------- #


def test_the_gate_is_in_the_workspace_gates_list():
    """R6.7. `WG`'s GATES array is what every other workspace runs."""
    listed = subprocess.run(
        ["bash", str(_DISPATCHER), "--list"],
        capture_output=True,
        text=True,
        check=True,
        env=_SCRUBBED_ENV,
    ).stdout.split()
    assert "py_ceiling_marker_check.py" in listed


def test_the_dispatcher_runs_the_gate_over_this_worktree():
    """R6.8. The ranked risk is registering in lefthook.yml only."""
    result = subprocess.run(
        ["bash", str(_DISPATCHER), "--repo", str(_ROOT), "--scope", "worktree"],
        capture_output=True,
        text=True,
        check=False,
        env=_SCRUBBED_ENV,
    )
    assert "py_ceiling_marker_check" in (result.stdout + result.stderr)


def test_the_gate_is_registered_in_lefthook():
    """R6.6's other half of the double registration: pre-commit in this repo."""
    assert "py_ceiling_marker_check.py" in _LEFTHOOK.read_text(encoding="utf-8")


def test_the_gate_script_is_inside_the_python_glob():
    """The gate grades itself, as py_organization_check.py does."""
    text = _LEFTHOOK.read_text(encoding="utf-8")
    assert ".lefthook/scripts/*.py" in text


# --------------------------------------------------------------------------- #
# R7.4 — the scan shells git only through run_git
# --------------------------------------------------------------------------- #


def test_the_package_shells_git_only_through_run_git():
    """R7.4. GIT_DIR beats cwd, so a raw `subprocess.run(["git", ...])` in the
    code-site scan can enumerate the wrong repository entirely."""
    package = _ROOT / "server" / "loregarden" / "services" / "asserted_not_proven"
    sources = sorted(package.glob("*.py"))
    assert sources, "the asserted_not_proven package should exist"

    result = subprocess.run(
        [sys.executable, str(_SCRIPTS / "py_git_subprocess_check.py"), *map(str, sources)],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        env=_SCRUBBED_ENV,
    )
    assert result.returncode == 0, result.stdout + result.stderr


# --------------------------------------------------------------------------- #
# ADVERSARIAL — the corners above leave open
#
# Why these: a textual line scan is the obvious implementation of this gate, and
# it is the one that cannot be committed. Every file in this suite carries
# deliberately malformed `ceiling:` markers inside string literals, and so does
# the checker's own error-message text. A gate that matches on the line rather
# than on the comment fails its own change, which is discovered at `git commit`
# and not here.
# --------------------------------------------------------------------------- #


_IN_A_STRING = '''
BAD_EXAMPLE = "# ceiling: this is simplified"
MESSAGE = "a ceiling: needs a quantity and an upgrade: clause"


def render():
    """Docstring mentioning a # ceiling: this is simplified — upgrade: later."""
    return BAD_EXAMPLE
'''


def test_a_marker_inside_a_string_literal_is_not_a_marker(tmp_path):
    """The marker is a COMMENT (R6: "on the line or in the comment block
    directly above it"). A gate that scans raw lines flags the checker's own
    message text, this suite's fixtures, and every doctest that quotes one.
    """
    assert _violations(tmp_path, _IN_A_STRING) == []


@pytest.mark.parametrize(
    "name",
    [
        "test_ceiling_marker_gate.py",
        "test_asserted_not_proven.py",
        "test_asserted_not_proven_adversarial.py",
        "test_asserted_not_proven_listing.py",
        "asserted_not_proven_helpers.py",
    ],
)
def test_the_gate_passes_on_the_suites_that_quote_it(name):
    """The decisive case of the one above: this change cannot land if its own
    tests trip it. The pre-commit glob is `server/**/*.py`, which is these."""
    path = _ROOT / "server" / "tests" / name
    if not path.exists():
        pytest.skip(f"{name} is not in this checkout")
    assert checker.violations_in(path, repo=None) == []


def test_the_gate_passes_on_its_own_source():
    """The checker names `ceiling:` in its own strings and error messages."""
    assert checker.violations_in(_CHECKER_PATH, repo=None) == []


def test_every_violation_in_a_file_is_reported_not_only_the_first(tmp_path):
    """A gate that returns on the first failure costs one commit per marker,
    and the author fixes them one at a time without ever seeing the count."""
    source = """
def one(x):
    # ceiling: this is simplified — upgrade: do it properly later
    return x


def two(x):
    # ceiling: 500 tickets
    return x


def three(x):
    # ceiling: silent-ok: best effort — upgrade: rewrite the reader entirely
    return x
"""
    assert [lineno for lineno, _ in _violations(tmp_path, source)] == [3, 8, 13]


def test_crlf_line_endings_do_not_break_a_well_formed_marker(tmp_path):
    """A stray `\\r` lands at the end of the upgrade clause and inside the
    separator split. A Windows-authored file must not be told its ceiling is
    malformed, and a malformed one must not pass because the parse fell over.
    """
    good = "def join_all(tickets):\r\n    # ceiling: 500 tickets — upgrade: paginate the join and merge per page\r\n    return list(tickets)\r\n"
    path = tmp_path / "crlf.py"
    path.write_bytes(good.encode("utf-8"))
    assert checker.violations_in(path, repo=None) == []

    bad = "def join_all(tickets):\r\n    # ceiling: this is simplified — upgrade: do it properly later\r\n    return list(tickets)\r\n"
    path.write_bytes(bad.encode("utf-8"))
    assert checker.violations_in(path, repo=None)


def test_an_undecodable_file_does_not_crash_the_gate(tmp_path):
    """A crashed gate greps as zero findings.

    This repo has met that exact failure: a checker that raises under the hook
    reports nothing and reads as clean. Whatever this does with a file it
    cannot decode, it must not raise.
    """
    path = tmp_path / "binary.py"
    path.write_bytes(
        "# ceiling: 500 tickets — upgrade: paginate the join\nx = 1\n".encode() + b"\xff\xfe\n"
    )
    result = checker.violations_in(path, repo=None)
    assert isinstance(result, list)


def test_an_empty_file_is_not_a_violation(tmp_path):
    assert _violations(tmp_path, "") == []


def test_the_gate_is_deterministic_on_the_same_input(tmp_path):
    """A gate whose output varies between runs turns a red into a dice roll,
    which is the one thing this repo will not retry its way out of."""
    source = """
def one(x):
    # ceiling: this is simplified — upgrade: do it properly later
    return x


def two(x):
    # ceiling: 500 tickets
    return x
"""
    runs = [_violations(tmp_path, source) for _ in range(3)]
    assert runs[0] == runs[1] == runs[2]


@pytest.mark.parametrize(
    "clause",
    ["# ceiling: 500 tickets — upgrade:", "# ceiling: 500 tickets —", "# ceiling:", "# ceiling: "],
    ids=["empty-upgrade", "separator-only", "bare-marker", "bare-marker-space"],
)
def test_a_marker_with_nothing_after_it_is_refused(tmp_path, clause):
    """R6.3 and R6.4 at their floor: an empty clause is not a short clause."""
    source = f"""
def join_all(tickets):
    {clause}
    return list(tickets)
"""
    assert _violations(tmp_path, source), f"{clause!r} must be refused"


def test_a_marker_above_a_decorated_definition_is_attached(tmp_path):
    """R6.6. A decorator is a code line; a marker above one names a real site."""
    source = """
import functools


@functools.cache
def ranked(items):
    # ceiling: O(n log n) — upgrade: bucket by kind so the sort is per group
    return sorted(items)
"""
    assert _violations(tmp_path, source) == []


def test_a_marker_at_the_end_of_the_file_with_no_code_under_it_is_refused(tmp_path):
    """The other shape of R6.6's unattached marker: nothing follows it at all,
    so it is unresolvable as `path/to/file.py:<line>` (R2.2)."""
    source = """
def join_all(tickets):
    return list(tickets)


# ceiling: 500 tickets — upgrade: paginate the join and merge per page
"""
    assert _violations(tmp_path, source), "a trailing marker names no code site"
