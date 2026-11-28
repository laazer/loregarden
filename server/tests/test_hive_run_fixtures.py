"""Record the Hive map's run fixtures from real logs, and fail when they go stale (S8).

    LOREGARDEN_RECORD_FIXTURES=1 pytest tests/test_hive_run_fixtures.py   # write
    pytest tests/test_hive_run_fixtures.py                                # check

Five fixtures in `client/src/test/fixtures/hive-runs/`, each carrying one run's `adapter`,
`call_lines_expected`, `status`, `started_at` and log lines, so a jest test can replay a real log
instead of three hand-written rows:

| fixture            | run        | why it is here                                               |
|--------------------|------------|--------------------------------------------------------------|
| `busy.json`        | `5e46fc12` | the worst measured rate: 40 call lines inside one 10 s window |
| `busiest.json`     | `e38b0c24` | the most calls of any run: 191, collapsing to 78 acts         |
| `failing.json`     | `fcc2a8a7` | a run-level failure — `ERR`/`FAIL` after 82 clean calls        |
| `no-calls.json`    | `1866fb01` | a `cursor-agent` run: no call lines, and none expected        |
| `failed-call.json` | payload    | the only source of a failed *call*; see below                 |

Four of the five are read from `agent_runs` / `run_log_lines`. The fifth cannot be: every log line
in the database was written before S1a, so `is_error` was dropped and no historical log holds a
`result · failed` line at all. It is derived instead from `fixtures/run_75d1f3_tool_calls.jsonl` —
a verbatim trim of `data/run-logs/run_75d1f3-2cd98e21.out`, which still has the raw `is_error`
flags — driven through the real server formatter, so the lines the client replays are the lines the
server would write rather than lines a test author chose.

`busiest.json` is not in the acceptance criteria. S8 names `5e46fc12` as "busy" while S3 attributes
"191 calls collapse to 78 acts, one act at 31" to "the busy fixture"; measured, those numbers are
`e38b0c24`'s (407 lines, 191 calls) and `5e46fc12` has 117 calls collapsing to 65 acts. Both runs
are recorded so that no test asserts a number about a run that does not have it. See the
test-design artifact's spec-gap list.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from loregarden.config import resolved_database_path
from loregarden.services.run_log_stream import format_stream_payload
from tests.repo_paths import OWN_CHECKOUT_ROOT, REPO_ROOT

#: Mirror of `tool_call_line.CALL_LINE_ADAPTERS`, as adapter ids. A literal rather than an import
#: so the unconditional shape checks below keep working before S1b lands and in any checkout with
#: no live database — this file's job is to catch a stale fixture, which is not S1b's business.
#: The real constant is pinned in `test_adapter_for_command.py`.
CALL_LINE_ADAPTER_IDS = frozenset({"claude"})

# The fixtures are committed in *this* tree, so they are located from this file rather than
# through `REPO_ROOT`, which an inherited `LOREGARDEN_REPO_ROOT` can point at another checkout.
FIXTURES = OWN_CHECKOUT_ROOT / "client" / "src" / "test" / "fixtures" / "hive-runs"
PAYLOAD = Path(__file__).parent / "fixtures" / "run_75d1f3_tool_calls.jsonl"
RECORD = os.environ.get("LOREGARDEN_RECORD_FIXTURES") == "1"

#: fixture name -> the live `agent_runs.id` it is recorded from.
RECORDED_RUNS = {
    "busy": "5e46fc12-45e5-45a1-b6ab-a6b7f8b975e2",
    "busiest": "e38b0c24-93ee-4d6f-99fb-0f4a98d71b8b",
    "failing": "fcc2a8a7-9235-4c08-a5a6-da1a678845f4",
    "no-calls": "1866fb01-a1ca-4267-a83e-7c0bdd1b80f6",
}

#: The synthetic clock `failed-call.json`'s lines are stamped on, one second apart. The payload
#: carries no per-event timestamp, and a recorded-at clock would re-stamp the fixture on every
#: recording and make the committed file churn.
PAYLOAD_START = datetime(2026, 10, 16, 16, 0, 0)
PAYLOAD_RUN_ID = "75d1f3ff-0000-4000-8000-2cd98e210000"

#: What the model reads off every fixture. A fixture missing one of these is unusable, and a
#: `KeyError` in a jest test says far less than this does.
REQUIRED_KEYS = {
    "run_id",
    "run_code",
    "agent_id",
    "skill_name",
    "stage_key",
    "status",
    "adapter",
    "call_lines_expected",
    "started_at",
    "lines",
}

#: Measured over these exact fixtures with the real `classifyLine`, on 2026-11-26. The client's
#: own tests assert the act-level numbers; these are the raw log facts the fixtures must keep, so
#: a re-recording that silently truncated a log fails here rather than in a jest assertion that
#: looks like a model bug.
EXPECTED_SHAPE = {
    # fixture: (lines, call lines, result lines, failed-result lines, adapter, call_lines_expected)
    "busy": (253, 117, 117, 0, "claude", True),
    "busiest": (407, 191, 191, 0, "claude", True),
    "failing": (180, 82, 82, 0, "", False),
    "no-calls": (100, 0, 0, 0, "cursor", False),
    "failed-call": (32, 16, 16, 2, "claude", True),
}


def _live_db() -> Path | None:
    raw = os.environ.get("LOREGARDEN_HIVE_FIXTURE_DB")
    candidate = Path(raw) if raw else resolved_database_path()
    return candidate if candidate.is_file() else None


def _headline(text: str) -> str:
    return text.split("\n", 1)[0]


def _counts(lines: list[dict]) -> tuple[int, int, int, int]:
    heads = [_headline(line["text"]) for line in lines if line["tag"] == "TOOL"]
    return (
        len(lines),
        sum(1 for h in heads if h.startswith("call · ")),
        sum(1 for h in heads if h == "result" or h.startswith("result · ")),
        sum(1 for h in heads if h.startswith("result · ")),
    )


def _record_from_database(name: str, run_id: str, db: Path) -> dict:
    # Imported here, not at module scope: `adapter_for_command` arrives with S1b, and the shape
    # checks in this file must stay runnable until it does.
    from loregarden.services.cli_settings import adapter_for_command

    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "select id, run_code, agent_id, skill_name, stage_key, status, command, started_at"
            " from agent_runs where id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            pytest.fail(f"{name}: run {run_id} is not in {db}; re-point RECORDED_RUNS")
        lines = [
            {"time": time, "tag": tag, "text": text}
            for time, tag, text in conn.execute(
                "select time, tag, text from run_log_lines where run_id = ? order by seq", (run_id,)
            )
        ]
    finally:
        conn.close()
    adapter = adapter_for_command(row[6] or "")
    return {
        "run_id": row[0],
        "run_code": row[1],
        "agent_id": row[2],
        "skill_name": row[3] or "",
        "stage_key": row[4],
        "status": row[5],
        "adapter": adapter,
        "call_lines_expected": adapter in CALL_LINE_ADAPTER_IDS,
        "started_at": str(row[7]).replace(" ", "T") + "Z",
        "lines": lines,
    }


def _derive_failed_call() -> dict:
    """The payload through the real formatter. No rule of this test's own decides a failure."""
    lines = []
    for index, raw in enumerate(PAYLOAD.read_text().splitlines()):
        if not raw.strip():
            continue
        line = format_stream_payload(json.loads(raw))
        assert line is not None, f"payload event {index} formats to nothing"
        assert line.tag == "TOOL", f"payload event {index} is {line.tag}, not TOOL"
        lines.append(
            {
                "time": (PAYLOAD_START + timedelta(seconds=index)).strftime("%H:%M:%S"),
                "tag": line.tag,
                "text": line.text,
            }
        )
    return {
        "run_id": PAYLOAD_RUN_ID,
        "run_code": "run_75d1f3",
        "agent_id": "backend_implementer",
        "skill_name": "",
        "stage_key": "implement",
        "status": "succeeded",
        "adapter": "claude",
        "call_lines_expected": True,
        "started_at": PAYLOAD_START.isoformat() + "Z",
        "source_payload": "server/tests/fixtures/run_75d1f3_tool_calls.jsonl",
        "lines": lines,
    }


