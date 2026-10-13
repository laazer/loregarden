"""Static contract for `.github/workflows/ci.yml` (bors staging/trying + durable CI).

Maps acceptance criteria for the bors try-merge CI surface. These are file-shape
assertions — no Actions run required. They must stay red until implement widens
the push allowlist and adds a fail-closed aggregator job named exactly `CI`.

Risks covered:
- R1: workflow-level `name: CI` alone does not satisfy (job-level `name:` required)
- R2: `success()`-only / needs-skip aggregator is non-compliant (fail-closed required)
- R3: tmp-branch names / globs must not appear in triggers
- R4: concurrency block must remain the pre-change baseline

Test-break mutations (must raise under the same helpers as live AC tests):
- workflow title alone / wrong-case job name
- success()-only, always() without !cancelled(), job if gated on needs.*.result
- token-echo without exit, step-level if skip-on-red
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"

# Frozen pre-change job bodies (AC4). Implement must leave these byte-stable.
_BASELINE_SERVER: dict[str, Any] = {
    "name": "Server (Python)",
    "runs-on": "ubuntu-latest",
    "defaults": {"run": {"working-directory": "server"}},
    "steps": [
        {"uses": "actions/checkout@v4"},
        {
            "name": "Install uv",
            "uses": "astral-sh/setup-uv@v5",
            "with": {"enable-cache": True},
        },
        {"name": "Set up Python", "run": "uv python install 3.10"},
        {"name": "Install dependencies", "run": "uv sync --extra dev"},
        {
            "name": "Set up Node",
            "uses": "actions/setup-node@v4",
            "with": {
                "node-version": 20,
                "cache": "npm",
                "cache-dependency-path": "client/package-lock.json",
            },
        },
        {
            "name": "Install client dependencies (the TypeScript gate parses with them)",
            "working-directory": "client",
            "run": "npm ci",
        },
        {"name": "Ruff lint", "run": "uv run ruff check ."},
        {"name": "Ruff format check", "run": "uv run ruff format --check ."},
        {
            "name": "Pytest",
            "run": "uv run pytest -q -n auto",
            "env": {"LOREGARDEN_REPO_ROOT": "${{ github.workspace }}"},
        },
    ],
}

_BASELINE_CLIENT: dict[str, Any] = {
    "name": "Client (TypeScript)",
    "runs-on": "ubuntu-latest",
    "defaults": {"run": {"working-directory": "client"}},
    "steps": [
        {"uses": "actions/checkout@v4"},
        {
            "name": "Set up Node",
            "uses": "actions/setup-node@v4",
            "with": {
                "node-version": 20,
                "cache": "npm",
                "cache-dependency-path": "client/package-lock.json",
            },
        },
        {"name": "Install dependencies", "run": "npm ci"},
        {"name": "Lint", "run": "npm run lint"},
        {"name": "Type check", "run": "npx tsc -b"},
        {"name": "Test", "run": "npm test"},
    ],
}

_BASELINE_CONCURRENCY: dict[str, Any] = {
    "group": "ci-${{ github.workflow }}-${{ github.ref }}",
    "cancel-in-progress": True,
}

_FORBIDDEN_TMP_BRANCHES = (
    "staging.tmp",
    "trying.tmp",
    "staging-squash-merge.tmp",
)

_PUSH_ALLOWLIST = frozenset({"main", "staging", "trying"})


def _load_ci() -> dict[str, Any]:
    assert CI_YML.is_file(), f"missing workflow at {CI_YML}"
    doc = yaml.safe_load(CI_YML.read_text(encoding="utf-8"))
    assert isinstance(doc, dict), "ci.yml must parse to a mapping"
    return doc


def _jobs(doc: dict[str, Any]) -> dict[str, Any]:
    jobs = doc.get("jobs")
    assert isinstance(jobs, dict) and jobs, "ci.yml must define jobs"
    return jobs


def _branch_entries(on_event: Any) -> list[str]:
    """Flatten branch filters from a workflow `on` event value."""
    if on_event is None or on_event is True:
        return []
    if not isinstance(on_event, dict):
        return []
    branches = on_event.get("branches")
    if branches is None:
        return []
    if isinstance(branches, str):
        return [branches]
    if isinstance(branches, list):
        return [str(b) for b in branches]
    return [str(branches)]


def _all_trigger_branch_strings(doc: dict[str, Any]) -> list[str]:
    on = doc.get("on") or doc.get(True)  # YAML 1.1 may parse `on` as True
    if not isinstance(on, dict):
        return []
    out: list[str] = []
    for key in ("push", "pull_request", "pull_request_target"):
        out.extend(_branch_entries(on.get(key)))
    return out


def _ci_aggregator_jobs(jobs: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Jobs whose job-level `name:` is exactly CI (not the workflow title)."""
    found: list[tuple[str, dict[str, Any]]] = []
    for job_id, job in jobs.items():
        if not isinstance(job, dict):
            continue
        if job.get("name") == "CI":
            found.append((job_id, job))
    return found


