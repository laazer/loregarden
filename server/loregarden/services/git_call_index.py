"""Every place the code runs git or gh, and the command each one runs.

Answering "what moves an integration branch?" for lg-milestone-that-2624 meant
grepping for verbs across ten modules, then reading each hit to learn whether
it ran through a wrapper (`_git(repo, "update-ref", …)`) and who called that.
`py-git-subprocess` already routes every call through `run_git` / `run_gh`, so
the answer is static: this module reads it from the source.

It walks every module once, finds the calls into `run_git`, `run_gh` and a raw
`subprocess.run(["git", …])`, and follows wrappers: a module-level function
whose git argv takes one of its own parameters is a wrapper, its argv a
template, and each call to it fills the template from that call's arguments.
That repeats until no new wrapper appears, so a wrapper of a wrapper resolves
too. What is not a literal shows as `…` (`…*` for any number of tokens). A call
through `self.` or a dynamic name is not followed, and a wrapper with no caller
found is listed itself, so a command is never dropped for want of a caller.

    loregarden git calls --writes          # who moves or deletes a ref
    loregarden git calls --sub update-ref  # one subcommand
"""

from __future__ import annotations

import ast
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path


class GitTool(StrEnum):
    GIT = "git"
    GH = "gh"


class RefWrite(StrEnum):
    """The ref writes `py_ref_writers_check` keeps inside `integration_branch_moves`."""

    UPDATE_REF = "update-ref"
    BRANCH_FORCE = "branch -D/-d/-f"
    CHECKOUT_FORCE = "checkout -B"
    PUSH_FORCE = "push --force"
    GH_DELETE_REF = "gh api DELETE git/refs"


_Key = tuple[str, str]

#: The chokepoints `py-git-subprocess` routes every call through.
_SEEDS: dict[_Key, GitTool] = {
    ("loregarden.services.git_subprocess", "run_git"): GitTool.GIT,
    ("loregarden.services.git_subprocess", "run_gh"): GitTool.GH,
}
_TOOL_NAMES = frozenset(tool.value for tool in GitTool)
_SUBPROCESS_RUNNERS = frozenset({"run", "Popen", "call", "check_call", "check_output"})
#: Global git options that take the next token as their value.
_VALUED_OPTIONS = frozenset({"-C", "-c"})
_FORCE_BRANCH = frozenset({"-D", "-d", "--delete", "-f", "--force"})
_CHECKOUTS = frozenset({"checkout", "switch"})
_FORCE_CHECKOUT = frozenset({"-B", "-C"})
_SEQUENCE_BUILDERS = ("list", "tuple")
_UNKNOWN = "…"
_UNKNOWN_SPREAD = "…*"


def ref_write_kind(tokens: Sequence[str]) -> RefWrite | None:
    """What kind of ref write ``tokens`` spells: the gate's rules, kept equal by a test."""
    present = set(tokens)
    if "update-ref" in present:
        return RefWrite.UPDATE_REF
    if "branch" in present and present & _FORCE_BRANCH:
        return RefWrite.BRANCH_FORCE
    if present & _CHECKOUTS and present & _FORCE_CHECKOUT:
        return RefWrite.CHECKOUT_FORCE
    if "push" in present and ("-f" in present or any(t.startswith("--force") for t in tokens)):
        return RefWrite.PUSH_FORCE
    if "api" in present and "DELETE" in present and any("git/refs" in t for t in tokens):
        return RefWrite.GH_DELETE_REF
    return None


# --- argv templates -----------------------------------------------------------------------


@dataclass(frozen=True)
class _Lit:
    text: str


@dataclass(frozen=True)
class _Param:
    """One of the enclosing function's parameters, as one token."""

    name: str


@dataclass(frozen=True)
class _Splat:
    """One of the enclosing function's parameters, spread as any number of tokens."""

    name: str


@dataclass(frozen=True)
class _Unknown:
    spread: bool = False


_Element = _Lit | _Param | _Splat | _Unknown
_Template = tuple[_Element, ...]


