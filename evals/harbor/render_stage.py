"""Render the prompt loregarden's own agent would receive for one stage. Runs in the task container.

    python3 render_stage.py --instruction <file> --stage implement --out <dir>

Initializes a fresh control-plane database in the container's checkout, files the
task's instruction as a bug (under the initiative > milestone > feature parents a
bug requires), moves it to `--stage`, and checks that stage out to an external
harness with `loregarden_begin_external_stage`, the same call an outside driver
makes. Writes `<out>/prompt.txt` and `<out>/stage.json` (the run's ids, minus
the prompt).

Every step goes through `scripts/loregarden-cli.sh mcp call`, and any failure
exits non-zero with the tool's own words: a trial must never run on a prompt
that was not rendered.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path("/app")
CLI = REPO / "scripts" / "loregarden-cli.sh"
WORKSPACE = "loregarden"


def cli(*args: str) -> str:
    proc = subprocess.run(
        [str(CLI), *args], cwd=REPO, capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        raise SystemExit(
            f"render_stage: `{' '.join(args[:3])}` exited {proc.returncode}\n"
            f"{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
        )
    return proc.stdout


def call(tool: str, **arguments: str) -> dict:
    out = cli("mcp", "call", tool, *(f"{k}={v}" for k, v in arguments.items()))
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"render_stage: {tool} returned non-JSON: {out[:500]!r}"
        ) from exc


def render(instruction_path: Path, stage: str, out: Path) -> None:
    cli("db", "init")
    parent = ""
    for kind in ("initiative", "milestone", "feature"):
        args = {"title": f"Eval {kind}", "work_item_type": kind}
        if kind != "initiative":
            args |= {"workspace_slug": WORKSPACE, "parent": parent}
        parent = call("loregarden_create_ticket", **args)["id"]
    ticket = call(
        "loregarden_create_ticket",
        workspace_slug=WORKSPACE,
        title="Eval task",
        work_item_type="bug",
        description=f"@{instruction_path}",
        parent=parent,
    )
    call("loregarden_set_ticket_workflow", ticket_id=ticket["id"], stage_key=stage)
    orch = call(
        "loregarden_start_orchestration",
        ticket_id=ticket["id"],
        driver="external_mcp",
        external_harness="claude_code",
    )
    begun = call("loregarden_begin_external_stage", run_id=orch["id"], stage_key=stage)
    runs = begun.get("runs") or []
    if begun.get("stage_key") != stage or len(runs) != 1 or not runs[0].get("prompt"):
        raise SystemExit(
            f"render_stage: expected one {stage} run with a prompt, got stage "
            f"{begun.get('stage_key')!r} with {len(runs)} run(s): {begun.get('message')!r}"
        )
    run = runs[0]
    out.mkdir(parents=True, exist_ok=True)
    (out / "prompt.txt").write_text(run["prompt"])
    meta = {k: v for k, v in run.items() if k != "prompt"}
    meta |= {
        "ticket_id": ticket["id"],
        "orchestration_run_id": orch["id"],
        "stage_key": stage,
    }
    (out / "stage.json").write_text(json.dumps(meta, indent=2))
    print(
        f"render_stage: {stage} prompt for {run['agent_id']}, {len(run['prompt'])} chars"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--instruction", type=Path, required=True)
    parser.add_argument("--stage", default="implement")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    render(args.instruction, args.stage, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
