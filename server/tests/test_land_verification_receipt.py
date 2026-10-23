"""Every landing records whether a suite ran, and what it said (lg-milestone-that-1419).

R3, R5 and R6 of the specification. AC4's census, AC7, AC8 and AC9.

The question these pin is one an operator could not ask before: *was a suite run
against this ticket's landing?* The words "no land verification is configured for
this workspace" reached only `LandResult.detail` and were persisted on nothing,
and `_record_full_output` wrote an Artifact only under `if not outcome.ok` — so
`select count(*) from artifacts where title like 'Land verification output%'` was
**0** across 19,418 live rows on 2026-10-23. Three recurrences of a ticket marked
done with its implementation missing were invisible until somebody ran the full
suite by hand.

The design these tests pin, so the implementer is not guessing at names:

- `ArtifactKind.LAND_VERIFICATION = "land_verification"`, one new enum member.
- **One row per landing, on every verdict, from one call site.** The title is
  `Land verification — <status> — <branch>` with `<status>` one of `passed` /
  `failed` / `disabled`, and `content_json` carries `status`, `stage_key`,
  `outcome`, `branch`, `target`, `candidate_sha`, `command`, `exit_code` and the
  FULL `output`.
- The four `GateOutcome` values map onto those three words:
  `PASSED`→`passed`, `FAILED`→`failed`, `UNAVAILABLE`→`failed` (the land was
  refused, so the tone is right) with `outcome: unavailable` keeping the
  distinction, `DISABLED`→`disabled`; and a landing that produced no verdict at
  all →`disabled` with `outcome: already_contained`.
- It REPLACES `_record_full_output`'s `ArtifactKind.LOG` row rather than sitting
  beside it, so the cost is one added row per landing and no data migration (the
  live count is 0).
- The writer is in `land_ticket`, reached through the `LandVerifier`, **not**
  inside the verify callback: `merge_tips` calls `verify` only when a candidate
  merge exists, so a writer in the callback would miss exactly the landings
  nobody verified — the subject of this ticket.

Why the census (`test_every_landing_that_records_a_landed_sha_writes_a_receipt`,
which AC4 names as the invariant's `prevented_by`) is an AST walk over
`land_ticket._record`'s call sites and not one exercised path: a path that
records a `landed_sha` and skips the receipt makes the `land_verification_ran`
detector fire on a landing that was fine. That is the lg-milestone-that-1418
learning — one gate, every writer reaching it, proved by a caller census rather
than by review.
"""

from __future__ import annotations

import ast
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest
from loregarden.models.domain import (
    Artifact,
    ArtifactKind,
    DockerFootprint,
    GateOutcome,
    TicketState,
)
from loregarden.services import land_verification
from loregarden.services.artifact_records import is_system_record
from loregarden.services.gate_capacity import GateCapacityUnavailable
from loregarden.services.git_merge_noco import merge_tips, read_branch_tips
from loregarden.services.git_subprocess import run_git
from loregarden.services.land_ticket import LandSkip, land_ticket
from loregarden.services.land_verification import (
    LAND_VERIFY_TIMEOUT_SECONDS,
    VerifyOutcome,
    land_verifier,
    run_verify_command,
)
from loregarden.services.orchestration_profile import (
    PRESERVED_GIT_KEYS,
    load_profile_from_path,
    orchestration_dir,
)
from sqlmodel import Session, col, select
from tests import land_receipt_helpers as helpers
from tests.land_receipt_helpers import FAILING_SUITE, PASSING_SUITE
from tests.worktree_helpers import commit_on, git

REPO_ROOT = Path(__file__).resolve().parents[2]

#: `sysexits.h` EX_UNAVAILABLE: the gate convention for "this never ran".
NEVER_RAN = "python3 -c 'import sys; sys.exit(69)'"

#: The three words `verdictTone` colours, and the whole of the client contract.
PASSED, FAILED, DISABLED = "passed", "failed", "disabled"

#: The one source file the census reads.
LAND_TICKET_SOURCE = REPO_ROOT / "server" / "loregarden" / "services" / "land_ticket.py"


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


# ---------------------------------------------------------------------------
# AC4's census. The named test is the invariant's `prevented_by`.
# ---------------------------------------------------------------------------

#: The functions in `land_ticket` that may record a landing. A `_record` call in
#: any other function is a landing path this file does not exercise, and
#: therefore one the receipt may silently skip.
RECORDING_FUNCTIONS = frozenset({"land_ticket", "_held_without_shared_history"})


def _record_call_sites(source: Path) -> dict[str, int]:
    """Every `_record(...)` call in `source`, by the function that makes it."""
    tree = ast.parse(source.read_text(encoding="utf-8"))
    sites: dict[str, int] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):  # py-org: allow-isinstance
            continue
        if node.name == "_record":
            continue
        calls = [
            sub
            for sub in ast.walk(node)
            if isinstance(sub, ast.Call)  # py-org: allow-isinstance
            and isinstance(sub.func, ast.Name)  # py-org: allow-isinstance
            and sub.func.id == "_record"
        ]
        if calls:
            sites[node.name] = len(calls)
    return sites