def _literal(node: ast.expr) -> str | None:
    """A string literal's text; an f-string's literal head, marked as continuing."""
    match node:
        case ast.Constant(value=str() as text):
            return text
        case ast.JoinedStr(values=[ast.Constant(value=str() as head), *_]):
            return head + _UNKNOWN
    return None


def _element(node: ast.expr, params: frozenset[str]) -> _Element:
    text = _literal(node)
    if text is not None:
        return _Lit(text)
    match node:
        case ast.Name(id=name) if name in params:
            return _Param(name)
        case ast.Starred(value=ast.Name(id=name)) if name in params:
            return _Splat(name)
        case ast.Starred():
            return _Unknown(spread=True)
    return _Unknown()


def _sequence(node: ast.expr, params: frozenset[str]) -> _Template:
    """The tokens an argv expression spreads into: a list literal, `list(x)`, or a parameter."""
    match node:
        case ast.List(elts=elts) | ast.Tuple(elts=elts):
            return tuple(_element(elt, params) for elt in elts)
        case ast.Call(func=ast.Name(id=builder), args=[inner]) if builder in _SEQUENCE_BUILDERS:
            return _sequence(inner, params)
        case ast.Name(id=name) if name in params:
            return (_Splat(name),)
    return (_Unknown(spread=True),)


def _is_template(argv: _Template) -> bool:
    """Whether ``argv`` still takes the enclosing function's parameters."""
    for element in argv:
        match element:
            case _Param() | _Splat():
                return True
    return False


# --- modules ------------------------------------------------------------------------------


@dataclass
class _Call:
    node: ast.Call
    callee: _Key


@dataclass
class _Function:
    module: str
    qualname: str
    path: Path
    params: frozenset[str] = frozenset()
    positional: tuple[str, ...] = ()
    vararg: str | None = None
    #: Only a module-level function's callers can be found by name.
    wrappable: bool = False
    calls: list[_Call] = field(default_factory=list)

    @property
    def key(self) -> _Key:
        return (self.module, self.qualname)


def _module_name(relative: Path) -> str:
    parts = list(relative.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _imports(tree: ast.Module, module: str, is_package: bool) -> dict[str, _Key]:
    """Local name → (module, attribute) for `from … import`; a module alias → (module, "")."""
    names: dict[str, _Key] = {}
    package = module if is_package else module.rpartition(".")[0]
    for node in ast.walk(tree):
        match node:
            case ast.ImportFrom(module=source, names=aliases, level=level):
                base = package.rsplit(".", level - 1)[0] if level else ""
                origin = ".".join(p for p in (base, source or "") if p)
                for alias in aliases:
                    names[alias.asname or alias.name] = (origin, alias.name)
            case ast.Import(names=aliases):
                for alias in aliases:
                    local = alias.asname or alias.name.partition(".")[0]
                    names[local] = (alias.name if alias.asname else local, "")
    return names


def _top_level_defs(tree: ast.Module) -> set[str]:
    defs: set[str] = set()
    for node in tree.body:
        match node:
            case ast.FunctionDef(name=name) | ast.AsyncFunctionDef(name=name):
                defs.add(name)
    return defs


class _Collector(ast.NodeVisitor):
    """Every function in a module, each with the calls made directly in its body."""

    def __init__(self, module: str, path: Path, tree: ast.Module, is_package: bool) -> None:
        self.module = module
        self.imports = _imports(tree, module, is_package)
        self.defs = _top_level_defs(tree)
        self.path = path
        self.scope: list[str] = []
        self.stack = [_Function(module, "<module>", path)]
        self.functions = [self.stack[0]]

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        args = node.args
        positional = tuple(a.arg for a in [*args.posonlyargs, *args.args])
        vararg = args.vararg.arg if args.vararg else None
        named = [*positional, *(a.arg for a in args.kwonlyargs), *([vararg] if vararg else [])]
        function = _Function(
            module=self.module,
            qualname=".".join([*self.scope, node.name]),
            path=self.path,
            params=frozenset(named),
            positional=positional,
            vararg=vararg,
            wrappable=not self.scope,
        )
        self.functions.append(function)
        self.stack.append(function)
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()
        self.stack.pop()

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function

    def visit_Call(self, node: ast.Call) -> None:
        self.stack[-1].calls.append(_Call(node, self._callee(node.func)))
        self.generic_visit(node)

    def _callee(self, func: ast.expr) -> _Key:
        match func:
            case ast.Name(id=name) if name in self.defs:
                return (self.module, name)
            case ast.Name(id=name):
                return self.imports.get(name, ("", name))
            case ast.Attribute(value=ast.Name(id=owner), attr=attr) if owner in self.imports:
                origin, imported = self.imports[owner]
                return (f"{origin}.{imported}" if imported else origin, attr)
            case ast.Attribute(attr=attr):
                return ("", attr)
        return ("", "")


def _is_test(relative: Path) -> bool:
    return "tests" in relative.parts or relative.name.startswith("test_")


def _collect(root: Path, include_tests: bool) -> list[_Function]:
    """Every function under ``root``. A file that does not parse raises: it was not indexed."""
    functions: list[_Function] = []
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root.parent)
        if not include_tests and _is_test(relative):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        collector = _Collector(_module_name(relative), relative, tree, path.name == "__init__.py")
        collector.visit(tree)
        functions.extend(collector.functions)
    return functions