def _write(name: str, doc: dict) -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    (FIXTURES / f"{name}.json").write_text(json.dumps(doc, indent=2) + "\n")


def _read(name: str) -> dict:
    path = FIXTURES / f"{name}.json"
    if not path.is_file():
        pytest.fail(f"{path} is missing; record it with LOREGARDEN_RECORD_FIXTURES=1")
    return json.loads(path.read_text())


@pytest.mark.parametrize("name", sorted(RECORDED_RUNS))
def test_a_recorded_run_fixture_matches_the_database(name: str):
    """Record, or check against the live rows. A finished run's lines never change, so a mismatch
    is the fixture drifting from the schema, not the run moving on."""
    db = _live_db()
    if RECORD:
        if db is None:
            pytest.fail(
                "LOREGARDEN_RECORD_FIXTURES=1 but no database; set LOREGARDEN_HIVE_FIXTURE_DB"
            )
        _write(name, _record_from_database(name, RECORDED_RUNS[name], db))
    committed = _read(name)
    assert committed["run_id"] == RECORDED_RUNS[name]
    if db is None:
        # Not a pass: nothing was compared. CI and other checkouts have no live database, and the
        # shape tests below still run over the committed bytes.
        pytest.skip(f"no live database to re-read {name} from; shape is still checked")
    assert committed == _record_from_database(name, RECORDED_RUNS[name], db)