def _verified(session, tmp_path, name, commands):
    return helpers.build(session, tmp_path, name, commands=commands)


def _already_contained(session, tmp_path, name):
    """A branch the target already holds, landed for the first time.

    `merge_tips` calls `verify` only after its `commit-tree`, and only when a
    candidate merge exists, so this path produces NO verdict while still
    recording a `landed_sha` — the landing a writer inside the verify callback
    would miss.
    """
    landing = helpers.build(session, tmp_path, name, commands=[PASSING_SUITE])
    git(landing.repo, "update-ref", f"refs/heads/{landing.target}", landing.tip)
    return landing


def _squashed_onto_the_target(session, tmp_path, name):
    """The target holds the branch's work as a squash, sharing no commit with it."""
    landing = helpers.build(session, tmp_path, name, commands=[PASSING_SUITE])
    commit_on(landing.repo, landing.target, f"{name}.txt", "work\n")
    commit_on(landing.repo, landing.target, f"{name}.txt", "work, revised on the target\n")
    return landing


def _conflicted(session, tmp_path, name):
    landing = helpers.build(session, tmp_path, name, commands=[PASSING_SUITE])
    commit_on(landing.repo, landing.ticket.branch, "shared.txt", "ticket\n")
    commit_on(landing.repo, landing.target, "shared.txt", "sibling\n")
    return landing


def _uncommitted(session, tmp_path, name):
    landing = helpers.build(session, tmp_path, name, commands=[PASSING_SUITE])
    tree = helpers.ticket_tree(session, landing)
    (tree / "seed.txt").write_text("edited and never committed\n")
    return landing


#: Every landing path, what the receipt must say about it, and whether the land
#: happens. `None` is a call that is not a landing at all: nothing reached
#: `merge_without_checkout`, nothing recorded a `landed_sha`, and those refusals
#: are already visible as blocks.
SCENARIOS: tuple[tuple[str, object, tuple[str, str] | None], ...] = (
    (
        "a passing verification",
        lambda s, p, n: _verified(s, p, n, [PASSING_SUITE]),
        (PASSED, GateOutcome.PASSED.value),
    ),
    (
        "a failing verification",
        lambda s, p, n: _verified(s, p, n, [FAILING_SUITE]),
        (FAILED, GateOutcome.FAILED.value),
    ),
    (
        "a verification that never ran",
        lambda s, p, n: _verified(s, p, n, [NEVER_RAN]),
        (FAILED, GateOutcome.UNAVAILABLE.value),
    ),
    (
        "a workspace that configured nothing",
        lambda s, p, n: helpers.build(s, p, n),
        (DISABLED, GateOutcome.DISABLED.value),
    ),
    ("a branch the target already holds", _already_contained, (DISABLED, "already_contained")),
    (
        "the target holding the work as a squash",
        _squashed_onto_the_target,
        (DISABLED, "already_contained"),
    ),
    (
        "no repository at the workspace root",
        lambda s, p, n: helpers.build(s, p, n, repo=False),
        None,
    ),
    (
        "no branch in the repository",
        lambda s, p, n: helpers.build(s, p, n, with_branch=False),
        None,
    ),
    ("a base-branch target", lambda s, p, n: helpers.build(s, p, n, parented=False), None),
    ("uncommitted work in the ticket's tree", _uncommitted, None),
    ("a merge conflict", _conflicted, None),
)


def test_the_census_itself_reads_a_module_with_recording_paths_in_it():
    """A zero here would make the census below vacuous: an AST walk that finds
    no call site asserts nothing about the ones that exist."""
    sites = _record_call_sites(LAND_TICKET_SOURCE)

    assert sites, f"no `_record` call site found in {LAND_TICKET_SOURCE}"
    assert sum(sites.values()) >= 3, f"the landing paths measured at 58b4d0f4 were 3: {sites}"


def test_every_landing_that_records_a_landed_sha_writes_a_receipt(session, tmp_path):
    """AC4's `prevented_by`, and it is a census rather than one exercised path.

    Two halves, and both are load-bearing. The AST walk enumerates every caller
    of `land_ticket._record` — the only writer of `landed_sha` — and fails when
    one appears in a function this file does not exercise. Then each landing
    path is actually driven, and the receipt it leaves is counted: exactly one
    for every landing, none for a call that landed nothing.

    A path that records a landing and skips the receipt makes the
    `land_verification_ran` detector fire on a landing that was fine, which is
    the failure mode that costs a later ticket its verify stage arguing
    attribution.
    """
    sites = _record_call_sites(LAND_TICKET_SOURCE)
    assert set(sites) == RECORDING_FUNCTIONS, (
        f"`_record` is called from {sorted(set(sites) - RECORDING_FUNCTIONS)} as well; that is a "
        "landing path no scenario below exercises. Add it to SCENARIOS and to "
        "RECORDING_FUNCTIONS, or route it through the one that already writes the receipt"
    )

    for index, (label, build, expected) in enumerate(SCENARIOS):
        landing = build(session, tmp_path, f"c{index}")
        result = land_ticket(session, landing.ticket, landing.workspace)
        session.refresh(landing.ticket)
        rows = helpers.receipts(session, landing.ticket)

        if expected is None:
            assert rows == [], f"{label}: landed nothing and wrote {len(rows)} receipt(s)"
            assert landing.ticket.landed_sha == "", f"{label}: recorded a landing after all"
            continue

        status, outcome = expected
        assert len(rows) == 1, f"{label}: {len(rows)} receipts, not one — {[r.title for r in rows]}"
        payload = helpers.content(rows[0])
        assert (payload.get("status"), payload.get("outcome")) == (status, outcome), (
            f"{label}: {payload.get('status')!r}/{payload.get('outcome')!r}"
        )
        landed = status != FAILED
        assert bool(landing.ticket.landed_sha) is landed, (
            f"{label}: landed_sha={landing.ticket.landed_sha!r} with a {status} receipt"
        )
        assert result.ok is landed, f"{label}: {result}"


