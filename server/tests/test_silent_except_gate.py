"""The lint gate that refuses exception handlers which catch everything and say nothing.

The gate has to stay narrow in both directions. Too loose and a `pass` slips
through, so a caller reports success it never had. Too strict and it flags the
handlers that already surface the failure — recording it on a result, handing it
back to the caller — and the next agent learns to waive it by reflex. These
tests pin both halves.
"""

import ast
import importlib.util
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from tests.repo_paths import REPO_ROOT

_SCRIPTS = REPO_ROOT / ".lefthook" / "scripts"
_CHECKER_PATH = _SCRIPTS / "py_silent_except_check.py"


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    # Registered before exec: @dataclass resolves annotations through
    # sys.modules[cls.__module__], which is None for an unregistered module.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


checker = _load_script("py_silent_except_check")


#: The label every unclassified finding opens with, from the criterion's own
#: words ("unclassified construct at <file>:<line>"). Spelled literally here
#: rather than imported from `.lefthook/scripts/gate_unclassified.py` so that
#: this file's existing coverage does not go dark while that module is being
#: written; `test_gate_unclassified.py` pins the two to the same string.
UNCLASSIFIED_LABEL = "unclassified construct at"


def _write(tmp_path: Path, source: str, name: str = "service.py") -> Path:
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return path