def _job_if_expr(job: dict[str, Any]) -> str:
    raw = job.get("if", "")
    return "" if raw is None else str(raw)


def _step_run_texts(job: dict[str, Any]) -> list[str]:
    steps = job.get("steps") or []
    texts: list[str] = []
    if not isinstance(steps, list):
        return texts
    for step in steps:
        if not isinstance(step, dict):
            continue
        run = step.get("run")
        if run is not None:
            texts.append(str(run))
    return texts


def _assert_no_skip_as_success_step_if(job: dict[str, Any]) -> None:
    """Step-level if on needs.*.result == success skips the step on red → job green.

    The assert belongs in an always-running `run:` that exits non-zero, not in a
    step `if:` that disappears when a dep fails.
    """
    steps = job.get("steps") or []
    if not isinstance(steps, list):
        return
    for step in steps:
        if not isinstance(step, dict):
            continue
        step_if = "" if step.get("if") is None else str(step.get("if"))
        if not step_if:
            continue
        compact = re.sub(r"\s+", "", step_if)
        if "needs.server.result" in compact or "needs.client.result" in compact:
            assert "always()" in compact, (
                "aggregator step if must not gate on needs.*.result alone "
                f"(skipped step on red leaves job success); got if={step_if!r}"
            )


def _assert_fail_closed_result_gate(run_texts: list[str]) -> None:
    """A step must fail unless both needs.server and needs.client are success.

    Substring presence of needs.*.result + 'success' is not enough — a comment or
    echo of those tokens would silent-pass while the step always exits 0.
    """
    blob = "\n".join(run_texts)
    assert "needs.server.result" in blob, (
        "aggregator must inspect needs.server.result in a step (AC7); "
        "job-level if alone that references result skips on red"
    )
    assert "needs.client.result" in blob, (
        "aggregator must inspect needs.client.result in a step (AC7)"
    )
    # Comparison to success for both deps (not a bare token dump).
    server_cmp = re.search(
        r"needs\.server\.result.{0,80}(?:==|!=|=)\s*['\"]?success['\"]?"
        r"|['\"]success['\"].{0,80}needs\.server\.result"
        r"|\[\[\s*[\"']?\$\{\{\s*needs\.server\.result\s*\}\}[\"']?.{0,40}success",
        blob,
        flags=re.IGNORECASE | re.DOTALL,
    )
    client_cmp = re.search(
        r"needs\.client\.result.{0,80}(?:==|!=|=)\s*['\"]?success['\"]?"
        r"|['\"]success['\"].{0,80}needs\.client\.result"
        r"|\[\[\s*[\"']?\$\{\{\s*needs\.client\.result\s*\}\}[\"']?.{0,40}success",
        blob,
        flags=re.IGNORECASE | re.DOTALL,
    )
    assert server_cmp, (
        "aggregator step must compare needs.server.result to success "
        "(not merely mention the tokens)"
    )
    assert client_cmp, (
        "aggregator step must compare needs.client.result to success "
        "(not merely mention the tokens)"
    )
    # Fail-closed: some non-zero exit path must exist when the comparison fails.
    has_exit = bool(
        re.search(r"\bexit\s+[1-9]\d*\b", blob)
        or re.search(r"\bfalse\b", blob)
        or re.search(r"\|\|\s*exit\b", blob)
    )
    assert has_exit, (
        "aggregator step must exit non-zero when a dep is not success "
        "(exit 1 / false / || exit); token echo alone is non-compliant"
    )