def test_an_idempotent_re_land_writes_no_second_receipt(session, tmp_path):
    """One row per landing, and a re-land of a branch already on record is not a
    second landing — `land_ticket` skips `_record` when the sha and branch are
    unchanged, and the receipt must skip with it. A row per reconcile-driven
    retry would be a per-ticket feed nobody can read."""
    landing = helpers.build(session, tmp_path, "reland", commands=[PASSING_SUITE])

    first = land_ticket(session, landing.ticket, landing.workspace)
    after_first = helpers.receipts(session, landing.ticket)
    second = land_ticket(session, landing.ticket, landing.workspace)

    assert first.ok and second.ok and second.skipped is LandSkip.ALREADY_LANDED
    assert len(after_first) == 1, [r.title for r in after_first]
    assert len(helpers.receipts(session, landing.ticket)) == 1, "the re-land wrote a second row"


def test_the_verify_callback_writes_no_receipt_of_its_own(session, tmp_path):
    """One writer, and it is not in the callback.

    `merge_tips` calls `verify` only when a candidate merge exists, so a writer
    inside the callback misses the landings that produced no verdict at all —
    exactly the landings nobody verified. Driving the verifier on its own must
    therefore leave no row: the verdict rides back to `land_ticket` and one call
    site writes it.
    """
    landing = helpers.build(session, tmp_path, "callback", commands=[PASSING_SUITE])
    candidate = _candidate(landing)

    verifier = land_verifier(session, landing.ticket, landing.workspace)
    outcome = verifier(candidate)

    assert outcome.outcome is GateOutcome.PASSED, outcome
    assert helpers.receipts(session, landing.ticket) == [], (
        "the verify callback wrote the receipt; a landing with no verdict then gets none"
    )


def _candidate(landing) -> str:
    """The merge commit a land would write, made here and left unpublished."""
    before = helpers.sha(landing.repo, landing.target)
    tips = read_branch_tips(landing.repo, landing.target, landing.ticket.branch)
    outcome = merge_tips(
        landing.repo, tips, target=landing.target, source=landing.ticket.branch, subject="candidate"
    )
    assert outcome.ok, outcome.detail
    git(landing.repo, "update-ref", f"refs/heads/{landing.target}", before, outcome.sha)
    return outcome.sha


# ---------------------------------------------------------------------------
# AC9. The verdict is readable in the table without opening the row, and
# without changing any client file.
# ---------------------------------------------------------------------------


@pytest.fixture(name="passed")
def passed_fixture(session, tmp_path):
    landing = helpers.build(session, tmp_path, "passing", commands=[PASSING_SUITE])
    result = land_ticket(session, landing.ticket, landing.workspace)
    assert result.ok and result.landed_sha, result
    session.refresh(landing.ticket)
    rows = helpers.receipts(session, landing.ticket)
    assert len(rows) == 1, f"fixture: {len(rows)} receipts, not one"
    return landing, rows[0], helpers.content(rows[0])


@pytest.fixture(name="refused")
def refused_fixture(session, tmp_path):
    landing = helpers.build(session, tmp_path, "failing", commands=[FAILING_SUITE])
    result = land_ticket(session, landing.ticket, landing.workspace)
    assert not result.ok, "fixture: the land must have been refused"
    session.refresh(landing.ticket)
    rows = helpers.receipts(session, landing.ticket)
    assert len(rows) == 1, f"fixture: {len(rows)} receipts, not one"
    return landing, rows[0], helpers.content(rows[0])


def test_the_title_carries_the_verdict_and_the_branch(passed):
    """AC9. The Title column and the Outputs tab's `matches()` search both read
    titles, so the verdict has to be in one — a row titled "Land verification"
    alone makes every landing look the same in the table."""
    landing, receipt, _ = passed

    assert receipt.title == f"Land verification — {PASSED} — {landing.ticket.branch}", receipt.title


def test_the_content_carries_what_report_verdict_requires_and_what_it_was_about(passed):
    """AC9. `reportVerdict` returns null without BOTH `status` and `stage_key`,
    and without it there is no pill and no badge. The rest is what the row is
    about: nobody can act on a verdict without knowing which landing it judged.
    """
    landing, _, payload = passed

    assert payload["status"] == PASSED
    assert isinstance(payload["stage_key"], str) and payload["stage_key"], payload
    assert payload["stage_key"] == landing.ticket.workflow_stage_key
    assert payload["branch"] == landing.ticket.branch
    assert payload["target"] == landing.target
    assert re.fullmatch(r"[0-9a-f]{40}", payload["candidate_sha"] or ""), payload["candidate_sha"]
    assert payload["candidate_sha"] == landing.ticket.landed_sha
    assert payload["command"] == PASSING_SUITE
    assert payload["exit_code"] == 0
    assert "42 passed" in payload["output"]


