"""A second reading of the runner's speed, so a slowdown in every file cannot pass as one.

The suite-time check reads a run's speed as the median of each file's time
against main's (`files_speed`) and scales main's baseline by it. That absorbs
GitHub's runners, which differ by a third. It also absorbs a change that slows
every file alike, which moves the median exactly as a slower runner does.

`runner_bench.mjs` times fixed work on the same runner just before the suite, so
its reading moves with the runner and not with the change. On main the two
readings stand in a ratio that is about constant; main's baseline records it
(`calibrate`). A pull request whose files ran slower than its benchmark allows,
at that ratio, has the difference counted as growth (`cap`).

The cap acts only once main's own runs show the benchmark tracking the runner:
at least `MIN_RUNS` of them carrying the current `version`, with the ratio
spread no wider than `MAX_SPREAD`. Until then the check runs exactly as before
and the summary says which condition is missing. A benchmark that does not
track the runner never fails a pull request; it leaves the blind spot as it was.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

#: The benchmark's output, beside jest's in the client job's artifact.
BENCH_FILE = "client-runner.json"
#: Main runs carrying the benchmark before the cap acts.
MIN_RUNS = 8
#: The widest max/min of main's file-to-benchmark ratios at which the cap acts.
MAX_SPREAD = 1.25
#: Room above the largest ratio main has shown.
MARGIN = 0.10
#: Files with at least this much time in main's baseline are read for the speed
#: (shorter ones are mostly fixed overhead) ...
SPEED_FILE_SECONDS = 1.0
#: ... and only when a suite has this many of them.
SPEED_MIN_FILES = 10


def files_speed(before: dict[str, float], now: dict[str, float]) -> tuple[float, int] | None:
    """The median of each file's now/baseline seconds, and how many files; None with too few."""
    ratios = [
        now[path] / seconds
        for path, seconds in before.items()
        if seconds >= SPEED_FILE_SECONDS and path in now
    ]
    if len(ratios) < SPEED_MIN_FILES:
        return None
    return statistics.median(ratios), len(ratios)


def reading(path: Path) -> dict | str:
    """The benchmark's reading at `path`, or why there is none to use."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return f"no runner benchmark at {path}"
    except (OSError, json.JSONDecodeError) as exc:
        return f"runner benchmark at {path} unreadable: {exc}"
    if (
        type(doc) is not dict
        or type(doc.get("version")) is not int
        or type(doc.get("ms")) not in (int, float)
        or doc["ms"] <= 0
    ):
        return f"runner benchmark at {path} is not a reading: {doc!r:.200}"
    return {"version": doc["version"], "ms": doc["ms"]}


def runners_of(profiles: list[dict]) -> dict[str, dict]:
    """Each suite's benchmark reading in one run's profiles, where one was recorded."""
    return {p["suite"]: p["runner"] for p in profiles if type(p.get("runner")) is dict}


def calibrate(
    base_files: dict[str, float], readings: list[tuple[dict[str, float], dict | None]]
) -> dict | None:
    """Main's benchmark median and each run's file-to-benchmark ratio, newest version only.

    `readings` are the baseline's runs, newest first: each one's per-file seconds
    and its benchmark reading (None when it recorded none).
    """
    benched = [(files, bench) for files, bench in readings if bench is not None]
    if not benched:
        return None
    version = benched[0][1]["version"]
    same = [(files, bench) for files, bench in benched if bench["version"] == version]
    bench_ms = statistics.median(bench["ms"] for _files, bench in same)
    ratios = []
    for files, bench in same:
        read = files_speed(base_files, files)
        if read is not None:
            ratios.append(round(read[0] / (bench["ms"] / bench_ms), 4))
    return {"version": version, "bench_ms": bench_ms, "ratios": ratios}


def _inactive(calibration: dict | None, bench: dict | None) -> str | None:
    """Why the cap cannot act on this run, or None when it can."""
    if calibration is None:
        return "main's baseline holds no benchmark readings yet"
    if bench is None:
        return "this run recorded no benchmark"
    if bench["version"] != calibration["version"]:
        return (
            f"this run's benchmark is version {bench['version']}, main's baseline "
            f"holds version {calibration['version']}"
        )
    ratios = calibration["ratios"]
    if len(ratios) < MIN_RUNS:
        return f"{len(ratios)} of the {MIN_RUNS} main runs it needs carry the benchmark"
    spread = max(ratios) / min(ratios)
    if spread > MAX_SPREAD:
        return (
            f"on main's runs it tracks the runner only within {spread:.2f}x, "
            f"wider than {MAX_SPREAD:.2f}x"
        )
    return None


def cap(calibration: dict | None, speed: float, bench: dict | None) -> tuple[float, str]:
    """`speed`, lowered to what this run's benchmark allows, and a line saying which."""
    why = _inactive(calibration, bench)
    if why is not None:
        return speed, f"runner benchmark not applied: {why}"
    runner = bench["ms"] / calibration["bench_ms"]
    allowed = runner * max(calibration["ratios"]) * (1 + MARGIN)
    if speed <= allowed:
        return speed, (
            f"runner benchmark {runner:.2f}x main's agrees (it allows files up to {allowed:.2f}x)"
        )
    return allowed, (
        f"runner benchmark {runner:.2f}x main's allows files up to {allowed:.2f}x, and they "
        f"took {speed:.2f}x, so the difference counts as growth"
    )
