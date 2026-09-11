"""Shared run failure messages."""

from __future__ import annotations

import re

TIMEOUT_HARD_CAP_MULTIPLIER = 4

_LEGACY_TIMEOUT_SUFFIX = re.compile(
    r"^(Agent timed out after \d+s): Command .* timed out after \d+ seconds?$",
    re.DOTALL,
)


def agent_timeout_message(timeout_seconds: int | float) -> str:
    seconds = int(timeout_seconds)
    return f"Agent timed out after {seconds}s"


def normalize_timeout_stderr(
    stderr: str,
    *,
    timeout_seconds: int | float | None = None,
) -> str:
    """Strip misleading subprocess TimeoutExpired suffixes from legacy messages."""
    cleaned = stderr.strip()
    legacy = _LEGACY_TIMEOUT_SUFFIX.match(cleaned)
    if legacy:
        return legacy.group(1)
    if timeout_seconds is not None:
        prefix = agent_timeout_message(timeout_seconds)
        if cleaned.startswith(prefix):
            return prefix
    return cleaned


#: What a FAILED run's stderr says when nothing said anything. Deliberately a
#: sentence about the absence rather than an empty string: "it failed" and "there
#: is nothing to report" must not collapse into one answer, and a blank
#: `blocking_issues` is what the workflow pane renders for a healthy ticket.
NO_RECORDED_REASON = (
    "Agent run failed with no reason recorded — the process produced neither an "
    "error nor any output. Check the run log; if it is also empty the failure "
    "happened before the agent wrote anything."
)


def failure_reason(*, stderr: str, stdout: str) -> str:
    """The best available account of why a run failed, never "".

    Measured need: 19 of 179 failed runs in this installation carried an empty
    `stderr`. Two had no `stdout` either; the rest had output — one of them
    86,814 characters of it — that nothing ever looked at, so the operator got a
    blocked ticket whose stated cause was the empty string. That is the
    repository's own *no silent failures* rule broken in its own data, and it is
    broken at the moment of writing the row rather than anywhere a reader could
    recover from.

    The last non-blank line of stdout is a good account when there is one: the
    CLIs stream JSON and their final envelope is the result line, which carries
    the terminal reason when the process knew it. Falling back to a sentence
    about the absence, rather than to nothing, is what makes an unexplained
    failure legible as unexplained.
    """
    if stderr.strip():
        return stderr
    tail = next((line for line in reversed(stdout.splitlines()) if line.strip()), "")
    if tail:
        return f"Agent run failed with no error output. Last line of its output: {tail[:1000]}"
    return NO_RECORDED_REASON