def test_a_passing_receipt_carries_every_commands_output_and_not_an_empty_pass(session, tmp_path):
    """AC9's `command`/`exit_code`/`output` on the path that is easy to leave blank.

    `_verify_candidate` returns one verdict for the whole configured list, and
    the shortest way to write its success case is `VerifyOutcome(ok=True,
    outcome=PASSED)` with nothing else on it — which is what it did before this
    ticket, because only refusals were ever recorded. A receipt built from that
    answers "did it pass?" and not "what did the suite say?", which is the
    question this row exists for. So the passing verdict carries the last
    command it ran and the output of every one of them.
    """
    first = "python3 -c 'print(\"first check ok\")'"
    landing = helpers.build(session, tmp_path, "multi", commands=[first, PASSING_SUITE])

    result = land_ticket(session, landing.ticket, landing.workspace)

    assert result.ok, result.detail
    payload = helpers.content(helpers.receipts(session, landing.ticket)[0])
    assert payload["command"] == PASSING_SUITE, (
        f"the verdict is about the last command that ran: {payload['command']!r}"
    )
    assert payload["exit_code"] == 0
    assert "first check ok" in payload["output"], (
        f"an earlier command's output was discarded: {payload['output']!r}"
    )
    assert "42 passed" in payload["output"]


def test_a_refusal_is_failed_and_names_the_command_that_said_so(refused):
    """AC9. `failed` is in `FAILING`, so the pill is bad — and the land was
    refused, which is the fact an operator acts on."""
    landing, receipt, payload = refused

    assert payload["status"] == FAILED
    assert payload["outcome"] == GateOutcome.FAILED.value
    assert payload["command"] == FAILING_SUITE
    assert payload["exit_code"] == 1
    assert payload["target"] == landing.target
    assert receipt.title.startswith(f"Land verification — {FAILED} — ")
    session_ticket = landing.ticket
    assert session_ticket.landed_sha == "", "a refused land must record nothing"


def test_a_verification_that_never_ran_is_bad_toned_but_still_distinguishable(session, tmp_path):
    """AC9's four-into-three decision, made deliberately and pinned here.

    `GateOutcome` has four values and `verdictTone` colours three words. "The
    tests never ran" and "the tests failed" route to different people, so the
    distinction must survive — but both refuse the land, so `bad` is the right
    tone for both. The pill stays at three words and `outcome` keeps the fourth.
    """
    landing = helpers.build(session, tmp_path, "nevr", commands=[NEVER_RAN])

    result = land_ticket(session, landing.ticket, landing.workspace)

    assert not result.ok
    payload = helpers.content(helpers.receipts(session, landing.ticket)[0])
    assert payload["status"] == FAILED, "UNAVAILABLE refuses the land, so the tone is bad"
    assert payload["outcome"] == GateOutcome.UNAVAILABLE.value, (
        "collapsing 'never ran' into 'failed' loses a distinction the module's own "
        "docstring says routes elsewhere"
    )


def test_an_unverified_landing_is_warn_toned_rather_than_passing(session, tmp_path):
    """AC9. `disabled` is in neither `PASSING` nor `FAILING`, so `verdictTone`
    falls through to `warn` — the right tone for a landing nobody verified,
    which is the state this ticket exists because of. It must not read as a pass.
    """
    landing = helpers.build(session, tmp_path, "unarmed")

    result = land_ticket(session, landing.ticket, landing.workspace)

    assert result.ok and result.landed_sha, "an unarmed workspace lands as it does today"
    payload = helpers.content(helpers.receipts(session, landing.ticket)[0])
    assert payload["status"] == DISABLED
    assert payload["outcome"] == GateOutcome.DISABLED.value
    assert payload["status"] != PASSED, "nothing ran; this must never read as a pass"


def _ts_source(relative: str) -> str:
    return (REPO_ROOT / "client" / "src" / "lib" / relative).read_text(encoding="utf-8")


def test_the_three_verdict_words_are_the_ones_the_client_already_colours():
    """AC9 and AC12 together: the pill is inherited, so the words are not ours
    to choose. Read from the client's own source rather than restated, because a
    restatement cannot go stale and this contract can."""
    source = _ts_source("ticketOutputs.ts")
    passing = set(
        re.findall(r'"([^"]+)"', re.search(r"PASSING = new Set\(\[([^\]]*)\]", source)[1])
    )
    failing = set(
        re.findall(r'"([^"]+)"', re.search(r"FAILING = new Set\(\[([^\]]*)\]", source)[1])
    )

    assert PASSED in passing, f"`{PASSED}` is not coloured good by verdictTone: {passing}"
    assert FAILED in failing, f"`{FAILED}` is not coloured bad by verdictTone: {failing}"
    assert DISABLED not in passing | failing, (
        "`disabled` must fall through to warn; a landing nobody verified is not a pass"
    )


