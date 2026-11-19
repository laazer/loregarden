"""Build a Harbor task directory from a merged loregarden PR.

    python3 evals/harbor/build_task.py relative-repo-root [--out evals/harbor/.build]

Reads `specs/<name>/task.json` and `specs/<name>/instruction.md` and writes
`<out>/<name>/` in Harbor's task layout:

- `environment/`: the repository at the merge's first parent (`git archive`, so only
  committed files), with the server's dependencies synced.
- `tests/`: the test files as the merge left them, copied over the agent's tree only
  after it finishes, plus `grade.py` and the fail-to-pass / pass-to-pass ids.
- `solution/solve.sh`: the merge's non-test diff, for the oracle agent.

The agent never sees the merge's test files: they reach the container through `tests/`,
which Harbor uploads after the agent has run.

Run (Harbor 0.26.0, `uv tool install harbor==0.26.0`; needs Docker Compose >= 2.1.1),
from `evals/harbor/`, under the capacity wrapper:

    harbor run -p .build/<name> -a oracle -o jobs -n 1 -y     # must score resolved=1
    harbor run -p .build/<name> -a nop    -o jobs -n 1 -y     # must score resolved=0
    harbor run -p .build/<name> -a claude-code -m <model> -o jobs -n 1 -y

`harbor run` exits 0 even when every trial raised; read `jobs/<job>/result.json`.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
TEST_DIR_PREFIX = "server/tests/"
UV_IMAGE = "ghcr.io/astral-sh/uv:0.9.7"
BASE_IMAGE = "python:3.11-slim-bookworm"

TASK_TOML = """\
schema_version = "1.4"

[task]
name = "loregarden/{name}"
description = "Historical loregarden fix, PR #{pr}."
keywords = ["loregarden", "{category}"]

[metadata]
difficulty = "{difficulty}"
category = "{category}"
source_pr = {pr}
merge_commit = "{merge}"

[verifier]
environment_mode = "shared"
timeout_sec = 900.0

[agent]
timeout_sec = 1800.0

[environment]
build_timeout_sec = 1200.0
cpus = 2
memory_mb = 4096
"""

DOCKERFILE = f"""\
FROM {BASE_IMAGE}
COPY --from={UV_IMAGE} /uv /usr/local/bin/uv
RUN apt-get update \\
    && apt-get install -y --no-install-recommends git ca-certificates curl procps tmux \\
    && rm -rf /var/lib/apt/lists/*
# Tests build throwaway repositories and commit in them; a host has an identity, so must this.
RUN git config --global user.email eval@localhost \\
    && git config --global user.name eval \\
    && git config --global init.defaultBranch main \\
    && git config --system --add safe.directory '*'
# Harbor runs the agent and verifier as a user other than the one that built /app, and
# git then refuses the repository ("dubious ownership"); `init_db`'s guard needs it.
WORKDIR /app
COPY src.tar.gz /tmp/src.tar.gz
RUN tar -xzf /tmp/src.tar.gz -C /app && rm /tmp/src.tar.gz \\
    && git init -q && git add -A \\
    && git -c user.email=eval@localhost -c user.name=eval commit -qm baseline
RUN cd /app/server && uv sync --frozen
RUN mkdir -p /logs
"""

TEST_SH = """\
#!/bin/bash
set -euo pipefail
mkdir -p /logs/verifier
# The merge's own test files win over anything the agent wrote at those paths.
cp -a /tests/files/. /app/
cd /app/server
uv run --frozen python /tests/grade.py /tests/config.json
"""

SOLVE_SH = """\
#!/bin/bash
set -euo pipefail
cd /app
git apply --whitespace=nowarn <<'__LOREGARDEN_SOLUTION__'
{patch}
__LOREGARDEN_SOLUTION__
"""


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout


def build(name: str, out_root: Path) -> Path:
    spec_dir = HERE / "specs" / name
    spec = json.loads((spec_dir / "task.json").read_text())
    merge = spec["merge_commit"]
    parent = git("rev-parse", f"{merge}^1").strip()

    changed = git("diff", "--name-status", parent, merge).splitlines()
    test_files: list[str] = []
    source_files: list[str] = []
    for line in changed:
        status, path = line.split("\t", 1)
        if path.startswith(TEST_DIR_PREFIX):
            if status.startswith("D"):
                raise SystemExit(
                    f"{name}: the merge deletes test file {path}; not supported"
                )
            test_files.append(path)
        else:
            source_files.append(path)
    if not test_files:
        raise SystemExit(f"{name}: the merge changes no file under {TEST_DIR_PREFIX}")
    if not source_files:
        raise SystemExit(
            f"{name}: the merge changes only tests; there is nothing to solve"
        )

    task = out_root / name
    if task.exists():
        shutil.rmtree(task)
    (task / "environment").mkdir(parents=True)
    (task / "tests" / "files").mkdir(parents=True)
    (task / "solution").mkdir()

    (task / "task.toml").write_text(
        TASK_TOML.format(
            name=name,
            pr=spec["source_pr"],
            merge=merge,
            difficulty=spec.get("difficulty", "unknown"),
            category=spec.get("category", "unknown"),
        )
    )
    shutil.copyfile(spec_dir / "instruction.md", task / "instruction.md")

    git(
        "archive",
        "--format=tar.gz",
        "-o",
        str(task / "environment" / "src.tar.gz"),
        parent,
    )
    (task / "environment" / "Dockerfile").write_text(DOCKERFILE)

    for path in test_files:
        dest = task / "tests" / "files" / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(
            subprocess.run(
                ["git", "show", f"{merge}:{path}"],
                cwd=REPO,
                check=True,
                capture_output=True,
            ).stdout
        )
    (task / "tests" / "config.json").write_text(
        json.dumps(
            {
                "fail_to_pass": spec["fail_to_pass"],
                "pass_to_pass": spec["pass_to_pass"],
            },
            indent=2,
        )
    )
    shutil.copyfile(HERE / "grade.py", task / "tests" / "grade.py")
    test_sh = task / "tests" / "test.sh"
    test_sh.write_text(TEST_SH)
    test_sh.chmod(0o755)

    patch = git("diff", "--binary", parent, merge, "--", *source_files)
    if "__LOREGARDEN_SOLUTION__" in patch:
        raise SystemExit(f"{name}: the patch contains the heredoc terminator")
    solve = task / "solution" / "solve.sh"
    solve.write_text(SOLVE_SH.format(patch=patch.rstrip("\n")))
    solve.chmod(0o755)

    print(
        f"{name}: parent {parent[:10]}, {len(source_files)} source / {len(test_files)} test files -> {task}"
    )
    return task


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("names", nargs="+", help="spec directories under specs/")
    parser.add_argument("--out", type=Path, default=HERE / ".build")
    args = parser.parse_args()
    for name in args.names:
        build(name, args.out.resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
