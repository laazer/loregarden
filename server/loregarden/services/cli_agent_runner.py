"""Run a single CLI agent turn and return its reply.

Four surfaces drive a one-shot CLI agent this way — triage chat, branch triage chat, ticket
studio, and studio agent generation. Each used to carry its own copy of the same forty lines:
write the prompt to a temp file, build the invocation, Popen it, feed stdin, wait with a
timeout, unpack a non-zero exit, and pull the reply out of stdout. Only the agent id, some
labels, two env var names and a reply cap ever differed, so those are the profile and the rest
lives here.

Not for the approval-gated runs: those go through PermissionBridgeRunner, which is a genuinely
different mechanism (tool interception, mid-run approvals) rather than another copy of this one.

A turn here can still stream. Pass a ``thinking_sink`` and the CLI is asked for NDJSON, stdout is
read as it arrives, and the agent's reasoning reaches the operator's chat panel while the turn
runs — the reply itself is still returned when the process exits. That is the whole difference
between the two paths now: approvals, not visibility.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from loregarden.agents.cli_adapters import build_triage_invocation
from loregarden.agents.registry import get_agent
from loregarden.config import settings
from loregarden.models.domain import Workspace
from loregarden.services.cli_output import extract_triage_reply
from loregarden.services.run_stream_sink import RunStreamSink
from loregarden.services.subprocess_lines import SubprocessLineReader
from loregarden.services.workspace_paths import resolve_workspace_root

MIN_AGENT_TIMEOUT_SECONDS = 30

#: How long to block on stdout before checking the clock again. Short enough
#: that a turn cancelled by its timeout dies promptly, long enough that a
#: thinking agent is not polled hundreds of times a second.
STREAM_READ_TIMEOUT_SECONDS = 0.5


@dataclass(frozen=True)
class CliAgentProfile:
    """The per-surface facts about driving one CLI agent."""

    agent_id: str
    assistant_label: str
    """Operator-facing name, used when the turn times out or comes back empty."""
    cli_label: str
    """Process-facing name, used when the CLI itself exits non-zero."""
    stub_env: str
    timeout_env: str
    tmp_prefix: str
    reply_cap: int


def stub_response(profile: CliAgentProfile) -> str | None:
    """The canned reply for tests, or None to actually run the agent.

    Callers check this before building a prompt so a stubbed turn touches neither the
    workspace nor the model.
    """
    return os.environ.get(profile.stub_env)


def resolve_agent_timeout(agent: dict, env_var: str) -> int:
    env = os.environ.get(env_var)
    if env:
        return max(MIN_AGENT_TIMEOUT_SECONDS, int(env))
    return int(agent.get("timeout", settings.triage_timeout_seconds))


def _drain_to_sink(
    proc: subprocess.Popen,
    sink: RunStreamSink,
    *,
    timeout: int,
    assistant_label: str,
) -> str:
    """Read stdout line by line into `sink`, returning the whole of it.

    The buffered `communicate()` this replaces is still the right call when
    nobody is watching; it is only wrong when someone is, because it hands over
    everything the agent said at the moment it stops saying it.

    stderr is left to `communicate()` afterwards. It is small, it is not read
    until the process has exited, and the pipe holds far more than a CLI's
    stderr ever fills — so there is no deadlock to design around here.
    """
    assert proc.stdout is not None
    reader = SubprocessLineReader(proc.stdout)
    lines: list[str] = []
    deadline = time.time() + timeout
    last_touch = time.time()

    while True:
        if time.time() >= deadline:
            proc.kill()
            raise TimeoutError(f"{assistant_label} timed out after {timeout}s")
        line = reader.readline(timeout=STREAM_READ_TIMEOUT_SECONDS)
        if line is None:
            if proc.poll() is not None:
                break
            # Nothing arrived; keep whatever is buffered in the sink from
            # sitting unwritten through a long silence.
            if time.time() - last_touch >= 2.0:
                sink.touch()
                last_touch = time.time()
            continue
        lines.append(line)
        sink.append_stream_line(line)
        last_touch = time.time()

    # The process is gone but its pipe may still hold buffered output.
    while True:
        line = reader.readline(timeout=0.1)
        if line is None:
            break
        lines.append(line)
        sink.append_stream_line(line)

    return "".join(lines)


def run_cli_agent_turn(
    profile: CliAgentProfile,
    *,
    workspace: Workspace,
    prompt: str,
    reply_cap: int | None = None,
    user_prompt: str | None = None,
    run_id: str = "",
    workspace_slug: str = "",
    granted_tools: list[str] | None = None,
    read_only: bool = False,
    extra_dirs: Sequence[Path | str] = (),
    thinking_sink: RunStreamSink | None = None,
) -> str:
    """Run one turn to completion and return the assistant's reply.

    `workspace` should already carry any runtime overrides for this surface. `reply_cap`
    overrides the profile's cap for a turn whose output is legitimately larger.

    ``run_id`` / ``granted_tools`` are forwarded for LM Studio so the runner can
    speak MCP; Claude/Cursor ignore them (they configure MCP themselves).
    `extra_dirs` grants read access to directories outside the workspace repo.

    `thinking_sink` makes the turn visible while it runs: the CLI is asked for
    NDJSON and stdout is read as it arrives rather than in one block at the end.
    Only claude and cursor can produce those events — for any other adapter the
    turn runs exactly as before and the sink simply sees nothing, which is why
    it is safe to pass one unconditionally.
    """
    repo_root = resolve_workspace_root(workspace)
    if not repo_root.is_dir():
        raise ValueError(f"Workspace repo path does not exist: {repo_root}")

    agent = get_agent(profile.agent_id)
    if not agent:
        raise ValueError(f"Unknown {profile.cli_label.lower()} agent: {profile.agent_id}")

    timeout = resolve_agent_timeout(agent, profile.timeout_env)
    tools = granted_tools
    if tools is None and run_id:
        tools = list(agent.get("mcp_tools") or [])

    with tempfile.TemporaryDirectory(prefix=profile.tmp_prefix) as tmp:
        prompt_file = Path(tmp) / "prompt.md"
        prompt_file.write_text(prompt, encoding="utf-8")
        invocation = build_triage_invocation(
            agent_id=profile.agent_id,
            adapter=agent.get("adapter", "claude"),
            prompt=prompt,
            prompt_file=prompt_file,
            skill_name="",
            workspace_root=repo_root,
            workspace=workspace,
            user_prompt=user_prompt,
            run_id=run_id,
            workspace_slug=workspace_slug or workspace.slug or "",
            granted_tools=tools,
            read_only=read_only,
            extra_dirs=extra_dirs,
            stream_json=thinking_sink is not None,
        )
        proc = subprocess.Popen(
            invocation.argv,
            cwd=invocation.cwd or str(repo_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE if invocation.stdin_prompt else None,
            bufsize=0,
        )
        if invocation.stdin_prompt and proc.stdin:
            proc.stdin.write(invocation.stdin_prompt.encode("utf-8"))
            proc.stdin.close()
            # CPython's ``communicate()`` flushes stdin even after we closed it,
            # which raises ``ValueError: I/O operation on closed file`` (Codex
            # feeds the prompt on stdin). Drop the handle so a later communicate
            # — or any flush — does not touch the closed pipe.
            proc.stdin = None
        if thinking_sink is not None:
            stdout_text = _drain_to_sink(
                proc,
                thinking_sink,
                timeout=timeout,
                assistant_label=profile.assistant_label,
            )
            # Process already exited and stdout is drained; only stderr remains.
            # Do not call ``communicate()`` — same closed-stdin trap, and it would
            # also try to re-read stdout. Match the stage print-mode runner.
            stderr_text = (
                proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
            )
        else:
            try:
                stdout, stderr = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                raise TimeoutError(
                    f"{profile.assistant_label} timed out after {timeout}s"
                ) from None
            stdout_text = stdout.decode("utf-8", errors="replace")
            stderr_text = stderr.decode("utf-8", errors="replace")

        if proc.returncode != 0:
            detail = stderr_text.strip() or stdout_text.strip()
            raise RuntimeError(
                detail or f"{profile.cli_label} CLI exited with code {proc.returncode}"
            )

        # Reads plain text and NDJSON alike, so the reply does not depend on
        # whether anyone happened to be watching this turn.
        reply = extract_triage_reply(stdout_text)
        if not reply:
            raise RuntimeError(f"{profile.assistant_label} returned an empty response")
        return reply[: reply_cap if reply_cap is not None else profile.reply_cap]
