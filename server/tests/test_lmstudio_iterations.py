"""Fresh-context iterations for the LM Studio runner.

A small local model drowns in an ever-growing conversation long before it
finishes a real stage. The runner drove one conversation capped at
`MAX_TOOL_ROUNDS`, and when that fired it printed the last assistant message and
gave up — which the stage-report parser reads as output like any other.

Each iteration now starts a NEW message list from the same task prompt. The
state a fresh start needs already lives outside the conversation (ticket row,
checkpoints, working tree) and the model reads it back through its tools.

The two caps are deliberately separate, which is the subtlety worth testing:
`MAX_TOOL_ROUNDS` bounds one conversation, `max_iterations` bounds how many
conversations a stage gets.
"""

from unittest import mock

import pytest
from loregarden.agents.executors import lmstudio_runner
from loregarden.agents.executors.lmstudio_runner import _IterationResult, _run_iterations
from loregarden.models.domain import Workspace
from loregarden.services.cli_settings import (
    MAX_LMSTUDIO_ITERATIONS,
    resolve_lmstudio_max_iterations,
)


def _iterations(results, *, max_iterations):
    """Drive the outer loop over a scripted sequence of iteration outcomes."""
    with mock.patch.object(lmstudio_runner, "_chat_with_tools", side_effect=results) as inner:
        text = _run_iterations(
            client=mock.Mock(),
            base_url="http://x/v1",
            model="m",
            prompt="do the stage",
            bridge=mock.Mock(),
            tools=[{"type": "function"}],
            effort="",
            max_iterations=max_iterations,
        )
    return text, inner


# -- AC1 / AC2: the loop and its termination -----------------------------------


def test_a_stage_that_answers_first_time_runs_one_iteration():
    text, inner = _iterations([_IterationResult(text="done", finished=True)], max_iterations=4)

    assert text == "done"
    assert inner.call_count == 1, "a finished stage must not spend further iterations"


def test_a_stage_that_needs_several_iterations_gets_them():
    text, inner = _iterations(
        [
            _IterationResult(text="ran out of room", finished=False),
            _IterationResult(text="still going", finished=False),
            _IterationResult(text="done", finished=True),
        ],
        max_iterations=4,
    )

    assert text == "done"
    assert inner.call_count == 3


def test_each_iteration_starts_from_the_same_prompt_not_the_last_conversation():
    """AC1's substance. If an iteration inherited the previous message list the
    context would keep growing, which is the failure being replaced."""
    _, inner = _iterations(
        [
            _IterationResult(text="a", finished=False),
            _IterationResult(text="b", finished=True),
        ],
        max_iterations=4,
    )

    prompts = {call.kwargs["prompt"] for call in inner.call_args_list}
    assert prompts == {"do the stage"}
    assert all("messages" not in call.kwargs for call in inner.call_args_list)


def test_an_exhausted_cap_fails_the_run_rather_than_returning_text():
    """AC2. Every iteration prints, and the stage-report parser reads stdout, so
    returning the last text quietly would let a stage that never finished read as
    one that did."""
    with pytest.raises(RuntimeError, match="did not finish within 2"):
        _iterations(
            [
                _IterationResult(text="nope", finished=False),
                _IterationResult(text="nope", finished=False),
            ],
            max_iterations=2,
        )


# -- AC3 / AC5: resolution precedence and validation ---------------------------


def test_the_cap_resolves_env_then_workspace_then_default(monkeypatch):
    workspace = Workspace(slug="w", name="W", repo_path="/nonexistent/w")

    monkeypatch.delenv("LOREGARDEN_LMSTUDIO_MAX_ITERATIONS", raising=False)
    from loregarden.config import settings

    assert resolve_lmstudio_max_iterations(None) == settings.lmstudio_max_iterations

    workspace.lmstudio_max_iterations = 6
    assert resolve_lmstudio_max_iterations(workspace) == 6

    monkeypatch.setenv("LOREGARDEN_LMSTUDIO_MAX_ITERATIONS", "9")
    assert resolve_lmstudio_max_iterations(workspace) == 9, "env must win over the workspace"


@pytest.mark.parametrize("bad", ["0", "-3", "lots", str(MAX_LMSTUDIO_ITERATIONS + 1)])
def test_a_nonsense_cap_fails_at_resolution_not_mid_run(bad, monkeypatch):
    """AC5. Refused rather than clamped: silently substituting a different number
    for the one an operator wrote is how a setting stops meaning what it says."""
    monkeypatch.setenv("LOREGARDEN_LMSTUDIO_MAX_ITERATIONS", bad)

    with pytest.raises(ValueError, match="LOREGARDEN_LMSTUDIO_MAX_ITERATIONS"):
        resolve_lmstudio_max_iterations(None)


# -- AC7: the two caps are not the same cap ------------------------------------


def test_the_per_iteration_round_cap_is_not_the_iteration_cap():
    """Conflating them would make a long-but-progressing stage look like a stuck
    one — the distinction lg-workflow-integrity-455 exists to make elsewhere."""
    assert lmstudio_runner.MAX_TOOL_ROUNDS != MAX_LMSTUDIO_ITERATIONS
    assert resolve_lmstudio_max_iterations(None) < lmstudio_runner.MAX_TOOL_ROUNDS