# --- resolving ----------------------------------------------------------------------------


@dataclass(frozen=True)
class _Run:
    """One command a call runs: its argv, and the `run_git`/`run_gh` line that runs it."""

    tool: GitTool
    via: str
    argv: _Template
    origin: str


def _split_args(call: ast.Call) -> tuple[list[ast.expr], list[ast.expr]]:
    plain: list[ast.expr] = []
    spread: list[ast.expr] = []
    for arg in call.args:
        match arg:
            case ast.Starred():
                spread.append(arg)
            case _:
                plain.append(arg)
    return plain, spread


def _bind(call: ast.Call, wrapper: _Function) -> dict[str, list[ast.expr]]:
    """Each of the wrapper's parameters → the argument expressions the call passes it."""
    plain, spread = _split_args(call)
    bound: dict[str, list[ast.expr]] = {}
    for name, arg in zip(wrapper.positional, plain, strict=False):
        bound[name] = [arg]
    if wrapper.vararg:
        bound[wrapper.vararg] = [*plain[len(wrapper.positional) :], *spread]
    for keyword in call.keywords:
        if keyword.arg:
            bound[keyword.arg] = [keyword.value]
    return bound


def _spread(args: list[ast.expr], params: frozenset[str]) -> _Template:
    """The arguments bound to a spread parameter: a vararg's values, or one whole argv."""
    match args:
        case [ast.List() | ast.Tuple() | ast.Call() as whole]:
            return _sequence(whole, params)
    return tuple(_element(arg, params) for arg in args)


def _fill(
    template: _Template, bound: dict[str, list[ast.expr]], params: frozenset[str]
) -> _Template:
    """The wrapper's template with the call's arguments in place of its parameters."""
    out: list[_Element] = []
    for element in template:
        match element:
            case _Param(name) if bound.get(name):
                out.append(_element(bound[name][0], params))
            case _Splat(name) if name in bound:
                out.extend(_spread(bound[name], params))
            case _Param():
                out.append(_Unknown())
            case _Splat():
                out.append(_Unknown(spread=True))
            case _:
                out.append(element)
    return tuple(out)


