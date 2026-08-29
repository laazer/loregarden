"""Read what a stage run consumed out of whatever its CLI printed.

Every supervised adapter prints its own token accounting, in its own shape, at
its own moment in the stream. This is the one place that knows the dialects, so
the executor stores one normalized `RunUsage` no matter what ran. Each was read
off a live CLI rather than a changelog:

``claude``
    A single terminal ``{"type": "result"}`` event carries ``usage`` with
    ``input_tokens`` / ``output_tokens`` / ``cache_read_input_tokens`` /
    ``cache_creation_input_tokens``, plus a ``modelUsage`` map keyed by the
    model ids the turn actually used. ``input_tokens`` excludes both cache
    figures (observed: 2 fresh input tokens beside 21786 cache reads).

``cursor``
    Also a terminal ``{"type": "result"}`` event, but camelCase and shorter:
    ``inputTokens`` / ``outputTokens`` / ``cacheReadTokens`` /
    ``cacheWriteTokens``. It names no model there, so the model comes from the
    ``system``/``init`` event that opens the stream. ``inputTokens`` excludes
    cache reads — confirmed by resuming a session: input fell from 10432 to
    6553 while cache reads rose from 5632 to 9984, which only adds up if the
    two are disjoint.

``codex``
    ``{"type": "turn.completed"}`` with ``input_tokens`` /
    ``cached_input_tokens`` / ``cache_write_input_tokens`` / ``output_tokens``.
    Unlike the other three, ``input_tokens`` **includes** the cached reads
    (observed: 32635 input containing 24320 cached), so the cached half is
    subtracted here to keep the three columns disjoint.

``opencode``
    No terminal total at all: one ``{"type": "step_finish"}`` per step, each
    with ``part.tokens`` = ``{total, input, output, reasoning, cache: {read,
    write}}``. The steps are summed. ``reasoning`` is billed as output and is
    disjoint from ``output`` (observed: ``total`` equals input + output +
    reasoning exactly), so the two are added together.

``lmstudio`` and ``local`` print no usage. They are absent from the dispatch
table and yield an empty `RunUsage` — the model and effort are still recorded
from the invocation, and the token columns stay NULL, which is the honest
answer rather than a fabricated zero.

Nothing here raises. A truncated stream, a run killed before its usage event,
or a CLI that changed its output shape yields whichever fields could be read
and leaves the rest unset, because an unreadable figure is exactly the
*unmeasured* case the nullable columns exist for.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal, TypeVar

from loregarden.models.domain import CliAdapter, RunUsage
from pydantic import BaseModel, ConfigDict, Field, ValidationError

# Foreign payloads, so every field is optional and unknown keys are dropped: a
# CLI is free to add fields, and a stream line that is some other event still
# validates into an all-empty event this module then ignores by `type`.
_FOREIGN = ConfigDict(extra="ignore", protected_namespaces=())


class _StreamEvent(BaseModel):
    """Common head of every adapter's NDJSON event: the discriminator."""

    model_config = _FOREIGN

    type: str = ""


class _ClaudeUsageBlock(BaseModel):
    model_config = _FOREIGN

    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None


class _ClaudeModelUsage(BaseModel):
    model_config = _FOREIGN

    outputTokens: int | None = None  # noqa: N815 - claude's own key
    canonicalModel: str = ""  # noqa: N815 - claude's own key


class _ClaudeEvent(_StreamEvent):
    usage: _ClaudeUsageBlock = Field(default_factory=_ClaudeUsageBlock)
    modelUsage: dict[str, _ClaudeModelUsage] = Field(  # noqa: N815 - claude's own key
        default_factory=dict
    )


class _CursorUsageBlock(BaseModel):
    model_config = _FOREIGN

    inputTokens: int | None = None  # noqa: N815 - cursor's own key
    outputTokens: int | None = None  # noqa: N815 - cursor's own key
    cacheReadTokens: int | None = None  # noqa: N815 - cursor's own key
    cacheWriteTokens: int | None = None  # noqa: N815 - cursor's own key


class _CursorEvent(_StreamEvent):
    subtype: str = ""
    model: str = ""
    usage: _CursorUsageBlock = Field(default_factory=_CursorUsageBlock)


class _CodexUsageBlock(BaseModel):
    model_config = _FOREIGN

    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    cache_write_input_tokens: int | None = None
    output_tokens: int | None = None


class _CodexEvent(_StreamEvent):
    usage: _CodexUsageBlock = Field(default_factory=_CodexUsageBlock)


class _OpencodeCache(BaseModel):
    model_config = _FOREIGN

    read: int | None = None
    write: int | None = None


class _OpencodeTokens(BaseModel):
    model_config = _FOREIGN

    input: int | None = None
    output: int | None = None
    reasoning: int | None = None
    cache: _OpencodeCache = Field(default_factory=_OpencodeCache)


class _OpencodePart(BaseModel):
    model_config = _FOREIGN

    tokens: _OpencodeTokens = Field(default_factory=_OpencodeTokens)


class _OpencodeEvent(_StreamEvent):
    part: _OpencodePart = Field(default_factory=_OpencodePart)


#: The terminal event each CLI names its usage block on. A `Literal` rather
#: than an enum because the vocabulary belongs to the CLIs, not to this repo.
_UsageEventType = Literal["result", "system", "turn.completed"]

