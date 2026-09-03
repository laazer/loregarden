#!/usr/bin/env python3
"""Refuse an interpreter too old to run the gates, in a way the runner can tell
apart from a finding.

The gate scripts need Python 3.11: `py_string_vocab` reads `ast.Match`/
`ast.match_case` to see `match` statements in graded code, and
`py_git_subprocess_check` evaluates PEP 604 (`str | None`) annotations at import.
Neither degrades gracefully — they raise `AttributeError` and `TypeError` at
module level, before a single file has been examined.

That would be merely noisy if the runner could tell what happened. It cannot:
`gate_runner._run_command` maps *any* non-zero exit to `GateOutcome.FAILED`, and
a crash exits 1 exactly like a real violation does. So a gate that never ran
gets reported as a gate that found something, and the autofix loop is sent to
repair a violation that does not exist — the failure `_blocking` already records
having happened once with a hung `npx`.

Hence a distinct code rather than a distinct message: `EX_UNAVAILABLE` (69, from
`sysexits.h`), which `_run_command` maps to `GateOutcome.UNAVAILABLE`. The
outcome the operator sees is then "this gate could not run", which is true, and
not "this gate failed you", which is not.

This module must stay importable and runnable on interpreters it exists to
reject, so it is written to 3.7-era syntax: no PEP 604 unions, no dataclasses,
no f-strings in annotations, no `match`. Nothing here may import a sibling gate
module — every one of them is a candidate for the crash being prevented.
"""

import sys

#: The floor the gate scripts actually need, stated once. Enforced two ways:
#: at runtime by `require_supported_python`, and in CI by
#: `server/tests/test_gate_python_guard.py`, which asserts every gate entry
#: point consults this guard before importing a sibling — so a new gate cannot
#: quietly skip the floor — and that `gate_runner.GATE_EX_UNAVAILABLE` still
#: agrees with `EX_UNAVAILABLE` below.
#:
#: Note the gate scripts are deliberately *not* covered by `server/pyproject.
#: toml`; they are standalone and ruff never reaches them (582).
MIN_PYTHON = (3, 11)

#: `sysexits.h` EX_UNAVAILABLE. Chosen over 127 (command not found) because the
#: command *was* found and did run; what is unavailable is a usable interpreter.
EX_UNAVAILABLE = 69


def format_refusal(script_name, version):
    """The operator-facing sentence. Names the interpreter that ran, not just
    the one required, because the common cause is a `python3` on PATH that is
    not the one the project uses."""
    running = ".".join(str(part) for part in version[:3])
    required = ".".join(str(part) for part in MIN_PYTHON)
    return (
        "%s: needs Python >= %s but was run under %s (%s).\n"
        "This gate examined nothing — it is unavailable, not passing or failing.\n"
        "Run it through .lefthook/scripts/server_python.sh, which resolves the "
        "project interpreter."
    ) % (script_name, required, running, sys.executable)


def require_supported_python(script_name, version=None, stream=None):
    """Exit `EX_UNAVAILABLE` unless the running interpreter is new enough.

    `version` is injectable so the refusal can be tested on any interpreter;
    production callers pass nothing and get `sys.version_info`.
    """
    if version is None:
        version = sys.version_info
    if tuple(version[:3]) >= MIN_PYTHON:
        return
    if stream is None:
        stream = sys.stderr
    stream.write(format_refusal(script_name, tuple(version[:3])) + "\n")
    raise SystemExit(EX_UNAVAILABLE)