def _assert_aggregator_fail_closed(job: dict[str, Any]) -> None:
    """Full AC7 shape on a job dict — used by live ci.yml and mutation probes."""
    if_expr = _job_if_expr(job)
    assert if_expr, (
        "aggregator must set job-level if: always() && !cancelled() "
        "(default needs-skip on red is non-compliant)"
    )
    compact = re.sub(r"\s+", "", if_expr)
    assert "always()" in compact, (
        f"aggregator if must include always() so the job runs when a dep fails; got {if_expr!r}"
    )
    assert "cancelled()" in compact and (
        "!cancelled()" in compact or "cancelled()==false" in compact.lower()
    ), f"aggregator if must continue only when not cancelled; got {if_expr!r}"
    assert "success()" not in compact or "always()" in compact, (
        f"bare success() without always() skips on red; got {if_expr!r}"
    )
    assert "needs.server.result" not in if_expr and "needs.client.result" not in if_expr, (
        "do not gate the job if on needs.*.result == success — that skips CI on red; "
        f"assert results inside a step instead. if={if_expr!r}"
    )
    _assert_no_skip_as_success_step_if(job)
    _assert_fail_closed_result_gate(_step_run_texts(job))


# --- AC1 / AC2 ------------------------------------------------------------


def test_ac1_push_branches_are_exactly_main_staging_trying():
    """AC1 — on.push.branches membership is exactly {main, staging, trying}."""
    doc = _load_ci()
    on = doc.get("on") or doc.get(True)
    assert isinstance(on, dict), "workflow `on:` must be a mapping"
    push = on.get("push")
    assert isinstance(push, dict), "on.push must be a mapping with branches"
    branches = _branch_entries(push)
    assert set(branches) == _PUSH_ALLOWLIST, (
        f"on.push.branches must be exactly {sorted(_PUSH_ALLOWLIST)}; got {branches!r}"
    )
    assert len(branches) == len(_PUSH_ALLOWLIST), (
        f"on.push.branches must not duplicate entries; got {branches!r}"
    )


def test_ac2_triggers_do_not_list_or_glob_tmp_bors_branches():
    """AC2 — no staging.tmp / trying.tmp / staging-squash-merge.tmp or matching globs."""
    doc = _load_ci()
    entries = _all_trigger_branch_strings(doc)
    for name in _FORBIDDEN_TMP_BRANCHES:
        assert name not in entries, f"trigger lists forbidden branch {name!r}"
    for entry in entries:
        assert "*" not in entry and "?" not in entry, (
            f"branch filter {entry!r} uses a glob that could match *.tmp bors refs"
        )
        for forbidden in _FORBIDDEN_TMP_BRANCHES:
            assert not (entry.endswith("*") and forbidden.startswith(entry.rstrip("*"))), (
                f"glob {entry!r} would match forbidden {forbidden!r}"
            )


# --- AC3 ------------------------------------------------------------------


def test_ac3_pull_request_trigger_present_without_branch_filter():
    """AC3 — on.pull_request remains; no branches filter."""
    doc = _load_ci()
    on = doc.get("on") or doc.get(True)
    assert isinstance(on, dict)
    assert "pull_request" in on, "on.pull_request must remain"
    pr = on["pull_request"]
    # `pull_request:` with no keys parses as None; empty mapping is also fine.
    if pr is None or pr is True:
        return
    assert isinstance(pr, dict), f"unexpected pull_request shape: {pr!r}"
    assert "branches" not in pr, (
        f"on.pull_request must have no branch filter; found branches={pr.get('branches')!r}"
    )


# --- AC4 ------------------------------------------------------------------


def test_ac4_server_and_client_jobs_keep_their_required_check_names():
    """AC4 — jobs.server / jobs.client keep the names main's ruleset requires.

    Pinned to the names, not a snapshot of the steps: the ruleset requires the
    checks `Server (Python)` and `Client (TypeScript)` by name, and the steps
    are free to change (main later gave the server job `fetch-depth: 0`, which a
    byte-for-byte snapshot read as a regression).
    """
    jobs = _jobs(_load_ci())
    assert "server" in jobs and "client" in jobs
    assert jobs["server"]["name"] == "Server (Python)"
    assert jobs["client"]["name"] == "Client (TypeScript)"