def test_every_gate_outcome_has_a_receipt_word_so_no_verdict_can_raise(session, tmp_path):
    """The map is subscripted, and it runs AFTER the landing is committed.

    `record_land_verification` is called from `_recording_the_verification`,
    which `_record` has already committed `landed_sha` through by the time the
    receipt is written — so a `GateOutcome` member missing from `_RECEIPT_STATUS`
    is a `KeyError` that leaves a STAMPED landing with no receipt. That is the
    one state the AC4 detector reports and the census above cannot see, because
    the census reads `_record`'s call sites and this failure is past all of them.
    `SKIPPED` was the missing member; the assertion is totality, not that one
    name, so the next member added to the enum fails here instead.
    """
    missing = set(GateOutcome) - set(land_verification._RECEIPT_STATUS)
    assert not missing, (
        f"{sorted(m.value for m in missing)} would raise KeyError inside "
        "record_land_verification, after the landing is already stamped"
    )

    landing = helpers.build(session, tmp_path, "skipped")
    receipt = land_verification.record_land_verification(
        session,
        landing.ticket,
        branch=landing.ticket.branch,
        target=landing.target,
        candidate_sha=landing.tip,
        outcome=VerifyOutcome(ok=True, outcome=GateOutcome.SKIPPED, detail="not run"),
    )

    payload = helpers.content(receipt)
    assert payload["status"] == DISABLED, "no suite ran, so nobody verified the landing"
    assert payload["outcome"] == GateOutcome.SKIPPED.value, (
        "the status is one of three words; which outcome produced it stays in `outcome`"
    )


def test_report_verdict_still_needs_the_two_keys_the_receipt_carries():
    """AC9. `reportVerdict` is what renders the pill at all, and it returns null
    unless both keys are strings. If the client's requirement changes, this fails
    here rather than the row quietly losing its badge."""
    source = _ts_source("ticketTimeline.ts")

    guard = re.search(r"export function reportVerdict[\s\S]*?\n}", source)[0]
    assert 'record.status !== "string"' in guard, guard
    assert 'record.stage_key !== "string"' in guard, guard


# ---------------------------------------------------------------------------
# AC7 and AC8. The receipt replaces the log row, and nothing filters it out.
# ---------------------------------------------------------------------------


def test_the_receipt_replaces_the_log_row_the_full_output_used_to_live_in(session, refused):
    """AC7. One added row per landing is the volume argument AC8 rests on, so
    the receipt takes `_record_full_output`'s place rather than sitting beside
    it. No data migration: the live count of those rows is 0.
    """
    landing, _, payload = refused

    logs = session.exec(
        select(Artifact).where(
            col(Artifact.ticket_id) == landing.ticket.id,
            col(Artifact.kind) == ArtifactKind.LOG,
        )
    ).all()
    assert [a for a in logs if a.title.startswith("Land verification output")] == [], (
        "the old LOG row is still written beside the receipt"
    )
    assert "FAILED tests/test_x.py::case_0" in payload["output"], (
        "the receipt must carry the FULL output the LOG row used to hold"
    )


def test_the_receipt_is_not_a_system_record_so_it_shows_with_the_default_filter(passed):
    """AC8, and it is right by omission today — which is why it is pinned.

    `is_system_record` filters the StageBudget kinds, CONTEXT/'Run context' and
    LOG/'Run <code>', so a new kind passes through with no change to
    `artifact_records.py`. Adding this kind to that filter later would silently
    re-hide the one record this ticket exists to make visible, with 'Show system
    records' off — the default.
    """
    _, receipt, _ = passed

    for run_code in (None, "run_ee906b", receipt.title):
        assert not is_system_record(kind=receipt.kind, title=receipt.title, run_code=run_code), (
            f"the receipt is filtered out of the Outputs tab when run_code={run_code!r}"
        )


def test_the_receipt_has_a_kind_of_its_own():
    """AC7. A kind, not a title convention: the Outputs tab's Kind filter is
    built from the live rows' kinds, so `land_verification (n)` appears in it
    unaided."""
    assert ArtifactKind.LAND_VERIFICATION == "land_verification"
    assert ArtifactKind.LAND_VERIFICATION not in (ArtifactKind.LOG, ArtifactKind.EVIDENCE)


# ---------------------------------------------------------------------------
# AC7's second half. The guard lg-milestone-that-1238 built has never run,
# because no profile has ever armed it. That is the measured cause of all three
# recurrences, and the command's FORM is the rank-1 risk of arming it.
# ---------------------------------------------------------------------------

ARMED_COMMAND = "uv run --directory server pytest -q -n auto"


def _loregarden_profile():
    return load_profile_from_path(orchestration_dir() / "loregarden.yaml")


def test_loregarden_arms_the_land_verification_it_has_never_run():
    """AC7. `grep -rn land_verify agent_context/orchestration/` matched nothing
    before this change: `land_verification.py` landed 2026-10-17, is an ancestor
    of both reds' test-landing commits, and has never executed once."""
    git_config = _loregarden_profile().git

    assert git_config.land_verify_commands == [ARMED_COMMAND], git_config.land_verify_commands
    assert git_config.land_verify_capacity_footprint is DockerFootprint.HEAVY, (
        "a land verification is a suite on the orchestration thread; it queues like one"
    )
    assert "land_verify_commands" in PRESERVED_GIT_KEYS, (
        "the automation panel sends its whole view on every toggle and would blank this"
    )


