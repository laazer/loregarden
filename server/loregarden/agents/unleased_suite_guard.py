#!/usr/bin/env python3
"""Refuse an agent's parallel test suite that holds no capacity lease.

Every heavy run on this machine queues for a lease (`capacity-run.sh`), and the
ledger prices what it can see: an agent run books a quarter of a cpu. In the 14
days to 2026-11-24 agents started a parallel pytest without a lease 109 times, in
47 runs, against 137 leased test commands. One was a verifier's `pytest -n 6`
over the whole suite beside a leased one: load average 200 on 10 cores, while
the ledger counted 5 of 7 cpus in use and kept eleven suites waiting. The prompt
already said to queue (`common_assets/test_selection_v1.md`), so this is
enforced where the command is issued.

Refused, unless `capacity-run.sh` (or `loregarden capacity run`) runs it:

- pytest with two or more xdist workers (`-n 4`, `-n auto`);
- jest with nothing that narrows it, which takes every core but one by default.

Serial runs pass, whatever their length: one process is about what the agent's
own lease books. `-n "$N"` passes too, since nobody can read it. So does
`npm test`: it does not say what it runs, and a workspace may allow it by rule.

Agent runs only. It reads command text with `shared_stash_guard`'s reader, so
the same limits hold: it stops an agent that reaches for the command, not one
set on hiding it in a script file.

Standard library only, Python 3.9: hooks run under the system `python3`.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Iterator
from pathlib import Path

if __package__:
    from loregarden.agents.shared_stash_guard import command_of, respond, shell_commands
else:  # run as a hook script: its directory is on the path, the package is not
    from shared_stash_guard import (  # type: ignore[no-redef]
        command_of,
        respond,
        shell_commands,
    )

_CAPACITY_RUN = Path(__file__).resolve().parents[3] / ".lefthook" / "scripts" / "capacity-run.sh"

#: Programs that take a runner's name as text to look for or print, not to run.
_READERS = frozenset(
    {"grep", "rg", "ag", "git", "echo", "printf", "cat", "sed", "awk", "which", "npm"}
)
_WORKERS = re.compile(r"(?:-n|--numprocesses|-w|--maxWorkers)=?(.*)\Z")
_JEST_NARROWS = re.compile(
    r"(?:-t|-i|-o|--(?:testNamePattern|testPathPatterns?|runInBand|onlyChanged|changedSince|"
    r"findRelatedTests|runTestsByPath|listTests|showConfig|version|help|clearCache))(?:=.*)?\Z"
)

_REASON = (
    "Blocked: `{form}` without a capacity lease. Every suite on this machine shares one budget, "
    "and this one is not on the ledger, so it slows the ones that queued. Name the test files "
    "that reach your change and run them serially (no `-n`); that needs no lease, and CI runs "
    'the rest. If it must be parallel, queue for it: {wrapper} "<what>" --footprint heavy -- '
    "sh -c '{example}'"
)


def is_leased(words: list[str]) -> bool:
    """Whether this command runs what follows under a lease."""
    if any(os.path.basename(word) == "capacity-run.sh" for word in words):
        return True
    return any(words[i : i + 2] == ["capacity", "run"] for i in range(len(words)))


def _option(words: list[str], flags: tuple[str, ...]) -> str:
    """The value one of ``flags`` is given: `-n 4`, `-n4`, `--maxWorkers=4`. "" for none."""
    for index, word in enumerate(words):
        if word in flags:
            return words[index + 1] if index + 1 < len(words) else ""
        match = _WORKERS.match(word)
        if match and word.startswith(flags):
            return match.group(1)
    return ""


def _is_parallel(workers: str) -> bool:
    if workers in ("auto", "logical"):
        return True
    if workers.endswith("%"):
        return True
    return workers.isdigit() and int(workers) > 1


def _pytest_refusal(arguments: list[str]) -> str:
    workers = _option(arguments, ("-n", "--numprocesses"))
    return f"pytest -n {workers}" if _is_parallel(workers) else ""


def _jest_refusal(arguments: list[str]) -> str:
    return "" if _jest_is_small(arguments) else "jest"


def _jest_is_small(arguments: list[str]) -> bool:
    """Narrowed or held to one worker. Over named files jest starts no more workers than files."""
    if any(_JEST_NARROWS.match(word) or not word.startswith("-") for word in arguments):
        return True
    workers = _option(arguments, ("-w", "--maxWorkers"))
    return bool(workers) and not _is_parallel(workers)


_RUNNERS = {
    "pytest": _pytest_refusal,
    "py.test": _pytest_refusal,
    "jest": _jest_refusal,
}


def _refusals(words: list[str]) -> Iterator[str]:
    if not words or os.path.basename(words[0]) in _READERS or is_leased(words):
        return
    for index, word in enumerate(words):
        runner = _RUNNERS.get(os.path.basename(word))
        if runner is not None:
            # The outermost runner: a path called `pytest` is its argument.
            yield runner(words[index + 1 :])
            return


def denial(command: str) -> str:
    """Why an agent may not run ``command``, or "" when it may."""
    if not any(name in command for name in _RUNNERS):
        return ""
    try:
        for words in shell_commands(command, enters=lambda words: not is_leased(words)):
            form = next(filter(None, _refusals(words)), "")
            if form:
                return _REASON.format(
                    form=form, wrapper=_CAPACITY_RUN, example=_example(form.split()[0])
                )
    except (
        RecursionError
    ):  # silent-ok: the stash guard, run beside this one, refuses it and says so
        return ""
    return ""


def _example(runner: str) -> str:
    if runner == "pytest":
        return "uv run pytest -n $LOREGARDEN_CAPACITY_WORKERS <files>"
    return "npx jest --maxWorkers=$LOREGARDEN_CAPACITY_WORKERS"


def main(argv: list[str]) -> int:
    return respond(argv, lambda payload: denial(command_of(payload)), name="unleased_suite_guard")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
