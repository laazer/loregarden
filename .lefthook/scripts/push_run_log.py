#!/usr/bin/env python3
"""How long each pre-push server run took, and which ones ran the full suite.

A push runs at most a budget of test files (push-base.sh), so a full-suite
run on a push is a mapping gap or an operator's choice, never the default. On
2026-11-25 pre-push suites held capacity for 1-4 hours each, and nobody saw it
creep: each run was one slow push. This keeps one line per run so a gap that
forces full runs shows up as a count, and a creeping push as a median.

    push_run_log.py record --repo PATH --elapsed S --budget S --exit N
                           [--files N] [--full-suite-reason TEXT] [--requested]
    push_run_log.py report --repo PATH [--days 7]

`server-tests.sh` records every push run (not a landing) when it exits. Lines
live in the git common directory beside the pass receipts (pass_receipt.py), so
worktrees share them and none is committed. Lines older than `KEEP_DAYS` are
dropped on each write.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

LOG = "loregarden-push-runs.jsonl"
KEEP_DAYS = 30
DAY = 24 * 3600


def _store(repo: Path) -> Path:
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}
    done = subprocess.run(
        [
            "git",
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ],  # py-git: allow - stdlib-only hook script
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return Path(done.stdout.strip()) / LOG


def _entries(log: Path) -> list[dict]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line]


def record(repo: Path, entry: dict, now: float) -> Path:
    """Append one run, dropping lines older than `KEEP_DAYS`. Replaced by rename."""
    log = _store(repo)
    kept = [old for old in _entries(log) if now - old["at"] <= KEEP_DAYS * DAY]
    kept.append({"at": now, **entry})
    partial = log.with_name(f"{log.name}.{os.getpid()}.tmp")
    partial.write_text("".join(json.dumps(item) + "\n" for item in kept), encoding="utf-8")
    os.replace(partial, log)
    return log


def summarize(entries: list[dict], now: float, days: int) -> dict:
    recent = [entry for entry in entries if now - entry["at"] <= days * DAY]
    fallbacks = [e for e in recent if e.get("full_suite_reason") and not e.get("requested")]
    elapsed = [entry["elapsed"] for entry in recent]
    return {
        "days": days,
        "runs": len(recent),
        "median_seconds": statistics.median(elapsed) if elapsed else None,
        "over_budget": sum(1 for entry in recent if entry["elapsed"] > entry["budget"]),
        "full_suite_fallbacks": len(fallbacks),
        "full_suite_requested": sum(1 for entry in recent if entry.get("requested")),
        "fallback_reasons": sorted({entry["full_suite_reason"] for entry in fallbacks}),
    }


def _report(repo: Path, days: int) -> int:
    summary = summarize(_entries(_store(repo)), time.time(), days)
    median = summary["median_seconds"]
    print(
        f"pre-push, last {days} days: {summary['runs']} run(s), median "
        f"{'n/a' if median is None else f'{median:.0f}s'}, {summary['over_budget']} over budget, "
        f"{summary['full_suite_fallbacks']} full-suite fallback(s), "
        f"{summary['full_suite_requested']} full run(s) asked for"
    )
    for reason in summary["fallback_reasons"]:
        print(f"  fallback: {reason}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("record")
    rec.add_argument("--repo", required=True, type=Path)
    rec.add_argument("--elapsed", required=True, type=int)
    rec.add_argument("--budget", required=True, type=int)
    rec.add_argument("--exit", required=True, type=int)
    rec.add_argument("--files", type=int, default=0)
    rec.add_argument("--full-suite-reason", default="")
    rec.add_argument("--requested", action="store_true")
    rep = sub.add_parser("report")
    rep.add_argument("--repo", required=True, type=Path)
    rep.add_argument("--days", type=int, default=7)
    args = parser.parse_args()
    repo = args.repo.resolve()
    if args.command == "report":
        return _report(repo, args.days)
    record(
        repo,
        {
            "elapsed": args.elapsed,
            "budget": args.budget,
            "exit": args.exit,
            "files": args.files,
            "full_suite_reason": args.full_suite_reason,
            "requested": args.requested,
        },
        time.time(),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
