#!/usr/bin/env python3
"""Write a run's measured wall clock into a tool's JSON report.

Stryker's json reporter does not reliably carry a `performance` block, and a
reducer that sums an absent block reports 0.0 — a wall clock of zero seconds
beside a sixteen-minute run, which reads as a measurement rather than as a
gap. The script that ran the tool is the thing that actually watched the
clock, so it records what it saw. The server side says the same fact the same
way, through `collect_server_run.py --wall-clock-seconds`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 2:
        print("usage: record_wall_clock.py <report.json> <seconds>", file=sys.stderr)
        return 2
    path, seconds = Path(argv[0]), float(argv[1])
    report = json.loads(path.read_text(encoding="utf-8"))
    report["wall_clock_seconds"] = seconds
    path.write_text(json.dumps(report), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
