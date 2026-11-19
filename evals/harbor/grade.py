"""Verifier for a loregarden Harbor task. Runs inside the task container, after the agent.

Runs the task's fail-to-pass and pass-to-pass tests in one pytest invocation and writes
`/logs/verifier/reward.json`:

- `resolved`: 1 only when every fail-to-pass test passed and every pass-to-pass test
  passed.
- `fail_to_pass`, `pass_to_pass`: the fraction of each set that passed (pass-to-pass is
  1.0 when the changed files hold no test beyond the fail-to-pass set).

A test the report does not contain counts as failed, and so does a skipped one: a run
that collected nothing must score 0, not "nothing failed", and a test skipped for a
missing tool is an environment gap the oracle must expose.
"""

from __future__ import annotations

import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REWARD_DIR = Path("/logs/verifier")
SERVER_PREFIX = "server/"


def _server_relative(node_id: str) -> str:
    if not node_id.startswith(SERVER_PREFIX):
        raise SystemExit(f"grade: test id {node_id!r} is not under {SERVER_PREFIX}")
    return node_id[len(SERVER_PREFIX) :]


def _junit_key(node_id: str) -> tuple[str, str]:
    """(classname, name) as pytest's junit report spells a node id relative to server/."""
    path, *rest = node_id.split("::")
    if not rest:
        raise SystemExit(f"grade: fail-to-pass id {node_id!r} names no test")
    module = path.removesuffix(".py").replace("/", ".")
    return ".".join([module, *rest[:-1]]), rest[-1]


def _module_of(target: str) -> str:
    return target.split("::")[0].removesuffix(".py").replace("/", ".")


def _passed(case: ET.Element) -> bool:
    return not any(child.tag in ("failure", "error", "skipped") for child in case)


def main(config_path: str) -> int:
    config = json.loads(Path(config_path).read_text())
    f2p = [_server_relative(i) for i in config["fail_to_pass"]]
    p2p = [_server_relative(i) for i in config["pass_to_pass"]]
    report = Path("/tmp/grade-junit.xml")

    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-p",
        "no:cacheprovider",
        "-q",
        f"--junitxml={report}",
        "-o",
        "junit_family=xunit1",
        *f2p,
        *p2p,
    ]
    print("grade: running", " ".join(cmd), flush=True)
    proc = subprocess.run(cmd, check=False)
    print(f"grade: pytest exited {proc.returncode}", flush=True)

    cases: dict[tuple[str, str], bool] = {}
    if report.is_file():
        for case in ET.parse(report).iter("testcase"):
            cases[(case.get("classname", ""), case.get("name", ""))] = _passed(case)
    else:
        print("grade: pytest wrote no report; every test counts as failed", flush=True)

    f2p_keys = {_junit_key(i) for i in f2p}
    f2p_passed = sum(1 for key in f2p_keys if cases.get(key, False))
    p2p_modules = {_module_of(t) for t in p2p}
    p2p_cases = {
        key: ok
        for key, ok in cases.items()
        if key not in f2p_keys
        and any(key[0] == m or key[0].startswith(m + ".") for m in p2p_modules)
    }
    p2p_passed = sum(p2p_cases.values())

    for key in sorted(f2p_keys):
        print(
            f"grade: F2P {'PASS' if cases.get(key, False) else 'FAIL'} {key[0]}::{key[1]}"
        )
    print(f"grade: P2P {p2p_passed}/{len(p2p_cases)} passed")
    if not p2p_cases:
        # Legitimate when every test in the files is fail-to-pass. A run that collected
        # nothing cannot pass through here: its fail-to-pass tests are missing, so failed.
        print("grade: no pass-to-pass tests beyond the fail-to-pass set", flush=True)

    resolved = (
        bool(f2p_keys) and f2p_passed == len(f2p_keys) and p2p_passed == len(p2p_cases)
    )
    reward = {
        "resolved": 1.0 if resolved else 0.0,
        "fail_to_pass": f2p_passed / len(f2p_keys) if f2p_keys else 0.0,
        "pass_to_pass": p2p_passed / len(p2p_cases) if p2p_cases else 1.0,
    }
    REWARD_DIR.mkdir(parents=True, exist_ok=True)
    (REWARD_DIR / "reward.json").write_text(json.dumps(reward))
    print("grade: reward", json.dumps(reward), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
