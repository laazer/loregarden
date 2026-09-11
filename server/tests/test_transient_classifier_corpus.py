"""`is_transient_failure` against every failure this installation has produced.

The classifier is a substring list, and a substring list drifts silently: it
keeps returning an answer after the thing it matches stops being written. It has
drifted at least four times, and each was found the same way — by looking at real
output rather than at the code:

1. The Claude CLI moved to "You've hit your session limit · resets 3pm", which
   contains none of the words the quota signatures were written for.
2. It had never heard of `INTERRUPTED_RUN_MESSAGE` — the control plane stopping
   its own agent, and the single largest cause of failed runs here at 82 of 179.
3. Nor of the five chat surfaces' own restart wording, which is why they are
   matched by `RESTART_INTERRUPTION_MARKER` and built from one helper now.
4. It answered "transient" for any output containing both "terminal_reason" and
   "api_error" anywhere — satisfied by an agent writing *about* quota handling,
   including the agent that edited this module.

So the corpus below is the distinct `stderr` values from `agent_runs`, taken from
the live database on 2026-09-11 (179 failed runs, ~25 distinct shapes), paths and
ids abbreviated but wording preserved exactly. Each is pinned with the verdict it
*should* get and the reason why, because the interesting half of this classifier
is what it must answer NO to: every entry in the second list is a real defect —
in the code, the config, the routing, or a human's decision — that a retry would
paper over and a reroute would hand to the wrong participant.

When a CLI changes its wording, this file is where it should fail. Add the new
string here from the database rather than inventing one; a signature invented to
match a signature is a tautology, which is how three of the four drifts survived.
"""

import pytest
from loregarden.services.stage_report import is_transient_failure, structured_terminal_reason

# --------------------------------------------------------------------------- #
# Transient: the run never reached an opinion about the work                    #
# --------------------------------------------------------------------------- #

TRANSIENT = [
    pytest.param(
        "Agent run interrupted before completion (server reload or worker stopped). "
        "Re-run the stage to continue.",
        id="server-reload",
    ),
    pytest.param(
        "Stage was left running with no agent run behind it (the run ended before its "
        "stage was settled). Re-run the stage to continue.",
        id="stranded-stage",
    ),
    pytest.param(
        "Agent run lease expired: nothing has renewed this run, so the thread that was "
        "supervising it is gone. Failed by the reconciliation sweep rather than by a restart.",
        id="lease-expired",
    ),
    pytest.param(
        "Parent orchestration is already terminal; this run was left in flight.",
        id="orphan-of-terminal-parent",
    ),
    pytest.param(
        "Baxter was interrupted by a server restart and did not finish this turn. "
        "Send the message again.",
        id="chat-surface-restart",
    ),
    pytest.param(
        "The scoper was interrupted by a server restart and did not finish this turn. "
        "Run it again.",
        id="scoper-restart",
    ),
    pytest.param(
        "Error: Cursor couldn't find your saved login in the macOS keychain.\n"
        "Log out and sign back in to refresh your saved login: run `agent logout`, "
        "then start agent again.",
        id="cursor-lost-login",
    ),
    pytest.param(
        "Error: The macOS keychain item already exists "
        "(errSecDuplicateItem, security exit code 45).",
        id="keychain-contention",
    ),
]


@pytest.mark.parametrize("stderr", TRANSIENT)
def test_infrastructure_failures_are_transient(stderr):
    assert is_transient_failure("", stderr) is True


# --------------------------------------------------------------------------- #
# Not transient: every one of these is a real defect or a real decision         #
# --------------------------------------------------------------------------- #

