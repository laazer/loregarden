import os
import shlex
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from loregarden.agents.mcp_context import append_mcp_cli_args, resolve_mcp_url
from loregarden.config import settings
from loregarden.services.cli_settings import (
    resolve_claude_model,
    resolve_cursor_model,
    resolve_effective_adapter,
    resolve_lmstudio_base_url,
    resolve_lmstudio_model,
)

DEFAULT_CLAUDE_USER_PROMPT = (
    "Execute the Loregarden stage task described in the appended system prompt. "
    "Work in the workspace directory and complete the stage deliverables."
)

DEFAULT_CURSOR_USER_PROMPT = (
    "Execute the Loregarden stage task below. Work in the workspace and complete "
    "the stage deliverables.\n\n"
)

DEFAULT_TRIAGE_USER_PROMPT = (
    "Reply to the operator based on the ticket context in the system prompt. "
    "Be concise and actionable."
)


DEFAULT_BRANCH_TRIAGE_USER_PROMPT = (
    "Execute the operator's request in the workspace repository. "
    "Run git and shell commands when needed, then report what you did and relevant output."
)


@dataclass(frozen=True)
class CliInvocation:
    argv: list[str]
    stdin_prompt: str | None = None
    use_prompt_file: bool = False
    interactive: bool = False
    adapter: str = "local"
    cwd: str = ""
    resume_session_id: str = ""


def _bin(name: str, env_key: str) -> str:
    override = os.environ.get(env_key)
    if override:
        return override
    found = shutil.which(name)
    return found or name


def permission_bypass_enabled() -> bool:
    if os.environ.get("LOREGARDEN_ALLOW_PERMISSION_BYPASS", "").lower() in {"1", "true", "yes"}:
        return True
    return settings.allow_permission_bypass


def _claude_permission_mode() -> str:
    if permission_bypass_enabled():
        return os.environ.get("LOREGARDEN_CLAUDE_PERMISSION_MODE", "bypassPermissions")
    return os.environ.get("LOREGARDEN_CLAUDE_PERMISSION_MODE", settings.claude_permission_mode)


def _append_model_flag(argv: list[str], model: str) -> None:
    if model:
        argv.extend(["--model", model])


def _env_command_override(
    *,
    agent_id: str,
    prompt: str,
    prompt_file: Path,
    skill_name: str,
    workspace_root: Path,
) -> CliInvocation | None:
    """Honor a per-agent ``LOREGARDEN_AGENT_<ID>_CMD`` argv template, if set."""
    override = os.environ.get(f"LOREGARDEN_AGENT_{agent_id.upper()}_CMD")
    if not override:
        return None
    argv = shlex.split(
        override.format(
            prompt_file=str(prompt_file),
            prompt=prompt,
            agent_id=agent_id,
            skill=skill_name,
            workspace=str(workspace_root),
        )
    )
    return CliInvocation(argv=argv, stdin_prompt=None, cwd=str(workspace_root))


def _codex_invocation(*, prompt: str, workspace_root: Path) -> CliInvocation:
    return CliInvocation(
        argv=[_bin("codex", "LOREGARDEN_CODEX_BIN"), "exec", "-"],
        stdin_prompt=prompt,
        adapter="codex",
        cwd=str(workspace_root),
    )


