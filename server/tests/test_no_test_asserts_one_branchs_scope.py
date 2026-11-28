"""No test may assert one branch's scope: a branch diff read from the real checkout states a rule.

#1031 landed `test_no_client_surface_is_added_by_this_change` and
`test_the_change_ships_no_migration`. They diffed the real checkout against
`origin/main` and asserted what lg-code-knowledge-1055 had not touched. True on
that ticket's branch; once it landed, every other branch was "this change", and
every PR that edited a client component failed CI on them until #1037 removed
both. A scope check on one change belongs to that change's review.

Reading the real checkout's branch against main is legitimate for a rule every
branch keeps, as `test_slow_test_ratchet` and `test_migration_registry` do. So
each such read must say which rule it is:

    # branch-rule: <the rule every branch keeps, in words a reviewer can check>

on its line or in the comment block directly above it. Like `silent-ok:`, the
reason is a claim the reviewer checks. One shape is refused outright: a read in
a module that names a ticket in a string constant, since a rule for every branch
has no ticket to name. That is how the two tests knew which branch was "this
change" (`TICKET = "lg-code-knowledge-1055"`).

A read counts when a git call compares a branch (a `...HEAD` / `HEAD..` range,
`merge-base`, `origin/main` or `SHIPPED_REF`) and its `cwd` is the real checkout
(`REPO_ROOT`, `OWN_CHECKOUT_ROOT`, or a local bound to one), directly or through
helpers that pass the path along, in any module under `server/tests`. A git call
in a throwaway repository is never one.

Not seen: a root reached some other way (a depth climb from `__file__`, an import
alias of `REPO_ROOT`), or a forwarding helper in a module that neither runs git
nor names the checkout. Both are shapes `repo_paths` exists to replace.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

import pytest
from tests.repo_paths import OWN_CHECKOUT_ROOT

TESTS = OWN_CHECKOUT_ROOT / "server" / "tests"
MARKER = "branch-rule:"
#: A reason needs at least this many words; "ok" or "needed" says nothing to check.
MIN_REASON_WORDS = 4
ROOT_NAMES = frozenset({"REPO_ROOT", "OWN_CHECKOUT_ROOT"})
#: String arguments that make a git call compare this branch with another.
BRANCH_ARG = re.compile(r"\.\.\.?HEAD\b|\bHEAD\.\.|^merge-base$|origin/main")
BRANCH_NAMES = frozenset({"SHIPPED_REF"})
TICKET_ID = re.compile(r"^[a-z]{2,4}-[a-z]+(?:-[a-z]+)*-\d+$")
#: A module that can take part in a read runs git or names the real checkout.
CAN_TAKE_PART = re.compile(r"run_git|[\"']git[\"']|\bREPO_ROOT\b|\bOWN_CHECKOUT_ROOT\b")
SUBPROCESS_CALLS = frozenset({"run", "check_output", "check_call", "call", "Popen"})


@dataclass(frozen=True)
class Read:
    """One place a test reads the real checkout's branch against main."""

    path: Path
    line: int
    reason: str | None

    def __str__(self) -> str:
        return f"{self.path.relative_to(TESTS)}:{self.line}"


def _name(node: ast.AST) -> str:
    if type(node) is ast.Name:
        return node.id
    if type(node) is ast.Attribute:
        return node.attr
    return ""


def _strings(node: ast.AST) -> list[str]:
    return [n.value for n in ast.walk(node) if type(n) is ast.Constant and type(n.value) is str]


def _compares_a_branch(call: ast.Call) -> bool:
    args = [*call.args, *(k.value for k in call.keywords if k.arg != "cwd")]
    for arg in args:
        if any(BRANCH_ARG.search(s) for s in _strings(arg)):
            return True
        if any(type(n) is ast.Name and n.id in BRANCH_NAMES for n in ast.walk(arg)):
            return True
    return False


def _is_git(call: ast.Call) -> bool:
    if _name(call.func) == "run_git":
        return True
    if _name(call.func) in SUBPROCESS_CALLS and call.args:
        first = call.args[0]
        return (
            type(first) is ast.List
            and bool(first.elts)
            and type(first.elts[0]) is ast.Constant
            and first.elts[0].value == "git"
        )
    return False


def _cwd(call: ast.Call) -> ast.AST | None:
    return next((k.value for k in call.keywords if k.arg == "cwd"), None)


def names_a_ticket(tree: ast.Module) -> bool:
    """Whether a string constant is a ticket id, whole: a docstring mentioning one is not."""
    return any(
        TICKET_ID.match(n.value)
        for n in ast.walk(tree)
        if type(n) is ast.Constant and type(n.value) is str
    )


def _functions(tree: ast.Module) -> list[ast.FunctionDef]:
    return [n for n in ast.walk(tree) if type(n) in (ast.FunctionDef, ast.AsyncFunctionDef)]


def _calls(node: ast.AST) -> list[ast.Call]:
    return [n for n in ast.walk(node) if type(n) is ast.Call]


def _params(fn: ast.FunctionDef) -> list[str]:
    return [a.arg for a in (*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs)]


