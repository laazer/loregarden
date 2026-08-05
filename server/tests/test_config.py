from loregarden import config


def test_prime_claude_oauth_token_env_loads_cached_token(tmp_path, monkeypatch):
    """Every subprocess this backend spawns (Baxter, CLI adapters) inherits the
    process environment, not just this process's own HTTP client — so the
    cached token needs to land in os.environ, regardless of how the backend
    was launched (dev-server.sh sets it too, but the Tauri desktop app spawns
    `python -m loregarden` directly and never runs that script)."""
    monkeypatch.setattr(config.settings, "repo_root", tmp_path)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    token_dir = tmp_path / "data"
    token_dir.mkdir()
    (token_dir / ".claude-oauth-token").write_text("a-clean-token", encoding="utf-8")

    config._prime_claude_oauth_token_env()

    assert config.os.environ["CLAUDE_CODE_OAUTH_TOKEN"] == "a-clean-token"


def test_prime_claude_oauth_token_env_does_not_override_existing_env_var(tmp_path, monkeypatch):
    monkeypatch.setattr(config.settings, "repo_root", tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "already-set")
    token_dir = tmp_path / "data"
    token_dir.mkdir()
    (token_dir / ".claude-oauth-token").write_text("cached-token", encoding="utf-8")

    config._prime_claude_oauth_token_env()

    assert config.os.environ["CLAUDE_CODE_OAUTH_TOKEN"] == "already-set"


def test_prime_claude_oauth_token_env_ignores_malformed_cached_file(tmp_path, monkeypatch):
    """Guards the same corruption case usage_service.py's file reader guards —
    captured terminal output (spinners, prompts) must never reach os.environ."""
    monkeypatch.setattr(config.settings, "repo_root", tmp_path)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    token_dir = tmp_path / "data"
    token_dir.mkdir()
    (token_dir / ".claude-oauth-token").write_text("garbled\n✳ output", encoding="utf-8")

    config._prime_claude_oauth_token_env()

    assert "CLAUDE_CODE_OAUTH_TOKEN" not in config.os.environ


def test_prime_claude_oauth_token_env_noop_when_file_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(config.settings, "repo_root", tmp_path)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)

    config._prime_claude_oauth_token_env()

    assert "CLAUDE_CODE_OAUTH_TOKEN" not in config.os.environ


def test_prime_cursor_api_key_env_loads_cached_key(tmp_path, monkeypatch):
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    token_dir = tmp_path / "data"
    token_dir.mkdir()
    (token_dir / ".cursor-api-key").write_text("cursor_test_key", encoding="utf-8")
    monkeypatch.setattr(
        "loregarden.services.cursor_cli_auth.read_cursor_ide_access_token",
        lambda: None,
    )
    from loregarden.services.cursor_cli_auth import prime_cursor_api_key_env

    assert prime_cursor_api_key_env(repo_root=tmp_path) == "file"
    assert config.os.environ["CURSOR_API_KEY"] == "cursor_test_key"


def test_prime_cursor_api_key_env_falls_back_to_ide_token(tmp_path, monkeypatch):
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    monkeypatch.setattr(
        "loregarden.services.cursor_cli_auth.read_cursor_ide_access_token",
        lambda: "ide_session_token",
    )
    from loregarden.services.cursor_cli_auth import prime_cursor_api_key_env

    assert prime_cursor_api_key_env(repo_root=tmp_path) == "ide"
    assert config.os.environ["CURSOR_API_KEY"] == "ide_session_token"


def test_prime_cursor_api_key_env_does_not_override_existing_env_var(tmp_path, monkeypatch):
    monkeypatch.setenv("CURSOR_API_KEY", "already-set")
    token_dir = tmp_path / "data"
    token_dir.mkdir()
    (token_dir / ".cursor-api-key").write_text("cached-key", encoding="utf-8")
    from loregarden.services.cursor_cli_auth import prime_cursor_api_key_env

    assert prime_cursor_api_key_env(repo_root=tmp_path) == "env"
    assert config.os.environ["CURSOR_API_KEY"] == "already-set"


def test_format_cli_auth_hint_for_cursor_headless():
    from loregarden.services.cli_auth_errors import format_agent_unavailable

    msg = format_agent_unavailable(
        "Baxter",
        RuntimeError(
            "Authentication required. Please run 'agent login' first, or set CURSOR_API_KEY environment variable."
        ),
    )
    assert "Cursor IDE login" in msg
    assert "task cursor:setup-key" in msg
    assert "cursor.com/dashboard/integrations" in msg
    assert "LM Studio" in msg


def test_format_cli_auth_hint_for_lmstudio_down():
    from loregarden.services.cli_auth_errors import format_agent_unavailable

    msg = format_agent_unavailable(
        "Baxter",
        RuntimeError("LM Studio has no loaded models; load a model or set lmstudio_model"),
    )
    assert "Start LM Studio" in msg
    assert "127.0.0.1:1234" in msg


def test_format_agent_unavailable_for_missing_codex_cli():
    from loregarden.services.cli_auth_errors import format_agent_unavailable

    msg = format_agent_unavailable(
        "Baxter",
        FileNotFoundError(2, "No such file or directory", "codex"),
    )
    assert "Codex CLI" in msg
    assert "`codex`" in msg
    assert "LOREGARDEN_CODEX_BIN" in msg
    assert "not installed" in msg.lower() or "not on PATH" in msg
    assert "Errno 2" in msg or "No such file" in msg


def test_format_agent_unavailable_for_missing_cursor_agent():
    from loregarden.services.cli_auth_errors import format_agent_unavailable

    msg = format_agent_unavailable(
        "Baxter",
        FileNotFoundError("[Errno 2] No such file or directory: 'cursor-agent'"),
    )
    assert "Cursor Agent" in msg
    assert "cursor-agent" in msg
    assert "LOREGARDEN_CURSOR_BIN" in msg


def test_format_agent_unavailable_for_codex_chatgpt_model_mismatch():
    from loregarden.services.cli_auth_errors import format_agent_unavailable

    dump = (
        "2026-08-05T20:22:57Z ERROR rmcp::transport::worker: worker quit with fatal: "
        "Deserialize error: data did not match any variant of untagged enum JsonRpcMessage\n"
        "OpenAI Codex v0.146.1\n"
        "--------\n"
        "model: gpt-5\n"
        "user\n"
        "# Baxter — Home chat\n"
        + ("x" * 2000)
        + '\nERROR: {"type":"error","status":400,"error":{"type":"invalid_request_error",'
        '"message":"The \'gpt-5\' model is not supported when using Codex with a ChatGPT account."}}\n'
    )
    msg = format_agent_unavailable("Baxter", RuntimeError(dump))
    assert "ChatGPT-signed-in account" in msg
    assert "Clear the Codex model pin" in msg
    assert "Home chat" not in msg  # must not dump the prompt into the thread
    assert len(msg) < 1200
    assert "gpt-5" in msg


def test_format_agent_unavailable_compacts_codex_mcp_noise():
    from loregarden.services.cli_auth_errors import format_agent_unavailable

    dump = "ERROR rmcp::transport::worker: Deserialize error: JsonRpcMessage\n" + (
        "prompt line\n" * 200
    )
    msg = format_agent_unavailable("Baxter", RuntimeError(dump))
    assert "JSON-RPC handshake" in msg or "JsonRpcMessage" in msg
    assert "prompt line" not in msg or msg.count("prompt line") <= 1
    assert len(msg) < 1200
