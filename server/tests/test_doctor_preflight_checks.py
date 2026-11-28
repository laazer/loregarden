"""The four environment assertions a run should make before it spends an agent.

Each one cost a real run on the blobert milestone 14 sweep (2026-08-15), and
each was discovered by dispatching an agent, failing a gate, and rerouting a
ticket for rework it could not perform. They are properties of the machine, not
of the ticket.

The membership question is as much the subject as the checks themselves.
`DISPATCH_PREFLIGHT_CHECKS` carries a rule in its own comment — a check belongs
there only when nothing downstream reports it well — and one of these four fails
that test on purpose.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import pytest
from loregarden.agents import mcp_context
from loregarden.agents.mcp_context import (
    STAGE_REPORT_SECTION_TITLE,
    WORKFLOW_ENFORCEMENT_DOC_REL,
)
from loregarden.config import settings
from loregarden.models.domain import DoctorCheck, DoctorStatus, Workspace
from loregarden.services import doctor
from loregarden.services.doctor import (
    CHECKS,
    DISPATCH_PREFLIGHT_CHECKS,
    check_gate_commands_resolve,
    check_git_writable,
    check_stage_report_contract,
    check_toolchain_installed,
)
from loregarden.services.orchestration_profile import (
    CriterionTestCommand,
    GatesConfig,
    GitAutomationConfig,
    OrchestrationProfile,
    load_profile_from_path,
)
from sqlmodel import Session
from tests.worktree_helpers import make_repo


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    return make_repo(tmp_path)


@pytest.fixture(name="workspace")
def workspace_fixture(session, repo):
    ws = Workspace(slug="proj", name="proj", repo_path=str(repo))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


def _write_contract(repo, body: str) -> None:
    """The workflow-enforcement doc as the loader expects to find it: dividers
    splitting the file into alternating title and body chunks."""
    path = repo / "agent_context" / WORKFLOW_ENFORCEMENT_DOC_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"intro\n\n{'-' * 30}\n{STAGE_REPORT_SECTION_TITLE}\n{'-' * 30}\n{body}\n",
        encoding="utf-8",
    )


# -- AC2: the stage-report contract -------------------------------------------


@pytest.fixture(name="no_shared_contract")
def no_shared_contract_fixture(tmp_path):
    """This control plane with no contract of its own, so no fallback exists."""
    empty = tmp_path / "control-plane-agent-context"
    empty.mkdir()
    with mock.patch.object(mcp_context.settings, "agent_context_dir", empty):
        yield empty


def test_a_workspace_with_no_contract_doc_fails(session, workspace, repo, no_shared_contract):
    """Blobert measured 0 characters against loregarden's 3303, and nothing said
    so — every stage there failed on a report it was never told how to write.

    `make_repo` seeds the doc, because a fixture without one is a workspace that
    could not run; this test removes it to describe the workspace that could not.
    """
    (repo / "agent_context" / WORKFLOW_ENFORCEMENT_DOC_REL).unlink()

    finding = check_stage_report_contract(session, workspace, repo)

    assert finding.status is DoctorStatus.FAIL
    assert WORKFLOW_ENFORCEMENT_DOC_REL.name in finding.remediation


def test_a_contract_doc_missing_its_section_fails_the_same_way(
    session, workspace, repo, no_shared_contract
):
    """The second, quieter half: the doc is present, so a human looking for it
    finds it, but the loader matches on the section title and returns "" when it
    is absent. Both routes to empty must fail, or the check only covers the one
    that is easy to spot."""
    path = repo / "agent_context" / WORKFLOW_ENFORCEMENT_DOC_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("a doc with no such section\n", encoding="utf-8")

    finding = check_stage_report_contract(session, workspace, repo)

    assert finding.status is DoctorStatus.FAIL
    assert STAGE_REPORT_SECTION_TITLE in finding.remediation


def test_a_workspace_without_the_section_warns_and_uses_loregardens(session, workspace, repo):
    """Loremaker's module had no such section and every stage parked before an
    agent ran. Loregarden's own copy now stands in, and the gap is a WARN, which
    does not park a dispatch, rather than a silence."""
    path = repo / "agent_context" / WORKFLOW_ENFORCEMENT_DOC_REL
    path.write_text("a doc with no such section\n", encoding="utf-8")

    finding = check_stage_report_contract(session, workspace, repo)

    assert finding.status is DoctorStatus.WARN
    assert str(mcp_context.settings.agent_context_dir) in finding.finding
    assert STAGE_REPORT_SECTION_TITLE in finding.remediation


def test_a_populated_contract_passes_and_reports_its_size(session, workspace, repo):
    _write_contract(repo, "Report your stage like this.")

    finding = check_stage_report_contract(session, workspace, repo)

    assert finding.status is DoctorStatus.PASS
    assert finding.remediation == ""


# -- AC3: toolchains declared but not installed --------------------------------


def test_a_declared_toolchain_that_is_not_installed_fails(session, workspace, repo):
    """The worktree case. A worktree is created without ignored directories, so
    `node_modules` is exactly what it lacks while the checkout beside it has one."""
    (repo / "package.json").write_text("{}", encoding="utf-8")

    finding = check_toolchain_installed(session, workspace, repo)

    assert finding.status is DoctorStatus.FAIL
    assert "node_modules" in finding.finding


def test_a_tree_declaring_no_toolchain_is_not_judged_for_missing_one(session, workspace, repo):
    """The check derives the requirement from the tree rather than assuming
    loregarden's shape: a repo with no `package.json` is not missing one."""
    finding = check_toolchain_installed(session, workspace, repo)

    assert finding.status is DoctorStatus.PASS