def build_interactive_invocation(
    *,
    adapter: str,
    prompt_file: Path,
    workspace_root: Path,
    resume_session_id: str = "",
    claude_model: str = "",
    cursor_model: str = "",
    db_session=None,
) -> CliInvocation:
    """Headless CLIs with permission prompts routed through Loregarden."""
    cwd = str(workspace_root)

    if adapter == "claude":
        argv = [
            _bin("claude", "LOREGARDEN_CLAUDE_BIN"),
            "--output-format",
            "stream-json",
            "--input-format",
            "stream-json",
            "--verbose",
            "--permission-mode",
            _claude_permission_mode(),
            "--permission-prompt-tool",
            "stdio",
            "--add-dir",
            cwd,
            "--append-system-prompt-file",
            str(prompt_file),
        ]
        _append_model_flag(argv, claude_model)
        if resume_session_id:
            argv.extend(["--resume", resume_session_id])
        append_mcp_cli_args(argv, adapter="claude", session=db_session)
        return CliInvocation(
            argv=argv,
            interactive=True,
            adapter="claude",
            cwd=cwd,
            resume_session_id=resume_session_id,
            use_prompt_file=True,
        )

    if adapter == "cursor":
        argv = [
            _bin("cursor-agent", "LOREGARDEN_CURSOR_BIN"),
            "agent",
            "-p",
            "--output-format",
            os.environ.get("LOREGARDEN_CURSOR_OUTPUT_FORMAT", settings.cursor_output_format),
            "--workspace",
            cwd,
        ]
        _append_model_flag(argv, cursor_model)
        if permission_bypass_enabled():
            argv.extend(["--trust", "--force"])
        extra = os.environ.get("LOREGARDEN_CURSOR_AGENT_ARGS")
        if extra:
            argv[2:2] = shlex.split(extra)
        append_mcp_cli_args(argv, adapter="cursor", session=db_session)
        return CliInvocation(
            argv=argv,
            interactive=False,
            adapter="cursor",
            cwd=cwd,
            resume_session_id=resume_session_id,
        )

    raise ValueError(f"Interactive invocation unsupported for adapter: {adapter}")


def _claude_terminal_handoff_invocation(
    *,
    prompt_file: Path,
    workspace_root: Path,
    claude_model: str = "",
) -> CliInvocation:
    """A normal interactive `claude` session, seeded with the stage's system prompt.

    Unlike `build_interactive_invocation`, this does not use `--permission-prompt-tool
    stdio` — that protocol expects the Loregarden app's own PermissionBridgeRunner on the
    other end of stdin/stdout. Here a human owns the terminal directly, so Claude Code's
    normal interactive permission prompting applies.
    """
    cwd = str(workspace_root)
    argv = [
        _bin("claude", "LOREGARDEN_CLAUDE_BIN"),
        "--add-dir",
        cwd,
        "--append-system-prompt-file",
        str(prompt_file),
        # A trailing positional prompt is submitted as the session's first message even in
        # interactive mode — without it, claude opens an empty REPL and waits for the human
        # to type something instead of starting on the stage immediately. This must come
        # before --model/--mcp-config below: claude's arg parser mis-resolves --mcp-config's
        # value to whichever bare positional comes *last* in argv, so a positional dropped in
        # after --mcp-config gets mistaken for its value (matches _claude_print_invocation's
        # ordering, which places its prompt positional before the flag block for this reason).
        os.environ.get("LOREGARDEN_CLAUDE_USER_PROMPT", DEFAULT_CLAUDE_USER_PROMPT),
    ]
    _append_model_flag(argv, claude_model)
    append_mcp_cli_args(argv, adapter="claude")
    return CliInvocation(
        argv=argv,
        interactive=True,
        adapter="claude",
        cwd=cwd,
        use_prompt_file=True,
    )


def resolve_terminal_handoff_invocation(
    *,
    agent_id: str,
    adapter: str,
    prompt: str,
    prompt_file: Path,
    skill_name: str,
    workspace_root: Path,
    workspace=None,
) -> CliInvocation:
    """Resolve a CLI invocation meant to be copied and run in a human's own terminal.

    Deliberately ignores the app's print-mode/permission-bridge branching in
    `resolve_cli_invocation` — those assume the Loregarden process supervises the
    subprocess. A terminal handoff has no such supervisor, so claude gets a plain
    interactive session and cursor gets its self-contained print-mode invocation
    (cursor has no interactive mode attested in this codebase to build against).
    """
    override = _env_command_override(
        agent_id=agent_id,
        prompt=prompt,
        prompt_file=prompt_file,
        skill_name=skill_name,
        workspace_root=workspace_root,
    )
    if override is not None:
        return override

    selected = resolve_effective_adapter(agent_adapter=adapter, workspace=workspace)

    if selected == "claude":
        return _claude_terminal_handoff_invocation(
            prompt_file=prompt_file,
            workspace_root=workspace_root,
            claude_model=resolve_claude_model(workspace),
        )

    if selected == "cursor":
        return _cursor_print_invocation(
            prompt=prompt,
            workspace_root=workspace_root,
            cursor_model=resolve_cursor_model(workspace),
        )

    raise ValueError(
        f"Terminal handoff only supports claude/cursor CLIs (workspace resolves to '{selected}')"
    )