def test_no_other_workspace_changes_behaviour():
    """AC7. `default.yaml` keeps its empty list, so every workspace without a
    profile of its own lands exactly as it does today."""
    assert (
        load_profile_from_path(orchestration_dir() / "default.yaml").git.land_verify_commands == []
    )


def test_the_armed_command_is_in_the_only_form_that_can_execute():
    """AC7, and all three of these were proposed by a planning lane.

    `run_verify_command` uses `shlex.split` and `subprocess.run` with no shell,
    so `cd server && …` and `bash -c …` cannot execute at all; `_verify_candidate`
    runs with cwd at the root of a throwaway `worktree add --detach` tree that has
    no `server/.venv`, which is what `--directory server` is for; and `_holding`
    already takes the capacity lease for the whole command list, so wrapping the
    command in `capacity-run.sh` would double-book the ledger.
    """
    command = _loregarden_profile().git.land_verify_commands[0]
    argv = shlex.split(command)

    assert argv[:4] == ["uv", "run", "--directory", "server"], argv
    assert "&&" not in command and ";" not in command, f"no shell runs this: {command!r}"
    assert not command.startswith("cd "), f"no shell runs this: {command!r}"
    assert "bash -c" not in command, f"no shell runs this: {command!r}"
    assert "capacity-run.sh" not in command, (
        "`_holding` already holds the lease for the whole list; wrapping it double-books"
    )
    assert LAND_VERIFY_TIMEOUT_SECONDS > 901, (
        f"the measured suite is 901s and the timeout is {LAND_VERIFY_TIMEOUT_SECONDS}s; "
        "a timeout REFUSES the land"
    )


@pytest.fixture(name="detached")
def detached_fixture():
    """A detached worktree of this repository at HEAD — the tree the configured
    commands will actually run in, with no `server/.venv` and cwd at the root."""
    holder = Path(tempfile.mkdtemp(prefix="lg-1419-armed-"))
    tree = holder / "tree"
    added = run_git(
        ["worktree", "add", "--detach", str(tree), "HEAD"],
        cwd=str(REPO_ROOT),
        check=False,
        capture_output=True,
        text=True,
    )
    assert added.returncode == 0, added.stderr
    try:
        yield tree
    finally:
        run_git(
            ["worktree", "remove", "--force", str(tree)],
            cwd=str(REPO_ROOT),
            check=False,
            capture_output=True,
            text=True,
        )
        shutil.rmtree(holder, ignore_errors=True)


def test_the_armed_command_parses_and_launches_where_it_will_actually_run(detached):
    """AC7. An unparseable or not-on-PATH command is UNAVAILABLE, and UNAVAILABLE
    REFUSES THE LAND — so a wrong string here blocks every landing in this
    workspace. Driven through `run_verify_command`'s own parse-and-launch path,
    in the detached worktree it will be given, with the launch captured rather
    than the 901s suite run.
    """
    command = _loregarden_profile().git.land_verify_commands[0]
    launched: dict[str, object] = {}
    real = subprocess.run

    def capture(argv, **kwargs):
        launched["argv"] = argv
        launched["cwd"] = kwargs.get("cwd")
        launched["env"] = kwargs.get("env") or {}
        return real([sys.executable, "-c", "print('captured')"], **kwargs)

    with mock.patch("loregarden.services.land_verification.subprocess.run", side_effect=capture):
        outcome = run_verify_command(command, detached, footprint=DockerFootprint.NONE)

    assert outcome.outcome is GateOutcome.PASSED, outcome
    assert launched["argv"] == shlex.split(command), launched["argv"]
    assert launched["cwd"] == str(detached), launched["cwd"]
    assert "GIT_DIR" not in launched["env"], (
        "GIT_DIR beats cwd for every git process beneath it; a test selector would "
        "then read the shared checkout and exit 0 over a tree nobody read"
    )


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is not on PATH on this machine")
def test_uv_resolves_the_server_project_from_the_detached_worktrees_root(detached):
    """AC7's measured constraint, exercised for real rather than reasoned about.

    `--directory server` is the whole reason the command is in this form: the
    tree is a fresh `worktree add --detach` with no `server/.venv` and cwd at the
    repository root. This runs the configured command's first four tokens with a
    trivial payload, so it proves uv resolves the project there without paying
    for the suite.
    """
    command = _loregarden_profile().git.land_verify_commands[0]
    prefix = shlex.split(command)[:4]
    probe = shlex.join([*prefix, "python", "-c", "import pytest; print('resolved')"])

    outcome = run_verify_command(probe, detached, footprint=DockerFootprint.NONE)

    assert outcome.outcome is GateOutcome.PASSED, f"{outcome.detail}\n{outcome.output}"
    assert "resolved" in outcome.output


