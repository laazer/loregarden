"""Writing a workspace's GatesConfig (enabled/commands/transition_script)
back to its orchestration profile YAML — the write path backing the Studio
"Gates" editor.

Orchestration profiles always resolve from loregarden's own repo tree
(settings.repo_root), never from the workspace's own repo_path — so every
test here redirects settings.repo_root to a throwaway tmp_path rather than
writing into the real agent_context/orchestration/ directory.
"""

import ast
import inspect
import textwrap
from unittest import mock

import pytest
import yaml
from fastapi.testclient import TestClient
from loregarden.api import orchestration as orchestration_api
from loregarden.config import settings
from loregarden.models.domain import DockerFootprint, Workspace
from loregarden.services.orchestration_profile import (
    GatesConfig,
    ProfileUnreadableError,
    _profile_path_for_write,
    list_profiles,
    load_profile_from_path,
    orchestration_dir,
    resolve_orchestration_profile,
    update_gates_config,
)
from sqlmodel import Session


def _workspace(slug="gates-write-test", repo_path=".") -> Workspace:
    return Workspace(slug=slug, name="Gates Write Test", repo_path=repo_path)


def test_writes_new_profile_file_when_none_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "repo_root", tmp_path)
    ws = _workspace()
    profile = update_gates_config(
        ws, GatesConfig(enabled=True, commands=["echo hi"], transition_script="ci/gate.py")
    )
    assert profile.gates.enabled is True
    assert profile.gates.commands == ["echo hi"]
    assert profile.gates.transition_script == "ci/gate.py"

    path = orchestration_dir() / f"{ws.slug}.yaml"
    assert path.is_file()
    raw = yaml.safe_load(path.read_text())
    assert raw["gates"] == {
        "enabled": True,
        "commands": ["echo hi"],
        "transition_script": "ci/gate.py",
        "autofix_commands": [],
        "autofix_agent_fallback": True,
        "autofix_max_agent_attempts": GatesConfig().autofix_max_agent_attempts,
        "capacity_footprint": GatesConfig().capacity_footprint.value,
    }


def test_preserves_other_fields_in_existing_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "repo_root", tmp_path)
    ws = _workspace()
    path = orchestration_dir() / f"{ws.slug}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "slug: gates-write-test\n"
        "name: Custom Name\n"
        "driver: builtin_autopilot\n"
        "workflow_template: blobert-tdd\n"
        "gates:\n"
        "  enabled: false\n",
        encoding="utf-8",
    )

    update_gates_config(ws, GatesConfig(enabled=True, commands=["true"], transition_script=""))

    raw = yaml.safe_load(path.read_text())
    assert raw["name"] == "Custom Name"
    assert raw["workflow_template"] == "blobert-tdd"
    assert raw["gates"] == {
        "enabled": True,
        "commands": ["true"],
        "transition_script": "",
        "autofix_commands": [],
        "autofix_agent_fallback": True,
        "autofix_max_agent_attempts": GatesConfig().autofix_max_agent_attempts,
        "capacity_footprint": GatesConfig().capacity_footprint.value,
    }

    profile = resolve_orchestration_profile(ws)
    assert profile.workflow_template == "blobert-tdd"
    assert profile.gates.enabled is True


def test_preserves_autofix_settings_the_editor_does_not_manage(tmp_path, monkeypatch):
    """The Gates editor only writes enabled/commands/transition_script. A save
    from it must not wipe a hand-configured autofix policy already in the file."""
    monkeypatch.setattr(settings, "repo_root", tmp_path)
    ws = _workspace()
    path = orchestration_dir() / f"{ws.slug}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "slug: gates-write-test\n"
        "gates:\n"
        "  enabled: false\n"
        "  autofix_commands:\n"
        "    - ruff check --fix server/\n"
        "  autofix_max_agent_attempts: 5\n",
        encoding="utf-8",
    )

    update_gates_config(ws, GatesConfig(enabled=True, commands=["true"], transition_script=""))

    raw = yaml.safe_load(path.read_text())
    assert raw["gates"]["enabled"] is True
    assert raw["gates"]["commands"] == ["true"]
    assert raw["gates"]["autofix_commands"] == ["ruff check --fix server/"]
    assert raw["gates"]["autofix_max_agent_attempts"] == 5

    profile = resolve_orchestration_profile(ws)
    assert profile.gates.autofix_commands == ["ruff check --fix server/"]
    assert profile.gates.autofix_max_agent_attempts == 5