def _passed_to(call: ast.Call, fn_params: list[str], positions: set[int]) -> list[ast.AST]:
    """The arguments `call` passes into the parameters at `positions` of a function."""
    wanted = {fn_params[i] for i in positions if i < len(fn_params)}
    got = [arg for i, arg in enumerate(call.args) if i in positions]
    got += [k.value for k in call.keywords if k.arg in wanted]
    return got


class Scan:
    """Every branch read from the real checkout across a set of modules.

    Two indexes, each a fixpoint over every function in every module, keyed by
    function name (a name defined twice is the union of both, which can only
    widen what counts):

    - a *runner* runs git in a repository it is handed: a parameter reaches a git
      call's `cwd`, directly or through another runner (`_git_out(root, *args)`);
    - a *reader* compares a branch in a repository it is handed: a branch git call
      gets that parameter as its repository (`paths_this_change_adds(root, ...)`).
    """

    def __init__(self, sources: dict[Path, str]) -> None:
        self.trees = {path: ast.parse(src) for path, src in sources.items()}
        self.lines = {path: src.splitlines() for path, src in sources.items()}
        self._branch_calls: dict[int, bool] = {}
        self.functions = {
            path: [(fn, _params(fn), _calls(fn)) for fn in _functions(tree)]
            for path, tree in self.trees.items()
        }
        self.runners: dict[str, tuple[list[str], set[int]]] = {}
        self.readers: dict[str, tuple[list[str], set[int]]] = {}
        self.reader_modules: dict[str, set[Path]] = {}
        self._fixpoint(self.runners, self._runs_git_in, None)
        self._fixpoint(self.readers, self._reads_branch_in, self.reader_modules)

    def _fixpoint(self, index, reaches, modules) -> None:
        changed = True
        while changed:
            changed = False
            for path, functions in self.functions.items():
                for fn, params, calls in functions:
                    found = {i for i, p in enumerate(params) if reaches(calls, p)}
                    known = index.get(fn.name, (params, set()))[1]
                    if found - known:
                        index[fn.name] = (params, known | found)
                        if modules is not None:
                            modules.setdefault(fn.name, set()).add(path)
                        changed = True

    def _repos(self, call: ast.Call, index) -> list[ast.AST]:
        """What `call` hands a function in `index` as its repository."""
        entry = index.get(_name(call.func))
        return [] if entry is None else _passed_to(call, *entry)

    def _git_repos(self, call: ast.Call) -> list[ast.AST]:
        """Where `call` runs git: its `cwd`, or what it hands a runner."""
        if _is_git(call):
            cwd = _cwd(call)
            return [] if cwd is None else [cwd]
        return self._repos(call, self.runners)

    def _branch_repos(self, call: ast.Call) -> list[ast.AST]:
        """Where `call` compares a branch: a git call with a branch argument, or a reader."""
        repos = self._git_repos(call)
        if repos:
            key = id(call)
            if key not in self._branch_calls:
                self._branch_calls[key] = _compares_a_branch(call)
            repos = repos if self._branch_calls[key] else []
        return repos + self._repos(call, self.readers)

    def _runs_git_in(self, calls: list[ast.Call], param: str) -> bool:
        return any(
            type(repo) is ast.Name and repo.id == param
            for call in calls
            for repo in self._git_repos(call)
        )

    def _reads_branch_in(self, calls: list[ast.Call], param: str) -> bool:
        return any(
            type(repo) is ast.Name and repo.id == param
            for call in calls
            for repo in self._branch_repos(call)
        )

    @staticmethod
    def _real_roots(scope: ast.AST) -> set[str]:
        """Names holding the real checkout in `scope`: the roots, and locals bound to them."""
        roots = set(ROOT_NAMES)
        for node in ast.walk(scope):
            if type(node) is ast.Assign and type(node.value) is ast.Name and node.value.id in roots:
                roots |= {t.id for t in node.targets if type(t) is ast.Name}
        return roots

    def reads(self) -> list[Read]:
        found = {}
        for path, tree in self.trees.items():
            if not any(name in "\n".join(self.lines[path]) for name in ROOT_NAMES):
                continue
            scopes = [(fn, calls) for fn, _params, calls in self.functions[path]]
            for scope, calls in [*scopes, (tree, _calls(tree))]:
                roots = self._real_roots(scope)
                for call in calls:
                    if any(
                        type(repo) is ast.Name and repo.id in roots
                        for repo in self._branch_repos(call)
                    ):
                        found[(path, call.lineno)] = Read(
                            path, call.lineno, self._reason(path, call.lineno)
                        )
        return sorted(found.values(), key=lambda r: (str(r.path), r.line))

    def _reason(self, path: Path, line: int) -> str | None:
        """The `branch-rule:` reason on `line` or in the comment block directly above it."""
        lines = self.lines[path]
        candidates = [lines[line - 1]]
        above = line - 2
        while above >= 0 and lines[above].lstrip().startswith("#"):
            candidates.append(lines[above])
            above -= 1
        for text in candidates:
            if MARKER in text:
                return text.split(MARKER, 1)[1].strip()
        return None

    def ticket_named_where(self, read: Read) -> list[Path]:
        """Modules involved in `read` that name a ticket: its own, and the readers it calls."""
        involved = {read.path}
        tree = self.trees[read.path]
        for call in (n for n in ast.walk(tree) if type(n) is ast.Call and n.lineno == read.line):
            involved |= self.reader_modules.get(_name(call.func), set())
        return sorted(p for p in involved if names_a_ticket(self.trees[p]))