def test_a_declared_toolchain_that_is_installed_passes(session, workspace, repo):
    (repo / "package.json").write_text("{}", encoding="utf-8")
    (repo / "node_modules").mkdir()

    finding = check_toolchain_installed(session, workspace, repo)

    assert finding.status is DoctorStatus.PASS


# -- AC4: the git directory must accept a write --------------------------------


def test_a_writable_git_directory_passes(session, workspace, repo):
    finding = check_git_writable(session, workspace, repo)

    assert finding.status is DoctorStatus.PASS


def test_an_unwritable_git_directory_fails(session, workspace, repo):
    """Asked by writing, not by reading a permission bit: the case this exists
    for is a sandbox that denies the write while the mode bits still allow it."""
    # The write is refused outright rather than by chmod: mode bits are exactly
    # what that sandbox ignores, and root (a CI container) ignores them too.
    denied = PermissionError(13, "Permission denied")
    with mock.patch.object(Path, "write_text", autospec=True, side_effect=denied):
        finding = check_git_writable(session, workspace, repo)

    assert finding.status is DoctorStatus.FAIL
    assert "not writable" in finding.finding


def test_the_write_probe_leaves_nothing_behind(session, workspace, repo):
    """A check that littered the git directory would be its own bug report."""
    check_git_writable(session, workspace, repo)

    assert not list((repo / ".git").glob(".loregarden-write-probe"))


# -- AC5: gate commands, and what this check cannot see ------------------------


def test_a_gate_command_that_does_not_resolve_fails(session, workspace, repo, monkeypatch):
    from loregarden.services import doctor
    from loregarden.services.orchestration_profile import GatesConfig, OrchestrationProfile

    profile = OrchestrationProfile(slug="proj")
    profile.gates = GatesConfig(enabled=True, commands=["./scripts/nope.sh check"])
    monkeypatch.setattr(doctor, "resolve_orchestration_profile", lambda _ws: profile)

    finding = check_gate_commands_resolve(session, workspace, repo)

    assert finding.status is DoctorStatus.FAIL
    assert "./scripts/nope.sh" in finding.finding


def test_a_gate_command_present_in_the_tree_resolves(session, workspace, repo, monkeypatch):
    from loregarden.services import doctor
    from loregarden.services.orchestration_profile import GatesConfig, OrchestrationProfile

    script = repo / "scripts" / "gate.sh"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

    profile = OrchestrationProfile(slug="proj")
    profile.gates = GatesConfig(enabled=True, commands=["./scripts/gate.sh check"])
    monkeypatch.setattr(doctor, "resolve_orchestration_profile", lambda _ws: profile)

    finding = check_gate_commands_resolve(session, workspace, repo)

    assert finding.status is DoctorStatus.PASS


def _stub_bin_dir(tmp_path: Path, names: tuple[str, ...]) -> str:
    """Create an isolated bin directory with executable stubs for deterministic PATH checks."""
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir(exist_ok=True)
    for name in names:
        stub = bin_dir / name
        stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        stub.chmod(0o755)
    return str(bin_dir)


