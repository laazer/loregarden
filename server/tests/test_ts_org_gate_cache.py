"""The ts-organization gate's two costs, and that removing them changed nothing.

Measured on loregarden's own `client/src` (650 files, 122,247 lines) before
lg-workflow-integrity-808: grading one staged file took 4697ms, of which 3186ms
(68%) was rebuilding the cross-file DRY catalog from scratch and 455ms was
loading the TypeScript parser. Grading *zero* files — a backend-only change,
which is the common case at a stage transition — still cost 1290ms, because the
parser was required at module top level before the early return could avoid it.

Both are now avoided. The risk in avoiding the second one is the interesting
part: a stale catalog entry does not fail loudly, it silently weakens DRY
detection, so these tests are mostly about the cache being unable to hide a
finding. The cache is keyed on file *content* rather than mtime or size, which
is why `test_editing_a_catalogued_file_is_never_served_from_cache` can be a
straightforward assertion rather than a race.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / ".lefthook" / "scripts"
_GATE = _SCRIPTS / "ts_organization_check.cjs"

#: A function long enough to clear MIN_DUPLICATE_BODY_LINES. The name is part of
#: the key the gate builds — `normalizeBody` slices from the declaration line —
#: so both copies must declare the same name to be a duplicate at all.
DUPLICATED_FUNCTION = """export function sharedHelper(a: number, b: number): number {
  const total = a + b;
  const doubled = total * 2;
  const tripled = total * 3;
  const parts = String(total).split("");
  const joined = parts.join("-");
  const upper = joined.toUpperCase();
  const size = upper.length + tripled;
  if (doubled > 10) { return doubled; }
  if (total < 0) { return parts.length; }
  return total + size;
}
"""

UNRELATED_FUNCTION = "export function unrelated(): number {\n  return 1;\n}\n"


def _scrubbed_env() -> dict[str, str]:
    """GIT_DIR/GIT_WORK_TREE beat cwd, and a run nested in a worktree's hook
    inherits them pointing at the real repository."""
    return {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=_scrubbed_env())


@pytest.fixture
def ts_repo(tmp_path: Path) -> Path:
    """A repository with a `client/src` tree the gate will treat as its source root.

    A subdirectory of `tmp_path`, not `tmp_path` itself, so the cache directory
    beside it is genuinely outside the repository — otherwise
    `test_the_cache_lives_outside_the_repository` would be asserting against a
    layout the test itself created.

    Built in a fixture, so a repro whose scaffolding breaks reports an ERROR
    rather than looking like the behaviour under test.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main", ".")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    src = repo / "client" / "src"
    src.mkdir(parents=True)
    (src / "base.ts").write_text("export const x = 1;\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    return repo


def _run_gate(repo: Path, *files: str, env: dict[str, str] | None = None):
    return subprocess.run(
        ["node", str(_GATE), "--repo", str(repo), "--scope", "staged", *files],
        capture_output=True,
        text=True,
        timeout=120,
        env=env or _scrubbed_env(),
    )


def _cache_dir(tmp_path: Path) -> dict[str, str]:
    """Run the gate with its own TMPDIR, so one test's cache cannot reach another.

    The gate puts the cache under the system temp dir on purpose — a running
    orchestrator commits the whole working tree, so a cache inside the
    repository would be swept into an unrelated ticket's commit.
    """
    env = _scrubbed_env()
    cache_home = tmp_path / "gate-cache"
    cache_home.mkdir(exist_ok=True)
    env["TMPDIR"] = str(cache_home)
    return env


def _stage_duplicate(ts_repo: Path) -> str:
    """One catalogued copy, one graded copy, of the same function."""
    src = ts_repo / "client" / "src"
    (src / "catalogued.ts").write_text(DUPLICATED_FUNCTION)
    (src / "graded.ts").write_text(DUPLICATED_FUNCTION)
    _git(ts_repo, "add", "client/src/graded.ts")
    return str(src / "graded.ts")


def test_warm_and_cold_report_the_same_finding(ts_repo: Path, tmp_path: Path):
    """The whole risk of the cache, in one assertion: a second run must not be
    cheaper by being wrong."""
    env = _cache_dir(tmp_path)
    graded = _stage_duplicate(ts_repo)

    cold = _run_gate(ts_repo, graded, env=env)
    warm = _run_gate(ts_repo, graded, env=env)

    assert cold.returncode == 1, cold.stderr
    assert "duplicates existing code" in cold.stderr
    assert warm.returncode == cold.returncode
    assert warm.stderr == cold.stderr


def test_editing_a_catalogued_file_is_never_served_from_cache(ts_repo: Path, tmp_path: Path):
    """Keyed on content, so the edit cannot be missed — no mtime granularity to
    reason about. The finding must disappear, and come back when it is undone."""
    env = _cache_dir(tmp_path)
    graded = _stage_duplicate(ts_repo)
    catalogued = ts_repo / "client" / "src" / "catalogued.ts"

    assert _run_gate(ts_repo, graded, env=env).returncode == 1  # warms the cache

    catalogued.write_text(UNRELATED_FUNCTION)
    after_edit = _run_gate(ts_repo, graded, env=env)
    assert after_edit.returncode == 0, after_edit.stderr
    assert "duplicates existing code" not in after_edit.stderr

    catalogued.write_text(DUPLICATED_FUNCTION)
    restored = _run_gate(ts_repo, graded, env=env)
    assert restored.returncode == 1, restored.stdout
    assert "duplicates existing code" in restored.stderr


def test_a_corrupt_cache_rebuilds_rather_than_reporting_a_weakened_result(
    ts_repo: Path, tmp_path: Path
):
    """Fail open. A cache that cannot be read means "parse everything", which is
    slow and correct; the failure mode to avoid is a clean run over a catalog
    that was never built."""
    env = _cache_dir(tmp_path)
    graded = _stage_duplicate(ts_repo)
    assert _run_gate(ts_repo, graded, env=env).returncode == 1

    caches = list(Path(env["TMPDIR"]).glob("loregarden-ts-org-catalog-*.json"))
    assert caches, "the gate wrote no cache, so this test would prove nothing"
    caches[0].write_text("{ not json at all")

    after = _run_gate(ts_repo, graded, env=env)
    assert after.returncode == 1, after.stdout
    assert "duplicates existing code" in after.stderr


def test_the_cache_lives_outside_the_repository(ts_repo: Path, tmp_path: Path):
    """An orchestration sweep commits the whole working tree. A cache written
    inside it would land in an unrelated ticket's commit."""
    env = _cache_dir(tmp_path)
    graded = _stage_duplicate(ts_repo)
    _run_gate(ts_repo, graded, env=env)

    _git(ts_repo, "add", "-A")
    tracked = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=ts_repo,
        capture_output=True,
        text=True,
        env=_scrubbed_env(),
    ).stdout
    # The cache's own name, not the substring "catalog" — the fixture's
    # `catalogued.ts` matches that and would make this pass for the wrong reason.
    assert "loregarden-ts-org-catalog" not in tracked, tracked
    assert not list(ts_repo.rglob("loregarden-ts-org-catalog-*.json"))
    # And it really was written somewhere, or this proves nothing.
    assert list(Path(env["TMPDIR"]).glob("loregarden-ts-org-catalog-*.json"))


