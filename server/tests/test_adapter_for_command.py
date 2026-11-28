"""`cli_settings.adapter_for_command` — which CLI a recorded run was executed by (S1b).

`AgentRun.command` is the only place the adapter can be read from: no column records it. The Hive
map needs it to tell "this adapter writes no tool-call lines" from "this run has made no call yet",
which are the two readings S7 refuses to collapse.

Every shape below was counted on the live database read-only on 2026-11-26, over all 6,211
`agent_runs` rows. The acceptance criterion names four shapes; there are nine, and two of the four
it names do not occur in the data under the spelling it gives them (`[external-harness] claude …`
and `[external-harness] cursor-agent …` are really `[external-harness] claude_code` and
`[external-harness] cursor`, because `external_harness.py:265` writes the `ExternalHarness` enum
value, not a binary). The spec gap is recorded in the test-design artifact; these tests are over
what the database actually holds.
"""

from __future__ import annotations

import pytest
from loregarden.models.domain.enums import CliAdapter
from loregarden.services.cli_settings import ADAPTER_BINARIES, adapter_for_command
from loregarden.services.tool_call_line import CALL_LINE_ADAPTERS

CLAUDE = (
    "/Users/jacobbrandt/.local/bin/claude -p --verbose --output-format stream-json --model opus"
)
CURSOR = "/Users/jacobbrandt/.local/bin/cursor-agent agent -p --trust --force"
CODEX = "/Users/jacobbrandt/.local/bin/codex exec --json --sandbox danger-full-access"
OPENCODE = "/Users/jacobbrandt/.opencode/bin/opencode run --format json --dir /Users/x"
HARNESS_CLAUDE = "[external-harness] claude_code"
HARNESS_CURSOR = "[external-harness] cursor"
HANDOFF = "[terminal-handoff] /Users/jacobbrandt/.local/bin/claude --add-dir /Users/x"
PYTHON = "/Users/jacobbrandt/workspace/loregarden/server/.venv/bin/python3 -m loregarden.x"


@pytest.mark.parametrize(
    ("command", "adapter", "live_rows"),
    [
        # Every command shape in `agent_runs`, with the row count it stands for.
        pytest.param(CLAUDE, "claude", 3518, id="absolute-claude-binary"),
        pytest.param(CURSOR, "cursor", 1265, id="absolute-cursor-agent-binary"),
        pytest.param("", "", 790, id="empty-command"),
        pytest.param(HARNESS_CLAUDE, "", 367, id="external-harness-claude_code"),
        pytest.param(CODEX, "codex", 207, id="absolute-codex-binary"),
        pytest.param(HARNESS_CURSOR, "cursor", 44, id="external-harness-cursor"),
        pytest.param(HANDOFF, "", 10, id="terminal-handoff-prefix"),
        pytest.param(PYTHON, "", 8, id="python-entrypoint"),
        pytest.param(OPENCODE, "opencode", 1, id="absolute-opencode-binary"),
    ],
)
def test_every_command_shape_the_database_holds_resolves(command, adapter, live_rows):
    """S1-AC2, over the nine real shapes rather than the four the criterion names.

    `[external-harness] claude_code` resolves to `""` on purpose, and that is the whole reason this
    matters: `claude_code` is an `ExternalHarness` value, not a binary or an adapter id, so nothing
    in `ADAPTER_BINARIES` claims it. Those 367 runs are executed by a harness outside this process,
    which streams nothing into `run_log_lines` — 0 of the 367 carry a single `call ·` line — so an
    unresolvable adapter is the honest answer, and S7 then renders them `activity unknown`. Mapping
    the enum value through to `claude` would make every one of them read `no calls yet`, which is
    the idleness reading S7 exists to prevent.
    """
    assert live_rows > 0  # the shape is not hypothetical
    assert adapter_for_command(command) == adapter


@pytest.mark.parametrize("adapter", sorted(ADAPTER_BINARIES))
def test_both_an_adapter_id_and_its_binary_name_resolve_to_the_adapter(adapter):
    """`cursor`'s binary is `cursor-agent`; `codex`'s is `codex`. A table lookup that checked only
    one side would silently lose one of the two live shapes, so both are required."""
    binary, _env = ADAPTER_BINARIES[adapter]
    assert adapter_for_command(f"/opt/bin/{binary} --flag") == adapter
    assert adapter_for_command(f"{adapter} --flag") == adapter


@pytest.mark.parametrize(
    "command",
    [
        "   ",
        "\n",
        "ls -la",
        "claudette --not-claude",
        "my-claude-wrapper run",
        "/opt/bin/claude.backup",
        "[external-harness] other",
        "[external-harness]",
        "--model opus",
    ],
    ids=repr,
)
def test_an_unresolvable_command_is_the_empty_string_and_never_a_guess(command):
    """ "" means "no adapter was recorded", a reading S7 renders differently from every other.

    `claudette` and `my-claude-wrapper` are here because a substring test (`"claude" in command`)
    passes every assertion above and claims them too; `claude.backup` because a suffix-tolerant
    basename would.
    """
    assert adapter_for_command(command) == ""


def test_the_adapter_is_read_from_the_first_word_not_from_anywhere_in_the_command():
    """A Claude command naming cursor in its own arguments is still a Claude run. Every live
    `claude` command carries `--mcp-config …` JSON and an `--append-system-prompt-file` path, so
    the argument tail is long, attacker-free but adversarial by accident."""
    assert adapter_for_command(f"{CLAUDE} --add-dir /Users/x/cursor-agent/codex") == "claude"
    assert adapter_for_command(f"{CURSOR} --resume 'migrate off claude'") == "cursor"


def test_call_lines_are_expected_of_claude_alone():
    """S1b's `call_lines_expected` is `adapter in CALL_LINE_ADAPTERS`, and nothing else.

    Asserted on the enum members so a new adapter that starts writing call lines has to be added
    deliberately, and on the resolved strings so the two vocabularies cannot drift apart.
    """
    expected = {a.value for a in CALL_LINE_ADAPTERS}
    assert expected == {"claude"}
    assert adapter_for_command(CLAUDE) in expected
    for command in (CURSOR, CODEX, OPENCODE, HARNESS_CLAUDE, HARNESS_CURSOR, "", HANDOFF, PYTHON):
        assert adapter_for_command(command) not in expected
    assert CliAdapter.DEFAULT not in CALL_LINE_ADAPTERS
    assert CliAdapter.LOCAL not in CALL_LINE_ADAPTERS