def _claude_oauth_env_prefix() -> str:
    """`CLAUDE_CODE_OAUTH_TOKEN=... ` prefix when a cached `claude setup-token` token exists.

    dev-server.sh / config.py prime this same token into the backend's own process env so
    every subprocess *this server* spawns picks it up — see `4fe6525` ("Fix misleading 'not
    logged in' Claude auth errors"). A terminal-handoff command runs in the human's own shell,
    a separate process tree the backend never touches, so it needs the same token applied
    explicitly. Without it, whichever `claude` binary happens to resolve first on the user's
    PATH must already be logged in on its own — which may be a different install/session than
    the one they normally use (e.g. a standalone CLI binary vs. a desktop-app-managed one).
    Reads the token file at paste-time (`$(cat ...)`) rather than inlining the token value
    itself, so a copied command never carries the raw secret and always uses the current token.
    """
    from loregarden.services.usage_service import claude_oauth_token_file_path

    token_path = claude_oauth_token_file_path()
    if not token_path.is_file():
        return ""
    return f'CLAUDE_CODE_OAUTH_TOKEN="$(cat {shlex.quote(str(token_path))})" '


def render_terminal_handoff_command(
    invocation: CliInvocation, *, cleanup_path: Path | None = None
) -> str:
    """Render an invocation as a short, paste-ready shell command.

    The system prompt is written to disk ahead of time (see
    CliAgentExecutor.prepare_terminal_handoff) and referenced by path, rather than inlined
    via heredoc — a full stage prompt can run tens of KB, and pasting that much text
    directly into a terminal can overwhelm some terminals' paste handling.
    """
    prefix = _claude_oauth_env_prefix() if invocation.adapter == "claude" else ""
    command = prefix + " ".join(shlex.quote(token) for token in invocation.argv)
    if cleanup_path is not None:
        command += f" ; rm -rf {shlex.quote(str(cleanup_path))}"
    return command


def _claude_print_invocation(
    *,
    prompt_file: Path,
    workspace_root: Path,
    claude_model: str = "",
) -> CliInvocation:
    output_format = os.environ.get("LOREGARDEN_CLAUDE_OUTPUT_FORMAT", settings.claude_output_format)
    argv = [
        _bin("claude", "LOREGARDEN_CLAUDE_BIN"),
        "-p",
        "--output-format",
        output_format,
        "--permission-mode",
        _claude_permission_mode(),
        "--add-dir",
        str(workspace_root),
        "--append-system-prompt-file",
        str(prompt_file),
        os.environ.get("LOREGARDEN_CLAUDE_USER_PROMPT", DEFAULT_CLAUDE_USER_PROMPT),
    ]
    _append_model_flag(argv, claude_model)
    append_mcp_cli_args(argv, adapter="claude")
    return CliInvocation(argv=argv, use_prompt_file=True, adapter="claude", cwd=str(workspace_root))


