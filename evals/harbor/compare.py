"""Compare Harbor jobs task by task, from each trial's `result.json`.

    python3 compare.py jobs/base-cc jobs/base-lg [--json]

Per job and task: resolved trials over trials run, and the mean agent time, input
tokens and cost of those trials. A trial that raised, or whose verifier wrote no
reward, is counted as **errored** and is not counted as resolved or unresolved:
`harbor run` exits 0 either way, and an infrastructure failure must not read as
the agent failing (or the other way round).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean


def _seconds(span: dict | None) -> float | None:
    if not span or not span.get("started_at") or not span.get("finished_at"):
        return None
    start = datetime.fromisoformat(span["started_at"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(span["finished_at"].replace("Z", "+00:00"))
    return (end - start).total_seconds()


def load(job: Path) -> dict[str, list[dict]]:
    trials: dict[str, list[dict]] = defaultdict(list)
    results = sorted(job.glob("*/result.json"))
    if not results:
        raise SystemExit(f"compare: {job} holds no trial result.json")
    for path in results:
        data = json.loads(path.read_text())
        rewards = (data.get("verifier_result") or {}).get("rewards") or {}
        agent = data.get("agent_result") or {}
        errored = bool(data.get("exception_info")) or "resolved" not in rewards
        trials[data["task_name"].split("/")[-1]].append(
            {
                "trial": data["trial_name"],
                "errored": errored,
                "error": (data.get("exception_info") or {}).get("exception_type"),
                "resolved": (not errored) and rewards["resolved"] == 1.0,
                "seconds": _seconds(data.get("agent_execution")),
                "input_tokens": agent.get("n_input_tokens"),
                "cost_usd": agent.get("cost_usd"),
            }
        )
    return trials


def _mean(values: list) -> float | None:
    present = [v for v in values if v is not None]
    return mean(present) if present else None


def summarize(trials: list[dict]) -> dict:
    graded = [t for t in trials if not t["errored"]]
    return {
        "resolved": sum(t["resolved"] for t in graded),
        "graded": len(graded),
        "errored": len(trials) - len(graded),
        "errors": sorted({t["error"] for t in trials if t["error"]}),
        "seconds": _mean([t["seconds"] for t in graded]),
        "input_tokens": _mean([t["input_tokens"] for t in graded]),
        "cost_usd": _mean([t["cost_usd"] for t in graded]),
    }


def _fmt(s: dict) -> str:
    def num(v: float | None, spec: str) -> str:
        return "-" if v is None else format(v, spec)

    cell = f"{s['resolved']}/{s['graded']}"
    if s["errored"]:
        cell += f" (+{s['errored']} err)"
    return (
        f"{cell:>13} {num(s['seconds'], '6.0f')}s "
        f"{num((s['input_tokens'] or 0) / 1000 if s['input_tokens'] else None, '6.0f')}k "
        f"${num(s['cost_usd'], '5.2f')}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("jobs", nargs="+", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    jobs = {job.name: load(job) for job in args.jobs}
    tasks = sorted({task for trials in jobs.values() for task in trials})
    table = {
        name: {task: summarize(trials.get(task, [])) for task in tasks}
        | {"ALL": summarize([t for ts in trials.values() for t in ts])}
        for name, trials in jobs.items()
    }
    if args.json:
        print(json.dumps(table, indent=2))
        return 0

    width = max(len(t) for t in [*tasks, "ALL"])
    print(" " * width + "".join(f" | {name:^36}" for name in jobs))
    for task in [*tasks, "ALL"]:
        print(
            f"{task:<{width}}" + "".join(f" | {_fmt(table[n][task]):>36}" for n in jobs)
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