@dataclass
class _Resolver:
    by_key: dict[_Key, _Function]
    #: Each wrapper's commands, in the terms of its own parameters.
    wrappers: dict[_Key, tuple[_Run, ...]] = field(default_factory=dict)

    def runs(self, call: _Call, function: _Function) -> Iterator[_Run]:
        """Each command ``call`` runs, as an argv in the terms of ``function``'s parameters."""
        node, (origin, name) = call.node, call.callee
        here = f"{function.path}:{node.lineno}"
        if call.callee in _SEEDS:
            if node.args:
                argv = _sequence(node.args[0], function.params)
                yield _Run(_SEEDS[call.callee], name, argv, here)
        elif call.callee in self.wrappers:
            bound = _bind(node, self.by_key[call.callee])
            for run in self.wrappers[call.callee]:
                yield _Run(run.tool, name, _fill(run.argv, bound, function.params), run.origin)
        elif not node.args:
            return
        elif origin == "subprocess" and name in _SUBPROCESS_RUNNERS:
            yield from _raw_run(name, _sequence(node.args[0], function.params), here)

    def settle(self, functions: list[_Function]) -> None:
        """Find wrappers until a pass changes nothing; a wrapper of a wrapper takes a pass more."""
        for _ in range(len(functions) + 1):
            found: dict[_Key, tuple[_Run, ...]] = {}
            for function in functions:
                # Deduplicated, so a wrapper that calls itself settles; `via` is the
                # caller's to fill, so it is not part of what makes two the same.
                templated = tuple(
                    dict.fromkeys(
                        replace(run, via="")
                        for call in function.calls
                        for run in self.runs(call, function)
                        if _is_template(run.argv)
                    )
                )
                # A chokepoint's own `subprocess.run(["git", *args])` makes it one too:
                # `runs` reads a call to it as a seed first, and it is not listed itself.
                if function.wrappable and templated:
                    found[function.key] = templated
            if found == self.wrappers:
                return
            self.wrappers = found
        raise RuntimeError("git wrapper resolution did not settle")


def _raw_run(runner: str, argv: _Template, here: str) -> Iterator[_Run]:
    """`subprocess.run(["git", …])`: only when the first token names git or gh."""
    match argv:
        case (_Lit(text=tool), *rest) if tool in _TOOL_NAMES:
            yield _Run(GitTool(tool), f"subprocess.{runner}", tuple(rest), here)


# --- the index ----------------------------------------------------------------------------


@dataclass(frozen=True)
class GitCall:
    """One command the code runs, at the line that runs it."""

    path: str
    line: int
    function: str
    via: str
    tool: GitTool
    argv: tuple[str, ...]
    subcommand: str
    ref_write: RefWrite | None
    #: The `run_git`/`run_gh` line that runs it: this line, or inside the wrapper ``via``.
    runs_at: str


def _render(element: _Element) -> str:
    match element:
        case _Lit(text):
            return text
        case _Splat() | _Unknown(spread=True):
            return _UNKNOWN_SPREAD
    return _UNKNOWN


def _command(tokens: Sequence[str]) -> tuple[str, ...]:
    """``tokens`` from the subcommand on, past git's global options (`-C <path>`, `-c k=v`).

    The global `-C` is not `checkout -C`, so a ref write is read from here.
    """
    skip = False
    for at, token in enumerate(tokens):
        if skip or token in _VALUED_OPTIONS:
            skip = not skip
        elif not token.startswith("-"):
            return tuple(tokens[at:])
    return ()


def _subcommand(command: Sequence[str]) -> str:
    """The subcommand, or "" when it is not a literal."""
    if not command or command[0].startswith(_UNKNOWN):
        return ""
    return command[0]


def index_git_calls(root: Path, *, include_tests: bool = False) -> list[GitCall]:
    """Every git and gh command run under ``root``, a package directory, in path order.

    Paths and module names are relative to ``root``'s parent, so ``root`` must be
    the top-level package (``server/loregarden``) for imports to resolve.
    """
    functions = _collect(root, include_tests)
    resolver = _Resolver({f.key: f for f in functions})
    resolver.settle(functions)
    called = {call.callee for f in functions for call in f.calls}
    calls: list[GitCall] = []
    for function in functions:
        # A called wrapper's templated commands are listed at its callers, filled in.
        filled_elsewhere = function.key in resolver.wrappers and function.key in called
        for call in function.calls:
            for run in resolver.runs(call, function):
                if filled_elsewhere and _is_template(run.argv):
                    continue
                tokens = tuple(_render(e) for e in run.argv)
                command = _command(tokens)
                calls.append(
                    GitCall(
                        path=str(function.path),
                        line=call.node.lineno,
                        function=function.qualname,
                        via=run.via,
                        tool=run.tool,
                        argv=tokens,
                        subcommand=_subcommand(command),
                        ref_write=ref_write_kind(command),
                        runs_at=run.origin,
                    )
                )
    return sorted(calls, key=lambda c: (c.path, c.line, c.argv))