def _cursor_print_invocation(
    *,
    prompt: str,
    workspace_root: Path,
    cursor_model: str = "",
) -> CliInvocation:
    argv = [
        _bin("cursor-agent", "LOREGARDEN_CURSOR_BIN"),
        "agent",
        "-p",
        "--output-format",
        os.environ.get("LOREGARDEN_CURSOR_OUTPUT_FORMAT", settings.cursor_output_format),
        "--workspace",
        str(workspace_root),
        f"{os.environ.get('LOREGARDEN_CURSOR_USER_PROMPT', DEFAULT_CURSOR_USER_PROMPT)}{prompt}",
    ]
    _append_model_flag(argv, cursor_model)
    if permission_bypass_enabled():
        argv[3:3] = ["--trust", "--force"]
    extra = os.environ.get("LOREGARDEN_CURSOR_AGENT_ARGS")
    if extra:
        argv[2:2] = shlex.split(extra)
    append_mcp_cli_args(argv, adapter="cursor")
    return CliInvocation(argv=argv, adapter="cursor", cwd=str(workspace_root))


def _local_invocation(*, agent_id: str, skill_name: str, prompt_file: Path) -> CliInvocation:
    return CliInvocation(
        argv=[
            sys.executable,
            "-m",
            "loregarden.agents.executors.local_runner",
            "--agent-id",
            agent_id,
            "--skill",
            skill_name,
            "--prompt-file",
            str(prompt_file),
        ],
        adapter="local",
    )


def _lmstudio_invocation(
    *,
    prompt_file: Path,
    workspace_root: Path,
    base_url: str,
    model: str,
    run_id: str = "",
    workspace_slug: str = "",
    granted_tools: list[str] | None = None,
) -> CliInvocation:
    argv = [
        sys.executable,
        "-m",
        "loregarden.agents.executors.lmstudio_runner",
        "--prompt-file",
        str(prompt_file),
        "--base-url",
        base_url,
    ]
    if model:
        argv.extend(["--model", model])
    # LM Studio speaks no MCP of its own, so the runner is told where the
    # endpoint is and which run it belongs to. Passed as argv rather than env:
    # the subprocess inherits this process's environment, which is shared with
    # every concurrently running ticket.
    if run_id:
        argv.extend(["--mcp-url", resolve_mcp_url(), "--run-id", run_id])
        if workspace_slug:
            argv.extend(["--workspace-slug", workspace_slug])
        if granted_tools:
            argv.extend(["--tools", ",".join(granted_tools)])
    return CliInvocation(
        argv=argv,
        use_prompt_file=True,
        adapter="lmstudio",
        cwd=str(workspace_root),
    )


def resolve_cli_invocation(
    *,
    agent_id: str,
    adapter: str,
    prompt: str,
    prompt_file: Path,
    skill_name: str,
    workspace_root: Path,
    workspace=None,
    resume_session_id: str = "",
    ticket_adapter: str = "default",
    ticket_claude_model: str = "",
    ticket_cursor_model: str = "",
    stage_model: str = "",
    agent_model: str = "",
    run_id: str = "",
    workspace_slug: str = "",
    granted_tools: list[str] | None = None,
    db_session=None,
) -> CliInvocation:
    """Resolve subprocess argv. Agents are adapters — no orchestration logic here."""
    override = _env_command_override(
        agent_id=agent_id,
        prompt=prompt,
        prompt_file=prompt_file,
        skill_name=skill_name,
        workspace_root=workspace_root,
    )
    if override is not None:
        return override

    selected = resolve_effective_adapter(
        agent_adapter=adapter, workspace=workspace, ticket_adapter=ticket_adapter
    )
    claude_model = resolve_claude_model(
        workspace,
        ticket_model=ticket_claude_model,
        stage_model=stage_model,
        agent_model=agent_model,
    )
    cursor_model = resolve_cursor_model(
        workspace,
        ticket_model=ticket_cursor_model,
        stage_model=stage_model,
        agent_model=agent_model,
    )

    if selected == "local":
        return _local_invocation(
            agent_id=agent_id,
            skill_name=skill_name,
            prompt_file=prompt_file,
        )

    if selected in {"claude", "cursor"} and not permission_bypass_enabled():
        return build_interactive_invocation(
            adapter=selected,
            db_session=db_session,
            prompt_file=prompt_file,
            workspace_root=workspace_root,
            resume_session_id=resume_session_id,
            claude_model=claude_model if selected == "claude" else "",
            cursor_model=cursor_model if selected == "cursor" else "",
        )

    if selected == "claude":
        return _claude_print_invocation(
            prompt_file=prompt_file,
            workspace_root=workspace_root,
            claude_model=claude_model,
        )

    if selected == "cursor":
        return _cursor_print_invocation(
            prompt=prompt,
            workspace_root=workspace_root,
            cursor_model=cursor_model,
        )

    if selected == "codex":
        return _codex_invocation(prompt=prompt, workspace_root=workspace_root)

    if selected == "lmstudio":
        return _lmstudio_invocation(
            prompt_file=prompt_file,
            workspace_root=workspace_root,
            base_url=resolve_lmstudio_base_url(workspace),
            model=resolve_lmstudio_model(workspace),
            run_id=run_id,
            workspace_slug=workspace_slug,
            granted_tools=granted_tools,
        )

    raise ValueError(f"Unknown CLI adapter: {selected}")