NOT_TRANSIENT = [
    pytest.param(
        "Agent timed out after 600s",
        "A budget question, not a blip. Measured per-stage p95s run to 2559s for "
        "`implement`, so a timeout is usually the ceiling being wrong — see "
        "STAGE_TIMEOUT_BUDGETS. Retrying a hang burns the budget again and a "
        "reroute blames the agent for the clock.",
        id="timeout",
    ),
    pytest.param(
        "Backend Implementer Agent (backend_implementer) is scoped to server/** and "
        "cannot use Edit on '/…/client/src/lib/hive/world.ts'",
        "The scope-denial reroute owns this: it hands the stage to the sibling "
        "implementer via the `scope_reroute_agent` pin. Calling it transient would "
        "re-run the same wrongly-scoped agent instead.",
        id="scope-denial",
    ),
    pytest.param(
        "Failed to checkout branch: Command '['git', 'checkout', '-B', "
        "'loregarden/88-gate-outcomes-…']' returned non-zero exit status 1.",
        "Deterministic. The branch state that refused this checkout will refuse the "
        "next one identically.",
        id="git-checkout-refused",
    ),
    pytest.param(
        "'blocked' is not among the defined enum values. Enum name: ticketstate. "
        "Possible values: BACKLOG, IN_PROGRESS, BLOCKED, …, WONT_DO",
        "A bug in this control plane. Five retries produce five identical failures "
        "and hide the defect behind an eventual block.",
        id="enum-bug",
    ),
    pytest.param(
        "Error: Invalid MCP configuration:\nmcpServers: Invalid input: expected record, "
        "received undefined",
        "Configuration. It cannot fix itself between attempts.",
        id="bad-mcp-config",
    ),
    pytest.param(
        "Error: No prompt provided for print mode",
        "A dispatch defect — the run was built wrong before the CLI started.",
        id="empty-prompt",
    ),
    pytest.param(
        "Permission denied via Loregarden inbox",
        "A person said no. Retrying is overruling them.",
        id="human-denial",
    ),
    pytest.param(
        "Unknown agent: ",
        "A routing defect, and the empty name is part of the evidence.",
        id="unknown-agent",
    ),
    pytest.param(
        "Agent run failed",
        "The bare fallback: no information at all. Treating no evidence as evidence "
        "of a blip is how a real crash becomes five real crashes.",
        id="no-information",
    ),
    pytest.param(
        "Traceback (most recent call last):\n  AssertionError: expected 2, got 3",
        "The work failing, which is the rework loop's business.",
        id="assertion-error",
    ),
]


@pytest.mark.parametrize("stderr,why", NOT_TRANSIENT)
def test_real_defects_are_not_transient(stderr, why):
    assert is_transient_failure("", stderr) is False, why


# --------------------------------------------------------------------------- #
# The structured reason outranks the transcript                                 #
# --------------------------------------------------------------------------- #

RESULT_LINE = (
    '{"type":"result","subtype":"error","terminal_reason":"api_error",'
    '"usage":{"input_tokens":10},"uuid":"x"}'
)


def test_the_cli_result_envelope_is_read_as_a_field():
    assert structured_terminal_reason(RESULT_LINE) == "api_error"
    assert is_transient_failure(RESULT_LINE, "") is True


def test_an_agent_writing_about_api_errors_is_not_an_api_error():
    """The drift that mattered most, because it fires on this repository's own
    work: prose naming both `terminal_reason` and `api_error` used to satisfy the
    whole-blob substring test."""
    prose = (
        "I looked at how terminal_reason is classified. An api_error is transient, "
        "so `is_transient_failure` should return True for it."
    )
    assert structured_terminal_reason(prose) is None
    assert is_transient_failure(prose, "") is False


def test_the_last_envelope_wins_over_earlier_ones():
    """A long run streams many events; the result line is written last."""
    stream = (
        '{"type":"system","terminal_reason":"api_error"}\n'
        '{"type":"result","subtype":"success","terminal_reason":"completed"}\n'
    )
    assert structured_terminal_reason(stream) == "completed"
    assert is_transient_failure(stream, "") is False


def test_plain_text_interleaved_with_the_stream_is_not_a_verdict():
    """The CLIs mix prose into the stream, and a parse failure is not an answer."""
    stream = 'Thinking...\n{"type":"result" BROKEN JSON "terminal_reason":"api_error"\n'
    assert structured_terminal_reason(stream) is None


def test_a_structured_non_transient_reason_stops_the_substring_fallback():
    """When the CLI names its reason, that answer is final: an agent transcript
    mentioning a keychain must not override the harness's own verdict."""
    stream = (
        "Checked whether the macOS keychain was the problem; it was not.\n"
        '{"type":"result","subtype":"success","terminal_reason":"completed"}\n'
    )
    assert is_transient_failure(stream, "") is False


def test_a_usage_limit_with_no_structured_reason_still_lands():
    """The wording that started all of this, and it carries no terminal_reason."""
    stdout = (
        '{"type":"thread.started","thread_id":"01a0"}\n'
        '{"type":"turn.started"}\n'
        '{"type":"error","message":"You\'ve hit your usage limit. Upgrade to Pro '
        'or try again at Aug 12th, 2026 10:07 AM."}\n'
    )
    assert structured_terminal_reason(stdout) is None
    assert is_transient_failure(stdout, "") is True