# --- AC5 / R1 -------------------------------------------------------------


def test_ac5_durable_check_is_job_level_name_ci_not_workflow_title():
    """AC5 / R1 — a job has name: CI; workflow name alone is insufficient."""
    doc = _load_ci()
    # Document the trap: workflow title may already be CI.
    assert doc.get("name") == "CI", "baseline workflow title stays `CI` (not the bors check)"
    aggregators = _ci_aggregator_jobs(_jobs(doc))
    assert aggregators, (
        "no job with job-level name: CI — bors status=['CI'] matches the job name "
        "from 'Workflow / Job', not the workflow-level name"
    )
    assert len(aggregators) == 1, (
        f"expected exactly one job named CI; found {[j for j, _ in aggregators]}"
    )


# --- AC6 ------------------------------------------------------------------


def test_ac6_aggregator_needs_server_and_client():
    """AC6 — the CI aggregator needs: [server, client]."""
    jobs = _jobs(_load_ci())
    aggregators = _ci_aggregator_jobs(jobs)
    assert aggregators, "missing aggregator job named CI"
    _job_id, job = aggregators[0]
    needs = job.get("needs")
    if isinstance(needs, str):
        needs_list = [needs]
    else:
        assert isinstance(needs, list), f"aggregator needs must be a list; got {needs!r}"
        needs_list = list(needs)
    assert needs_list == ["server", "client"], (
        f"aggregator needs must be exactly [server, client]; got {needs_list!r}"
    )


# --- AC7 / R2 -------------------------------------------------------------


def test_ac7_aggregator_is_fail_closed_not_skip_on_red():
    """AC7 / R2 — always()+!cancelled(); step fails unless both deps succeeded.

    Bare needs + success() (or job if that requires needs.*.result == success)
    skips the aggregator on red deps; bors then times out waiting for CI.
    """
    jobs = _jobs(_load_ci())
    aggregators = _ci_aggregator_jobs(jobs)
    assert aggregators, "missing aggregator job named CI"
    _job_id, job = aggregators[0]
    _assert_aggregator_fail_closed(job)


# --- Mutation probes (test-break) -----------------------------------------
# Each mutation must raise under the same helpers the live AC tests use.
# A probe that only documents a forbidden shape without asserting rejection
# is itself a silent-pass gap.


def test_mutation_workflow_name_alone_is_not_durable_check():
    """R1 — workflow-level name: CI with no job named CI must fail AC5."""
    fake_jobs = {
        "server": dict(_BASELINE_SERVER),
        "client": dict(_BASELINE_CLIENT),
        # Deliberately no job-level name: CI — only workflow title would be CI.
        "ci": {"needs": ["server", "client"], "runs-on": "ubuntu-latest", "steps": []},
    }
    assert _ci_aggregator_jobs(fake_jobs) == []
    with pytest.raises(AssertionError, match="job-level name: CI"):
        aggregators = _ci_aggregator_jobs(fake_jobs)
        assert aggregators, (
            "no job with job-level name: CI — bors status=['CI'] matches the job name "
            "from 'Workflow / Job', not the workflow-level name"
        )


def test_mutation_wrong_case_job_name_is_rejected():
    """R1 — name: ci / Ci must not satisfy durable check identity."""
    for wrong in ("ci", "Ci", "cI", "CI "):
        jobs = {"x": {"name": wrong, "needs": ["server", "client"]}}
        assert _ci_aggregator_jobs(jobs) == [], f"wrong-case name {wrong!r} must not match"


def test_mutation_success_only_aggregator_rejected_by_ac7_helpers():
    """R2 — success()-only aggregator must raise under AC7 helpers (not just document)."""
    fake = {
        "name": "CI",
        "needs": ["server", "client"],
        "if": "success()",
        "runs-on": "ubuntu-latest",
        "steps": [{"run": "echo ok"}],
    }
    with pytest.raises(AssertionError, match="always\\(\\)"):
        _assert_aggregator_fail_closed(fake)


