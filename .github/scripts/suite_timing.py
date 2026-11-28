#!/usr/bin/env python3
"""CI's half of the suite-time gate: which runs' profiles count, and where they are kept.

    suite_timing.py collect  --profiles DIR --client-root DIR --out DIR
                             [--repo R --sha SHA --event E --ref REF]
    suite_timing.py baseline --repo R --run-id ID --current DIR --work DIR --out FILE
    suite_timing.py check    --repo R --current DIR --work DIR [--summary FILE]
    suite_timing.py ceiling  --profiles DIR [--minutes N]

Run under server/'s environment: the measuring, the median and the comparison are
`lore_eden.testing.profile_delta`, next to the `pytest_profile` plugin that writes
the server suite's profiles. This script only decides which runs those see.

`collect` gathers one run's profiles into one directory, uploaded as the
`suite-profiles` artifact on every run: each server shard's profile, and the
client's `jest --json` converted to the same schema. Main usually does not rerun
the server suite (tested_tree.py: the merge queue already ran it on the tree that
lands), so such a run carries the server profiles of the pull request run that
tested its tree, and main's record still only ever holds trees that landed on it.

`baseline`, on a push to main, takes main's newest `suite-profiles` artifacts until
every suite has `profile_delta.DEFAULT_RUNS` complete runs, and stores their median
as the `suite-timing-baseline` artifact. Only main's runs of this repository are
read, so a pull request cannot raise the bar it is measured against.

`check`, on a pull request, compares the run with main's newest stored baseline,
failing when a suite grew more than `profile_delta.DEFAULT_THRESHOLD` and naming
the files that grew most. The baseline is first scaled by the runner's speed
(`runner_speed`): GitHub's runners differ by a third, and main's own unchanged
runs swung 329-560s on the client, so an unscaled total failed pull requests that
changed nothing it measures.

`ceiling` is a hard limit rather than a trend: each server shard's pytest wall
time, and all shards summed (about what one runner would take), must stay within
`CEILING_MINUTES`. Per-test seconds cannot say this: under xdist they add up to
several times the wall clock, so each shard writes its own (`server-wall.json`).
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Any, Callable

from lore_eden.testing import profile_delta

PROFILES_ARTIFACT = "suite-profiles"
BASELINE_ARTIFACT = "suite-timing-baseline"
BASELINE_FILE = "baseline.json"
MAIN_BRANCH = "main"
MAIN_REF = f"refs/heads/{MAIN_BRANCH}"
#: Artifact pages (100 each) searched for main's runs before giving up.
ARTIFACT_PAGES = 5
SERVER_PROFILE = "server.json"
JEST_RESULTS = "client-jest.json"
SERVER_WALL = "server-wall.json"
#: The hard ceiling on the server suite's wall time, per shard and summed.
CEILING_MINUTES = 30
#: Every suite CI records; main's history is read until each has a full baseline.
SUITES = ("server", "client")
#: A run's speed is read from files with at least this much time in main's baseline
#: (shorter ones are mostly fixed overhead) ...
SPEED_FILE_SECONDS = 1.0
#: ... and only when a suite has this many of them; with fewer it is compared unscaled.
SPEED_MIN_FILES = 10
#: The runner speeds main's own runs showed, widened: measured 2026-11-28 over 20
#: runs as 0.79-1.37 (client and server). A run outside it is clamped, so a
#: slowdown no runner explains still counts as growth. It cannot tell a slower
#: runner from code that slowed every file alike by less than the clamp: on the
#: server the 30-minute `ceiling` still holds that; on the client nothing does,
#: so a client-wide slowdown under 1.5x passes this check.
RUNNER_SPEED = (0.75, 1.5)

Reader = Callable[[str], Any]
Fetcher = Callable[[str, int], bytes]


def _sibling(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tested_tree = _sibling("tested_tree")
CannotTell = tested_tree.CannotTell
read_api = tested_tree.read_api


# -- GitHub ---------------------------------------------------------------------


def fetch_zip(repo: str, artifact_id: int) -> bytes:
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}
    path = f"repos/{repo}/actions/artifacts/{artifact_id}/zip"
    try:
        proc = subprocess.run(
            ["gh", "api", path], capture_output=True, timeout=120, check=False, env=env
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CannotTell(f"gh api {path}: {exc}") from exc
    if proc.returncode != 0:
        raise CannotTell(f"gh api {path}: {proc.stderr.decode(errors='replace').strip()}")
    return proc.stdout


def artifacts(repo: str, name: str, read: Reader) -> list[dict]:
    """Unexpired artifacts called `name` from this repository's own runs, newest first."""
    found = []
    for page in range(1, ARTIFACT_PAGES + 1):
        batch = read(f"repos/{repo}/actions/artifacts?name={name}&per_page=100&page={page}")
        for artifact in batch["artifacts"]:
            run = artifact["workflow_run"]
            if not artifact["expired"] and run["head_repository_id"] == run["repository_id"]:
                found.append(artifact)
        if len(batch["artifacts"]) < 100:
            break
    return sorted(found, key=lambda a: a["created_at"], reverse=True)