_Event = TypeVar("_Event", bound=_StreamEvent)


def _events(stdout: str, event: type[_Event]) -> list[_Event]:
    """Every NDJSON object on ``stdout``, validated into ``event``, in order.

    Partial-message deltas, log lines and a half-written final line all fail
    validation or parse to nothing useful, and contribute nothing.
    """
    parsed: list[_Event] = []
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            parsed.append(event.model_validate_json(stripped))
        except ValidationError:
            continue
    return parsed


def _positive(value: int | None) -> int | None:
    """A token count, or None when it was absent or nonsensical.

    A negative count is dropped rather than stored: it is not a figure anyone
    should sum, and *unmeasured* is the truthful reading of a number that
    cannot be one.
    """
    if value is None or value < 0:
        return None
    return value


def _difference(total: int | None, part: int | None) -> int | None:
    """``total - part``, floored at zero, or None when the total is unknown.

    Floored rather than reported negative: a provider whose parts exceed its
    total is a shape read wrong here, and a negative count would poison every
    sum downstream.
    """
    if total is None:
        return None
    return max(total - (part or 0), 0)


def _added(left: int | None, right: int | None) -> int | None:
    """``left + right``, where a single known side counts as the whole.

    None + None stays None: adding two unmeasured steps must not invent a zero.
    """
    if left is None:
        return right
    if right is None:
        return left
    return left + right


def _claude_model(result: _ClaudeEvent) -> str | None:
    """The model that produced the most output in this turn.

    Claude Code reports usage per model, and a turn that handed a side task to
    a cheaper model lists both. The token columns already aggregate across
    them, so a single id is inherently a summary; the dominant producer is the
    one a cost estimate should be priced against.
    """
    best_id: str | None = None
    best_output = -1
    for model_id, entry in result.modelUsage.items():
        output = _positive(entry.outputTokens) or 0
        if output > best_output:
            best_output = output
            best_id = entry.canonicalModel or model_id or None
    return best_id


def _last_of_type(events: list[_Event], event_type: _UsageEventType) -> _Event | None:
    for event in reversed(events):
        if event.type == event_type:
            return event
    return None


def _claude_usage(stdout: str) -> RunUsage:
    result = _last_of_type(_events(stdout, _ClaudeEvent), "result")
    if result is None:
        return RunUsage()
    return RunUsage(
        input_tokens=_positive(result.usage.input_tokens),
        output_tokens=_positive(result.usage.output_tokens),
        cache_read_tokens=_positive(result.usage.cache_read_input_tokens),
        cache_write_tokens=_positive(result.usage.cache_creation_input_tokens),
        model=_claude_model(result),
    )


def _cursor_usage(stdout: str) -> RunUsage:
    events = _events(stdout, _CursorEvent)
    init = _last_of_type(events, "system")
    model = (init.model or None) if init else None
    result = _last_of_type(events, "result")
    if result is None:
        return RunUsage(model=model)
    return RunUsage(
        input_tokens=_positive(result.usage.inputTokens),
        output_tokens=_positive(result.usage.outputTokens),
        cache_read_tokens=_positive(result.usage.cacheReadTokens),
        cache_write_tokens=_positive(result.usage.cacheWriteTokens),
        model=model,
    )


def _codex_usage(stdout: str) -> RunUsage:
    completed = _last_of_type(_events(stdout, _CodexEvent), "turn.completed")
    if completed is None:
        return RunUsage()
    cached = _positive(completed.usage.cached_input_tokens)
    return RunUsage(
        input_tokens=_difference(_positive(completed.usage.input_tokens), cached),
        output_tokens=_positive(completed.usage.output_tokens),
        cache_read_tokens=cached,
        cache_write_tokens=_positive(completed.usage.cache_write_input_tokens),
    )


def _opencode_usage(stdout: str) -> RunUsage:
    totals = RunUsage()
    for event in _events(stdout, _OpencodeEvent):
        if event.type != "step_finish":
            continue
        tokens = event.part.tokens
        totals = RunUsage(
            input_tokens=_added(totals.input_tokens, _positive(tokens.input)),
            output_tokens=_added(
                totals.output_tokens,
                _added(_positive(tokens.output), _positive(tokens.reasoning)),
            ),
            cache_read_tokens=_added(totals.cache_read_tokens, _positive(tokens.cache.read)),
            cache_write_tokens=_added(totals.cache_write_tokens, _positive(tokens.cache.write)),
        )
    return totals


_PARSERS: dict[CliAdapter, Callable[[str], RunUsage]] = {
    CliAdapter.CLAUDE: _claude_usage,
    CliAdapter.CURSOR: _cursor_usage,
    CliAdapter.CODEX: _codex_usage,
    CliAdapter.OPENCODE: _opencode_usage,
}


def parse_run_usage(stdout: str, *, adapter: CliAdapter) -> RunUsage:
    """Token usage this adapter reported on ``stdout``, as far as it can be read.

    An adapter with no usage surface, an empty stream, or a stream whose usage
    event never arrived all return an empty `RunUsage`: unmeasured, not free.
    """
    parser = _PARSERS.get(adapter)
    if parser is None or not stdout:
        return RunUsage()
    return parser(stdout)
