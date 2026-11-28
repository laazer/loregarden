"""The kill matrix mutmut does not have: which tests killed WHICH mutant.

mutmut 3.8.1's only test-level data is COVERAGE: its StatsCollector fills a
map of tests by mangled function name in `pytest_runtest_teardown`, from the
functions whose trampoline ran. Substituting coverage for kills omits exactly
the tests that touch the code and assert nothing — which are the tests a
zero-kill review list exists to find, so it fails in the damaging direction
(ticket 434). That map's name is deliberately not written out here: a test
scans this whole directory for it, so nobody can reintroduce it as a kill
matrix by copying a line out of a comment.

This plugin is the kill matrix instead. mutmut's `run_tests` ignores its
`mutant_name` argument and selects the mutant through the `MUTANT_UNDER_TEST`
environment variable (`mutation/trampoline.py:43,54`), so that variable is the
key: one row per mutant, holding the SET of test node ids in each outcome.

It reaches mutmut's sandbox through two `[tool.mutmut]` keys, both required:

    also_copy           = ["mutmut_killers.py"]          # it is THERE
    pytest_add_cli_args = ["-p", "mutmut_killers", ...]  # it is LOADED

mutmut runs pytest with `cwd=mutants`, so a plugin outside `also_copy` is not
in the sandbox at all and `-p mutmut_killers` fails the entire run.

`also_copy` names a bare top-level filename because mutmut 3.8.1 resolves the
entry against its own CWD (server/), copies it with `shutil.copy2` without
creating the destination's parent, and puts only `mutants/` on sys.path. A
nested path is therefore skipped in silence or raises FileNotFoundError, and
is unimportable either way. `run-server.sh` stages this file to
`server/mutmut_killers.py` for the run and deletes it afterwards, so the
repository keeps exactly one copy of it, here.

Rows are written to `$MUTMUT_KILL_MATRIX_DIR`, one JSON file per mutant, because
mutmut runs each mutant in its own pytest process and a shared file would race.
`run-server.sh` merges them into the run document the reducer reads. With the
variable unset the plugin writes nothing and changes nothing, so loading it in
an ordinary pytest run is harmless.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

# pytest-timeout's SIGALRM fails the single test rather than the process, so
# pytest exits 1 and mutmut records an ordinary kill. The only trace that a
# timeout fired at all is in the failure text, and AC-7 (iv) requires the
# report to name which instrument fired.
_TIMEOUT_MARKER = re.compile(r"Failed:\s+Timeout\s|timeout:\s|_pytest\.timeout", re.IGNORECASE)

_BUCKETS = ("failing", "passed", "skipped", "timed_out", "errored")

# mutmut drives its own phases through the same variable, with these literals
# standing in for a mutant name (`mutmut/__main__.py`: "fail" at the
# baseline check, "stats" while collecting coverage, plus "list_all_tests"
# and "mutant_generation"). They are pytest runs, so without this they each
# write a row and arrive at the collector looking like a mutant that nothing
# killed. An empty value is mutmut saying "no mutant", and is already handled
# by the `if not mutant_under_test` guard below.
_MUTMUT_PHASE_SENTINELS = frozenset({"fail", "stats", "list_all_tests", "mutant_generation"})


class KillMatrixRecorder:
    """Accumulates one mutant's row across the tests of a single pytest run."""

    def __init__(self, mutant_under_test, out_dir):
        self.mutant_under_test = mutant_under_test
        self.out_dir = out_dir
        self.row = {bucket: set() for bucket in _BUCKETS}

    def pytest_runtest_logreport(self, report):
        node_id = report.nodeid
        if report.skipped:
            # Only the call phase of a skip is interesting; a skipped setup
            # reports in the setup phase and means the same thing to us.
            self.row["skipped"].add(node_id)
            return
        if report.failed:
            if report.when != "call":
                # A FAILURE OUTSIDE THE CALL PHASE IS A KILL, AND IS MARKED.
                # The operator decided this: credit it, and mark it so the
                # review list can discount it. The cost the mark pays for is
                # real — an import-fatal mutant errors every test in a module
                # during setup, and `server/tests/conftest.py` imports 20+
                # loregarden modules at module scope, so one such mutant
                # credits a whole suite, and a credited test leaves
                # `zero_kill_tests` for good. So the credit goes in `failing`
                # and the mark in `errored`, and the reducer turns the pair
                # into `killed_by_error` / `killers_only_by_error`: the tests
                # that look like killers and detected nothing.
                self.row["errored"].add(node_id)
            # Every failing test is credited, not just the first. That is what
            # `--maxfail=0` buys: under mutmut's forced `-x` only the first
            # killer is ever seen, so a real killer is recorded as having
            # killed nothing and lands on the review list as a delete.
            self.row["failing"].add(node_id)
            if _TIMEOUT_MARKER.search(str(report.longrepr or "")):
                self.row["timed_out"].add(node_id)
            return
        if report.when == "call" and report.passed:
            self.row["passed"].add(node_id)

    def write(self):
        self.out_dir.mkdir(parents=True, exist_ok=True)
        payload = {bucket: sorted(node_ids) for bucket, node_ids in self.row.items()}
        path = self.out_dir / f"{_filename(self.mutant_under_test)}.json"
        path.write_text(
            json.dumps({"mutant": self.mutant_under_test, **payload}, indent=1),
            encoding="utf-8",
        )


def _filename(mutant_under_test):
    """A mutant name is a dotted path and may carry mutmut's U+01C1 separator."""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", mutant_under_test)


def pytest_configure(config):
    mutant_under_test = os.environ.get("MUTANT_UNDER_TEST")
    out_dir = os.environ.get("MUTMUT_KILL_MATRIX_DIR")
    if not mutant_under_test or not out_dir:
        # Not a mutmut-driven run (or the run script did not say where to
        # write). Record nothing rather than guess a destination — a row
        # written somewhere nobody merges is worse than no row at all.
        return
    if mutant_under_test in _MUTMUT_PHASE_SENTINELS:
        # One of mutmut's own phases, not a mutant. Writing a row here would
        # put a non-mutant on the review list with no line and no operator.
        return
    config.pluginmanager.register(
        KillMatrixRecorder(mutant_under_test, Path(out_dir)), "mutmut-kill-matrix-recorder"
    )


def pytest_sessionfinish(session):
    recorder = session.config.pluginmanager.get_plugin("mutmut-kill-matrix-recorder")
    if recorder is not None:
        recorder.write()