#: Records every module id node loads, then runs the gate in-process. The gate
#: calls `process.exit`, so the result is written from an `exit` handler.
_PARSER_PROBE = """
const Module = require("module");
const fs = require("fs");
const out = process.env.PROBE_OUT;
let loadedParser = false;
const original = Module._load;
Module._load = function (request, ...rest) {
  if (String(request).includes("typescript-estree")) loadedParser = true;
  return original.call(this, request, ...rest);
};
process.on("exit", () => fs.writeFileSync(out, JSON.stringify({ loadedParser })));
process.argv = [process.argv[0], process.env.PROBE_GATE, ...JSON.parse(process.env.PROBE_ARGS)];
require(process.env.PROBE_GATE);
"""


def _parser_was_loaded(repo: Path, args: list[str], tmp_path: Path) -> bool:
    probe = tmp_path / "probe.cjs"
    probe.write_text(_PARSER_PROBE)
    out = tmp_path / "probe.json"
    env = _cache_dir(tmp_path)
    env |= {"PROBE_OUT": str(out), "PROBE_GATE": str(_GATE), "PROBE_ARGS": json.dumps(args)}
    subprocess.run(["node", str(probe)], capture_output=True, text=True, timeout=120, env=env)
    return json.loads(out.read_text())["loadedParser"]


def test_the_parser_is_not_loaded_when_nothing_is_graded(ts_repo: Path, tmp_path: Path):
    """The 1290ms a backend-only stage transition used to pay to discover it had
    no work. Asserted by watching module loads, not by timing, which does not
    survive load."""
    args = ["--repo", str(ts_repo), "--scope", "staged"]

    assert _parser_was_loaded(ts_repo, args, tmp_path) is False


def test_the_parser_is_still_loaded_when_there_is_something_to_parse(ts_repo: Path, tmp_path: Path):
    """The control for the test above: it would also pass if the gate had simply
    stopped parsing."""
    graded = _stage_duplicate(ts_repo)
    args = ["--repo", str(ts_repo), "--scope", "staged", graded]

    assert _parser_was_loaded(ts_repo, args, tmp_path) is True
