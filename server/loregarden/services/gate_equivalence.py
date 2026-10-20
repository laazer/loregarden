"""Whether a configured gate command already does what a toolchain preset does.

The gates panel ticks a preset when the workspace runs it. Matching by string
missed every spelling but the preset's own: loregarden runs ``bash -c "cd
server && uv run ruff check ."`` where the preset reads ``uv run --directory
server ruff check``. The panel showed the preset unticked, and ticking it would
have added the same check to every stage transition a second time.

So both sides are reduced to a `CanonicalCommand` (the directory it runs in and
the argv it runs there) by a small, closed set of rules, one per spelling gate
commands are actually written in:

- ``bash -c``/``sh -c`` with a script of at most ``cd DIR && COMMAND``;
- ``uv run [--directory DIR] …`` and ``uvx …``: the tool they run;
- ``npm --prefix DIR run S`` (and pnpm ``--dir``, yarn/bun ``--cwd``): the
  script ``S`` from ``DIR/package.json``, when it is one plain command;
- ``npx [--prefix DIR] [--no-install|--yes|-y] TOOL …``: the tool, run where npx was;
- ``cargo … --manifest-path DIR/Cargo.toml`` and ``go -C DIR …``;
- a trailing ``.`` argument, which names the directory the command already runs in.

Anything outside those rules reduces to nothing and matches nothing. That is
the safe failure: an unrecognised spelling stays listed as the workspace's own
check, exactly as before, and is never mistaken for a preset.
"""

from __future__ import annotations

import logging
import posixpath
import shlex
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)

#: Spellings of other people's tools, not a vocabulary of ours.
_SHELL_SCRIPT_FLAGS = frozenset({"-c", "-lc"})  # py-org: allow-string
_NPX_FLAGS = frozenset({"--no-install", "--yes", "-y"})  # py-org: allow-string
#: Package managers and the flag each takes for the directory to run a script in.
_SCRIPT_RUNNERS = {
    "npm": "--prefix",  # py-org: allow-string
    "pnpm": "--dir",  # py-org: allow-string
    "yarn": "--cwd",  # py-org: allow-string
    "bun": "--cwd",  # py-org: allow-string
}
_WORKSPACE_ROOT = "{workspace_root}"
#: An npm script that runs ``npm run`` that runs ... stops here.
_MAX_DEPTH = 4


@dataclass(frozen=True)
class CanonicalCommand:
    #: Relative to the repository root, POSIX-style; "." for the root.
    directory: str
    argv: tuple[str, ...]


class _PackageJson(BaseModel):
    scripts: dict[str, str] = Field(default_factory=dict)


def _split(text: str) -> list[str] | None:
    try:
        return shlex.split(text)
    except ValueError:
        return None


def _join_dir(base: str, sub: str) -> str | None:
    """``sub`` taken from ``base``; None for anything that leaves the repository or is unresolved."""
    sub = sub.replace(f"{_WORKSPACE_ROOT}/", "").replace(_WORKSPACE_ROOT, ".")
    if "{" in sub or sub.startswith("/"):
        return None
    joined = posixpath.normpath(posixpath.join(base, sub))
    return None if joined.startswith("..") else joined


