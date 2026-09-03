"""Shrinking a conversation without losing the task that started it.

The LM Studio runner grows one `messages` list across its tool-call loop with no
bound: every round appends an assistant turn plus a tool result of up to 8,000
characters. `MAX_TOOL_ROUNDS` bounds the rounds, not what they accumulate
(lg-workflow-integrity-380).

Sibling 163 handles the same axis by discarding the conversation and starting
fresh. This is the graduated version: keep the head, keep a recent tail,
summarise the middle.
"""

from __future__ import annotations

import httpx
import pytest
from loregarden.agents.condenser import (
    DEFAULT_HEAD,
    DEFAULT_TAIL,
    CondensationResult,
    NoOpCondenser,
    SummarisingCondenser,
)


def _conversation(n: int, *, chars: int = 100) -> list[dict]:
    """A task statement followed by n-1 turns of filler."""
    head = {"role": "user", "content": "TASK: implement the widget and report"}
    return [head] + [
        {"role": "assistant" if i % 2 else "tool", "content": f"turn {i} " + "x" * chars}
        for i in range(1, n)
    ]


# --- the inert default --------------------------------------------------------


def test_the_default_condenser_does_nothing():
    """AC3. Every runner opts in explicitly, and the inert case has to be
    provably inert rather than a None check scattered through the loop."""
    messages = _conversation(50, chars=10_000)
    condenser = NoOpCondenser()

    assert condenser.should_condense(messages) is False
    result = condenser.condense(messages)
    assert result.messages == messages
    assert result.condensed is False
    assert result.dropped == 0


# --- head and tail ------------------------------------------------------------


def test_the_task_statement_survives_condensation():
    """The whole point. Losing the first message is how a run forgets what it
    was asked to do while still sounding busy."""
    messages = _conversation(40, chars=5_000)
    condenser = SummarisingCondenser(summarise=lambda m: "they did some work", max_chars=1_000)

    result = condenser.condense(messages)

    assert result.messages[0]["content"].startswith("TASK:")
    assert "implement the widget" in result.messages[0]["content"]


def test_the_recent_tail_is_kept_verbatim():
    """An assistant turn and its tool results travel together; a short tail can
    split a tool_call from its result."""
    messages = _conversation(40, chars=5_000)
    condenser = SummarisingCondenser(summarise=lambda m: "summary", max_chars=1_000)

    result = condenser.condense(messages)

    assert result.messages[-DEFAULT_TAIL:] == messages[-DEFAULT_TAIL:]


def test_the_middle_is_replaced_by_the_summary():
    """AC1."""
    messages = _conversation(40, chars=5_000)
    condenser = SummarisingCondenser(
        summarise=lambda m: "the agent explored three approaches", max_chars=1_000
    )

    result = condenser.condense(messages)

    assert result.dropped == 40 - DEFAULT_HEAD - DEFAULT_TAIL
    assert result.degraded is False
    body = " ".join(str(m["content"]) for m in result.messages)
    assert "three approaches" in body
    assert result.condensed_chars < result.original_chars


# --- triggers -----------------------------------------------------------------


def test_a_small_conversation_does_not_trigger():
    """AC2's threshold half."""
    condenser = SummarisingCondenser(summarise=lambda m: "x", max_chars=100_000)
    assert condenser.should_condense(_conversation(10, chars=100)) is False


def test_a_large_conversation_triggers():
    condenser = SummarisingCondenser(summarise=lambda m: "x", max_chars=1_000)
    assert condenser.should_condense(_conversation(40, chars=5_000)) is True


def test_an_already_minimal_conversation_never_triggers():
    """Head + tail is as small as this strategy goes. Returning True for it
    would spin the caller through a condensation that removes nothing every
    round."""
    condenser = SummarisingCondenser(summarise=lambda m: "x", max_chars=1)
    minimal = _conversation(DEFAULT_HEAD + DEFAULT_TAIL, chars=50_000)

    assert condenser.should_condense(minimal) is False
    # And condensing it anyway — as an overflow handler would — is a no-op
    # rather than an error, because that caller has nothing better to fall to.
    assert condenser.condense(minimal).messages == minimal


# --- degradation --------------------------------------------------------------