def _write_script(base: Path, rel: str) -> Path:
    script = base / rel
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    return script


def _profile_with(
    *,
    commands: list[str] | None = None,
    transition_script: str = "",
    criterion_commands: list[str] | None = None,
    land_verify_commands: list[str] | None = None,
) -> OrchestrationProfile:
    return OrchestrationProfile(
        slug="proj",
        gates=GatesConfig(
            enabled=True,
            commands=commands or [],
            transition_script=transition_script,
            criterion_test_commands=[
                CriterionTestCommand(prefix=f"p{idx}/", command=cmd)
                for idx, cmd in enumerate(criterion_commands or [])
            ],
        ),
        git=GitAutomationConfig(land_verify_commands=land_verify_commands or []),
    )


def _run_gate_check(
    session: Session,
    workspace: Workspace,
    repo: Path,
    profile: OrchestrationProfile,
    *,
    tmp_path: Path,
    path_bins: tuple[str, ...] = (),
):
    fake_path = _stub_bin_dir(tmp_path, path_bins)
    with (
        mock.patch.object(doctor, "resolve_orchestration_profile", return_value=profile),
        mock.patch.dict(os.environ, {"PATH": fake_path}),
    ):
        return check_gate_commands_resolve(session, workspace, repo)


# -- AC1: all four command sources + plain executable rules --------------------


def test_gate_commands_resolve_examines_gates_commands_source(session, workspace, repo, tmp_path):
    """AC1: unresolvable command configured only in gates.commands returns FAIL."""
    profile = _profile_with(commands=["./scripts/missing_gate.sh"])

    finding = _run_gate_check(session, workspace, repo, profile, tmp_path=tmp_path)

    assert finding.status is DoctorStatus.FAIL
    assert "./scripts/missing_gate.sh" in finding.finding


def test_gate_commands_resolve_examines_transition_script_source(
    session, workspace, repo, tmp_path
):
    """AC1: unresolvable command configured only in gates.transition_script returns FAIL."""
    profile = _profile_with(transition_script="./scripts/missing_transition.sh")

    finding = _run_gate_check(session, workspace, repo, profile, tmp_path=tmp_path)

    assert finding.status is DoctorStatus.FAIL
    assert "./scripts/missing_transition.sh" in finding.finding


def test_gate_commands_resolve_examines_land_verify_commands_source(
    session, workspace, repo, tmp_path
):
    """AC1: unresolvable command configured only in git.land_verify_commands returns FAIL."""
    profile = _profile_with(land_verify_commands=["./scripts/missing_land.sh"])

    finding = _run_gate_check(session, workspace, repo, profile, tmp_path=tmp_path)

    assert finding.status is DoctorStatus.FAIL
    assert "./scripts/missing_land.sh" in finding.finding


def test_gate_commands_resolve_examines_criterion_test_commands_source(
    session, workspace, repo, tmp_path
):
    """AC1: unresolvable command configured only in gates.criterion_test_commands returns FAIL."""
    profile = _profile_with(criterion_commands=["./scripts/missing_criterion.sh {tests}"])

    finding = _run_gate_check(session, workspace, repo, profile, tmp_path=tmp_path)

    assert finding.status is DoctorStatus.FAIL
    assert "./scripts/missing_criterion.sh" in finding.finding


def test_gate_commands_resolve_plain_bare_executable_uses_path(session, workspace, repo, tmp_path):
    """AC1: bare non-interpreter executables resolve via shutil.which on PATH."""
    profile = _profile_with(commands=["custom-linter --check"])

    missing = _run_gate_check(session, workspace, repo, profile, tmp_path=tmp_path, path_bins=())
    assert missing.status is DoctorStatus.FAIL
    assert "custom-linter" in missing.finding

    present = _run_gate_check(
        session, workspace, repo, profile, tmp_path=tmp_path, path_bins=("custom-linter",)
    )
    assert present.status is DoctorStatus.PASS


# -- AC2: interpreter -> script resolution -------------------------------------