def _script_argv(script: str) -> list[str] | None:
    """A ``bash -c`` script as one command, unwrapping a leading ``cd DIR &&``.

    Returns ``["cd", DIR, *argv]`` or ``argv``; None for any other shell syntax.
    """
    lexer = shlex.shlex(script, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return None
    operators = [i for i, token in enumerate(tokens) if token and set(token) <= set("();<>|&")]
    if not operators:
        return tokens
    if (
        [tokens[i] for i in operators] == ["&&"] and operators[0] == 2 and tokens[0] == "cd"
    ):  # py-org: allow-string
        return ["cd", tokens[1], *tokens[3:]]  # py-org: allow-string
    return None


def _scripts(root: Path, directory: str) -> dict[str, str]:
    path = root / directory / "package.json"
    try:
        return _PackageJson.model_validate_json(path.read_text(encoding="utf-8")).scripts
    except FileNotFoundError:  # silent-ok: no package.json means no such script; "no match" is the true answer, not a failure
        return {}
    except (OSError, ValidationError) as exc:
        # The command then reduces to nothing: listed as the workspace's own, not merged.
        logger.warning("cannot read %s to compare gate commands: %s", path, exc)
        return {}


#: What a reducer returns: the command it unwraps to, and where that runs.
#: None means the spelling is not one the rules cover.
_Step = tuple[list[str], str] | None


def _moved(directory: str, rest: list[str], flag: str) -> tuple[str, list[str]] | None:
    """``rest`` with a leading ``flag DIR`` consumed into ``directory``; unchanged without one."""
    if rest[:1] != [flag]:
        return directory, rest
    moved = _join_dir(directory, rest[1]) if len(rest) > 1 else None
    return None if moved is None else (moved, rest[2:])


def _shell(rest: list[str], directory: str, _root: Path) -> _Step:
    if len(rest) != 2 or rest[0] not in _SHELL_SCRIPT_FLAGS:
        return None
    inner = _script_argv(rest[1])
    if inner is None or inner[:1] != ["cd"]:  # py-org: allow-string
        return None if inner is None else (inner, directory)
    moved = _join_dir(directory, inner[1]) if len(inner) > 1 else None
    return None if moved is None else (inner[2:], moved)


def _uv(rest: list[str], directory: str, _root: Path) -> _Step:
    if rest[:1] != ["run"]:  # py-org: allow-string
        return None
    step = _moved(directory, rest[1:], "--directory")  # py-org: allow-string
    return None if step is None else (step[1], step[0])


def _uvx(rest: list[str], directory: str, _root: Path) -> _Step:
    return rest, directory


def _npx(rest: list[str], directory: str, _root: Path) -> _Step:
    # `--prefix` picks whose node_modules the tool comes from; unlike
    # `npm --prefix … run`, it does not change the working directory.
    while rest and (rest[0] in _NPX_FLAGS or rest[0] == "--prefix"):  # py-org: allow-string
        rest = rest[2:] if rest[0] == "--prefix" else rest[1:]  # py-org: allow-string
    return rest, directory


def _script_runner(flag: str):
    def reduce(rest: list[str], directory: str, root: Path) -> _Step:
        step = _moved(directory, rest, flag)
        if step is None:
            return None
        directory, rest = step
        if rest[:1] != ["run"] or len(rest) < 2:  # py-org: allow-string
            return None
        body = _scripts(root, directory).get(rest[1])
        script = _script_argv(body) if body else None
        if script is None or script[:1] == ["cd"]:  # py-org: allow-string
            return None
        extra = rest[3:] if rest[2:3] == ["--"] else rest[2:]
        return [*script, *extra], directory

    return reduce


def _cargo(rest: list[str], directory: str, _root: Path) -> _Step:
    if "--manifest-path" not in rest:  # py-org: allow-string
        return None
    at = rest.index("--manifest-path")  # py-org: allow-string
    if at + 1 >= len(rest):
        return None
    moved = _join_dir(directory, posixpath.dirname(rest[at + 1]) or ".")
    return None if moved is None else (["cargo-in-dir", *rest[:at], *rest[at + 2 :]], moved)


def _go(rest: list[str], directory: str, _root: Path) -> _Step:
    step = _moved(directory, rest, "-C")  # py-org: allow-string
    return None if step is None else (["go-in-dir", *step[1]], step[0])


#: One reducer per wrapper a gate command is written with, keyed by its first word.
_REDUCERS = {
    "bash": _shell,  # py-org: allow-string
    "sh": _shell,  # py-org: allow-string
    "uv": _uv,  # py-org: allow-string
    "uvx": _uvx,  # py-org: allow-string
    "npx": _npx,  # py-org: allow-string
    "cargo": _cargo,  # py-org: allow-string
    "go": _go,  # py-org: allow-string
    **{runner: _script_runner(flag) for runner, flag in _SCRIPT_RUNNERS.items()},
}
#: Markers `_cargo`/`_go` leave so the tool is not unwrapped a second time.
_IN_DIR = {"cargo-in-dir": "cargo", "go-in-dir": "go"}  # py-org: allow-string


def _reduce(argv: list[str], directory: str, root: Path, depth: int) -> CanonicalCommand | None:
    """Unwrap ``argv`` one wrapper at a time until it is the tool itself."""
    if not argv or depth > _MAX_DEPTH:
        return None
    reducer = _REDUCERS.get(argv[0])
    if reducer is not None:
        step = reducer(argv[1:], directory, root)
        return None if step is None else _reduce(step[0], step[1], root, depth + 1)
    argv = [_IN_DIR.get(argv[0], argv[0]), *argv[1:]]
    if any("{" in token for token in argv):
        # An unfilled placeholder (a script path under {loregarden_root}): not a toolchain command.
        return None
    if len(argv) > 1 and argv[-1] == ".":
        argv = argv[:-1]
    return CanonicalCommand(directory=directory, argv=tuple(argv))


def canonical(command: str, root: Path) -> CanonicalCommand | None:
    """What ``command`` runs and where, for a repository at ``root``; None when the rules do not cover it."""
    argv = _split(command)
    return None if argv is None else _reduce(argv, ".", root, 0)


def running_equivalents(
    presets: Iterable[str], configured: Iterable[str], root: Path
) -> dict[str, str]:
    """Each preset the workspace already runs, mapped to the configured command that runs it.

    An exact match wins; otherwise the first configured command with the same
    canonical form. A preset the workspace does not run is absent.
    """
    configured = list(configured)
    exact = set(configured)
    forms = {command: canonical(command, root) for command in configured}
    found: dict[str, str] = {}
    for preset in presets:
        if preset in exact:
            found[preset] = preset
            continue
        form = canonical(preset, root)
        if form is None:
            continue
        match = next((command for command, other in forms.items() if other == form), None)
        if match is not None:
            found[preset] = match
    return found