def test_a_failing_summariser_drops_the_middle_rather_than_the_run():
    """AC4. A summariser calls a model, so it is a cost and a failure point of
    its own. Failing the parent run over a compression step is not survivable;
    losing the middle is."""

    def boom(_middle):
        raise TimeoutError("model did not respond in time")

    messages = _conversation(40, chars=5_000)
    result = SummarisingCondenser(summarise=boom, max_chars=1_000).condense(messages)

    assert result.degraded is True
    assert result.dropped == 40 - DEFAULT_HEAD - DEFAULT_TAIL
    assert result.messages[0]["content"].startswith("TASK:")
    marker = " ".join(str(m["content"]) for m in result.messages)
    assert "dropped" in marker
    assert "model did not respond in time" in marker


def test_an_empty_summary_also_degrades():
    messages = _conversation(40, chars=5_000)
    result = SummarisingCondenser(summarise=lambda m: "   ", max_chars=1_000).condense(messages)
    assert result.degraded is True


def test_no_summariser_at_all_still_bounds_the_conversation():
    """The strategy must work before anyone wires a model to it."""
    messages = _conversation(40, chars=5_000)
    result = SummarisingCondenser(max_chars=1_000).condense(messages)

    assert result.degraded is True
    assert result.condensed_chars < result.original_chars


def test_the_model_is_told_something_is_missing():
    """A silent gap is worse than a marker: the model would treat head and tail
    as adjacent and reason about a conversation that never happened."""
    messages = _conversation(40, chars=5_000)
    result = SummarisingCondenser(max_chars=1_000).condense(messages)

    assert any("removed without a summary" in str(m["content"]) for m in result.messages)


# --- what it hands back -------------------------------------------------------


def test_the_result_carries_what_a_recorder_would_need():
    """`lg-agent-prompt-379` owns condensation-as-an-event. This module records
    nothing durable on purpose; it returns the facts so 379 can."""
    messages = _conversation(40, chars=5_000)
    condenser = SummarisingCondenser(summarise=lambda m: "summary text", max_chars=1_000)

    result = condenser.condense(messages)

    assert isinstance(result, CondensationResult)
    assert result.summary
    assert result.dropped > 0
    assert result.original_chars > result.condensed_chars
    assert condenser.events == [result]


# --- the provider overflow signal ---------------------------------------------


def test_a_context_overflow_response_is_recognised():
    """AC2's other half, and the one that matters: this turns a hard 400 into a
    recoverable round."""
    from loregarden.agents.executors.lmstudio_runner import _is_context_overflow

    def error(status: int, body: str) -> httpx.HTTPStatusError:
        request = httpx.Request("POST", "http://localhost/v1/chat/completions")
        response = httpx.Response(status, text=body, request=request)
        return httpx.HTTPStatusError("boom", request=request, response=response)

    assert _is_context_overflow(error(400, "This model's maximum context length is 8192")) is True
    assert _is_context_overflow(error(413, "too many tokens in request")) is True
    # Not every 400 is an overflow — a rejected parameter must still surface.
    assert _is_context_overflow(error(400, "unknown field reasoning_effort")) is False
    assert _is_context_overflow(error(500, "context length")) is False


# --- AC5: measured, not assumed ------------------------------------------------


def test_condensation_measurably_shrinks_a_realistic_conversation():
    """AC5, on real message shapes at the sizes the runner actually produces:
    tool results are capped at 8,000 characters and every round appends one.

    Measured against the real strategy, not a model — no LM Studio server runs
    in this suite, so the reduction below is of the code path, and a run against
    a live model is still owed before the 2x figure in the ticket is quoted.
    """
    messages = _conversation(60, chars=8_000)
    before = sum(len(str(m["content"])) for m in messages)

    result = SummarisingCondenser(
        summarise=lambda m: "Earlier rounds explored the schema and settled on a migration.",
        max_chars=120_000,
    ).condense(messages)

    after = result.condensed_chars
    assert before > 400_000, "fixture no longer resembles a real conversation"
    assert after < before / 5, f"expected a large reduction, got {before} -> {after}"
    # The task statement is part of what survived, not collateral.
    assert result.messages[0]["content"].startswith("TASK:")


@pytest.mark.parametrize("size", [10, 100, 500])
def test_condensed_size_does_not_grow_with_conversation_length(size):
    """History growth goes from quadratic to linear — the property the ticket
    cites. The kept set is head + summary + tail regardless of input length."""
    result = SummarisingCondenser(summarise=lambda m: "s", max_chars=1).condense(
        _conversation(size, chars=100)
    )
    assert len(result.messages) == DEFAULT_HEAD + 1 + DEFAULT_TAIL
