import os
import shlex
import shutil
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from loregarden.agents.mcp_context import (
    append_mcp_cli_args,
    mcp_cli_env,
    resolve_api_base_url,
    resolve_mcp_url,
)
from loregarden.config import settings
from loregarden.models.domain import CliAdapter
from loregarden.services.cli_settings import (
    adapter_model_pins_apply,
    apply_cursor_effort,
    resolve_effective_adapter,
    resolve_effort_for_adapter,
    resolve_lmstudio_base_url,
    resolve_model_for_adapter,
    ticket_effort_for_adapter,
    ticket_model_for_adapter,
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
    # Overlaid on the spawning process's environment, not a replacement for it.
    # Only opencode populates this: it has no MCP flag, so its per-run config
    # travels as OPENCODE_CONFIG_CONTENT (see ``mcp_context.mcp_cli_env``).
    env: dict[str, str] = field(default_factory=dict)
    # What this invocation actually pinned, after the workspace/ticket/stage/agent
    # tiers were resolved — recorded on the run so a token count can be turned
    # into a cost. Empty means nothing was pinned and the CLI chose for itself,
    # which is unknown rather than a default anyone here can name; the stream's
    # own report wins over these when it carries one (see ``agents.run_usage``).
    model: str = ""
    effort: str = ""


def invocation_env(invocation: CliInvocation) -> dict[str, str] | None:
    """Full environment for spawning ``invocation``, or None to inherit unchanged.

    ``None`` rather than a copy of ``os.environ`` so a run with no overlay keeps
    the pre-existing inherit-the-parent behaviour exactly, including any variable
    the supervising process sets after import.
    """
    if not invocation.env:
        return None
    return {**os.environ, **invocation.env}


def _bin(name: str, env_key: str) -> str:
    override = os.environ.get(env_key)
    if override:
        return override
    found = shutil.which(name)
    return found or name


def permission_bypass_enabled() -> bool:
    raw = os.environ.get("LOREGARDEN_ALLOW_PERMISSION_BYPASS")
    if raw is None:
        return False
    return raw.lower() in {"1", "true", "yes"}


def _claude_permission_mode() -> str:
    if permission_bypass_enabled():
        return os.environ.get("LOREGARDEN_CLAUDE_PERMISSION_MODE", "bypassPermissions")
    return os.environ.get("LOREGARDEN_CLAUDE_PERMISSION_MODE", settings.claude_permission_mode)


def _append_model_flag(argv: list[str], model: str) -> None:
    if model:
        argv.extend(["--model", model])


def _append_claude_effort_flag(argv: list[str], effort: str) -> None:
    """`claude --effort <low|medium|high|xhigh|max>`. Claude-only — cursor folds
    effort into the model id instead (see ``apply_cursor_effort``)."""
    if effort:
        argv.extend(["--effort", effort])


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


def _codex_invocation(
    *,
    prompt: str,
    workspace_root: Path,
    codex_model: str = "",
    orchestrated: bool = False,
) -> CliInvocation:
    # ``--json`` is the Codex equivalent of Claude/Cursor stream-json: events
    # land on stdout as the turn progresses. Without it, exec is silent until
    # the final message, and print-mode's idle timeout treats that silence as a
    # hung process (see CliAgentExecutor._run_print_mode).
    argv = [
        _bin("codex", "LOREGARDEN_CODEX_BIN"),
        "exec",
        "--json",
        "--cd",
        str(workspace_root),
    ]
    _append_model_flag(argv, codex_model)
    append_mcp_cli_args(argv, adapter="codex", orchestrated=orchestrated)
    argv.append("-")
    return CliInvocation(
        argv=argv,
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
    claude_effort: str = "",
    partial_messages: bool = False,
    db_session=None,
    orchestrated: bool = True,
) -> CliInvocation:
    """A headless `claude` session with permission prompts routed through Loregarden.

    Claude-only, and that is a property of the tools rather than a gap here: the
    bridge holds stdin open and speaks stream-json into the session, which needs
    `--input-format stream-json`. `cursor-agent` has no such flag (see
    `services.run_steering`), so it cannot be driven this way at all — cursor
    stage runs go through `_cursor_print_invocation` regardless of permission
    bypass.

    `partial_messages` adds the token-level events a chat surface needs to show
    reasoning as it forms; without it a thinking block only arrives once it is
    finished, which for a long thought is one jump from nothing to everything.
    Off by default: a stage run writes its stream to a log nobody watches
    keystroke by keystroke, and the extra events are pure volume there.

    ``orchestrated`` defaults True for stage runs. Chat surfaces (triage / Home /
    branch) must pass False so create_ticket and other interactive MCP tools are
    not denied at the MCP dispatch layer.
    """
    cwd = str(workspace_root)

    if adapter == CliAdapter.CLAUDE:
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
        if partial_messages:
            argv.append("--include-partial-messages")
        _append_model_flag(argv, claude_model)
        _append_claude_effort_flag(argv, claude_effort)
        if resume_session_id:
            argv.extend(["--resume", resume_session_id])
        append_mcp_cli_args(argv, adapter="claude", session=db_session, orchestrated=orchestrated)
        return CliInvocation(
            argv=argv,
            interactive=True,
            adapter="claude",
            cwd=cwd,
            resume_session_id=resume_session_id,
            use_prompt_file=True,
        )

    raise ValueError(f"Interactive invocation unsupported for adapter: {adapter}")


def _claude_terminal_handoff_invocation(
    *,
    prompt_file: Path,
    workspace_root: Path,
    claude_model: str = "",
    claude_effort: str = "",
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
    _append_claude_effort_flag(argv, claude_effort)
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
    model = resolve_model_for_adapter(selected, workspace)
    effort = resolve_effort_for_adapter(selected, workspace)

    if selected == CliAdapter.CLAUDE:
        return _claude_terminal_handoff_invocation(
            prompt_file=prompt_file,
            workspace_root=workspace_root,
            claude_model=model,
            claude_effort=effort,
        )

    if selected == CliAdapter.CURSOR:
        return _cursor_print_invocation(
            prompt=prompt,
            workspace_root=workspace_root,
            cursor_model=apply_cursor_effort(model, effort),
        )

    if selected == CliAdapter.CODEX:
        return _codex_invocation(
            prompt=prompt,
            workspace_root=workspace_root,
            codex_model=model,
        )

    if selected == CliAdapter.OPENCODE:
        return _opencode_invocation(
            prompt=prompt,
            prompt_file=prompt_file,
            workspace_root=workspace_root,
            opencode_model=model,
            opencode_effort=effort,
        )

    raise ValueError(
        "Terminal handoff only supports claude/cursor/codex/opencode CLIs "
        f"(workspace resolves to '{selected}')"
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
    invocation: CliInvocation, *, cleanup_path: Path | None = None, run_id: str | None = None
) -> str:
    """Render an invocation as a short, paste-ready shell command.

    The system prompt is written to disk ahead of time (see
    CliAgentExecutor.prepare_terminal_handoff) and referenced by path, rather than inlined
    via heredoc — a full stage prompt can run tens of KB, and pasting that much text
    directly into a terminal can overwhelm some terminals' paste handling.

    With ``run_id``, the command is bracketed by liveness pings: a check-in that
    records the pasting shell's pid before the CLI starts (``&&`` — a rejected
    check-in means the run was already reaped, so the CLI must not start against
    it), and an exit ping after the CLI ends so the run row is settled even when
    the session finishes without completing the stage. Without these, a handoff
    AgentRun stays RUNNING forever — blocking triage chat and the self-improve
    restart watcher — since no process supervises it.
    """
    prefix = _claude_oauth_env_prefix() if invocation.adapter == CliAdapter.CLAUDE else ""
    # An invocation whose MCP config rides in the environment (opencode) would
    # otherwise reach the operator's shell configured for nothing at all, and the
    # agent would run the stage with no loregarden_* tools rather than fail loudly.
    prefix += "".join(
        f"{key}={shlex.quote(value)} " for key, value in sorted(invocation.env.items())
    )
    command = prefix + " ".join(shlex.quote(token) for token in invocation.argv)
    if run_id is not None:
        base = f"{resolve_api_base_url()}/api/runs/{run_id}"
        checkin = (
            f"curl -fsS -X POST -H 'Content-Type: application/json' "
            f'--data "{{\\"pid\\": $$}}" {shlex.quote(base + "/handoff-checkin")} > /dev/null'
        )
        command = f"{checkin} && {command}"
    if cleanup_path is not None:
        command += f" ; rm -rf {shlex.quote(str(cleanup_path))}"
    if run_id is not None:
        exited = f"curl -fsS -X POST {shlex.quote(base + '/handoff-exited')} > /dev/null 2>&1"
        command += f" ; {exited}"
    return command


def _claude_print_invocation(
    *,
    prompt_file: Path,
    workspace_root: Path,
    claude_model: str = "",
    claude_effort: str = "",
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
    if output_format == "stream-json":
        # `-p --output-format stream-json` requires verbose mode, and partial
        # messages provide the stdout heartbeat print mode relies on.
        argv[2:2] = ["--verbose", "--include-partial-messages"]
    _append_model_flag(argv, claude_model)
    _append_claude_effort_flag(argv, claude_effort)
    append_mcp_cli_args(argv, adapter="claude", orchestrated=True)
    return CliInvocation(argv=argv, use_prompt_file=True, adapter="claude", cwd=str(workspace_root))


def _cursor_print_invocation(
    *,
    prompt: str,
    workspace_root: Path,
    cursor_model: str = "",
    orchestrated: bool = False,
) -> CliInvocation:
    """Shared by every cursor stage run (`orchestrated=True`) and a human terminal
    handoff (`orchestrated=False`, the default) — see `resolve_cli_invocation` vs
    `resolve_terminal_handoff_invocation`.

    Print mode is cursor's only usable stage mode: it cannot be bridged (no
    `--input-format`), so `--trust --force` is the one permission lever, and with
    bypass off the CLI applies its own defaults instead.

    Default ``stream-json`` (+ ``--stream-partial-output``) feeds the live run log
    as the agent thinks and tools fire; ``text`` only prints when the run ends.
    """
    output_format = os.environ.get("LOREGARDEN_CURSOR_OUTPUT_FORMAT", settings.cursor_output_format)
    argv = [
        _bin("cursor-agent", "LOREGARDEN_CURSOR_BIN"),
        "agent",
        "-p",
        "--output-format",
        output_format,
        "--workspace",
        str(workspace_root),
        f"{os.environ.get('LOREGARDEN_CURSOR_USER_PROMPT', DEFAULT_CURSOR_USER_PROMPT)}{prompt}",
    ]
    # Partial deltas only apply with print + stream-json; skip when the operator
    # forces text (or another format) via LOREGARDEN_CURSOR_OUTPUT_FORMAT.
    if output_format == "stream-json":
        fmt_idx = argv.index("--output-format")
        argv.insert(fmt_idx + 2, "--stream-partial-output")
    _append_model_flag(argv, cursor_model)
    if permission_bypass_enabled():
        argv[3:3] = ["--trust", "--force"]
    extra = os.environ.get("LOREGARDEN_CURSOR_AGENT_ARGS")
    if extra:
        argv[2:2] = shlex.split(extra)
    append_mcp_cli_args(argv, adapter="cursor", orchestrated=orchestrated)
    return CliInvocation(argv=argv, adapter="cursor", cwd=str(workspace_root))


DEFAULT_OPENCODE_USER_PROMPT = (
    "Execute the Loregarden stage task in the attached prompt file. "
    "Work in the workspace directory and complete the stage deliverables."
)


def _opencode_invocation(
    *,
    prompt: str,
    workspace_root: Path,
    opencode_model: str = "",
    opencode_effort: str = "",
    orchestrated: bool = False,
    read_only: bool = False,
    prompt_file: Path | None = None,
) -> CliInvocation:
    """`opencode run` in JSON-event mode, for stage runs and one-shot chat alike.

    ``--format json`` is opencode's stream-json equivalent: NDJSON events land on
    stdout as tools fire, which is what keeps print mode's idle timeout from
    treating a long turn as a hung process (see `_codex_invocation`).

    The prompt goes over stdin rather than as the positional ``message``. A stage
    prompt runs tens of KB, which is argv-length territory, and opencode reads
    stdin when no message is given. ``prompt_file`` switches that to ``--file``
    plus a short message, for a terminal handoff: the rendered command is a
    single pasteable line with no stdin producer to lose.

    ``--variant`` is opencode's effort control. Unlike `claude --effort`, a value
    the selected model does not define is ignored rather than rejected, so it is
    forwarded without gating on a per-model support table the way cursor's
    bracket parameter must be.
    """
    argv = [
        _bin("opencode", "LOREGARDEN_OPENCODE_BIN"),
        "run",
        "--format",
        "json",
        "--dir",
        str(workspace_root),
    ]
    if opencode_model:
        argv.extend(["--model", opencode_model])
    if opencode_effort:
        argv.extend(["--variant", opencode_effort])
    # opencode's own permission prompts have no headless surface: unanswered,
    # every write becomes a denial the agent reports as a failed stage. Read-only
    # callers get the CLI's defaults instead, which is what "ask" means here.
    if not read_only and permission_bypass_enabled():
        argv.append("--dangerously-skip-permissions")
    extra = os.environ.get("LOREGARDEN_OPENCODE_ARGS")
    if extra:
        argv[2:2] = shlex.split(extra)
    env = mcp_cli_env(adapter="opencode", orchestrated=orchestrated)
    if prompt_file is not None:
        argv.extend(
            [
                "--file",
                str(prompt_file),
                os.environ.get("LOREGARDEN_OPENCODE_USER_PROMPT", DEFAULT_OPENCODE_USER_PROMPT),
            ]
        )
        return CliInvocation(
            argv=argv,
            use_prompt_file=True,
            adapter="opencode",
            cwd=str(workspace_root),
            env=env,
        )
    return CliInvocation(
        argv=argv,
        stdin_prompt=prompt,
        adapter="opencode",
        cwd=str(workspace_root),
        env=env,
    )


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
    effort: str = "",
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
    if effort:
        argv.extend(["--effort", effort])
    # Token/stream output (and tool-round heartbeats in the runner) keep
    # print-mode's idle budget alive — same role as Claude partial messages
    # and Codex ``--json``. Opt out with LOREGARDEN_LMSTUDIO_STREAM=0.
    stream_off = os.environ.get("LOREGARDEN_LMSTUDIO_STREAM", "1").lower() in {
        "0",
        "false",
        "no",
    }
    if not stream_off:
        argv.append("--stream")
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
    ticket_codex_model: str = "",
    ticket_lmstudio_model: str = "",
    ticket_opencode_model: str = "",
    ticket_claude_effort: str = "",
    ticket_cursor_effort: str = "",
    ticket_lmstudio_effort: str = "",
    ticket_opencode_effort: str = "",
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
    pins_apply = adapter_model_pins_apply(agent_adapter=adapter, selected_adapter=selected)
    model = resolve_model_for_adapter(
        selected,
        workspace,
        ticket_model=ticket_model_for_adapter(
            selected,
            claude_model=ticket_claude_model,
            cursor_model=ticket_cursor_model,
            codex_model=ticket_codex_model,
            lmstudio_model=ticket_lmstudio_model,
            opencode_model=ticket_opencode_model,
        ),
        stage_model=stage_model if pins_apply else "",
        agent_model=agent_model if pins_apply else "",
    )
    effort = resolve_effort_for_adapter(
        selected,
        workspace,
        ticket_effort=ticket_effort_for_adapter(
            selected,
            claude_effort=ticket_claude_effort,
            cursor_effort=ticket_cursor_effort,
            lmstudio_effort=ticket_lmstudio_effort,
            opencode_effort=ticket_opencode_effort,
        ),
    )

    if selected == CliAdapter.LOCAL:
        invocation = _local_invocation(
            agent_id=agent_id,
            skill_name=skill_name,
            prompt_file=prompt_file,
        )
    elif selected == CliAdapter.CLAUDE and not permission_bypass_enabled():
        invocation = build_interactive_invocation(
            adapter=selected,
            db_session=db_session,
            prompt_file=prompt_file,
            workspace_root=workspace_root,
            resume_session_id=resume_session_id,
            claude_model=model,
            claude_effort=effort,
        )
    elif selected == CliAdapter.CLAUDE:
        invocation = _claude_print_invocation(
            prompt_file=prompt_file,
            workspace_root=workspace_root,
            claude_model=model,
            claude_effort=effort,
        )
    elif selected == CliAdapter.CURSOR:
        invocation = _cursor_print_invocation(
            prompt=prompt,
            workspace_root=workspace_root,
            cursor_model=apply_cursor_effort(model, effort),
            orchestrated=True,
        )
    elif selected == CliAdapter.CODEX:
        invocation = _codex_invocation(
            prompt=prompt,
            workspace_root=workspace_root,
            codex_model=model,
            orchestrated=True,
        )
    elif selected == CliAdapter.LMSTUDIO:
        invocation = _lmstudio_invocation(
            prompt_file=prompt_file,
            workspace_root=workspace_root,
            base_url=resolve_lmstudio_base_url(workspace),
            model=model,
            effort=effort,
            run_id=run_id,
            workspace_slug=workspace_slug,
            granted_tools=granted_tools,
        )
    elif selected == CliAdapter.OPENCODE:
        invocation = _opencode_invocation(
            prompt=prompt,
            workspace_root=workspace_root,
            opencode_model=model,
            opencode_effort=effort,
            orchestrated=True,
        )
    else:
        raise ValueError(f"Unknown CLI adapter: {selected}")

    # Stamped once here rather than in each builder: what was pinned is the same
    # question for every adapter, and the builders differ only in how they spell
    # it on the command line. `model` is the bare id — cursor's argv folds effort
    # into it, but the record keeps the two apart so both are queryable.
    return replace(invocation, model=model, effort=effort)


def _claude_triage_invocation(
    *,
    prompt_file: Path,
    workspace_root: Path,
    triage_user_prompt: str,
    model: str,
    effort: str,
    read_only: bool,
    extra_dirs: Sequence[Path | str],
    stream_json: bool,
) -> CliInvocation:
    """Claude's one-shot chat invocation — see ``build_triage_invocation``.

    Falls back to `haiku` rather than the workspace pin: a chat turn is short and
    latency-visible, and the caller has not asked for stage-grade reasoning.
    """
    triage_model = os.environ.get("LOREGARDEN_TRIAGE_CLAUDE_MODEL", "").strip() or model or "haiku"
    argv = [
        _bin("claude", "LOREGARDEN_CLAUDE_BIN"),
        "-p",
        "--output-format",
        "stream-json" if stream_json else "text",
        "--permission-mode",
        (
            "plan"
            if read_only
            else os.environ.get("LOREGARDEN_TRIAGE_PERMISSION_MODE", "bypassPermissions")
        ),
        "--append-system-prompt-file",
        str(prompt_file),
        triage_user_prompt,
    ]
    for extra in extra_dirs:
        argv.extend(["--add-dir", str(extra)])
    argv.extend(
        [
            "--append-system-prompt-file",
            str(prompt_file),
            triage_user_prompt,
        ]
    )
    if stream_json:
        # `--verbose` is required alongside stream-json for `-p`, and partial
        # messages are the whole point: without them a thinking block lands
        # finished, which is not streaming.
        argv[2:2] = ["--verbose", "--include-partial-messages"]
    _append_model_flag(argv, triage_model)
    _append_claude_effort_flag(argv, effort)
    # Chat/studio oneshot — not a pipeline stage. Keep create_ticket open.
    append_mcp_cli_args(argv, adapter="claude", orchestrated=False)
    return CliInvocation(
        argv=argv,
        use_prompt_file=True,
        adapter="claude",
        cwd=str(workspace_root),
    )


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
    run_id: str = "",
    workspace_slug: str = "",
    granted_tools: list[str] | None = None,
    read_only: bool = False,
    extra_dirs: Sequence[Path | str] = (),
    stream_json: bool = False,
) -> CliInvocation:
    """One-shot, non-interactive CLI for the triage chat channel.

    Stage runs use stream-json + the permission bridge; this returns the reply
    from stdout when the process exits.

    `stream_json` asks the CLI for NDJSON events instead of a block of text, so
    a caller reading stdout incrementally can show the agent's reasoning while
    it works. It changes what stdout looks like, not what the turn returns —
    `extract_triage_reply` reads either. Only claude and cursor can express it;
    for any other adapter the flag is silently a no-op, which is why callers
    treat streaming as a bonus rather than something to depend on. (opencode
    ignores it by streaming NDJSON either way — its text mode is decorated for a
    terminal, not for a parser.)

    ``run_id`` / ``granted_tools`` matter for LM Studio only: that runner has no
    native MCP, so the subprocess needs the control-plane endpoint + tool grant.
    Claude/Cursor CLIs configure MCP themselves.
    `extra_dirs` grants read access to directories outside the workspace root — the
    ticket studio uses it to hand the scoper a cloned reference repo. Only the
    claude adapter can express this, so callers that need it must check the
    effective adapter first rather than assume the grant took.
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
    # Workspace already carries any triage/studio runtime overrides. Stage/agent
    # pins are not a one-shot concern — same resolver, empty pin tiers.
    model = resolve_model_for_adapter(selected, workspace)
    effort = resolve_effort_for_adapter(selected, workspace)
    triage_user_prompt = user_prompt or os.environ.get(
        "LOREGARDEN_TRIAGE_USER_PROMPT", DEFAULT_TRIAGE_USER_PROMPT
    )

    if selected == CliAdapter.LOCAL:
        return _local_invocation(
            agent_id=agent_id,
            skill_name=skill_name,
            prompt_file=prompt_file,
        )

    if selected == CliAdapter.CLAUDE:
        return _claude_triage_invocation(
            prompt_file=prompt_file,
            workspace_root=workspace_root,
            triage_user_prompt=triage_user_prompt,
            model=model,
            effort=effort,
            read_only=read_only,
            extra_dirs=extra_dirs,
            stream_json=stream_json,
        )

    if selected == CliAdapter.CURSOR:
        argv = [
            _bin("cursor-agent", "LOREGARDEN_CURSOR_BIN"),
            "agent",
            "-p",
            "--output-format",
            "stream-json" if stream_json else "text",
            "--trust",
        ]
        if stream_json:
            # Cursor's token deltas ride on `--stream-partial-output`, which is
            # only legal with stream-json (see `_cursor_print_invocation`).
            argv.append("--stream-partial-output")
        if read_only:
            argv.extend(["--mode", "ask"])
        else:
            argv.append("--force")
        argv.extend(
            [
                "--workspace",
                str(workspace_root),
                f"{triage_user_prompt}\n\n{prompt}",
            ]
        )
        _append_model_flag(argv, apply_cursor_effort(model, effort))
        extra = os.environ.get("LOREGARDEN_CURSOR_AGENT_ARGS")
        if extra:
            argv[2:2] = shlex.split(extra)
        append_mcp_cli_args(argv, adapter="cursor", orchestrated=False)
        return CliInvocation(argv=argv, adapter="cursor", cwd=str(workspace_root))

    if selected == CliAdapter.CODEX:
        invocation = _codex_invocation(
            prompt=prompt,
            workspace_root=workspace_root,
            codex_model=model,
            orchestrated=False,
        )
        # Explicit either way: omiting -s leaves Codex on whatever ~/.codex says,
        # which is how advisory Home turns accidentally stayed read-only forever
        # (or writable without wanting to). Pin the policy to the caller's intent.
        sandbox = "read-only" if read_only else "workspace-write"
        invocation.argv[2:2] = ["--sandbox", sandbox]
        return invocation

    if selected == CliAdapter.LMSTUDIO:
        return _lmstudio_invocation(
            prompt_file=prompt_file,
            workspace_root=workspace_root,
            base_url=resolve_lmstudio_base_url(workspace),
            model=model,
            effort=effort,
            run_id="" if read_only else run_id,
            workspace_slug=workspace_slug,
            granted_tools=[] if read_only else granted_tools,
        )

    if selected == CliAdapter.OPENCODE:
        return _opencode_invocation(
            prompt=f"{triage_user_prompt}\n\n{prompt}",
            workspace_root=workspace_root,
            opencode_model=model,
            opencode_effort=effort,
            read_only=read_only,
        )

    raise ValueError(f"Unknown CLI adapter for triage: {selected}")