def problems(scan: Scan) -> list[str]:
    out = []
    for read in scan.reads():
        tickets = scan.ticket_named_where(read)
        if tickets:
            named = ", ".join(str(p.relative_to(TESTS)) for p in tickets)
            out.append(
                f"{read}: reads this branch against main in a test that names a ticket "
                f"({named}); a scope check on one change belongs to its review, not the suite"
            )
        elif read.reason is None:
            out.append(f"{read}: reads this branch against main with no `# {MARKER}` reason")
        elif len(read.reason.split()) < MIN_REASON_WORDS:
            out.append(f"{read}: `# {MARKER} {read.reason}` does not state a rule")
    return out


def _scan_dir(root: Path) -> Scan:
    """Scan the modules that can take part: those running git, and those naming the checkout.

    A read happens where the checkout is named, through helpers that run git, so
    the other ~1,700 modules cannot hold one. Parsing all of them took 23s.
    """
    sources = {p: p.read_text(encoding="utf-8") for p in sorted(root.rglob("*.py"))}
    return Scan({p: s for p, s in sources.items() if CAN_TAKE_PART.search(s)})


def test_every_branch_read_in_the_suite_states_a_rule_for_every_branch():
    scan = _scan_dir(TESTS)
    assert problems(scan) == []
    # The two known rules are found, so a scan that matches nothing cannot pass.
    assert {r.path.name for r in scan.reads()} >= {
        "test_slow_test_ratchet.py",
        "test_migration_registry.py",
    }


# -- the analyzer, on the shapes it must and must not see --------------------------

HELPER_WITH_TICKET = """
TICKET = "lg-code-knowledge-1055"

def _git_out(root, *args):
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)

def paths_this_change_adds(root, prefixes):
    changed = _git_out(root, "diff", "--name-only", "origin/main...HEAD")
    return [p for p in changed.stdout.split() if p.startswith(prefixes)]
"""

TEST_CALLING_IT = """
def test_no_client_surface_is_added_by_this_change():
    root = REPO_ROOT
    assert paths_this_change_adds(root, ("client/src/",)) == []
"""


def _scan(**modules: str) -> Scan:
    return Scan({TESTS / f"{name}.py": src for name, src in modules.items()})


def test_the_removed_shape_is_refused_even_with_a_reason():
    """The #1031 pair: a helper naming its ticket, called with the real checkout."""
    marked = TEST_CALLING_IT.replace(
        "    assert", "    # branch-rule: no change adds a client surface here\n    assert"
    )
    for test in (TEST_CALLING_IT, marked):
        found = problems(_scan(helpers=HELPER_WITH_TICKET, test_x=test))
        assert len(found) == 1, found
        assert "names a ticket (helpers.py)" in found[0]


def test_a_direct_branch_read_needs_a_rule():
    src = """
def test_it():
    out = run_git(["diff", "--name-only", "origin/main...HEAD"], cwd=REPO_ROOT)
"""
    found = problems(_scan(test_x=src))
    assert [p.split(": ", 1)[1] for p in found] == [
        "reads this branch against main with no `# branch-rule:` reason"
    ]


@pytest.mark.parametrize(
    ("comment", "passes"),
    [
        ("# branch-rule: a merged migration id is never renamed or removed", True),
        ("# branch-rule: needed", False),
    ],
)
def test_a_rule_must_say_something(comment, passes):
    src = f"""
def test_it():
    {comment}
    base = run_git(["merge-base", "HEAD", SHIPPED_REF], cwd=REPO_ROOT)
"""
    assert (problems(_scan(test_x=src)) == []) is passes


def test_a_throwaway_repository_is_not_the_checkout():
    src = """
def test_it(tmp_path):
    repo = tmp_path / "r"
    run_git(["diff", "origin/main...HEAD"], cwd=repo)
    subprocess.run(["git", "merge-base", "HEAD", "main"], cwd=tmp_path)
    run_git(["ls-files"], cwd=REPO_ROOT)
"""
    assert _scan(test_x=src).reads() == []


def test_a_ticket_mentioned_in_a_docstring_is_not_a_ticket_constant():
    """Test docstrings name the ticket that added them everywhere; only a bare id names one."""
    src = '''
def test_it():
    """Added by lg-code-knowledge-1055."""
    # branch-rule: no branch adds an inherited slow test to the list
    run_git(["merge-base", "HEAD", SHIPPED_REF], cwd=REPO_ROOT)
'''
    assert problems(_scan(test_x=src)) == []
