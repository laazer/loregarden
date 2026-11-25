#!/usr/bin/env python3
"""Print the test files one CI runner runs, one per line, relative to server/.

    pytest_shard.py --shard 2 --of 4 [--server DIR]

Every file pytest would collect from server/tests is in exactly one shard. The
files are dealt out in name order, so a shard's share does not depend on
recorded timings that go stale, and slow neighbours (test_symbol_map_*) land on
different runners.

A shard with no files is an error: `pytest` with no arguments runs everything,
and four runners each running the whole suite would still be green.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

#: pytest's own default `python_files`; server/pyproject.toml does not change it.
PATTERNS = ("test_*.py", "*_test.py")


def test_files(server: Path) -> list[str]:
    found = {path for pattern in PATTERNS for path in (server / "tests").rglob(pattern)}
    return sorted(path.relative_to(server).as_posix() for path in found)


def shard_of(files: list[str], shard: int, of: int) -> list[str]:
    if not 1 <= shard <= of:
        raise ValueError(f"shard {shard} of {of}: shards are numbered 1 to {of}")
    return files[shard - 1 :: of]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shard", type=int, required=True)
    parser.add_argument("--of", type=int, required=True)
    parser.add_argument("--server", type=Path, default=Path(__file__).parents[2] / "server")
    args = parser.parse_args()
    try:
        files = shard_of(test_files(args.server), args.shard, args.of)
    except ValueError as exc:
        print(f"pytest_shard: {exc}", file=sys.stderr)
        return 2
    if not files:
        print(
            f"pytest_shard: shard {args.shard} of {args.of} has no test files in {args.server}",
            file=sys.stderr,
        )
        return 1
    print("\n".join(files))
    return 0


if __name__ == "__main__":
    sys.exit(main())