def test_the_failed_call_fixture_is_what_the_real_formatter_writes():
    """S8-AC3. Fails until S1a lands: today's formatter drops `is_error`, so it writes 0
    `result · failed` lines where the committed fixture has 2."""
    if RECORD:
        _write("failed-call", _derive_failed_call())
    assert _read("failed-call") == _derive_failed_call()


@pytest.mark.parametrize("name", sorted(EXPECTED_SHAPE))
def test_every_fixture_carries_the_fields_and_the_volume_the_model_reads(name: str):
    """Unconditional, so a stale or truncated fixture fails where there is no live database."""
    doc = _read(name)
    assert REQUIRED_KEYS <= set(doc), REQUIRED_KEYS - set(doc)
    lines, calls, results, failed, adapter, expected = EXPECTED_SHAPE[name]
    assert _counts(doc["lines"]) == (lines, calls, results, failed)
    assert doc["adapter"] == adapter
    assert doc["call_lines_expected"] is expected
    assert doc["call_lines_expected"] is (adapter in CALL_LINE_ADAPTER_IDS)
    assert all(set(line) == {"time", "tag", "text"} for line in doc["lines"])
    assert all(line["time"].count(":") == 2 for line in doc["lines"])
    assert doc["started_at"].endswith("Z")


def test_the_fixtures_cover_every_activity_reading_and_every_run_phase():
    """The set exists to span S6's phases and S7's three nothings. A fixture set that drifted into
    five Claude successes would leave `unknown` and `ended-by-failure` untested while every
    assertion above still passed."""
    docs = {name: _read(name) for name in EXPECTED_SHAPE}
    adapters = {doc["adapter"] for doc in docs.values()}
    assert "" in adapters, "no fixture has an unresolvable adapter (S7's 'adapter not recorded')"
    assert {"claude", "cursor"} <= adapters
    assert {doc["status"] for doc in docs.values()} >= {"succeeded", "failed"}
    # `no-calls` is the only fixture that must have no call lines, and `failed-call` the only one
    # with a failed one: swapping either would quietly delete a branch's only coverage.
    assert _counts(docs["no-calls"]["lines"])[1] == 0
    assert docs["no-calls"]["call_lines_expected"] is False
    assert _counts(docs["failed-call"]["lines"])[3] == 2
    # `failing` is S6's case, not S4's: it predates S1a and holds no failed *call*.
    assert _counts(docs["failing"]["lines"])[3] == 0
    assert docs["failing"]["status"] == "failed"
    assert any(line["tag"] in {"ERR", "FAIL"} for line in docs["failing"]["lines"])


