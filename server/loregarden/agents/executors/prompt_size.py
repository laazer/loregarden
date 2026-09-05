"""Record how large a rendered stage prompt was.

Its own module rather than a method on `CliAgentExecutor`, which sits at the
1000-line class cap: a helper method here costs the class ~30 lines it does not
have, and the gate is right that a service that size should be shedding code
rather than growing it.

WHY THIS IS NOT INSIDE `_build_prompt`, which is where it obviously belongs.
That function is a pure function of its inputs, and several suites call it with
an `AgentRun` that was never added to a session. Committing there fails their
foreign keys. Merely SETTING the attribute there is worse: mutating an attached
ORM object marks the session dirty, so the next autoflush persists it anyway —
which surfaced as "database is locked", a lost telemetry row, and a 0.6s -> 32.6s
slowdown, none of which resembles "someone set an integer". The dispatch path
owns the run's lifecycle, so the dispatch path records it.
"""

from __future__ import annotations

from loregarden.models.domain import AgentRun
from sqlmodel import Session


def record_prompt_size(session: Session, run: AgentRun, prompt: str) -> None:
    """Store the rendered prompt's length on the run.

    Chars, not tokens: exact and free, where a token count would be a second
    estimate of something the provider already reports on the same row. Its value
    comes from being read next to `cache_read_tokens` — the invariant boilerplate
    at the front of a stage prompt IS the cache prefix, so "large" and
    "expensive" are different claims. See lg-workflow-integrity-498.
    """
    run.prompt_chars = len(prompt)
    session.add(run)
    session.commit()