@pytest.mark.parametrize("interpreter", ["sh", "bash", "python3", "node"])
def test_gate_commands_resolve_interpreter_missing_script_fails(
    session, workspace, repo, tmp_path, interpreter
):
    """AC2: known interpreters resolve their first non-flag script against repo_root,
    naming the missing script rather than the interpreter."""
    profile = _profile_with(commands=[f"{interpreter} scripts/missing_script.sh arg1"])

    finding = _run_gate_check(
        session, workspace, repo, profile, tmp_path=tmp_path, path_bins=(interpreter,)
    )

    assert finding.status is DoctorStatus.FAIL
    assert "scripts/missing_script.sh" in finding.finding
    assert f"-> {interpreter}" not in finding.finding


def test_gate_commands_resolve_interpreter_basename_flags_and_path_forms(
    session, workspace, repo, tmp_path
):
    """AC2 break-pass: exposes mutations that skip basename matching (`/bin/sh`),
    require '/' in the script name (`python3 check.py`), fail to skip leading
    boolean flags (`bash -e -u`), or mishandle absolute script paths."""
    abs_missing = str(tmp_path / "does_not_exist.py")
    profile = _profile_with(
        commands=[
            "/bin/sh scripts/missing_via_abs_interp.sh",
            "python3 bare_script.py",
            "bash -e -u scripts/missing_after_flags.sh",
            f"python3 {abs_missing}",
        ]
    )

    finding = _run_gate_check(
        session,
        workspace,
        repo,
        profile,
        tmp_path=tmp_path,
        path_bins=("sh", "bash", "python3"),
    )

    assert finding.status is DoctorStatus.FAIL
    assert "scripts/missing_via_abs_interp.sh" in finding.finding
    assert "bare_script.py" in finding.finding
    assert "scripts/missing_after_flags.sh" in finding.finding
    assert abs_missing in finding.finding

    # Bare script filename without '/' resolves against repo_root once created.
    _write_script(repo, "bare_script.py")
    present_bare = _run_gate_check(
        session,
        workspace,
        repo,
        _profile_with(commands=["python3 bare_script.py"]),
        tmp_path=tmp_path,
        path_bins=("python3",),
    )
    assert present_bare.status is DoctorStatus.PASS


# -- AC3: uv run ---------------------------------------------------------------


def test_gate_commands_resolve_uv_run_bare_console_script_passes_without_venv(
    session, workspace, repo, tmp_path
):
    """AC3: `uv run` with a bare console script (pytest) does not PATH-resolve it."""
    profile = _profile_with(
        criterion_commands=["uv run --directory server pytest -q -p no:cacheprovider {tests}"]
    )

    finding = _run_gate_check(
        session, workspace, repo, profile, tmp_path=tmp_path, path_bins=("uv",)
    )

    assert finding.status is DoctorStatus.PASS


def test_gate_commands_resolve_uv_run_missing_uv_on_path_fails(session, workspace, repo, tmp_path):
    """AC3 break-pass: kills a mutation that skips PATH-resolving `uv` itself."""
    profile = _profile_with(commands=["uv run pytest"])

    finding = _run_gate_check(session, workspace, repo, profile, tmp_path=tmp_path, path_bins=())

    assert finding.status is DoctorStatus.FAIL
    assert "-> uv" in finding.finding


def test_gate_commands_resolve_uv_run_directory_and_flags_resolve_script(
    session, workspace, repo, tmp_path
):
    """AC3 break-pass: value-taking flags (--with, -p, --directory) consume their
    values, `--directory D` shifts the relative base to `repo_root/D`, and an
    interpreter under `uv run` is not required on PATH (uv resolves it from venv)."""
    cmd_interp = "uv run --with ruff -p 3.11 --directory server python3 scripts/check.py"
    cmd_path = "uv run --directory server scripts/run_tool.sh"
    # Place scripts at repo_root/scripts/ (wrong base) to prove `--directory server`
    # is actually used rather than falling back to repo_root.
    _write_script(repo, "scripts/check.py")
    _write_script(repo, "scripts/run_tool.sh")

    profile = _profile_with(commands=[cmd_interp, cmd_path])
    # Deliberately omit `python3` from PATH: only `uv` is on PATH.
    missing = _run_gate_check(
        session, workspace, repo, profile, tmp_path=tmp_path, path_bins=("uv",)
    )
    assert missing.status is DoctorStatus.FAIL
    assert "scripts/check.py" in missing.finding
    assert "scripts/run_tool.sh" in missing.finding

    _write_script(repo / "server", "scripts/check.py")
    _write_script(repo / "server", "scripts/run_tool.sh")
    present = _run_gate_check(
        session, workspace, repo, profile, tmp_path=tmp_path, path_bins=("uv",)
    )
    assert present.status is DoctorStatus.PASS