def test_resolves_by_slug_regardless_of_workspace_repo_path(tmp_path, monkeypatch):
    """The bug this guards against: a workspace whose repo_path points at some
    other checkout (e.g. a workspace orchestrating an external repo) must
    still resolve its profile from loregarden's own repo tree, not fall back
    to a different workspace's profile because its own repo has no
    agent_context/orchestration/ directory."""
    monkeypatch.setattr(settings, "repo_root", tmp_path)
    root = orchestration_dir()
    root.mkdir(parents=True, exist_ok=True)
    (root / "external-project.yaml").write_text(
        "slug: external-project\nworkflow_template: blobert-tdd\ngates:\n  enabled: true\n",
        encoding="utf-8",
    )

    ws = _workspace(slug="external-project", repo_path="/some/other/checkout/entirely")
    profile = resolve_orchestration_profile(ws)
    assert profile.workflow_template == "blobert-tdd"
    assert profile.gates.enabled is True


def test_update_workspace_gates_endpoint(
    client: TestClient, db_session: Session, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "repo_root", tmp_path)
    ws = Workspace(slug="gates-api-test", name="Gates API Test", repo_path=".")
    db_session.add(ws)
    db_session.commit()

    res = client.put(
        "/api/orchestration/workspaces/gates-api-test/profile/gates",
        json={"enabled": True, "commands": ["lefthook run pre-commit"], "transition_script": ""},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["gates_enabled"] is True
    assert body["gates_commands"] == ["lefthook run pre-commit"]

    followup = client.get("/api/orchestration/workspaces/gates-api-test/profile")
    assert followup.json()["gates_commands"] == ["lefthook run pre-commit"]


def test_update_workspace_gates_endpoint_unknown_workspace(client: TestClient):
    res = client.put(
        "/api/orchestration/workspaces/does-not-exist/profile/gates",
        json={"enabled": True, "commands": [], "transition_script": ""},
    )
    assert res.status_code == 404


# --- gates_enabled reflects whether gates can actually run (ticket 88) ---
#
# api/orchestration.py used to report gates_enabled straight from the raw
# config flag: gates.enabled=true + zero gates resolve = the Studio editor
# shows green for a config that gates nothing.


def test_gates_enabled_false_when_config_on_but_nothing_would_run(
    client: TestClient, db_session: Session, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "repo_root", tmp_path)
    ws = Workspace(slug="gates-empty-test", name="Gates Empty Test", repo_path=".")
    db_session.add(ws)
    db_session.commit()

    res = client.put(
        "/api/orchestration/workspaces/gates-empty-test/profile/gates",
        json={"enabled": True, "commands": [], "transition_script": ""},
    )
    assert res.status_code == 200
    assert res.json()["gates_enabled"] is False

    followup = client.get("/api/orchestration/workspaces/gates-empty-test/profile")
    assert followup.json()["gates_enabled"] is False


def test_gates_enabled_true_when_transition_script_resolves_with_no_commands(
    client: TestClient, db_session: Session, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "repo_root", tmp_path)
    script_dir = tmp_path / "ci" / "scripts"
    script_dir.mkdir(parents=True)
    (script_dir / "run_workflow_transition_gates.py").write_text(
        "import sys\nsys.exit(0)\n", encoding="utf-8"
    )

    ws = Workspace(slug="gates-script-test", name="Gates Script Test", repo_path=".")
    db_session.add(ws)
    db_session.commit()

    res = client.put(
        "/api/orchestration/workspaces/gates-script-test/profile/gates",
        json={"enabled": True, "commands": [], "transition_script": ""},
    )
    assert res.status_code == 200
    assert res.json()["gates_enabled"] is True


def test_gates_enabled_false_when_config_off_even_with_commands(
    client: TestClient, db_session: Session, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "repo_root", tmp_path)
    ws = Workspace(slug="gates-disabled-test", name="Gates Disabled Test", repo_path=".")
    db_session.add(ws)
    db_session.commit()

    res = client.put(
        "/api/orchestration/workspaces/gates-disabled-test/profile/gates",
        json={"enabled": False, "commands": ["lefthook run pre-commit"], "transition_script": ""},
    )
    assert res.status_code == 200
    assert res.json()["gates_enabled"] is False


# --- the Studio editor's view and its "Test checks" dry run ---


def test_profile_view_reports_raw_switch_resolved_script_and_placeholders(
    client: TestClient, db_session: Session, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "repo_root", tmp_path)
    ws = Workspace(slug="gates-view-test", name="Gates View Test", repo_path=".")
    db_session.add(ws)
    db_session.commit()

    res = client.put(
        "/api/orchestration/workspaces/gates-view-test/profile/gates",
        json={"enabled": True, "commands": [], "transition_script": ""},
    )
    body = res.json()
    # On but nothing runnable: the toggle must still read as on.
    assert body["gates_configured"] is True
    assert body["gates_enabled"] is False
    assert body["gates_transition_script_resolved"] == ""
    assert body["gates_placeholders"]["external_id"] == "SAMPLE-1"
    assert body["gates_placeholders"]["transition"] == "implement_to_verify"
    assert set(body["gates_placeholders"]) >= {"workspace_root", "loregarden_root"}


def test_update_gates_writes_autofix_only_when_supplied(
    client: TestClient, db_session: Session, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "repo_root", tmp_path)
    ws = Workspace(slug="gates-autofix-test", name="Gates Autofix Test", repo_path=".")
    db_session.add(ws)
    db_session.commit()
    url = "/api/orchestration/workspaces/gates-autofix-test/profile/gates"

    client.put(
        url,
        json={
            "enabled": True,
            "commands": ["true"],
            "transition_script": "",
            "autofix_commands": ["ruff format ."],
            "autofix_agent_fallback": False,
            "autofix_max_agent_attempts": 1,
        },
    )
    # A client that omits the autofix fields leaves them as they were.
    res = client.put(url, json={"enabled": True, "commands": ["false"], "transition_script": ""})
    body = res.json()
    assert body["gates_commands"] == ["false"]
    assert body["gates_autofix_commands"] == ["ruff format ."]
    assert body["gates_autofix_agent_fallback"] is False
    assert body["gates_autofix_max_agent_attempts"] == 1


def test_update_gates_keeps_a_hand_set_capacity_footprint(
    client: TestClient, db_session: Session, tmp_path, monkeypatch
):
    """The gates editor sends enabled/commands/transition_script only. A save from
    it must not quietly resize every gate lease back to the default."""
    monkeypatch.setattr(settings, "repo_root", tmp_path)
    ws = Workspace(slug="gates-capacity-test", name="Gates Capacity Test", repo_path=".")
    db_session.add(ws)
    db_session.commit()
    url = "/api/orchestration/workspaces/gates-capacity-test/profile/gates"

    client.put(url, json={"enabled": True, "commands": ["true"], "capacity_footprint": "stack"})
    client.put(url, json={"enabled": True, "commands": ["false"], "transition_script": ""})

    profile = load_profile_from_path(_profile_path_for_write(ws))
    assert profile.gates.commands == ["false"]
    assert profile.gates.capacity_footprint is DockerFootprint.STACK


def test_gate_test_endpoint_runs_every_command_and_saves_nothing(
    client: TestClient, db_session: Session, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "repo_root", tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    ws = Workspace(slug="gates-dry-run-test", name="Gates Dry Run", repo_path=str(repo))
    db_session.add(ws)
    db_session.commit()

    res = client.post(
        "/api/orchestration/workspaces/gates-dry-run-test/profile/gates/test",
        json={
            "commands": ["false", "  ", "echo {external_id} {transition}", "no-such-binary-xyz"],
            "from_stage": "plan",
            "to_stage": "spec",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["transition"] == "plan_to_spec"
    # The first failure does not stop the run, and the blank entry is dropped.
    assert [r["outcome"] for r in body["results"]] == ["failed", "passed", "unavailable"]
    assert body["results"][1]["stdout"] == "SAMPLE-1 plan_to_spec"
    assert body["results"][1]["template"] == "echo {external_id} {transition}"
    assert not (orchestration_dir() / "gates-dry-run-test.yaml").exists()


def test_gate_test_endpoint_unknown_workspace(client: TestClient):
    res = client.post(
        "/api/orchestration/workspaces/does-not-exist/profile/gates/test",
        json={"commands": ["true"]},
    )
    assert res.status_code == 404


# --- Gate Studio's "Stored in" and the unwritable profile (lg-gate-studio-863) ---


def test_a_profile_says_which_file_it_was_read_from(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "repo_root", tmp_path)
    path = orchestration_dir() / "stamped.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("slug: stamped\nsource_path: /etc/somewhere-else.yaml\n")

    profile = load_profile_from_path(path)

    assert profile.source_path == "agent_context/orchestration/stamped.yaml"


def test_the_built_in_fallback_has_no_source_path(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "repo_root", tmp_path)

    assert resolve_orchestration_profile(_workspace("no-file-anywhere")).source_path is None


def test_the_profile_endpoint_reports_its_source_path(
    client: TestClient, db_session: Session, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "repo_root", tmp_path)
    db_session.add(Workspace(slug="gates-source-test", name="Gates Source", repo_path="."))
    db_session.commit()
    client.put(
        "/api/orchestration/workspaces/gates-source-test/profile/gates",
        json={"enabled": True, "commands": ["true"], "transition_script": ""},
    )

    body = client.get("/api/orchestration/workspaces/gates-source-test/profile").json()

    assert body["source_path"] == "agent_context/orchestration/gates-source-test.yaml"


def test_an_unwritable_profile_is_a_403_naming_the_file(
    client: TestClient, db_session: Session, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "repo_root", tmp_path)
    db_session.add(Workspace(slug="gates-locked-test", name="Gates Locked", repo_path="."))
    db_session.commit()
    denied = PermissionError(13, "Permission denied", "/profiles/gates-locked-test.yaml")

    with mock.patch("loregarden.api.orchestration.update_gates_config", side_effect=denied):
        res = client.put(
            "/api/orchestration/workspaces/gates-locked-test/profile/gates",
            json={"enabled": True, "commands": ["true"], "transition_script": ""},
        )

    assert res.status_code == 403
    assert "gates-locked-test.yaml" in res.json()["detail"]


# --- the editor says the file is broken, instead of an empty gate list --------
#
# lg-milestone-that-1215 AC12. These three GETs are what the Gates editor reads,
# and the Gates editor is the surface that *writes* these files — so an
# interrupted write or a hand edit produces exactly the file they are given here.
# All three resolved the profile unguarded, so every shape of a broken YAML was a
# 500 with `{"detail": "Internal Server Error"}` and the panel rendered its
# controls over `null`: a plausible, wrong gate list with no error shown.
#
# The 400 carries the error's own text, which is the only string naming the file
# and the line that will not parse. `load_profile_from_path` resolves it
# absolute on purpose (C7): the operator is standing in a worktree, and
# `agent_context/orchestration/default.yaml` is not a path they can open from
# there.

PROFILE_GET_ENDPOINTS = ("profile", "profiles", "profile/git")

UNREADABLE_PROFILE_SHAPES = {
    "corrupt-syntax": "slug: default\nname: [unclosed\n",
    "non-mapping": "- one\n- two\n",
    "schema-invalid": "slug: default\nname: Default\nmonitor:\n  autofix: [stage_thrash]\n",
}


def _broken_default(tmp_path, shape: str = "corrupt-syntax"):
    """A `default.yaml` that will not parse, and its absolute path."""
    path = orchestration_dir() / "default.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(UNREADABLE_PROFILE_SHAPES[shape], encoding="utf-8")
    return path.resolve()


@pytest.mark.parametrize("endpoint", PROFILE_GET_ENDPOINTS)
@pytest.mark.parametrize("shape", sorted(UNREADABLE_PROFILE_SHAPES))
def test_a_profile_that_will_not_parse_is_a_400_naming_the_file(
    client: TestClient, db_session: Session, tmp_path, monkeypatch, endpoint: str, shape: str
):
    """AC12. Once per endpoint, once per shape, on a real file on disk.

    Parametrized over the shapes rather than written for the schema-invalid one
    because that is the only shape these three endpoints ever handled: a
    `ValidationError` is a `ValueError`, and the other two are not. A fix that
    classified only the pydantic case would leave two thirds of a half-written
    file as a 500 here.
    """
    monkeypatch.setattr(settings, "repo_root", tmp_path)
    db_session.add(Workspace(slug="gates-broken-test", name="Gates Broken", repo_path="."))
    db_session.commit()
    path = _broken_default(tmp_path, shape)

    res = client.get(f"/api/orchestration/workspaces/gates-broken-test/{endpoint}")

    assert res.status_code == 400, (
        f"/{endpoint} on a {shape} profile answered {res.status_code}: {res.text[:200]}"
    )
    detail = res.json()["detail"]
    assert str(path) in detail, f"the editor is not told which file to fix: {detail!r}"
    assert "could not be read as an orchestration profile" in detail, (
        f"the detail is not the error's own text: {detail!r}"
    )


def test_the_profile_endpoint_fails_on_the_shared_default_even_when_its_own_file_parses(
    client: TestClient, db_session: Session, tmp_path, monkeypatch
):
    """AC12's last sentence — the vector the obvious guard placement misses.

    `GET /profile` reads two files, not one: the workspace's own profile, and
    `default.yaml` through `default_gate_commands()`, which is what fills the
    editor's one-click suggestions. So a workspace with a perfectly good
    `<slug>.yaml` still 500s when the *shared* default is broken — and measured
    read-only on the live database (2026-10-17), no workspace sets
    `orchestration_profile_slug`, so three of six resolve that shared file and
    one corrupt write takes out half of them at once.

    A guard wrapped around `resolve_orchestration_profile` alone passes every
    test above and leaves this one a 500.
    """
    monkeypatch.setattr(settings, "repo_root", tmp_path)
    db_session.add(Workspace(slug="gates-own-file-test", name="Gates Own File", repo_path="."))
    db_session.commit()
    own = orchestration_dir() / "gates-own-file-test.yaml"
    own.parent.mkdir(parents=True, exist_ok=True)
    own.write_text("slug: gates-own-file-test\nname: Fine\n", encoding="utf-8")
    assert load_profile_from_path(own).name == "Fine", "fixture: this file must parse"
    path = _broken_default(tmp_path)

    res = client.get("/api/orchestration/workspaces/gates-own-file-test/profile")

    assert res.status_code == 400, (
        "the workspace's own profile parsed, so the read that failed is "
        f"default_gate_commands()'s: {res.status_code} {res.text[:200]}"
    )
    assert str(path) in res.json()["detail"]


def test_the_profile_list_refuses_rather_than_quietly_dropping_the_broken_file(
    tmp_path, monkeypatch
):
    """R4 / AC12. `list_profiles` propagates; it does not return a short list.

    Skipping the unreadable file is the tempting fix and the wrong one: the
    editor would show a list that is complete-looking and missing the file the
    operator is about to overwrite, which is the same empty-state-for-a-failure-
    state defect this ticket closes on the panel. Asserted on the service rather
    than through the route so that a route guard cannot be what makes it pass.
    """
    monkeypatch.setattr(settings, "repo_root", tmp_path)
    good = orchestration_dir() / "a-readable-one.yaml"
    good.parent.mkdir(parents=True, exist_ok=True)
    good.write_text("slug: a-readable-one\nname: Readable\n", encoding="utf-8")
    path = _broken_default(tmp_path)

    with pytest.raises(ProfileUnreadableError) as excinfo:
        list_profiles(_workspace("gates-list-test"))

    assert excinfo.value.path == path, (
        f"a different file was blamed than the one that is broken: {excinfo.value.path}"
    )


# --- adversarial: the three guards AC12 adds, and what they must not become ---
#
# AC12 says the three GETs "catch `ProfileUnreadableError` and return 400". It
# says nothing about what else they may catch, and it is the only criterion on
# this change that adds a guard without a "nothing broader" clause beside it —
# AC2 has one for the handoff guard and AC9 has one for the external harness.
# Measured: a prototype in which all three read
# `except Exception as exc: raise HTTPException(400, str(exc))` passes every
# test above, including all nine parametrizations of the 400 test.
#
# That is the same mistake C1 refuses one layer up, on a surface where it is
# harder to notice. A `Session` that is gone, an `AttributeError` in
# `_profile_view`, a `KeyError` in the gate renderer — each would reach the
# Gates editor as "Gate settings for <slug> could not be read: <some Python
# message>. Fix that file and reload." (AC13's copy), sending the operator to
# edit a file that is perfectly fine while the real failure is invisible. A 500
# is the better answer for all of them: it is honest, and it is in the log.


def _handler_except_names(func) -> list[set[str]]:
    """The names each `except` in ``func`` catches, read off the AST.

    Read structurally rather than by text so that re-ordering the tuple,
    aliasing a name at import, or splitting the guard into two handlers cannot
    change the answer.
    """
    source = textwrap.dedent(inspect.getsource(func))
    names = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.ExceptHandler):  # py-org: allow-isinstance - AST walk
            continue
        if node.type is None:
            names.append({"BaseException"})
            continue
        names.append(
            {
                child.id
                for child in ast.walk(node.type)
                if isinstance(child, ast.Name)  # py-org: allow-isinstance - AST walk
            }
        )
    return names


@pytest.mark.parametrize(
    "handler",
    [
        orchestration_api.get_workspace_profile,
        orchestration_api.list_workspace_profiles,
        orchestration_api.get_workspace_git_automation,
    ],
    ids=["profile", "profiles", "profile/git"],
)
def test_the_gate_studio_guards_name_the_unreadable_profile_and_nothing_broader(handler):
    """AC12 with the clause AC2 and AC9 both have and AC12 does not.

    Every one of these three endpoints answers 400 "go and fix this file" for a
    `ProfileUnreadableError`. The reason that is a good answer is that the
    error means exactly one thing: the bytes on disk are not a profile. An
    `except Exception` in the same slot makes the 400 mean "something went
    wrong somewhere under this read", and the panel renders the same sentence
    for it — so the one surface this ticket builds to tell an operator which
    file to open starts naming files that are not broken.

    `ProfileUnreadableError` is a `RuntimeError` (C2) precisely so the sites
    that handle it are the sites that *named* it. A broad catch here gives that
    away on three of the ten, and `grep -rn ProfileUnreadableError` (AC15)
    would still list them.
    """
    handlers = _handler_except_names(handler)
    assert handlers, (
        f"{handler.__name__} has no except handler at all: an unreadable profile is a "
        "500 with {'detail': 'Internal Server Error'} and the editor renders its "
        "controls over null"
    )
    for caught in handlers:
        assert "ProfileUnreadableError" in caught, (
            f"{handler.__name__} catches {sorted(caught)}, which does not name the error "
            "this ticket created — so the 400 is being produced by something else, and "
            "the next shape of broken file is a 500 again"
        )
        assert not (caught & {"Exception", "BaseException"}), (
            f"{handler.__name__} catches {sorted(caught)}: every failure under this read "
            "now tells the operator to go and fix a profile file, including the ones "
            "where the profile is fine and the bug is ours"
        )


def test_a_broken_profile_belonging_to_nobody_fails_the_list_and_not_the_single_read(
    client: TestClient, db_session: Session, tmp_path, monkeypatch
):
    """The over-broad fix in the other direction: a guard that reads too much.

    `list_profiles` globs the whole orchestration directory, so it is the one
    endpoint of the three that an unreadable file belonging to *another*
    workspace can break — and it should break, loudly (R4: no partial list).
    `GET /profile` and `GET /profile/git` read two named files, this
    workspace's and `default.yaml`, and a third workspace's broken profile is
    none of their business.

    A guard implemented by resolving the directory once and sharing the result
    across the three handlers passes every other test here and turns a file
    nobody on this page is looking at into a 400 telling this operator to fix
    it. The 200s below are the half of AC12 that no criterion states: the
    endpoint says the file is broken *when it is a file that endpoint reads*.
    """
    monkeypatch.setattr(settings, "repo_root", tmp_path)
    db_session.add(Workspace(slug="gates-neighbour-test", name="Gates Neighbour", repo_path="."))
    db_session.commit()
    own = orchestration_dir() / "gates-neighbour-test.yaml"
    own.parent.mkdir(parents=True, exist_ok=True)
    own.write_text("slug: gates-neighbour-test\nname: Fine\n", encoding="utf-8")
    (orchestration_dir() / "default.yaml").write_text(
        "slug: default\nname: Default\n", encoding="utf-8"
    )
    stranger = orchestration_dir() / "some-other-workspace.yaml"
    stranger.write_text(UNREADABLE_PROFILE_SHAPES["corrupt-syntax"], encoding="utf-8")

    for endpoint in ("profile", "profile/git"):
        res = client.get(f"/api/orchestration/workspaces/gates-neighbour-test/{endpoint}")
        assert res.status_code == 200, (
            f"/{endpoint} reads this workspace's own profile and default.yaml, both of "
            f"which parse — it answered {res.status_code}: {res.text[:200]}"
        )

    listed = client.get("/api/orchestration/workspaces/gates-neighbour-test/profiles")
    assert listed.status_code == 400, (
        "the profile list reads every file in the directory, so a broken one in it has "
        f"to refuse rather than come back short: {listed.status_code}"
    )
    assert str(stranger.resolve()) in listed.json()["detail"], (
        f"the list blamed a file other than the broken one: {listed.json()['detail']!r}"
    )
