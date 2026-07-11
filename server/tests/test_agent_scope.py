from loregarden.services.agent_scope import (
    check_agent_scope,
    extract_target_path,
    is_path_in_scope,
    relative_to_root,
)


def test_extract_target_path_prefers_file_path():
    assert extract_target_path({"file_path": "client/src/App.tsx"}) == "client/src/App.tsx"
    assert extract_target_path({"path": "server/loregarden/main.py"}) == "server/loregarden/main.py"
    assert extract_target_path({"command": "npm test"}) is None
    assert extract_target_path({}) is None
    assert extract_target_path("not-a-dict") is None


def test_relative_to_root_handles_absolute_and_relative(tmp_path):
    root = tmp_path / "repo"
    (root / "server").mkdir(parents=True)
    (root / "client").mkdir()

    absolute = str(root / "server" / "main.py")
    assert relative_to_root(absolute, str(root)) == "server/main.py"
    assert relative_to_root("client/src/App.tsx", str(root)) == "client/src/App.tsx"


def test_relative_to_root_returns_none_outside_repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "elsewhere" / "file.txt"
    assert relative_to_root(str(outside), str(root)) is None


def test_is_path_in_scope():
    assert is_path_in_scope("server/loregarden/main.py", ("server",)) is True
    assert is_path_in_scope("server", ("server",)) is True
    assert is_path_in_scope("client/src/App.tsx", ("server",)) is False
    # A sibling directory that merely starts with the same prefix string
    # must not be treated as in-scope (e.g. "server2" vs "server").
    assert is_path_in_scope("server2/main.py", ("server",)) is False


def test_check_agent_scope_denies_backend_implementer_writing_client(tmp_path):
    root = tmp_path / "repo"
    (root / "client" / "src").mkdir(parents=True)

    denial = check_agent_scope(
        agent_id="backend_implementer",
        agent_name="Backend Implementer Agent",
        tool_name="Edit",
        tool_input={"file_path": str(root / "client" / "src" / "ImportTicketsModal.tsx")},
        workspace_root=str(root),
    )
    assert denial is not None
    assert "backend_implementer" in denial
    assert "client" in denial


def test_check_agent_scope_allows_backend_implementer_writing_server(tmp_path):
    root = tmp_path / "repo"
    (root / "server" / "loregarden").mkdir(parents=True)

    denial = check_agent_scope(
        agent_id="backend_implementer",
        agent_name="Backend Implementer Agent",
        tool_name="Edit",
        tool_input={"file_path": str(root / "server" / "loregarden" / "main.py")},
        workspace_root=str(root),
    )
    assert denial is None


def test_check_agent_scope_allows_frontend_implementer_writing_client(tmp_path):
    root = tmp_path / "repo"
    (root / "client").mkdir(parents=True)

    denial = check_agent_scope(
        agent_id="frontend_implementer",
        agent_name="Frontend Implementer Agent",
        tool_name="Write",
        tool_input={"file_path": str(root / "client" / "New.tsx")},
        workspace_root=str(root),
    )
    assert denial is None


def test_check_agent_scope_ignores_non_write_tools():
    # Read/Bash/Grep etc. are not gated — only tools that create/modify files.
    denial = check_agent_scope(
        agent_id="backend_implementer",
        agent_name="Backend Implementer Agent",
        tool_name="Read",
        tool_input={"file_path": "client/src/App.tsx"},
        workspace_root="/repo",
    )
    assert denial is None


def test_check_agent_scope_ignores_unrestricted_agents():
    # planner/spec/reviewers etc. have no declared scope and are unrestricted.
    denial = check_agent_scope(
        agent_id="planner",
        agent_name="Planner Agent",
        tool_name="Write",
        tool_input={"file_path": "client/src/App.tsx"},
        workspace_root="/repo",
    )
    assert denial is None


def test_check_agent_scope_fails_closed_for_unresolvable_path(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    # A target path with no way to relate it to the workspace root — e.g. a
    # different absolute filesystem tree entirely.
    denial = check_agent_scope(
        agent_id="backend_implementer",
        agent_name="Backend Implementer Agent",
        tool_name="Write",
        tool_input={"file_path": "/completely/different/tree/file.py"},
        workspace_root=str(root),
    )
    assert denial is not None