def main_artifacts(repo: str, name: str, read: Reader) -> list[dict]:
    return [
        a for a in artifacts(repo, name, read) if a["workflow_run"]["head_branch"] == MAIN_BRANCH
    ]


def extract(blob: bytes, into: Path) -> Path:
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            archive.extractall(into)
    except zipfile.BadZipFile as exc:
        raise profile_delta.ProfileError(f"artifact for {into.name}: {exc}") from exc
    return into


# -- collecting a run -----------------------------------------------------------


def tested_server(
    repo: str,
    sha: str,
    read: Reader,
    fetch: Fetcher,
    work: Path,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[list[Path], str]:
    """The server profiles of the pull request run that tested the tree `sha` landed."""
    pulls = tested_tree.landed_by(repo, sha, read, sleep)
    if len(pulls) != 1:
        return [], f"{len(pulls)} merged pull requests produced {sha[:12]}, not one"
    number, head = pulls[0]["number"], pulls[0]["head"]["sha"]
    for artifact in artifacts(repo, PROFILES_ARTIFACT, read):
        run = artifact["workflow_run"]
        if run["head_sha"] != head:
            continue
        got = extract(fetch(repo, artifact["id"]), work / f"tested-{run['id']}")
        server = sorted(got.glob("server-*.json"))
        measured = profile_delta.measure(profile_delta.load_run(got)).get("server")
        if server and measured is not None and measured.complete:
            return server, f"carried from run {run['id']} on #{number} at {head[:12]}"
    return [], f"no complete server profiles recorded for #{number} at {head[:12]}"


def _collect(args: argparse.Namespace) -> int:
    args.out.mkdir(parents=True, exist_ok=True)
    shards = sorted(args.profiles.glob(f"profile-server-*/{SERVER_PROFILE}"))
    if not shards and args.event == "push" and args.ref == MAIN_REF:
        try:
            shards, why = tested_server(args.repo, args.sha, read_api, fetch_zip, args.out.parent)
        except (CannotTell, profile_delta.ProfileError, KeyError, TypeError) as exc:
            shards, why = [], f"could not read the tested run's profiles: {exc}"
        print(f"::notice title=Server timing::{why}")
    elif not shards:
        print(f"::warning title=Server timing::no server profiles under {args.profiles}")
    for n, shard in enumerate(shards, start=1):
        shutil.copyfile(shard, args.out / f"server-{n}.json")
    jest = args.profiles / "profile-client" / JEST_RESULTS
    if jest.is_file():
        results = json.loads(jest.read_text(encoding="utf-8"))
        client = profile_delta.from_jest(results, str(args.client_root))
        (args.out / "client.json").write_text(json.dumps(client, indent=2) + "\n", encoding="utf-8")
    else:
        print(f"::warning title=Client timing::no jest results at {jest}")
    if not any(args.out.glob("*.json")):
        print("::error title=Suite timing::this run recorded no profiles at all")
        return 1
    for name, measured in profile_delta.measure(profile_delta.load_run(args.out)).items():
        state = "complete" if measured.complete else "incomplete"
        print(f"{name}: {measured.total_seconds:.0f}s over {len(measured.files)} files, {state}")
    return 0


# -- main's baseline ------------------------------------------------------------


def main_runs(
    repo: str, read: Reader, fetch: Fetcher, work: Path, skip_run: int, want: int
) -> list[Path]:
    """Main's recorded runs, newest first, until every suite has `want` complete ones."""
    runs: list[Path] = []
    seen = {skip_run}
    complete: dict[str, int] = {}
    for artifact in main_artifacts(repo, PROFILES_ARTIFACT, read):
        run_id = artifact["workflow_run"]["id"]
        if run_id in seen:
            continue
        seen.add(run_id)
        run = extract(fetch(repo, artifact["id"]), work / f"main-{run_id}")
        runs.append(run)
        for name, measured in profile_delta.measure(profile_delta.load_run(run)).items():
            complete[name] = complete.get(name, 0) + measured.complete
        if all(complete.get(name, 0) >= want for name in SUITES):
            break
    return runs


def _baseline(args: argparse.Namespace) -> int:
    try:
        runs = [args.current] + main_runs(
            args.repo, read_api, fetch_zip, args.work, args.run_id, profile_delta.DEFAULT_RUNS
        )
    except (CannotTell, profile_delta.ProfileError, KeyError, TypeError) as exc:
        print(f"::error title=Suite time baseline::could not read main's profiles: {exc}")
        return 1
    argv = ["baseline", "--out", str(args.out)]
    for run in runs:
        argv += ["--run", str(run)]
    return profile_delta.main(argv)


def runner_speed(before: dict, now: profile_delta.Measurement) -> tuple[float, int] | None:
    """How fast this runner ran main's files: the median of each file's now/baseline.

    A median over a suite's files is the runner, not the change: a pull request
    that slows a few of the client's ~70 files or the server's ~450 barely moves it.
    Returns (clamped speed, files read), or None when too few files to tell.
    """
    ratios = [
        now.files[path] / seconds
        for path, seconds in before["files"].items()
        if seconds >= SPEED_FILE_SECONDS and path in now.files
    ]
    if len(ratios) < SPEED_MIN_FILES:
        return None
    low, high = RUNNER_SPEED
    return min(max(statistics.median(ratios), low), high), len(ratios)


def at_runner_speed(
    base: dict, current: dict[str, profile_delta.Measurement]
) -> tuple[dict, list[str]]:
    """`base` with each suite scaled to this runner's speed, and a line per suite saying so."""
    try:
        suites = base["suites"]
        scaled = {**base, "suites": dict(suites)}
        notes = []
        for name, before in suites.items():
            now = current.get(name)
            if now is None:
                continue
            read = runner_speed(before, now)
            if read is None:
                notes.append(
                    f"- {name}: compared unscaled; fewer than {SPEED_MIN_FILES} files "
                    f"of {SPEED_FILE_SECONDS:.0f}s or more to read the runner's speed from"
                )
                continue
            speed, files = read
            scaled["suites"][name] = {
                **before,
                "total_seconds": before["total_seconds"] * speed,
                "files": {path: s * speed for path, s in before["files"].items()},
            }
            low, high = RUNNER_SPEED
            clamped = " (clamped to the range runners show)" if speed in (low, high) else ""
            notes.append(
                f"- {name}: this runner took {speed:.2f}x main's median time per file "
                f"(median over {files} files){clamped}; the baseline above is scaled by it"
            )
    except (KeyError, TypeError, AttributeError) as exc:
        raise profile_delta.ProfileError(f"not a baseline: {exc!r}") from exc
    return scaled, notes


def _check(args: argparse.Namespace) -> int:
    try:
        stored = main_artifacts(args.repo, BASELINE_ARTIFACT, read_api)
        if stored:
            extract(fetch_zip(args.repo, stored[0]["id"]), args.work / "baseline")
    except (CannotTell, profile_delta.ProfileError, KeyError, TypeError) as exc:
        print(f"::error title=Suite time::could not read main's baseline: {exc}")
        return 1
    if not stored:
        # Not a pass on the evidence: main has stored none yet (the first runs
        # after this landed), or every one expired. Said where the PR shows it.
        print("::warning title=Suite time::main has stored no baseline yet; not compared")
        return 0
    base = json.loads((args.work / "baseline" / BASELINE_FILE).read_text(encoding="utf-8"))
    current = profile_delta.measure(profile_delta.load_run(args.current))
    scaled, notes = at_runner_speed(base, current)
    verdicts = profile_delta.compare(scaled, current)
    text = profile_delta.report(verdicts) + "\n### Runner speed\n\n" + "\n".join(notes) + "\n"
    print(text)
    if args.summary is not None:
        with args.summary.open("a", encoding="utf-8") as summary:
            summary.write(text)
    if all(verdict.passed for verdict in verdicts):
        return 0
    print(
        "::error title=Suite time::a suite grew more than 20% over main at this runner's "
        "speed; see the summary"
    )
    return 1


# -- the wall-time ceiling ---------------------------------------------------------


def _ceiling(args: argparse.Namespace) -> int:
    shards = sorted(args.profiles.glob("profile-server-*"))
    if not shards:
        print("::notice title=Server wall time::no server shard ran here (a reused tree)")
        return 0
    walls: dict[str, float] = {}
    for shard in shards:
        path = shard / SERVER_WALL
        try:
            walls[shard.name] = float(json.loads(path.read_text(encoding="utf-8"))["wall_seconds"])
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            print(
                f"::error title=Server wall time::{shard.name} has no readable {SERVER_WALL}: {exc}"
            )
            return 1
    limit = args.minutes * 60
    total = sum(walls.values())
    for name, seconds in walls.items():
        print(f"{name}: {seconds / 60:.1f} min")
    print(f"all {len(walls)} shards: {total / 60:.1f} min (ceiling {args.minutes} min)")
    over = [f"{name} took {s / 60:.1f} min" for name, s in walls.items() if s > limit]
    if total > limit:
        over.append(f"the whole suite took {total / 60:.1f} min over {len(walls)} shards")
    if over:
        print(f"::error title=Server wall time::{'; '.join(over)}: over {args.minutes} min")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)

    collect = commands.add_parser("collect")
    collect.add_argument("--profiles", type=Path, required=True)
    collect.add_argument("--client-root", type=Path, required=True)
    collect.add_argument("--out", type=Path, required=True)
    collect.add_argument("--repo", default="")
    collect.add_argument("--sha", default="")
    collect.add_argument("--event", default="")
    collect.add_argument("--ref", default="")
    collect.set_defaults(handler=_collect)

    base = commands.add_parser("baseline")
    base.add_argument("--repo", required=True)
    base.add_argument("--run-id", type=int, required=True)
    base.add_argument("--current", type=Path, required=True)
    base.add_argument("--work", type=Path, required=True)
    base.add_argument("--out", type=Path, required=True)
    base.set_defaults(handler=_baseline)

    check = commands.add_parser("check")
    check.add_argument("--repo", required=True)
    check.add_argument("--current", type=Path, required=True)
    check.add_argument("--work", type=Path, required=True)
    check.add_argument("--summary", type=Path)
    check.set_defaults(handler=_check)

    ceiling = commands.add_parser("ceiling")
    ceiling.add_argument("--profiles", type=Path, required=True)
    ceiling.add_argument("--minutes", type=float, default=CEILING_MINUTES)
    ceiling.set_defaults(handler=_ceiling)

    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (profile_delta.ProfileError, json.JSONDecodeError, OSError) as exc:
        print(f"::error title=Suite timing::{exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