def build_triage_invocation(
    *,
    agent_id: str,
    adapter: str,
    prompt: str,
    prompt_file: Path,
    skill_name: str,
    workspace_root: Path,
    workspace=None,
    user_prompt: str | None = None,
) -> CliInvocation:
    """One-shot, non-interactive CLI for the triage chat channel.

    Stage runs use stream-json + permission bridge; triage must return plain text
    from stdout in a single communicate() call.
    """
    override = _env_command_override(
        agent_id=agent_id,
        prompt=prompt,
        prompt_file=prompt_file,
        skill_name=skill_name,
        workspace_root=workspace_root,
    )
    if override is not None:
        return override

    selected = resolve_effective_adapter(agent_adapter=adapter, workspace=workspace)
    cursor_model = resolve_cursor_model(workspace)
    triage_user_prompt = user_prompt or os.environ.get(
        "LOREGARDEN_TRIAGE_USER_PROMPT", DEFAULT_TRIAGE_USER_PROMPT
    )

    if selected == "local":
        return _local_invocation(
            agent_id=agent_id,
            skill_name=skill_name,
            prompt_file=prompt_file,
        )

    if selected == "claude":
        triage_model = (
            os.environ.get("LOREGARDEN_TRIAGE_CLAUDE_MODEL", "").strip()
            or resolve_claude_model(workspace)
            or "haiku"
        )
        argv = [
            _bin("claude", "LOREGARDEN_CLAUDE_BIN"),
            "-p",
            "--output-format",
            "text",
            "--permission-mode",
            os.environ.get("LOREGARDEN_TRIAGE_PERMISSION_MODE", "bypassPermissions"),
            "--append-system-prompt-file",
            str(prompt_file),
            triage_user_prompt,
        ]
        _append_model_flag(argv, triage_model)
        return CliInvocation(
            argv=argv,
            use_prompt_file=True,
            adapter="claude",
            cwd=str(workspace_root),
        )

    if selected == "cursor":
        argv = [
            _bin("cursor-agent", "LOREGARDEN_CURSOR_BIN"),
            "agent",
            "-p",
            "--output-format",
            "text",
            "--trust",
            "--force",
            "--workspace",
            str(workspace_root),
            f"{triage_user_prompt}\n\n{prompt}",
        ]
        _append_model_flag(argv, cursor_model)
        extra = os.environ.get("LOREGARDEN_CURSOR_AGENT_ARGS")
        if extra:
            argv[2:2] = shlex.split(extra)
        return CliInvocation(argv=argv, adapter="cursor", cwd=str(workspace_root))

    if selected == "codex":
        return _codex_invocation(prompt=prompt, workspace_root=workspace_root)

    if selected == "lmstudio":
        return _lmstudio_invocation(
            prompt_file=prompt_file,
            workspace_root=workspace_root,
            base_url=resolve_lmstudio_base_url(workspace),
            model=resolve_lmstudio_model(workspace),
        )

    raise ValueError(f"Unknown CLI adapter for triage: {selected}")