def test_the_committed_payload_still_holds_the_run_whose_failures_it_was_trimmed_for():
    """The trim is the only committed source of a failed call; an empty or re-trimmed payload
    would make `failed-call.json` unverifiable while every check above still passed."""
    events = [json.loads(raw) for raw in PAYLOAD.read_text().splitlines() if raw.strip()]
    assert len(events) == 32
    errors = sum(
        1
        for event in events
        for block in ((event.get("message") or {}).get("content") or [])
        if block.get("type") == "tool_result" and block.get("is_error") is True
    )
    assert errors == 2


@pytest.mark.slow(
    reason="runs a second pytest process, whose conftest import is ~4s of the ~5s; FIXTURES "
    "is resolved at import, so no in-process override can move it"
)
def test_the_fixture_tests_read_their_own_checkout_under_a_repo_root_override(tmp_path):
    """S3(b)'s fixtures must be found in the tree this test file lives in, not in another checkout.

    `FIXTURES` is built from `tests.repo_paths.REPO_ROOT`, which honours a `LOREGARDEN_REPO_ROOT`
    override. The criteria check before the gate runs these tests from the server process, whose
    environment carries that variable naming the *primary* checkout (`cli_adapters`'
    `STATE_BINDING_ENV_VARS`, `mcp_context`), while `cwd` is the ticket's worktree — and nothing in
    `run_verify_command`'s `scrubbed_git_env()` removes it. So the fixtures this branch committed
    are looked for in a tree that does not have them, and the two tests below fail with
    "<other>/client/src/test/fixtures/hive-runs/busy.json is missing", which reads as a missing
    fixture rather than as a test reading the wrong tree. That is how S3 came back unanswered.

    The override here names a *real* loregarden checkout, because that is the live case: a decoy
    with no `.lefthook/scripts/pass_receipt.py` dies in `conftest.py` instead, which would be a
    different failure. Run in a subprocess because `FIXTURES` is resolved at import, so no
    in-process monkeypatch can move it.

    `PAYLOAD` is already located as `Path(__file__).parent / "fixtures" / ...`. The committed
    client fixtures must be located the same way.
    """
    other = tmp_path / "other-checkout"
    other.mkdir()
    (other / "lefthook.yml").write_text("# a real loregarden checkout, just not this one\n")
    # Everything `conftest.py` reads out of the root, so collection succeeds there as it does in
    # the primary checkout; the only thing missing is this branch's own client fixtures.
    (other / ".lefthook").symlink_to(REPO_ROOT / ".lefthook")
    here = Path(__file__)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            f"{here.parent.name}/{here.name}::test_the_fixtures_cover_every_activity_reading_and_every_run_phase",
            f"{here.parent.name}/{here.name}::test_the_failed_call_fixture_is_what_the_real_formatter_writes",
        ],
        # `server/`, the directory the criteria check runs pytest in (`uv run --directory server`).
        # From `tests/` the venv's editable install of `loregarden` would shadow this worktree's
        # own package with the primary checkout's, and the formatter under test would be the wrong
        # one — a trap that has nothing to do with what this test is about.
        cwd=here.parents[1],
        env={**os.environ, "LOREGARDEN_REPO_ROOT": str(other)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, (
        "the recorded-fixture tests read another checkout when LOREGARDEN_REPO_ROOT names one, "
        "which is how the criteria check before the gate runs them:\n" + proc.stdout[-3000:]
    )