FLAGGED = {
    # A conditional `raise` only covers the branch that takes it (658).
    "conditional_raise_leaves_the_other_path_quiet": """
import logging
logger = logging.getLogger(__name__)

def f(raw, root):
    try:
        return resolve(raw)
    except ValueError as exc:
        if "not allowed" in str(exc):
            raise
        logger.debug("did not resolve; falling back: %s", exc)
        return root
""",
    "conditional_raise_with_success_shaped_return": """
def load(raw) -> list[str]:
    try:
        return parse(raw)
    except ValueError as exc:
        if fatal(exc):
            raise
        return []
""",
    "observation_inside_a_branch_does_not_cover_the_fallthrough": """
import logging
logger = logging.getLogger(__name__)

def f(raw, root):
    try:
        return resolve(raw)
    except ValueError as exc:
        if noisy:
            logger.warning("did not resolve: %s", exc)
        logger.debug("falling back")
        return root
""",
    # --- rules folded in from the silent-failure audit (658) ---
    "quiet_debug_only": """
import logging
logger = logging.getLogger(__name__)

def f(path):
    try:
        return read(path)
    except OSError:
        logger.debug("could not read %s", path)
""",
    "quiet_info_only": """
import logging
logger = logging.getLogger(__name__)

def f(path):
    try:
        return read(path)
    except OSError:
        logger.info("could not read %s", path)
""",
    "success_shaped_empty_list": """
def allowlist(raw: str) -> list[str]:
    try:
        return parse(raw)
    except ValueError:
        return []
""",
    "success_shaped_false": """
def has_conflicts(repo) -> bool:
    try:
        return detect(repo)
    except OSError:
        return False
""",
    "discarded_run_git": """
from loregarden.services.git_subprocess import run_git

def sync(repo):
    run_git(["fetch", "origin"], cwd=repo, check=False)
""",
    "discarded_subprocess_run": """
import subprocess

def sync(repo):
    subprocess.run(["rsync", "-a", "src", "dst"], cwd=repo)
""",
    "waiver_with_no_reason": """
def warm(path):
    try:
        return read(path)
    except FileNotFoundError:  # silent-ok:
        pass
""",
    "waiver_with_throwaway_reason": """
def warm(path):
    try:
        return read(path)
    except FileNotFoundError:  # py-silent: allow - nope
        pass
""",
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
    # Path analysis must not over-fire: these all observe on every way out (658).
    "unconditional_reraise_after_a_quiet_log": """
import logging
logger = logging.getLogger(__name__)

def f(raw):
    try:
        return resolve(raw)
    except ValueError as exc:
        logger.debug("context: %s", exc)
        raise
""",
    "warning_before_a_conditional_raise": """
import logging
logger = logging.getLogger(__name__)

def f(raw, root):
    try:
        return resolve(raw)
    except ValueError as exc:
        logger.warning("did not resolve: %s", exc, exc_info=True)
        if fatal(exc):
            raise
        return root
""",
    "every_branch_observes": """
import logging
logger = logging.getLogger(__name__)

def f(raw, root):
    try:
        return resolve(raw)
    except ValueError as exc:
        if fatal(exc):
            raise
        logger.warning("did not resolve: %s", exc, exc_info=True)
        return root
""",
    "closure_logging_does_not_count_but_the_raise_does": """
def f(raw):
    try:
        return resolve(raw)
    except ValueError:
        raise
""",
    # --- counterparts for the folded-in rules (658) ---
    "optional_contract_is_not_a_lie": """
def try_parse(name: str) -> "Action | None":
    try:
        return Action(name)
    except ValueError:
        return None
""",
    "unannotated_narrow_return_is_left_alone": """
def f():
    try:
        return work()
    except FileNotFoundError:
        return None
""",
    "warning_beats_debug": """
import logging
logger = logging.getLogger(__name__)

def f(path):
    try:
        return read(path)
    except OSError:
        logger.warning("could not read %s", path, exc_info=True)
""",
    "waiver_with_a_real_reason": """
def warm(path):
    try:
        return read(path)
    except FileNotFoundError:  # silent-ok: best-effort warm; the next read repopulates
        pass
""",
    "waiver_reason_may_sit_above": """
from loregarden.services.git_subprocess import run_git

def sync(repo):
    # silent-ok: pruning is idempotent and best-effort; the next call prunes again
    run_git(["worktree", "prune"], cwd=repo, check=False)
""",
    "process_result_kept": """
from loregarden.services.git_subprocess import run_git

def sync(repo) -> bool:
    proc = run_git(["fetch", "origin"], cwd=repo, check=False)
    return proc.returncode == 0
""",
    "not_a_subprocess_run": """
import uvicorn

def serve():
    uvicorn.run("app:app", host="127.0.0.1", port=8000)
""",
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
    inv = checker.parse_gate_argv(["src/pkg/mod.py"])
    assert inv.diff_scope == "staged"
    assert inv.label == "pre-commit"
    assert [p.name for p in inv.files] == ["mod.py"]


def test_repo_and_scope_flags_are_gate_mode():
    inv = checker.parse_gate_argv(["--repo", "/tmp/ws", "--scope", "worktree"])
    assert inv.diff_scope == "worktree"
    assert inv.label == "gate"
    assert inv.repo == Path("/tmp/ws").resolve()
    assert inv.files == []


def test_unknown_scope_is_carried_through_to_be_refused_not_coerced():
    """See the organization gate's twin: a coerced scope read the empty index."""
    assert checker.parse_gate_argv(["--scope", "nonsense"]).diff_scope == "nonsense"


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

_REPO_ROOT = REPO_ROOT


def test_lefthook_runs_the_gate_pre_commit():
    lefthook = (_REPO_ROOT / "lefthook.yml").read_text(encoding="utf-8")
    assert "py_silent_except_check.py {staged_files}" in lefthook


def test_the_workspace_dispatcher_runs_the_gate():
    """Other workspaces' pre-commit and every orchestration profile reach this
    gate through `workspace-gates.sh`; its list is the only one."""
    listed = subprocess.run(
        ["bash", str(_REPO_ROOT / ".lefthook" / "scripts" / "workspace-gates.sh"), "--list"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert "py_silent_except_check.py" in listed


def test_every_orchestration_profile_runs_the_gate():
    """It rides with the organization gates, `default.yaml` included — a
    workspace with no profile of its own still gets the rule."""
    profiles = sorted((_REPO_ROOT / "agent_context" / "orchestration").glob("*.yaml"))
    assert profiles, "no orchestration profiles found"
    for profile in profiles:
        text = profile.read_text(encoding="utf-8")
        if "{loregarden_root}/.lefthook/scripts/" not in text:
            continue  # lore-eden runs its own copies
        assert "workspace-gates.sh --repo {workspace_root} --scope worktree" in text, profile.name


def test_workspace_hook_installer_ships_the_gate():
    """Other workspaces get it pre-commit too, from this checkout."""
    installer_path = _REPO_ROOT / "scripts" / "install_workspace_hooks.py"
    spec = importlib.util.spec_from_file_location("install_workspace_hooks", installer_path)
    installer = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = installer
    spec.loader.exec_module(installer)

    block = "\n".join(installer.render_block(_REPO_ROOT, ""))
    assert "workspace-gates.sh {staged_files}" in block


def test_real_services_are_clean():
    """The invariant holds across the actual server tree, not just fixtures.

    `repo=` is the real root, not None — see the same test in
    test_git_subprocess_gate.py. These two are the only tests that traverse a
    real repository tree, so they are where the read guard meets real source
    rather than fixtures (594).
    """
    repo_root = REPO_ROOT
    services = Path(__file__).resolve().parents[1] / "loregarden"
    offenders = [
        f"{py}:{lineno}"
        for py in services.rglob("*.py")
        for lineno, _ in checker.violations_in(py, repo=repo_root)
    ]
    assert offenders == []


# --------------------------------------------------------------------------- #
# A construct the walk reaches and no arm names (lg-code-knowledge-1084)
#
# `_paths_through` tests `Raise`, then `Return/Break/Continue`, then `If`, then
# `_CONTROL_FLOW_STMTS`, and then FALLS THROUGH to
# `nxt.add(state or _statement_observes(stmt))` — so a compound statement the
# tuple does not name is read as a plain statement, and a quiet path inside it
# is reported as observed because a sibling arm logs. `_nested_blocks` carries a
# duplicate of the same closed set.
#
# The arm must be NAMED rather than widened: a nested `def` is NOT-APPLICABLE by
# a named arm (its body does not run on the handler's path), and making it
# unclassified would fire the gate on ordinary code.
# --------------------------------------------------------------------------- #


def _unclassified(violations: list[tuple[int, str]]) -> list[tuple[int, str]]:
    return [v for v in violations if UNCLASSIFIED_LABEL in v[1]]


#: The statement-owning `ast.stmt` nodes the handler-body walk already names.
#: Written out because they are the criterion's arm table, not a derivation.
NAMED_ARMS = {"If", "Try", "For", "AsyncFor", "While", "With", "AsyncWith"}

#: A nested def or class is the subject of a named arm too — a NOT-APPLICABLE
#: one. Its body does not run on the handler's path (`_statement_observes`
#: already skips it), so it must never become an unclassified finding.
SCOPE_BOUNDARIES = {"FunctionDef", "AsyncFunctionDef", "ClassDef"}

_ASDL = re.compile(r"^\s*(?:\w+\s*=\s*)?(\w+)\((.*)\)\s*$", re.S)


def _grammar_fields(node_cls) -> list[tuple[str, str, str]] | None:
    """(type, cardinality, name) per field, read from CPython's own ASDL signature.

    `ast.Try.__doc__` is literally `Try(stmt* body, excepthandler* handlers,
    stmt* orelse, stmt* finalbody)` — the grammar, generated by CPython, and
    therefore a source INDEPENDENT of the gate's own helper. A test that asked
    the production helper which nodes own statements would prove nothing.

    Deliberately NOT `"body" in type(stmt)._fields`: that finds `TryStar` and
    MISSES `Match`, whose fields are `(subject, cases)` with the statements
    hanging off `match_case.body`. The helper and the test would then share one
    blind spot and the suite would pass with the defect intact.
    """
    match = _ASDL.match((node_cls.__doc__ or "").strip())
    if match is None:
        return None
    fields: list[tuple[str, str, str]] = []
    for part in match.group(2).split(","):
        part = part.strip()
        if not part:
            continue
        tokens = part.split()
        kind = tokens[0]
        name = tokens[1] if len(tokens) > 1 else ""
        cardinality = ""
        if kind[-1:] in "*?":
            cardinality, kind = kind[-1], kind[:-1]
        fields.append((kind, cardinality, name))
    return fields


def _concrete(kind: str):
    base = getattr(ast, kind, None)
    if not (isinstance(base, type) and issubclass(base, ast.AST)):
        return None
    subclasses = base.__subclasses__()
    return base if not subclasses else sorted(subclasses, key=lambda c: c.__name__)[0]


def _reaches_statements(node_cls, seen: set | None = None) -> bool:
    """Whether statements are reachable from this node, through helper nodes too."""
    seen = set() if seen is None else seen
    if node_cls in seen:
        return False
    seen.add(node_cls)
    for kind, _cardinality, _name in _grammar_fields(node_cls) or []:
        if kind == "stmt":
            return True
        base = getattr(ast, kind, None)
        if not (isinstance(base, type) and issubclass(base, ast.AST)):
            continue
        for sub in base.__subclasses__() or [base]:
            if _reaches_statements(sub, seen):
                return True
    return False


def _all_stmt_subclasses() -> set[type]:
    found: set[type] = set()
    stack = list(ast.stmt.__subclasses__())
    while stack:
        node_cls = stack.pop()
        found.add(node_cls)
        stack.extend(node_cls.__subclasses__())
    return found


def _statement_owning_nodes() -> set[type]:
    return {c for c in _all_stmt_subclasses() if _reaches_statements(c)}


def _unhandled_nodes() -> list[type]:
    """The grammar's statement-owning nodes, minus every named arm.

    Derived at TEST TIME, so a grammar node added by a future CPython becomes a
    pinned case here with no edit to a list.
    """
    left_over = [
        node_cls
        for node_cls in _statement_owning_nodes()
        if node_cls.__name__ not in NAMED_ARMS | SCOPE_BOUNDARIES
    ]
    return sorted(left_over, key=lambda c: c.__name__)


_PRIMITIVES = {"identifier": "x", "string": None, "int": 0, "constant": None, "bool": False}


def _value_for(kind: str, cardinality: str):
    if cardinality == "?":
        # Optional expressions are filled: `except*:` with no type is a
        # SyntaxError, so an unfilled TryStar would synthesize unparseable code.
        return ast.Name(id="x", ctx=ast.Load()) if kind == "expr" else None
    if kind == "stmt":
        value = ast.Pass()
    elif kind == "expr":
        value = ast.Name(id="x", ctx=ast.Load())
    elif kind in _PRIMITIVES:
        value = _PRIMITIVES[kind]
        return [value] if cardinality == "*" else value
    else:
        node_cls = _concrete(kind)
        if node_cls is None:
            return None
        value = _build(node_cls)
    return [value] if cardinality == "*" else value


def _build(node_cls):
    return node_cls(**{n: _value_for(k, c) for k, c, n in (_grammar_fields(node_cls) or [])})


def _synthesize(node_cls) -> str:
    """The smallest well-formed source for this grammar node, built from the grammar.

    Generic rather than a per-node table, for the same reason the case list is
    derived: a future node must not need an edit here either.
    """
    module = ast.Module(body=[_build(node_cls)], type_ignores=[])
    ast.fix_missing_locations(module)
    return ast.unparse(module)


#: `except Exception as exc:` is line 7 and the construct starts on line 8.
_HANDLER_LINE = 7
_CONSTRUCT_LINE = 8


def _in_a_handler(snippet: str) -> str:
    """The snippet inside a handler that otherwise reports its failure properly.

    The handler logs at warning and falls through, so the ONLY thing this gate
    can legitimately say about it is whatever the snippet provokes.
    """
    return (
        "import logging\n"
        "logger = logging.getLogger(__name__)\n"
        "\n"
        "def f(path):\n"
        "    try:\n"
        "        return read(path)\n"
        "    except Exception as exc:\n"
        f"{textwrap.indent(snippet, ' ' * 8)}\n"
        '        logger.warning("read failed: %s", exc)\n'
    )


def test_the_derived_case_list_is_the_grammar_minus_the_named_arms():
    """On CPython 3.11 exactly two statement-owning nodes have no arm."""
    names = {node_cls.__name__ for node_cls in _unhandled_nodes()}
    assert {"TryStar", "Match"} <= names, names


def test_the_derivation_finds_match_which_the_fields_shortcut_misses():
    """The blind spot this derivation exists to avoid, asserted directly."""
    assert "body" not in ast.Match._fields
    assert ast.Match in _statement_owning_nodes()


def test_the_named_arms_and_scope_boundaries_are_all_statement_owning():
    """If an arm's node stopped owning statements the arm table would be stale."""
    owning = {node_cls.__name__ for node_cls in _statement_owning_nodes()}
    assert NAMED_ARMS | SCOPE_BOUNDARIES <= owning


@pytest.mark.parametrize("node_cls", _unhandled_nodes(), ids=lambda node_cls: node_cls.__name__)
def test_every_unnamed_grammar_node_in_a_handler_is_named_unclassified(tmp_path, node_cls):
    snippet = _synthesize(node_cls)
    source = _in_a_handler(snippet)
    # A synthesis that does not parse would make this test prove nothing while
    # looking green, so it fails here rather than downstream.
    ast.parse(source)

    path = _write(tmp_path, source)
    findings = _unclassified(checker.violations_in(path, repo=None))

    assert len(findings) == 1, f"{node_cls.__name__}: {findings}"
    lineno, message = findings[0]
    assert lineno == _HANDLER_LINE, "anchored at the enclosing handler (AC5)"
    assert node_cls.__name__ in message, "the message names the node type"
    assert f"{path}:{_CONSTRUCT_LINE}" in message, "the message names the construct's own line"


def test_the_finding_is_anchored_where_the_touched_line_filter_can_see_it(tmp_path):
    """The anchor is the handler's lineno — the one `violations_in` already
    returns and `_check`'s touched-line filter already accepts. Anchoring at the
    construct's own line would silently change what the gate examines, which is
    the opposite of this ticket.
    """
    path = _write(tmp_path, _in_a_handler(_synthesize(ast.Match)))
    ((lineno, message),) = _unclassified(checker.violations_in(path, repo=None))

    assert lineno == _HANDLER_LINE
    assert f":{_CONSTRUCT_LINE}" in message
    assert lineno != _CONSTRUCT_LINE, "the anchor and the named line are deliberately different"


def test_a_construct_nested_in_a_named_arm_is_still_named(tmp_path):
    """`_nested_blocks` carries a duplicate of the same closed set, so the
    second walk gets the same treatment as the first."""
    source = _in_a_handler(
        "for item in items:\n    " + _synthesize(ast.Match).replace("\n", "\n    ")
    )
    path = _write(tmp_path, source)

    assert len(_unclassified(checker.violations_in(path, repo=None))) == 1


def test_the_unclassified_finding_fails_the_gate(tmp_path):
    """Printing while exiting 0 is this ticket's own defect applied to its fix."""
    path = _write(tmp_path, _in_a_handler(_synthesize(ast.Match)))
    assert checker.main([str(path)]) == 1


# --------------------------------------------------------------------------- #
# AC7a — the constructs the gate DOES name produce nothing new
# --------------------------------------------------------------------------- #

NAMED_BY_AN_ARM = {
    "an if with no else": "if ready:\n    cleanup()",
    "a nested for": "for item in items:\n    cleanup(item)",
    "a nested while": "while pending():\n    drain()",
    "a nested with": "with lock:\n    cleanup()",
    "a nested try": "try:\n    cleanup()\nexcept ValueError:\n    raise",
    "a nested def": "def later():\n    return None",
    "a nested async def": "async def later():\n    return None",
    "a nested class": "class Later:\n    pass",
    "a plain statement": "count = 1",
    "an expression": "cleanup()",
    "a comprehension": "kept = [x for x in items if x]",
}


@pytest.mark.parametrize("name", sorted(NAMED_BY_AN_ARM))
def test_a_named_construct_produces_no_unclassified_finding(tmp_path, name):
    path = _write(tmp_path, _in_a_handler(NAMED_BY_AN_ARM[name]))
    assert _unclassified(checker.violations_in(path, repo=None)) == [], name


@pytest.mark.parametrize("name", sorted(NAMED_BY_AN_ARM))
def test_a_named_construct_leaves_todays_verdict_unchanged(tmp_path, name):
    """The handler logs at warning and is clean today; it stays clean."""
    path = _write(tmp_path, _in_a_handler(NAMED_BY_AN_ARM[name]))
    assert checker.violations_in(path, repo=None) == [], name


def test_a_nested_def_is_not_applicable_rather_than_unclassified(tmp_path):
    """Collapsing NOT-APPLICABLE into UNCLASSIFIED would fire on ordinary code.

    A closure declared in a handler runs when it is called, which may be never —
    the reason `_statement_observes` already skips it. That is a named answer,
    not a missing one.
    """
    source = _in_a_handler('def later():\n    logger.warning("late")\n    return None')
    path = _write(tmp_path, source)
    assert checker.violations_in(path, repo=None) == []


def test_a_silent_handler_is_still_flagged_for_being_silent(tmp_path):
    """The new arm must not displace the finding the gate already makes."""
    source = (
        "def f(path):\n    try:\n        return read(path)\n    except Exception:\n        pass\n"
    )
    path = _write(tmp_path, source)
    violations = checker.violations_in(path, repo=None)

    assert len(violations) == 1
    assert _unclassified(violations) == []


# --------------------------------------------------------------------------- #
# AC5 / AC8 — both entry points, and the live tree
# --------------------------------------------------------------------------- #


def test_the_staged_precommit_path_fails_on_an_unclassifiable_construct(repo: Path):
    src = _commit_base(repo)
    (src / "mod.py").write_text(_in_a_handler(_synthesize(ast.Match)), encoding="utf-8")
    _git(repo, "add", "-A")

    result = _run_gate(repo, "src/pkg/mod.py")
    assert result.returncode == 1, result.stdout
    assert UNCLASSIFIED_LABEL in result.stdout
    assert "Match" in result.stdout


def test_the_worktree_gate_path_fails_on_an_unclassifiable_construct(repo: Path):
    src = _commit_base(repo)
    (src / "mod.py").write_text(_in_a_handler(_synthesize(ast.TryStar)), encoding="utf-8")

    result = _run_gate(repo, "--repo", str(repo), "--scope", "worktree")
    assert result.returncode == 1, result.stdout
    assert UNCLASSIFIED_LABEL in result.stdout
    assert "TryStar" in result.stdout


def test_the_live_tree_holds_no_unclassifiable_constructs():
    """Measured, not assumed: a count in the hundreds means the recognizer is
    wrong, not that the tree is. Zero was measured at spec time over these same
    three trees, so the new arm fires on nothing that exists today.
    """
    repo_root = REPO_ROOT
    # Named explicitly rather than `server/`: `server/.venv` is thousands of
    # third-party files written for other interpreters, and walking it would
    # measure something other than this repository.
    trees = [
        repo_root / "server" / "loregarden",
        repo_root / "server" / "tests",
        repo_root / ".lefthook" / "scripts",
        repo_root / "scripts",
    ]

    offenders = [
        f"{py}:{lineno}: {message}"
        for tree in trees
        for py in sorted(tree.rglob("*.py"))
        if "__pycache__" not in py.parts
        for lineno, message in _unclassified(checker.violations_in(py, repo=repo_root))
    ]
    assert offenders == []


# --------------------------------------------------------------------------- #
# AC1 / AC3 / AC5 — the recognizer is only as wide as the walk it rides on
#
# `_paths_through` walks handler bodies and the blocks `_nested_blocks` returns.
# It descends into neither a `_SCOPE_STMTS` body (a nested def or class, whose
# statements do not run on the handler's path) nor the `try` the handler belongs
# to. Every construct below is one this gate PARSES and that walk never reaches,
# so it is NOT-APPLICABLE and must produce nothing at all.
#
# These are the tests an `ast.walk(handler)` implementation fails while passing
# every positive case above, and an `ast.walk(tree)` one fails hardest. That is
# the ticket's R2 — a recognizer written wide, so both gates start firing on
# ordinary code — and it is only catchable from the negative side.
# --------------------------------------------------------------------------- #


def _module(body: str) -> str:
    return "import logging\nlogger = logging.getLogger(__name__)\n\n" + textwrap.dedent(body)


#: Each holds an `ast.Match` (the construct the positive cases prove IS named
#: when the walk reaches it) somewhere the walk does not go.
OUT_OF_THE_WALK_S_REACH = {
    "in a nested def inside the handler": _module(
        """
        def f(path):
            try:
                return read(path)
            except Exception as exc:
                def later():
                    match path:
                        case _:
                            return None
                logger.warning("read failed: %s", exc)
        """
    ),
    "in a nested async def inside the handler": _module(
        """
        def f(path):
            try:
                return read(path)
            except Exception as exc:
                async def later():
                    match path:
                        case _:
                            return None
                logger.warning("read failed: %s", exc)
        """
    ),
    "in a nested class inside the handler": _module(
        """
        def f(path):
            try:
                return read(path)
            except Exception as exc:
                class Later:
                    match path:
                        case _:
                            kind = 1
                logger.warning("read failed: %s", exc)
        """
    ),
    "in the try body the handler guards": _module(
        """
        def f(path):
            try:
                match path:
                    case _:
                        return read(path)
            except Exception as exc:
                logger.warning("read failed: %s", exc)
        """
    ),
    "in the try's else clause": _module(
        """
        def f(path):
            try:
                value = read(path)
            except Exception as exc:
                logger.warning("read failed: %s", exc)
            else:
                match value:
                    case _:
                        return value
        """
    ),
    "in the try's finally clause": _module(
        """
        def f(path):
            try:
                return read(path)
            except Exception as exc:
                logger.warning("read failed: %s", exc)
            finally:
                match path:
                    case _:
                        close(path)
        """
    ),
    "in a function with no handler at all": _module(
        """
        def f(path):
            match path:
                case _:
                    return read(path)
        """
    ),
    "at module level": _module(
        """
        match __name__:
            case _:
                CONFIGURED = True
        """
    ),
    "in a sibling function, in a file that has a handler": _module(
        """
        def f(path):
            try:
                return read(path)
            except Exception as exc:
                logger.warning("read failed: %s", exc)

        def g(path):
            match path:
                case _:
                    return None
        """
    ),
}


@pytest.mark.parametrize("name", sorted(OUT_OF_THE_WALK_S_REACH))
def test_a_construct_the_walk_never_reaches_is_not_applicable(tmp_path, name):
    source = OUT_OF_THE_WALK_S_REACH[name]
    ast.parse(source)  # a case that does not parse would prove nothing while green
    path = _write(tmp_path, source)

    assert _unclassified(checker.violations_in(path, repo=None)) == [], name


@pytest.mark.parametrize("name", sorted(OUT_OF_THE_WALK_S_REACH))
def test_a_construct_the_walk_never_reaches_leaves_the_verdict_untouched(tmp_path, name):
    """Not merely "no unclassified finding": no finding. Each handler here
    reports its failure at warning level and is clean today."""
    path = _write(tmp_path, OUT_OF_THE_WALK_S_REACH[name])
    assert checker.violations_in(path, repo=None) == [], name


def test_a_construct_in_a_nested_def_is_still_named_when_that_def_has_its_own_handler(tmp_path):
    """The complement, so the test above cannot be satisfied by ignoring every
    construct that has a `def` above it: the nested def's OWN handler body is
    walked, and the construct in it is a subject."""
    source = _module(
        """
        def f(path):
            def later():
                try:
                    return read(path)
                except Exception as exc:
                    match path:
                        case _:
                            pass
                    logger.warning("read failed: %s", exc)
            return later
        """
    )
    path = _write(tmp_path, source)
    assert len(_unclassified(checker.violations_in(path, repo=None))) == 1


# --------------------------------------------------------------------------- #
# AC1 — "each must become an unclassified finding", counted
# --------------------------------------------------------------------------- #


def test_two_unclassifiable_constructs_in_one_handler_are_both_named(tmp_path):
    """A finding deduplicated per handler names the first and loses the second,
    which is this ticket's defect with a smaller blast radius."""
    source = _module(
        """
        def f(path):
            try:
                return read(path)
            except Exception as exc:
                match path:
                    case _:
                        pass
                try:
                    close(path)
                except* OSError:
                    pass
                logger.warning("read failed: %s", exc)
        """
    )
    path = _write(tmp_path, source)
    findings = _unclassified(checker.violations_in(path, repo=None))

    assert len(findings) == 2, findings
    named = " ".join(message for _lineno, message in findings)
    assert "Match" in named
    assert "TryStar" in named


def test_two_handlers_each_get_their_own_finding_at_their_own_anchor(tmp_path):
    """Anchored at the ENCLOSING handler, so two handlers cannot collapse onto
    one line — the touched-line filter would then drop a staged finding."""
    source = _module(
        """
        def f(path):
            try:
                return read(path)
            except Exception as exc:
                match path:
                    case _:
                        pass
                logger.warning("first: %s", exc)

        def g(path):
            try:
                return read(path)
            except Exception as exc:
                match path:
                    case _:
                        pass
                logger.warning("second: %s", exc)
        """
    )
    path = _write(tmp_path, source)
    findings = _unclassified(checker.violations_in(path, repo=None))

    assert len(findings) == 2, findings
    assert len({lineno for lineno, _ in findings}) == 2, findings


def test_the_same_file_reports_the_same_findings_twice(tmp_path):
    """Determinism: a gate whose output depends on set iteration order makes a
    pre-commit failure impossible to reproduce from the message."""
    path = _write(tmp_path, _in_a_handler(_synthesize(ast.Match)))
    assert checker.violations_in(path, repo=None) == checker.violations_in(path, repo=None)


def test_an_unclassifiable_construct_does_not_displace_the_silent_finding(tmp_path):
    """A handler can be both silent AND hold a construct the gate cannot grade.
    Reporting one instead of the other loses a finding either way.
    """
    source = _module(
        """
        def f(path):
            try:
                return read(path)
            except Exception:
                match path:
                    case _:
                        pass
        """
    )
    path = _write(tmp_path, source)
    violations = checker.violations_in(path, repo=None)

    assert len(_unclassified(violations)) == 1, violations
    assert len(violations) == 2, "the silent handler is still reported for being silent"


# --------------------------------------------------------------------------- #
# AC5 — the finding is diff-scoped the same way every other finding is
# --------------------------------------------------------------------------- #


def test_precommit_leaves_an_untouched_unclassifiable_construct_alone(repo: Path):
    """The anchor test proves the lineno; this proves the filter actually uses it.

    An unclassifiable construct committed before this gate grew its arm is
    inherited debt. A gate that blocks a commit over it blocks every workspace's
    next commit, which is how a correct recognizer still ships as an outage.
    """
    src = repo / "src" / "pkg"
    src.mkdir(parents=True)
    (src / "mod.py").write_text(_in_a_handler(_synthesize(ast.Match)), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "inherited debt")

    (src / "mod.py").write_text(
        _in_a_handler(_synthesize(ast.Match)) + "\n\ndef g():\n    return 1\n", encoding="utf-8"
    )
    _git(repo, "add", "-A")

    result = _run_gate(repo, "src/pkg/mod.py")
    assert result.returncode == 0, result.stdout


def test_an_unclassifiable_construct_in_a_test_file_is_exempt(tmp_path):
    """Tests hold deliberately odd constructs; the gate already exempts them (in
    `main`, like every other finding) and this arm must not be the one exception
    that reaches in."""
    path = _write(tmp_path, _in_a_handler(_synthesize(ast.Match)), name="test_thing.py")
    assert checker.main([str(path)]) == 0
