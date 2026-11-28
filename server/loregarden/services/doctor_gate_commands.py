"""Resolve every command a workspace's orchestration profile configures to run.

`check_gate_commands_resolve` answers "can this workspace's configured commands
even start?". It used to read two of the four places a profile names a command,
and stopped at `parts[0]`, so `sh ci/scripts/land_verify.sh server` resolved
clean on the strength of `sh` while the script was missing
(lmkr-execution-leaves-97: eleven stages done, then the land verifier exited 127).

The resolver is its own module, like `doctor_docker`, because `doctor.py` is near
its size cap and because it needs no session or workspace: a profile and a root in,
the commands that cannot start out.

What is examined, per command:

- a plain executable: `parts[0]` against `repo_root` when it has a `/`, else on PATH;
- an interpreter (`sh`, `bash`, `python3`, `node`, ...): the interpreter on PATH, and
  the first non-flag token after it as a script path against `repo_root`. Tokens
  after the script belong to the script and are never read;
- `uv run [flags] <target>`: `uv` on PATH, then the target against
  `repo_root/<--directory>`. An interpreter target is resolved as above except that
  it is not looked up on PATH (uv takes it from the project venv); a target with a
  `/` is a path; a bare name (`pytest`) is a venv console script and is not looked
  up at all.

Tokens containing `{` or `$`, or starting with `~`, are placeholders or
shell-expanded and are neither resolved nor reported.

Known gaps, not modelled because the check does not own their semantics:
interpreter flags that take a separate value (`python3 -W error x.py`,
`bash -o pipefail x.sh`, `node -r mod x.js`) read the value as the script; env
wrappers (`env X=1 cmd`) and `cd x && y` compounds are not valid forms, since
commands run with no shell. Read-only: nothing is executed.
"""

from __future__ import annotations

import os
import shlex
import shutil
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from loregarden.services.orchestration_profile import OrchestrationProfile

#: The reason reported for a command `shlex` cannot tokenise.
UNPARSEABLE = "unparseable"

#: Interpreter -> the letters that, in a short-flag cluster (`-c`, `-lc`), make the
#: rest of the command line code or a module name rather than a script path.
#: `bash -e` is errexit, so `e` is inline only for node.
_INLINE_FLAG_LETTERS: dict[str, str] = {
    "sh": "c",
    "bash": "c",
    "zsh": "c",
    "dash": "c",
    "python": "cm",
    "python3": "cm",
    "node": "ep",
}
_NODE_INLINE_LONG_FLAGS = frozenset({"--eval", "--print"})

#: `uv run` flags whose next token is their value, not the target.
_UV_VALUE_FLAGS = frozenset(
    {
        "--directory",
        "--project",
        "--python",
        "-p",
        "--with",
        "-w",
        "--with-requirements",
        "--package",
        "--extra",
        "--group",
        "--env-file",
        "--index",
        "--config-file",
    }
)


class GateCommandSource(StrEnum):
    """The profile fields that configure a command, named as an operator edits them."""

    COMMANDS = "gates.commands"
    TRANSITION_SCRIPT = "gates.transition_script"
    LAND_VERIFY_COMMANDS = "git.land_verify_commands"
    CRITERION_TEST_COMMANDS = "gates.criterion_test_commands"


@dataclass(frozen=True)
class ConfiguredCommand:
    source: GateCommandSource
    command: str


@dataclass(frozen=True)
class UnresolvedCommand:
    source: GateCommandSource
    command: str
    targets: tuple[str, ...]

    def describe(self) -> str:
        return f"{self.source}: `{self.command}` -> {', '.join(self.targets)}"


def configured_gate_commands(profile: OrchestrationProfile) -> list[ConfiguredCommand]:
    """Every non-blank command the profile configures, tagged with its field."""
    gates = profile.gates
    tagged = [(GateCommandSource.COMMANDS, command) for command in gates.commands]
    tagged.append((GateCommandSource.TRANSITION_SCRIPT, gates.transition_script))
    tagged.extend(
        (GateCommandSource.LAND_VERIFY_COMMANDS, c) for c in profile.git.land_verify_commands
    )
    tagged.extend(
        (GateCommandSource.CRITERION_TEST_COMMANDS, entry.command)
        for entry in gates.criterion_test_commands
    )
    return [ConfiguredCommand(source, command) for source, command in tagged if command.strip()]


def unresolved_gate_commands(
    commands: list[ConfiguredCommand], repo_root: Path
) -> list[UnresolvedCommand]:
    """The commands whose executable or script is missing, in configuration order."""
    unresolved: list[UnresolvedCommand] = []
    for configured in commands:
        try:
            parts = shlex.split(configured.command)
        except ValueError:
            unresolved.append(
                UnresolvedCommand(configured.source, configured.command, (UNPARSEABLE,))
            )
            continue
        missing = _missing_targets(parts, repo_root) if parts else []
        if missing:
            unresolved.append(
                UnresolvedCommand(configured.source, configured.command, tuple(missing))
            )
    return unresolved


def _is_placeholder(token: str) -> bool:
    return "{" in token or "$" in token or token.startswith("~")


def _missing_executable(token: str, repo_root: Path) -> str | None:
    if not token or _is_placeholder(token):
        return None
    if "/" in token:
        return None if (repo_root / token).exists() else token
    return None if shutil.which(token, path=os.environ.get("PATH", "")) else token


def _missing_path(token: str, base: Path) -> str | None:
    if _is_placeholder(token):
        return None
    return None if (base / token).exists() else token


def _script_token(interpreter: str, args: list[str]) -> str | None:
    """The script an interpreter is given, or None for stdin, `-c`, `-m` and `-e` forms."""
    inline_letters = _INLINE_FLAG_LETTERS[interpreter]
    for arg in args:
        if arg == "-":
            return None
        if not arg.startswith("-"):
            return arg
        if interpreter == "node" and arg.partition("=")[0] in _NODE_INLINE_LONG_FLAGS:
            return None
        if not arg.startswith("--") and set(arg[1:]) & set(inline_letters):
            return None
    return None


def _missing_targets(parts: list[str], repo_root: Path) -> list[str]:
    executable = parts[0]
    if Path(executable).name == "uv" and parts[1:2] == ["run"]:
        return _missing_uv_run_targets(parts, repo_root)
    missing = [_missing_executable(executable, repo_root)]
    interpreter = Path(executable).name
    if interpreter in _INLINE_FLAG_LETTERS:
        script = _script_token(interpreter, parts[1:])
        if script is not None:
            missing.append(_missing_path(script, repo_root))
    return [target for target in missing if target]


def _missing_uv_run_targets(parts: list[str], repo_root: Path) -> list[str]:
    missing = [_missing_executable(parts[0], repo_root)]
    args = parts[2:]
    base: Path | None = repo_root
    index = 0
    while index < len(args) and args[index].startswith("-"):
        flag, has_value, value = args[index].partition("=")
        index += 1
        if flag not in _UV_VALUE_FLAGS:
            continue
        if not has_value:
            value = args[index] if index < len(args) else ""
            index += 1
        if flag == "--directory":
            # A placeholder directory has no base to resolve a relative target against.
            base = None if _is_placeholder(value) else repo_root / value
    if index < len(args) and base is not None:
        target = args[index]
        name = Path(target).name
        if name in _INLINE_FLAG_LETTERS:
            script = _script_token(name, args[index + 1 :])
            missing.append(_missing_path(script, base) if script is not None else None)
        elif "/" in target:
            missing.append(_missing_path(target, base))
    return [target for target in missing if target]
