"""How a cursor stage prompt reaches the agent: on stdin, never in argv.

`cursor-agent` takes its prompt as a positional argument and has no flag naming
a file, so the adapter put the whole rendered stage prompt in argv. On the live
path that argv is wrapped in a `sh -c` string and handed to `tmux new-session`,
and tmux refuses a command past its imsg limit — measured on a throwaway server
on 2026-10-24: 16 000 characters accepted, 20 000 answered `tmux said: command
too long`. A stage prompt is ~65 KB of boilerplate before the ticket, so EVERY
orchestrated cursor run died at spawn with `TmuxSpawnError`, the scoper never
started, and the ticket was stamped blocked with the diagnosis only in
`domain_events` (12 such runs in one afternoon across five tickets).

So the prompt travels on stdin, which both transports can supply and the CLI
reads when no positional is given: `agent_spawn` writes `stdin_prompt` to the
run's own prompt file and the wrapper redirects from it, and a pasted terminal
handoff gets the same redirect rendered into its command.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from loregarden.agents.cli_adapters import (
    DEFAULT_CURSOR_USER_PROMPT,
    cursor_resume_invocation,
    render_terminal_handoff_command,
    resolve_cli_invocation,
    resolve_terminal_handoff_invocation,
)
from loregarden.agents.executors.agent_spawn import _under_env
from loregarden.services.run_output_files import RunOutputPaths, wrap_for_files

#: The longest `tmux new-session` command accepted on this host, measured rather
#: than read off a header: 16 000 characters went through and 20 000 came back
#: `command too long`. The assertion below holds the wrapped pane command well
#: under the lower number, because the real margin is the one that matters —
#: before this fix the same command was 65 000-odd characters.
TMUX_COMMAND_LIMIT = 16_000

#: Roughly what a stage prompt runs to once the agent role, the common assets
#: and the ticket are rendered into it.
STAGE_PROMPT = "Fix the gate.\n" + ("every line of the role file and its assets. " * 1_500)

WORKSPACE = Path("/tmp/loregarden-cursor-workspace")
PROMPT_FILE = Path("/tmp/loregarden-handoff-xyz/prompt.md")


@pytest.fixture(name="cursor_paths")
def cursor_paths_fixture(tmp_path: Path) -> RunOutputPaths:
    return RunOutputPaths(
        out=tmp_path / "run.out",
        err=tmp_path / "run.err",
        rc=tmp_path / "run.rc",
        prompt=tmp_path / "run.prompt",
        env=tmp_path / "run.env",
    )


def _cursor_stage_invocation(prompt: str = STAGE_PROMPT):
    """The invocation a cursor stage run is dispatched with.

    The suite pins `LOREGARDEN_CLI_ADAPTER=local`; these assertions are about
    cursor's own shape, so resolve past the pin.
    """
    with patch("loregarden.agents.cli_adapters.resolve_effective_adapter", return_value="cursor"):
        return resolve_cli_invocation(
            agent_id="ticket_scoper",
            adapter="cursor",
            prompt=prompt,
            prompt_file=PROMPT_FILE,
            skill_name="",
            workspace_root=WORKSPACE,
            run_id="run-1",
        )


def _cursor_handoff_invocation(prompt: str = STAGE_PROMPT):
    with patch("loregarden.agents.cli_adapters.resolve_effective_adapter", return_value="cursor"):
        return resolve_terminal_handoff_invocation(
            agent_id="ticket_scoper",
            adapter="cursor",
            prompt=prompt,
            prompt_file=PROMPT_FILE,
            skill_name="",
            workspace_root=WORKSPACE,
        )


class TestDispatchedStageRun:
    def test_no_argv_element_carries_the_prompt(self):
        invocation = _cursor_stage_invocation()
        assert not [element for element in invocation.argv if STAGE_PROMPT[:40] in element]

    def test_the_prompt_travels_on_stdin_behind_its_user_preamble(self):
        invocation = _cursor_stage_invocation()
        assert invocation.stdin_prompt == f"{DEFAULT_CURSOR_USER_PROMPT}{STAGE_PROMPT}"

    def test_the_user_preamble_is_still_overridable(self, monkeypatch):
        monkeypatch.setenv("LOREGARDEN_CURSOR_USER_PROMPT", "Do the stage. ")
        invocation = _cursor_stage_invocation()
        assert invocation.stdin_prompt == f"Do the stage. {STAGE_PROMPT}"

    def test_the_wrapper_reads_the_prompt_file_it_was_written_to(
        self, cursor_paths: RunOutputPaths
    ):
        """What `spawn_agent` builds: `stdin_prompt` set means a `<` redirect."""
        invocation = _cursor_stage_invocation()
        wrapped = wrap_for_files(
            invocation.argv,
            cursor_paths,
            stdin_from_prompt=bool(invocation.stdin_prompt),
            cwd=invocation.cwd,
        )
        assert f"< {cursor_paths.prompt}" in wrapped[-1]

    def test_the_tmux_pane_command_stays_under_the_limit(self, cursor_paths: RunOutputPaths):
        """The regression itself: this was ~65 000 characters and tmux refused it."""
        invocation = _cursor_stage_invocation()
        wrapped = wrap_for_files(
            invocation.argv,
            cursor_paths,
            stdin_from_prompt=bool(invocation.stdin_prompt),
            cwd=invocation.cwd,
        )
        pane_command = " ".join(_under_env(cursor_paths, wrapped))
        assert len(pane_command) < TMUX_COMMAND_LIMIT


class TestResumedTurn:
    def test_the_follow_up_replaces_the_stdin_prompt(self):
        invocation = _cursor_stage_invocation()
        resumed = cursor_resume_invocation(invocation, session_id="sess-7", prompt="Report now.")
        assert resumed.stdin_prompt == "Report now."

    def test_the_resumed_argv_names_the_session_and_carries_no_prompt(self):
        invocation = _cursor_stage_invocation()
        resumed = cursor_resume_invocation(invocation, session_id="sess-7", prompt="Report now.")
        assert resumed.argv[resumed.argv.index("--resume") + 1] == "sess-7"
        assert resumed.argv.index("--resume") == resumed.argv.index("-p") + 1
        assert "Report now." not in resumed.argv
        assert not [element for element in resumed.argv if STAGE_PROMPT[:40] in element]

    def test_the_original_invocation_is_left_alone(self):
        invocation = _cursor_stage_invocation()
        before = list(invocation.argv)
        cursor_resume_invocation(invocation, session_id="sess-7", prompt="Report now.")
        assert invocation.argv == before
        assert invocation.stdin_prompt == f"{DEFAULT_CURSOR_USER_PROMPT}{STAGE_PROMPT}"


class TestTerminalHandoff:
    def test_the_pasted_command_reads_the_prompt_from_its_file(self):
        invocation = _cursor_handoff_invocation()
        command = render_terminal_handoff_command(invocation)
        assert command.endswith(f"< {PROMPT_FILE}")

    def test_the_redirect_feeds_the_cli_and_not_the_cleanup(self):
        """`rm -rf` and the exit ping follow under `;`; a redirect after them
        would feed the prompt to `rm` and leave cursor waiting on a tty."""
        invocation = _cursor_handoff_invocation()
        command = render_terminal_handoff_command(
            invocation, cleanup_path=PROMPT_FILE.parent, run_id="run-1"
        )
        redirect = command.index(f"< {PROMPT_FILE}")
        assert redirect < command.index("rm -rf")
        assert redirect < command.index("handoff-exited")
        assert command.index("handoff-checkin") < redirect

    def test_the_file_the_caller_writes_holds_what_reaches_the_agent(self):
        """`prepare_terminal_handoff` writes `stdin_prompt` to `prompt_file`, so
        the preamble the live path sends on stdin is in the pasted turn too."""
        invocation = _cursor_handoff_invocation()
        assert invocation.use_prompt_file is True
        assert invocation.stdin_file == str(PROMPT_FILE)
        assert invocation.stdin_prompt == f"{DEFAULT_CURSOR_USER_PROMPT}{STAGE_PROMPT}"

    def test_the_command_a_human_pastes_stays_short(self):
        invocation = _cursor_handoff_invocation()
        command = render_terminal_handoff_command(invocation)
        assert len(command) < TMUX_COMMAND_LIMIT