def test_mutation_always_without_cancelled_rejected():
    """R2 — always() alone (no !cancelled()) is incomplete AC7."""
    fake = {
        "name": "CI",
        "needs": ["server", "client"],
        "if": "always()",
        "runs-on": "ubuntu-latest",
        "steps": [
            {
                "run": (
                    'if [ "${{ needs.server.result }}" != "success" ] || '
                    '[ "${{ needs.client.result }}" != "success" ]; then exit 1; fi'
                )
            }
        ],
    }
    with pytest.raises(AssertionError, match="cancelled"):
        _assert_aggregator_fail_closed(fake)


def test_mutation_job_if_gated_on_needs_result_rejected():
    """R2 — job if: needs.*.result == success skips aggregator on red."""
    fake = {
        "name": "CI",
        "needs": ["server", "client"],
        "if": "always() && !cancelled() && needs.server.result == 'success' && needs.client.result == 'success'",
        "runs-on": "ubuntu-latest",
        "steps": [
            {
                "run": (
                    'if [ "${{ needs.server.result }}" != "success" ] || '
                    '[ "${{ needs.client.result }}" != "success" ]; then exit 1; fi'
                )
            }
        ],
    }
    with pytest.raises(AssertionError, match="needs\\.\\*\\.result"):
        _assert_aggregator_fail_closed(fake)


def test_mutation_token_echo_without_exit_rejected():
    """Silent-pass gap: mentioning needs.*.result + success without exiting non-zero."""
    fake = {
        "name": "CI",
        "needs": ["server", "client"],
        "if": "always() && !cancelled()",
        "runs-on": "ubuntu-latest",
        "steps": [{"run": ("echo needs.server.result success needs.client.result success")}],
    }
    with pytest.raises(AssertionError):
        _assert_aggregator_fail_closed(fake)


def test_mutation_step_if_skip_on_red_rejected():
    """Classic GHA trap: step if needs.*.result==success → skipped step, green job."""
    fake = {
        "name": "CI",
        "needs": ["server", "client"],
        "if": "always() && !cancelled()",
        "runs-on": "ubuntu-latest",
        "steps": [
            {
                "if": "needs.server.result == 'success' && needs.client.result == 'success'",
                "run": (
                    'if [ "${{ needs.server.result }}" != "success" ] || '
                    '[ "${{ needs.client.result }}" != "success" ]; then exit 1; fi'
                ),
            }
        ],
    }
    with pytest.raises(AssertionError, match="step if"):
        _assert_aggregator_fail_closed(fake)


def test_mutation_compliant_fail_closed_shape_accepted():
    """Control: the intended implement shape must pass AC7 helpers."""
    good = {
        "name": "CI",
        "needs": ["server", "client"],
        "if": "always() && !cancelled()",
        "runs-on": "ubuntu-latest",
        "steps": [
            {
                "name": "All required jobs passed",
                "run": (
                    'if [ "${{ needs.server.result }}" != "success" ] || '
                    '[ "${{ needs.client.result }}" != "success" ]; then\n'
                    "  exit 1\n"
                    "fi\n"
                ),
            }
        ],
    }
    _assert_aggregator_fail_closed(good)


# --- AC8 / R4 -------------------------------------------------------------


def test_ac8_concurrency_block_unchanged():
    """AC8 / R4 — concurrency group + cancel-in-progress stay at baseline."""
    doc = _load_ci()
    assert doc.get("concurrency") == _BASELINE_CONCURRENCY


def test_ac8_the_durable_check_lives_only_in_ci_yml():
    """AC8 — the `CI` job bors waits on is in ci.yml, and no other workflow defines one.

    Other workflows may exist (`pr-ux.yml` does); a second job named `CI` would
    give bors two checks of that name to wait on.
    """
    workflows = sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml")) + sorted(
        (REPO_ROOT / ".github" / "workflows").glob("*.yaml")
    )
    defining = [
        path.name
        for path in workflows
        if _ci_aggregator_jobs(_jobs(yaml.safe_load(path.read_text(encoding="utf-8"))))
    ]
    assert defining == ["ci.yml"], f"job named CI defined in {defining!r}"
