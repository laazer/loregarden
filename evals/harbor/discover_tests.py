"""Find a merged PR's fail-to-pass tests, and write its task spec.

    python3 evals/harbor/discover_tests.py <merge-sha> --workdir <scratch> [--write <name>]

On the merge's first parent, with the merge's own test files copied in, runs the test
files the merge changed: once as the parent left the source, once with the merge's
source diff applied. A test that passed after and did not pass before is fail-to-pass;
the changed test files are pass-to-pass.

A candidate is refused when nothing is fail-to-pass, or when any test in those files
still fails with the fix applied (the oracle would then score 0 for reasons that are
not the agent's). `--write` creates `specs/<name>/task.json`; the instruction is
written by hand, from the symptom and never the fix.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
TEST_DIR_PREFIX = "server/tests/"
TEST_FILE = re.compile(r"^server/tests/(.+/)?test_[^/]+\.py$")
PYTEST_TIMEOUT_SECONDS = 1200


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout


def scrubbed_env() -> dict[str, str]:
    """The ambient environment minus what would point a test at this checkout or its DB."""
    return {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("LOREGARDEN_") and k not in ("GIT_DIR", "GIT_WORK_TREE")
    }


def run_pytest(server: Path, files: list[str], report: Path) -> dict[str, bool]:
    cmd = [
        "uv",
        "run",
        "--frozen",
        "python",
        "-m",
        "pytest",
        "-p",
        "no:cacheprovider",
        "-q",
        "-o",
        "junit_family=xunit1",
        f"--junitxml={report}",
        *files,
    ]
    proc = subprocess.run(
        cmd,
        cwd=server,
        env=scrubbed_env(),
        capture_output=True,
        text=True,
        timeout=PYTEST_TIMEOUT_SECONDS,
        check=False,
    )
    tail = proc.stdout.strip().splitlines()[-1:] or ["(no output)"]
    print(f"  pytest exit {proc.returncode}: {tail[0]}", file=sys.stderr)
    if not report.is_file():
        raise SystemExit(
            f"pytest wrote no report:\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
        )
    results: dict[str, bool] = {}
    for case in ET.parse(report).iter("testcase"):
        node = node_id(server, case.get("classname", ""), case.get("name", ""))
        results[node] = not any(c.tag in ("failure", "error", "skipped") for c in case)
    return results


def node_id(server: Path, classname: str, name: str) -> str:
    """Invert pytest's junit (classname, name) into `server/tests/...::[Class::]name`."""
    parts = classname.split(".")
    for cut in range(len(parts), 0, -1):
        path = "/".join(parts[:cut]) + ".py"
        if (server / path).is_file():
            return "::".join([f"server/{path}", *parts[cut:], name])
    return f"server/{classname}::{name}"


def discover(merge: str, workdir: Path) -> dict:
    parent = git("rev-parse", f"{merge}^1").strip()
    changed = [
        line.split("\t", 1)
        for line in git("diff", "--name-status", parent, merge).splitlines()
    ]
    test_dir_files = [
        p for s, p in changed if p.startswith(TEST_DIR_PREFIX) and not s.startswith("D")
    ]
    run_files = [p for p in test_dir_files if TEST_FILE.match(p)]
    source = [p for _, p in changed if not p.startswith(TEST_DIR_PREFIX)]
    if not run_files or not source:
        raise SystemExit(f"{merge[:10]}: needs changed source and a changed test file")

    tree = workdir / merge[:10]
    if tree.exists():
        shutil.rmtree(tree)
    tree.mkdir(parents=True)
    archive = subprocess.run(
        ["git", "archive", parent], cwd=REPO, check=True, capture_output=True
    ).stdout
    subprocess.run(["tar", "-x", "-C", str(tree)], input=archive, check=True)
    # A git checkout, as the task image makes one: `init_db`'s shared-database guard
    # refuses to migrate from a code root it cannot place in a repository.
    for cmd in (
        ["git", "init", "-q"],
        ["git", "add", "-A"],
        [
            "git",
            "-c",
            "user.email=eval@localhost",
            "-c",
            "user.name=eval",
            "commit",
            "-qm",
            "base",
        ],
    ):
        subprocess.run(cmd, cwd=tree, env=scrubbed_env(), check=True)
    for path in test_dir_files:
        dest = tree / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(
            subprocess.run(
                ["git", "show", f"{merge}:{path}"],
                cwd=REPO,
                check=True,
                capture_output=True,
            ).stdout
        )
    server = tree / "server"
    subprocess.run(
        ["uv", "sync", "--frozen", "-q"], cwd=server, env=scrubbed_env(), check=True
    )

    rel = [p.removeprefix("server/") for p in run_files]
    print(f"{merge[:10]}: before", file=sys.stderr)
    before = run_pytest(server, rel, tree / "before.xml")
    patch = git("diff", "--binary", parent, merge, "--", *source)
    subprocess.run(
        ["git", "apply", "--whitespace=nowarn"],
        cwd=tree,
        input=patch,
        text=True,
        check=True,
    )
    print(f"{merge[:10]}: after", file=sys.stderr)
    after = run_pytest(server, rel, tree / "after.xml")

    still_failing = sorted(n for n, ok in after.items() if not ok)
    f2p = sorted(n for n, ok in after.items() if ok and not before.get(n, False))
    shutil.rmtree(tree)
    return {
        "merge_commit": git("rev-parse", merge).strip(),
        "fail_to_pass": f2p,
        "pass_to_pass": run_files,
        "after_total": len(after),
        "still_failing_after": still_failing,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("merge")
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--write", metavar="NAME")
    args = parser.parse_args()

    found = discover(args.merge, args.workdir.resolve())
    print(json.dumps(found, indent=2))
    if not found["fail_to_pass"]:
        print("refused: nothing is fail-to-pass", file=sys.stderr)
        return 2
    if found["still_failing_after"]:
        print("refused: tests still fail with the fix applied", file=sys.stderr)
        return 3
    if args.write:
        subject = git("log", "-1", "--format=%s", found["merge_commit"])
        pr = re.search(r"\(#(\d+)\)\s*$", subject)
        spec_dir = HERE / "specs" / args.write
        spec_dir.mkdir(parents=True, exist_ok=True)
        spec = {
            "source_pr": int(pr.group(1)) if pr else 0,
            "merge_commit": found["merge_commit"],
            "fail_to_pass": found["fail_to_pass"],
            "pass_to_pass": found["pass_to_pass"],
            "difficulty": "unknown",
            "category": "bugfix",
        }
        (spec_dir / "task.json").write_text(json.dumps(spec, indent=2) + "\n")
        print(f"wrote {spec_dir / 'task.json'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
