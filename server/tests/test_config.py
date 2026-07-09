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
