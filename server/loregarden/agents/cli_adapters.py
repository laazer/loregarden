import os
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CliInvocation:
    argv: list[str]
    stdin_prompt: str | None = None
    use_prompt_file: bool = False


def resolve_cli_invocation(
    *,
    agent_id: str,
    adapter: str,
    prompt: str,
    prompt_file: Path,
    skill_name: str,
) -> CliInvocation:
    """Resolve subprocess argv. Agents are adapters — no orchestration logic here."""
    env_key = f"LOREGARDEN_AGENT_{agent_id.upper()}_CMD"
    override = os.environ.get(env_key)
    if override:
        argv = shlex.split(
            override.format(
                prompt_file=str(prompt_file),
                prompt=prompt,
                agent_id=agent_id,
                skill=skill_name,
            )
        )
        return CliInvocation(argv=argv, stdin_prompt=None)

    selected = os.environ.get("LOREGARDEN_CLI_ADAPTER", adapter)

    if selected == "local":
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
            ]
        )

    if selected == "claude":
        return CliInvocation(argv=["claude", "-p", prompt], stdin_prompt=None)

    if selected == "cursor":
        return CliInvocation(
            argv=["cursor", "agent", "--print", prompt],
            stdin_prompt=None,
        )

    if selected == "codex":
        return CliInvocation(
            argv=["codex", "exec", "-"],
            stdin_prompt=prompt,
        )

    raise ValueError(f"Unknown CLI adapter: {selected}")