# -- AC4: no false FAIL on real loregarden profile -----------------------------


def test_gate_commands_resolve_passes_on_real_loregarden_profile(session, workspace, tmp_path):
    """AC4: loading the real loregarden.yaml with no venv on PATH returns PASS."""
    profile = load_profile_from_path(
        settings.repo_root / "agent_context" / "orchestration" / "loregarden.yaml"
    )

    finding = _run_gate_check(
        session,
        workspace,
        settings.repo_root,
        profile,
        tmp_path=tmp_path,
        path_bins=("uv", "bash", "node", "npm", "npx"),
    )

    assert finding.status is DoctorStatus.PASS


# -- AC5: stop at the script ---------------------------------------------------


def test_gate_commands_resolve_stops_at_script_and_ignores_trailing_args(
    session, workspace, repo, tmp_path
):
    """AC5: tokens after the resolved script (flags like -m, placeholders, or
    path-like arguments) are never resolved."""
    _write_script(repo, "gate.sh")
    profile = _profile_with(
        commands=[
            "bash gate.sh -m pytest -q {tests}",
            "bash gate.sh ./not/a/script/to/resolve.sh",
        ]
    )

    finding = _run_gate_check(
        session, workspace, repo, profile, tmp_path=tmp_path, path_bins=("bash",)
    )

    assert finding.status is DoctorStatus.PASS


# -- AC6: inline and module forms ----------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "python3 -m pytest",
        "python3 -c 'print(1)'",
        "bash -c 'echo hi'",
        "bash -lc 'echo hi'",
        "node -e 'process.exit(0)'",
        "sh -",
    ],
)
def test_gate_commands_resolve_inline_and_module_forms_pass(
    session, workspace, repo, tmp_path, command
):
    """AC6: inline/module forms (including short-flag clusters like -lc) resolve
    only the interpreter and pass when it is on PATH."""
    profile = _profile_with(commands=[command])

    finding = _run_gate_check(
        session,
        workspace,
        repo,
        profile,
        tmp_path=tmp_path,
        path_bins=("python3", "bash", "node", "sh"),
    )

    assert finding.status is DoctorStatus.PASS


def test_gate_commands_resolve_inline_form_fails_when_interpreter_missing(
    session, workspace, repo, tmp_path
):
    """AC6 break-pass: kills a mutation that returns ok for inline/module forms
    without verifying the interpreter itself is on PATH."""
    profile = _profile_with(commands=["bash -c 'echo hi'", "python3 -m pytest"])

    finding = _run_gate_check(session, workspace, repo, profile, tmp_path=tmp_path, path_bins=())

    assert finding.status is DoctorStatus.FAIL
    assert "-> bash" in finding.finding
    assert "-> python3" in finding.finding


# -- AC7: placeholders and tilde targets ---------------------------------------


def test_gate_commands_resolve_skips_placeholder_and_tilde_targets(
    session, workspace, repo, tmp_path
):
    """AC7: script/target tokens containing '{' or '$', or starting with '~', are
    neither resolved nor reported; `{tests}` never causes FAIL."""
    profile = _profile_with(
        commands=[
            "bash {loregarden_root}/scripts/missing.sh",
            "sh $SCRIPT_PATH",
            "python3 ~/bin/missing.py",
        ],
        criterion_commands=["sh ci/scripts/pytest_by_id.sh {tests}"],
    )
    _write_script(repo, "ci/scripts/pytest_by_id.sh")

    passed = _run_gate_check(
        session,
        workspace,
        repo,
        profile,
        tmp_path=tmp_path,
        path_bins=("bash", "sh", "python3"),
    )
    assert passed.status is DoctorStatus.PASS

    # Removing the real script fails on the script path, never on `{tests}`.
    (repo / "ci" / "scripts" / "pytest_by_id.sh").unlink()
    failed = _run_gate_check(
        session,
        workspace,
        repo,
        profile,
        tmp_path=tmp_path,
        path_bins=("bash", "sh", "python3"),
    )
    assert failed.status is DoctorStatus.FAIL
    assert "ci/scripts/pytest_by_id.sh" in failed.finding
    assert "-> {tests}" not in failed.finding


