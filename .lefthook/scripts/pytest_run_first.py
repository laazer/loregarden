"""pytest plugin: run the named test files before everything else.

Loaded by server-tests.sh as `-p pytest_run_first --run-first=FILE` on a full
pre-push run. FILE lists test files (relative to pytest's invocation directory,
one per line, closest to the change first) — the ones `select_pytest_targets.py --run-first-out` found the
pushed diff reaching. A full run is forced by any file the import graph cannot
map, and before this the tests the change actually reached ran wherever
collection put them: one broken test surfaced 23 minutes into a run that `-x`
then stopped. Moved to the front, the same failure arrives in the first minute.

It only reorders. Every collected test still runs, so a wrong or empty list
costs speed, never coverage. Under xdist every worker applies the same order
(the list and the collection are identical), and load scheduling hands the
front of the collection out first. `--ff` (pytest's cache) wraps this hook, so
last run's failures still go ahead of these.

The option rides the command line rather than the environment so a pytest this
suite starts itself does not inherit it, and a FILE that cannot be read is a
usage error: a full run that silently ignored its order would look exactly like
one that had none to apply.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-first",
        default="",
        metavar="FILE",
        help="file listing test files to run before the rest, one per line",
    )


def _listed(config: pytest.Config) -> dict[Path, int]:
    """Each listed file and its rank: the list is ordered, closest first."""
    raw = config.getoption("run_first")
    if not raw:
        return {}
    try:
        lines = Path(raw).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise pytest.UsageError(f"--run-first: cannot read {raw}: {exc}") from exc
    base = config.invocation_params.dir
    ranks: dict[Path, int] = {}
    for line in lines:
        if line.strip():
            ranks.setdefault((base / line.strip()).resolve(), len(ranks))
    return ranks


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    listed = _listed(config)
    if not listed:
        return
    first = [item for item in items if item.path.resolve() in listed]
    if not first:
        return
    # In the list's order; within one file, collection order (the sort is stable).
    first.sort(key=lambda item: listed[item.path.resolve()])
    chosen = set(map(id, first))
    items[:] = first + [item for item in items if id(item) not in chosen]
