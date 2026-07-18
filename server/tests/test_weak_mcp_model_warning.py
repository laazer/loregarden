from loregarden.services.cli_settings import weak_mcp_model_warning


def test_haiku_claude_agent_warns():
    warning = weak_mcp_model_warning("haiku", "claude")
    assert warning is not None
    assert "haiku" in warning.lower()


def test_haiku_model_id_variant_warns():
    warning = weak_mcp_model_warning("claude-haiku-4-5-20251001", "claude")
    assert warning is not None


def test_sonnet_does_not_warn():
    assert weak_mcp_model_warning("sonnet", "claude") is None


def test_non_claude_adapter_does_not_warn():
    # Cursor drives MCP differently; this heuristic is scoped to the claude adapter.
    assert weak_mcp_model_warning("haiku", "cursor") is None


def test_empty_model_does_not_warn():
    assert weak_mcp_model_warning("", "claude") is None