# -- AC8: field name, format, and remediation ----------------------------------


@pytest.mark.parametrize(
    ("source_field", "profile_kwargs"),
    [
        ("gates.commands", {"commands": ["./scripts/missing.sh arg"]}),
        ("gates.transition_script", {"transition_script": "./scripts/missing.sh arg"}),
        ("git.land_verify_commands", {"land_verify_commands": ["./scripts/missing.sh arg"]}),
        (
            "gates.criterion_test_commands",
            {"criterion_commands": ["./scripts/missing.sh arg"]},
        ),
    ],
)
def test_gate_commands_resolve_reports_field_name_and_format(
    session, workspace, repo, tmp_path, source_field, profile_kwargs
):
    """AC8: every unresolved command is formatted `<field>: `<command>` -> <target>`,
    and remediation names the profile fields."""
    profile = _profile_with(**profile_kwargs)

    finding = _run_gate_check(session, workspace, repo, profile, tmp_path=tmp_path)

    assert finding.status is DoctorStatus.FAIL
    assert f"{source_field}: `./scripts/missing.sh arg` -> ./scripts/missing.sh" in finding.finding
    for expected_field in (
        "gates.commands",
        "transition_script",
        "land_verify_commands",
        "criterion_test_commands",
    ):
        assert expected_field in finding.remediation


def test_gate_commands_resolve_joins_multiple_unresolved_with_semicolon(
    session, workspace, repo, tmp_path
):
    """AC8 break-pass: multiple unresolved commands are joined with '; ' rather
    than ', '."""
    profile = _profile_with(
        commands=["./scripts/a.sh"],
        land_verify_commands=["./scripts/b.sh"],
    )

    finding = _run_gate_check(session, workspace, repo, profile, tmp_path=tmp_path)

    assert finding.status is DoctorStatus.FAIL
    expected = (
        "gates.commands: `./scripts/a.sh` -> ./scripts/a.sh; "
        "git.land_verify_commands: `./scripts/b.sh` -> ./scripts/b.sh"
    )
    assert expected in finding.finding


# -- AC9: loremaker profile shape ----------------------------------------------


def test_gate_commands_resolve_loremaker_profile_shape(session, workspace, repo, tmp_path):
    """AC9: loremaker's configured commands FAIL when the scripts are absent
    (naming ci/scripts/land_verify.sh and git.land_verify_commands) and PASS once
    present."""
    profile = _profile_with(
        commands=[
            "bash {loregarden_root}/.lefthook/scripts/workspace-gates.sh "
            "--repo {workspace_root} --scope worktree"
        ],
        criterion_commands=["sh ci/scripts/pytest_by_id.sh {tests}"],
        land_verify_commands=[
            "sh ci/scripts/land_verify.sh server",
            "sh ci/scripts/land_verify.sh client",
        ],
    )

    missing = _run_gate_check(
        session, workspace, repo, profile, tmp_path=tmp_path, path_bins=("sh", "bash")
    )
    assert missing.status is DoctorStatus.FAIL
    assert "ci/scripts/land_verify.sh" in missing.finding
    assert "git.land_verify_commands" in missing.finding
    assert "ci/scripts/pytest_by_id.sh" in missing.finding
    assert "gates.criterion_test_commands" in missing.finding

    _write_script(repo, "ci/scripts/land_verify.sh")
    _write_script(repo, "ci/scripts/pytest_by_id.sh")
    present = _run_gate_check(
        session, workspace, repo, profile, tmp_path=tmp_path, path_bins=("sh", "bash")
    )
    assert present.status is DoctorStatus.PASS


# -- AC10: count and empty -----------------------------------------------------


def test_gate_commands_resolve_empty_profile_reports_no_commands(
    session, workspace, repo, tmp_path
):
    """AC10: nothing configured in any of the four sources reports the exact empty
    PASS text."""
    profile = _profile_with()

    finding = _run_gate_check(session, workspace, repo, profile, tmp_path=tmp_path)

    assert finding.status is DoctorStatus.PASS
    assert finding.finding == "No gate commands configured."


