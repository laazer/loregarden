"""Run a single CLI agent turn and return its reply.

Four surfaces drive a one-shot CLI agent this way — triage chat, branch triage chat, ticket
studio, and studio agent generation. Each used to carry its own copy of the same forty lines:
write the prompt to a temp file, build the invocation, Popen it, feed stdin, wait with a
timeout, unpack a non-zero exit, and pull the reply out of stdout. Only the agent id, some
labels, two env var names and a reply cap ever differed, so those are the profile and the rest
lives here.

Not for the streaming, approval-gated runs: those go through PermissionBridgeRunner, which is
a genuinely different mechanism (stream-json, tool interception, mid-run approvals) rather than
another copy of this one.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from loregarden.agents.cli_adapters import build_triage_invocation
from loregarden.agents.registry import get_agent
from loregarden.config import settings
from loregarden.models.domain import Workspace
from loregarden.services.cli_output import extract_triage_reply
from loregarden.services.workspace_paths import resolve_workspace_root

MIN_AGENT_TIMEOUT_SECONDS = 30


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


def run_cli_agent_turn(
    profile: CliAgentProfile,
    *,
    workspace: Workspace,
    prompt: str,
    reply_cap: int | None = None,
    user_prompt: str | None = None,
) -> str:
    """Run one turn to completion and return the assistant's reply.

    `workspace` should already carry any runtime overrides for this surface. `reply_cap`
    overrides the profile's cap for a turn whose output is legitimately larger.
    """
    repo_root = resolve_workspace_root(workspace)
    if not repo_root.is_dir():
        raise ValueError(f"Workspace repo path does not exist: {repo_root}")

    agent = get_agent(profile.agent_id)
    if not agent:
        raise ValueError(f"Unknown {profile.cli_label.lower()} agent: {profile.agent_id}")

    timeout = resolve_agent_timeout(agent, profile.timeout_env)

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
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise TimeoutError(f"{profile.assistant_label} timed out after {timeout}s") from None

        if proc.returncode != 0:
            detail = (
                stderr.decode("utf-8", errors="replace").strip()
                or stdout.decode("utf-8", errors="replace").strip()
            )
            raise RuntimeError(
                detail or f"{profile.cli_label} CLI exited with code {proc.returncode}"
            )

        reply = extract_triage_reply(stdout.decode("utf-8", errors="replace"))
        if not reply:
            raise RuntimeError(f"{profile.assistant_label} returned an empty response")
        return reply[: reply_cap if reply_cap is not None else profile.reply_cap]