def test_a_landing_in_a_state_other_than_done_still_gets_its_receipt(session, tmp_path):
    """The receipt is about the landing, not about the ticket's state: `landing`
    derives `done` after `land_ticket` returns, and the detector reads
    `state == DONE` AND a receipt. A receipt written only for already-done
    tickets would make every real landing a violation one tick later."""
    landing = helpers.build(session, tmp_path, "midflight", commands=[PASSING_SUITE])
    assert landing.ticket.state is TicketState.IN_PROGRESS

    land_ticket(session, landing.ticket, landing.workspace)

    assert len(helpers.receipts(session, landing.ticket)) == 1


# ---------------------------------------------------------------------------
# test-break. The refusals that carry nothing, the output that is not an
# excerpt, the two verdict words the AC8 fixture does not reach, and the
# writer of `landed_sha` the AST census above cannot see.
# ---------------------------------------------------------------------------

#: A command that is not on this machine, which is the refusal shape with
#: NOTHING on it: `run_verify_command` catches the `OSError` before anything
#: ran, so there is no exit status and no output at all.
NOT_ON_PATH = "loregarden-no-such-verifier-1419 --please"


def test_a_refusal_that_examined_nothing_still_writes_its_receipt(session, tmp_path):
    """The receipt the old guard would have skipped, and the likeliest one to lose.

    `_record_full_output` is reached under `if not outcome.ok and outcome.output`,
    and `and outcome.output` is the half that is easy to carry over when the
    writer moves. Every UNAVAILABLE that matters on a real machine has no
    output: a missing toolchain, a timeout, a lease that could not be had, a
    `worktree add` that failed. Those are the landings where "was this
    verified?" is hardest to answer from anywhere else, and AC4 requires a row
    for each of them.

    `exit_code` is `None` rather than absent or 0: nothing exited, and a 0 here
    would read as a clean run in the one field an operator checks first.
    """
    landing = helpers.build(session, tmp_path, "nopath", commands=[NOT_ON_PATH])

    result = land_ticket(session, landing.ticket, landing.workspace)
    session.refresh(landing.ticket)
    rows = helpers.receipts(session, landing.ticket)

    assert not result.ok, "a command that is not on PATH must refuse the land"
    assert len(rows) == 1, (
        f"a refusal with no output wrote {len(rows)} receipts; `and outcome.output` is "
        "still guarding the writer, so the broken-machine landings record nothing"
    )
    payload = helpers.content(rows[0])
    assert (payload["status"], payload["outcome"]) == (FAILED, GateOutcome.UNAVAILABLE.value)
    assert payload["command"] == NOT_ON_PATH, "which command could not be run"
    assert "exit_code" in payload, "AC9's field, present even when nothing exited"
    assert payload["exit_code"] is None, (
        f"nothing exited, so there is no status to report: {payload['exit_code']!r}"
    )
    assert payload["output"] == "", payload["output"]
    assert landing.ticket.landed_sha == ""


def test_a_refusal_with_no_command_at_all_still_writes_its_receipt(session, tmp_path):
    """A busy machine refuses the land before any command is chosen.

    `GateCapacityUnavailable` is raised by `_holding` around the whole list, so
    the refusal carries no command, no exit code and no output — three empty
    fields, and the one case where a writer keyed off any of them writes
    nothing. The land is refused and the operator is owed the reason: "nobody
    verified this because the machine was full" is a different next action from
    "the suite failed".
    """
    landing = helpers.build(session, tmp_path, "nocap", commands=[PASSING_SUITE])
    busy = GateCapacityUnavailable("the machine is full and the lease timed out")

    with mock.patch.object(land_verification, "acquire_gate_capacity", side_effect=busy):
        result = land_ticket(session, landing.ticket, landing.workspace)
    session.refresh(landing.ticket)
    rows = helpers.receipts(session, landing.ticket)

    assert not result.ok, "a verification that could not be held must refuse the land"
    assert len(rows) == 1, f"the capacity refusal wrote {len(rows)} receipts, not one"
    payload = helpers.content(rows[0])
    assert (payload["status"], payload["outcome"]) == (FAILED, GateOutcome.UNAVAILABLE.value)
    assert payload["command"] == "", f"no command was chosen: {payload['command']!r}"
    assert payload["exit_code"] is None
    assert payload["branch"] == landing.ticket.branch, "the row still says what it was about"
    assert landing.ticket.landed_sha == ""


def test_the_receipt_carries_the_whole_output_and_not_the_inline_excerpt(session, tmp_path):
    """AC9's FULL `output`, at the length that makes the shortcut tempting.

    `land_verification` already holds `_INLINE_OUTPUT_LINES = 20` and
    `_trailing`, built for `ticket.blocking_issues`, whose budget is small. Both
    are right there, and reusing either for the receipt leaves the operator the
    same twenty lines twice — which the module's own docstring says is the thing
    the artifact exists not to do. A real suite's output is thousands of lines
    and the collection banner at the head is what says WHICH suite ran.
    """
    lines = 400
    command = f"python3 -c 'print(chr(10).join(f\"line {{n:04d}}\" for n in range({lines})))'"
    landing = helpers.build(session, tmp_path, "longout", commands=[command])

    result = land_ticket(session, landing.ticket, landing.workspace)

    assert result.ok, result.detail
    output = helpers.content(helpers.receipts(session, landing.ticket)[0])["output"]
    assert "line 0000" in output, (
        "the head of the output is missing, so the receipt holds a tail excerpt "
        f"and not the run: {output[:200]!r}"
    )
    assert "line 0399" in output
    kept = [line for line in output.splitlines() if line.startswith("line ")]
    assert len(kept) == lines, f"{len(kept)} of {lines} lines survived"