def test_gate_commands_resolve_counts_commands_across_all_four_sources(
    session, workspace, repo, tmp_path
):
    """AC10: land_verify_commands alone is not treated as empty, and the PASS
    count equals the total non-blank commands examined across all four sources."""
    _write_script(repo, "scripts/one.sh")
    _write_script(repo, "scripts/two.sh")
    _write_script(repo, "scripts/three.sh")
    _write_script(repo, "scripts/four.sh")

    land_only = _profile_with(land_verify_commands=["sh scripts/one.sh"])
    land_finding = _run_gate_check(
        session, workspace, repo, land_only, tmp_path=tmp_path, path_bins=("sh",)
    )
    assert land_finding.status is DoctorStatus.PASS
    assert land_finding.finding != "No gate commands configured."
    assert land_finding.finding == f"All 1 gate command(s) resolve from {repo}."

    all_four = _profile_with(
        commands=["./scripts/one.sh", "   "],
        transition_script="./scripts/two.sh",
        land_verify_commands=["./scripts/three.sh"],
        criterion_commands=["./scripts/four.sh {tests}"],
    )
    all_finding = _run_gate_check(session, workspace, repo, all_four, tmp_path=tmp_path)
    assert all_finding.status is DoctorStatus.PASS
    assert all_finding.finding == f"All 4 gate command(s) resolve from {repo}."


# -- AC11: unparseable and blank commands --------------------------------------


def test_gate_commands_resolve_unbalanced_quote_fails_without_raising(
    session, workspace, repo, tmp_path
):
    """AC11: unbalanced quotes do not raise shlex ValueError; they return FAIL
    naming the field, command, and reason 'unparseable'."""
    bad_cmd = 'bash -c "unterminated'
    profile = _profile_with(land_verify_commands=[bad_cmd])

    finding = _run_gate_check(
        session, workspace, repo, profile, tmp_path=tmp_path, path_bins=("bash",)
    )

    assert finding.status is DoctorStatus.FAIL
    assert f"git.land_verify_commands: `{bad_cmd}` -> unparseable" in finding.finding


def test_gate_commands_resolve_blank_commands_are_skipped_and_uncounted(
    session, workspace, repo, tmp_path
):
    """AC11: whitespace-only entries in any of the four sources are skipped and
    never counted or reported."""
    profile = _profile_with(
        commands=["", "   "],
        transition_script="  ",
        land_verify_commands=["\t"],
        criterion_commands=["   "],
    )

    finding = _run_gate_check(session, workspace, repo, profile, tmp_path=tmp_path)

    assert finding.status is DoctorStatus.PASS
    assert finding.finding == "No gate commands configured."


# -- membership, which is the design decision ----------------------------------


def test_the_checks_nothing_else_reports_run_before_dispatch():
    """`DISPATCH_PREFLIGHT_CHECKS` earns members by one test, stated in its own
    comment: nothing downstream reports the failure well."""
    assert DoctorCheck.STAGE_REPORT_CONTRACT in DISPATCH_PREFLIGHT_CHECKS
    assert DoctorCheck.GIT_WRITABLE in DISPATCH_PREFLIGHT_CHECKS


def test_the_toolchain_check_cannot_park_this_very_workspace():
    """It reads loregarden's own root `package.json` as a missing toolchain.

    That manifest is the Tauri desktop host — devDependencies it never installs,
    with the real `node_modules` under `client/`. A manifest does not say whether
    the run needs what it declares, so parking on the difference would stop every
    dispatch in the workspace this control plane runs from. On demand it is still
    worth having: a human reading the finding knows which manifests matter.
    """
    assert DoctorCheck.TOOLCHAIN_INSTALLED not in DISPATCH_PREFLIGHT_CHECKS
    assert DoctorCheck.TOOLCHAIN_INSTALLED in CHECKS


def test_gate_command_resolution_is_deliberately_not_a_dispatch_check():
    """`gate_runner` already catches the exec failure and reports
    `GateOutcome.UNAVAILABLE` with the OS error and the command. Parking would
    replace a precise message with an approval a human has to clear — the same
    reason REPO_HAS_COMMIT is excluded.
    """
    assert DoctorCheck.GATE_COMMANDS_RESOLVE not in DISPATCH_PREFLIGHT_CHECKS
    assert DoctorCheck.REPO_HAS_COMMIT not in DISPATCH_PREFLIGHT_CHECKS


def test_every_check_is_reachable_on_demand(session, workspace, repo):
    """AC6: the checks are usable through the doctor, not only before a dispatch."""
    for check in DoctorCheck:
        assert check in CHECKS, f"{check.value} has no checker registered"