@pytest.mark.parametrize("status", [PASSED, FAILED, DISABLED])
def test_no_verdict_word_is_filtered_out_of_the_outputs_tab(status):
    """AC8 for all three titles, not only the passing one.

    `is_system_record` reads the title as well as the kind, so a filter added
    later could hide one verdict and not the others — and the one worth hiding
    by accident is `disabled`, the landing nobody verified, which is the state
    this ticket exists because of. Called with the title's own text as
    `run_code` too, since that is the argument that makes the LOG arm fire.
    """
    title = f"Land verification — {status} — integration/lg-ms-1419"

    for run_code in (None, "run_ee906b", title):
        assert not is_system_record(
            kind=ArtifactKind.LAND_VERIFICATION, title=title, run_code=run_code
        ), f"a `{status}` receipt is filtered out of the Outputs tab when run_code={run_code!r}"


#: Every writer of `tickets.landed_sha` under `loregarden/`, as module and
#: function. The AST census above enumerates the callers of
#: `land_ticket._record`, which is the gate the receipt hangs off — but
#: `_record` is not the only writer of the column the detector reads, and a
#: writer that does not reach the gate is a landing with no receipt.
#:
#: `publish_tree` is the second one, measured at 58b4d0f4
#: (`publish_tree.py:153`, documented in `db/migrations_landing.py`): it stamps
#: a tree ROOT's landing when the tree's PR publishes, on the base's leg that
#: `land_ticket` skips with `LandSkip.BASE_TARGET`. No land verification runs
#: there and no receipt is written, so a root that later reaches `done` is a
#: landing the AC4 detector reports forever. Not yet observed — on the live
#: database 2026-10-23, 6 roots carry a tree-published `landed_sha` and none of
#: them is `done` (8 `done` roots carry none) — which is why this is a census
#: that fails on the THIRD writer rather than an assertion about the second.
#: `prod_shape` is the third, and it is not a landing path at all: it is the
#: production-shaped SCENARIO builder, stamping the 80-of-474 done-and-landed
#: proportion AC10 requires so that "the first pass reports nothing" is a
#: statement about the bound and not about a detector with nothing to scan. Its
#: rows are dated BEFORE the invariant's `since` on purpose — they ARE the
#: backlog — so the detector must not report them, and no receipt is written for
#: them for the same reason. It writes to a test database and never to the live
#: one, so there is no landing here for a receipt to be about.
LANDED_SHA_WRITERS = frozenset(
    {
        ("loregarden/services/land_ticket.py", "_record"),
        ("loregarden/services/publish_tree.py", "publish_tree"),
        ("loregarden/testing/prod_shape.py", "_add_landings"),
    }
)


def _landed_sha_writers() -> set[tuple[str, str]]:
    """Every `<expr>.landed_sha = …` under `loregarden/`, by file and function."""
    package = REPO_ROOT / "server" / "loregarden"
    found: set[tuple[str, str]] = set()
    for source in sorted(package.rglob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(
                node, ast.FunctionDef | ast.AsyncFunctionDef
            ):  # py-org: allow-isinstance
                continue
            for sub in ast.walk(node):
                targets = (
                    [*sub.targets]
                    if isinstance(sub, ast.Assign)  # py-org: allow-isinstance
                    else [sub.target]
                    if isinstance(sub, ast.AnnAssign | ast.AugAssign)  # py-org: allow-isinstance
                    else []
                )
                if any(
                    isinstance(t, ast.Attribute)
                    and t.attr == "landed_sha"  # py-org: allow-isinstance
                    for t in targets
                ):
                    found.add((str(source.relative_to(REPO_ROOT / "server")), node.name))
    return found


def test_the_census_sees_every_writer_of_the_column_the_detector_reads():
    """A third writer of `landed_sha` must not appear unnoticed.

    AC4's census is scoped to the callers of `land_ticket._record`, so by
    construction it cannot see a landing recorded anywhere else — and AC4's own
    last sentence is that a path which records a landing and skips the receipt
    makes the detector fire on a landing that was fine. This is the half of
    "every writer reaching the gate" that an AST walk over one module cannot
    assert: the walk is over the whole package, and a new writer fails here with
    the question it has to answer rather than by review.
    """
    writers = _landed_sha_writers()

    assert writers, "the walk found no writer at all, so it asserts nothing"
    assert writers == LANDED_SHA_WRITERS, (
        f"`landed_sha` is written by {sorted(writers - LANDED_SHA_WRITERS)} as well. Every "
        "writer is a recorded landing the `land_verification_ran` detector will read: either "
        "route it through the one call site that writes the receipt, or state in "
        "LANDED_SHA_WRITERS why the detector must not report it"
    )
