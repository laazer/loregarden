"""Recognise a provider usage/quota limit in CLI output and say so plainly.

Hitting a plan limit is the single most common way an agent turn dies here, and
it is the one failure that is not a bug in the work: nothing about the ticket,
the prompt or the repo will change the outcome until the window resets. It used
to reach the operator as whatever the CLI happened to print — a ChatGPT upsell
sentence buried in a Codex TUI dump, or worse, a clean exit whose only symptom
was "emitted no parseable stage report", which reads like an agent defect.

So the signal is extracted at the point the text is still intact: which provider
refused, and when it will stop refusing. Everything downstream (chat hints,
blocking issues) formats from that, rather than each surface re-guessing.

Detection is deliberately conservative. A false positive would tell an operator
to wait out a window that does not exist and hide a real failure behind it, so a
match needs an explicit limit phrase — not merely the word "limit", and not a
bare 429, which an MCP server or a git host can just as easily emit.

The three adapters state a limit in three different places, which is why this
matches on more than prose:

- **Codex** writes one sentence with the reset in it.
- **Claude** composes the refusal locally from a fixed window vocabulary
  ("You've hit your <session|weekly|Opus|Sonnet> limit · resets <when>"), so its
  wording is stable enough to match and specific enough to be worth reporting.
- **Cursor** composes nothing. Its CLI receives a server error and prints
  whatever title the server sent, so the prose is not ours to rely on. What it
  *does* own is the error code — ``FREE_USER_USAGE_LIMIT``, ``RATE_LIMITED``,
  ``USAGE_PRICING_REQUIRED`` and friends, plus the ``resource_exhausted`` its
  transport maps every 429 to. Those are matched directly, so a Cursor refusal
  is recognised even when the server rewords it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

#: Phrases that only appear when a plan/quota window is exhausted.
#:
#: The Claude entries are its own strings, read out of the CLI bundle rather than
#: guessed: it builds every refusal as "You've hit your <window> · resets <when>"
#: over a fixed set of windows (session/weekly/Opus/Sonnet/usage/spend), and has
#: a separate vocabulary for running out of credits or being disabled by an admin.
_LIMIT_PHRASES = (
    "usage limit",
    "rate limit reached",
    "rate limit exceeded",
    "rate_limit_error",
    "you've reached your limit",
    "youve reached your limit",
    "you have reached your limit",
    "quota exceeded",
    "insufficient_quota",
    "out of credits",
    "no credits remaining",
    "monthly limit",
    "weekly limit",
    # Claude CLI
    "you've hit your",
    "youve hit your",
    "you've reached your",
    "out of usage credits",
    "out of usage",
    "usage allocation has been disabled",
    "seat type doesn't include usage",
    "credit balance too low",
    "credit balance is too low",
)

#: Cursor's own error codes, which are the only part of its refusal that Cursor
#: controls — see ``_CURSOR_CODES`` below for why that matters.
_CURSOR_CODES: tuple[tuple[str, str], ...] = (
    ("free_user_usage_limit", "free-plan usage limit"),
    ("pro_user_usage_limit", "Pro usage limit"),
    ("free_user_rate_limit_exceeded", "free-plan rate limit"),
    ("pro_user_rate_limit_exceeded", "Pro rate limit"),
    ("generic_rate_limit_exceeded", "rate limit"),
    ("rate_limited_changeable", "rate limit"),
    ("rate_limited", "rate limit"),
    ("usage_pricing_required_changeable", "usage-pricing limit"),
    ("usage_pricing_required", "usage-pricing limit"),
    ("pro_user_only", "plan limit"),
    # The connect-RPC code an HTTP 429 maps to. Cursor's transport turns every
    # 429 into this before any prose exists, so it survives wording changes.
    ("resource_exhausted", "rate limit"),
)

#: Cursor codes that no amount of waiting clears — the account needs a payment or
#: plan change, so "wait for the reset" would be the wrong instruction.
_CURSOR_NO_RESET_SCOPES = frozenset({"usage-pricing limit", "plan limit"})

#: Sentences that carry a limit phrase but are not a refusal — the turn ran, or
#: something other than this account's quota was the cause. Checked against the
#: matched sentence, so a warning early in a log cannot mask a real refusal later.
#:
#: These are not hypothetical. Claude emits "Server is temporarily limiting
#: requests (not your usage limit)" for a capacity 429, announces overage with
#: "You're now using usage credits · Your weekly limit resets 3pm" while the turn
#: keeps going, and degrades Fast mode with "Fast limit reached and temporarily
#: disabled" without stopping the run. Reading any of those as a stop would tell
#: an operator to wait out a window that is not blocking them.
_NON_REFUSAL_MARKERS = (
    "not your usage limit",
    "temporarily limiting requests",
    "you're close to",
    "youre close to",
    "now using usage credits",
    "now using your usage allocation",
    "you're now using",
    "youre now using",
    "approaching",
    "fast limit reached",
    "fast mode",
    "will consume a substantial portion",
)

#: Claude names the exhausted window in the refusal. Keeping its own wording is
#: the difference between "wait" and "wait, or switch off Opus for an hour".
_CLAUDE_SCOPE = re.compile(
    r"you'?ve (?:hit|reached) your\s+(?P<scope>[A-Za-z0-9' ]{0,40}?limit)",
    re.IGNORECASE,
)

#: Provider fingerprints, checked in order — the first hit names the provider.
#: Claude carries its own window vocabulary, which identifies it even when the
#: refusal never says "Claude" ("You've hit your session limit · resets 3pm").
_PROVIDER_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("codex", ("chatgpt.com", "codex", "openai")),
    (
        "claude",
        (
            "claude",
            "anthropic",
            "usage-credits",
            "session limit",
            "weekly limit",
            "opus limit",
            "sonnet limit",
            "usage allocation",
            "usage credits",
        ),
    ),
    ("cursor", ("cursor.com", "cursor-agent", "cursor")),
)

_PROVIDER_LABELS = {
    "codex": "Codex / ChatGPT",
    "claude": "Claude",
    "cursor": "Cursor",
    "": "The model provider",
}

#: Where each provider sends an operator who wants the window raised.
_PROVIDER_LINKS = {
    "codex": "https://chatgpt.com/codex/settings/usage",
    "claude": "https://claude.ai/settings/usage",
    "cursor": "https://cursor.com/dashboard",
}

# "try again at Aug 12th, 2026 10:07 AM" / "resets at Aug 12, 2026 10:07 AM"
_ABSOLUTE_RESET = re.compile(
    r"(?:try again at|resets? at|available again at|retry at)\s+"
    r"(?P<month>[A-Z][a-z]{2,8})\.?\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?,?\s+"
    r"(?P<year>\d{4})[,]?\s+(?P<hour>\d{1,2}):(?P<minute>\d{2})\s*(?P<ampm>[AaPp]\.?[Mm]\.?)?",
)

# "try again in 4 hours 12 minutes" / "resets in 35 minutes" / "retry after 300 seconds"
_RELATIVE_RESET = re.compile(
    r"(?:try again in|resets? in|retry (?:in|after))\s+(?P<span>[^.\n]{1,60})",
    re.IGNORECASE,
)

_SPAN_PART = re.compile(r"(\d+)\s*(second|minute|hour|day)s?", re.IGNORECASE)

# Cursor's transport carries the wait as connect metadata rather than prose:
# "retryAfterMs=120000". A plain "retry-after: 120" header is seconds.
_RETRY_AFTER_MS = re.compile(r"retry[-_ ]?after[-_ ]?ms[=:]\s*(?P<ms>\d+)", re.IGNORECASE)
_RETRY_AFTER_S = re.compile(r"retry[-_ ]?after[=:]\s*(?P<seconds>\d+)", re.IGNORECASE)

# The Claude CLI reports its reset as a unix timestamp: "…usage limit reached|1786530420"
_EPOCH_RESET = re.compile(r"usage limit reached\s*\|\s*(?P<epoch>\d{9,13})", re.IGNORECASE)

# Claude writes the reset with no "at" and no year: "· resets Sep 12 at 3pm",
# "· resets Sep 12". Year-bearing text is handled by _ABSOLUTE_RESET above.
_DATE_RESET = re.compile(
    r"resets?(?:\s+at)?\s+"
    r"(?P<month>[A-Z][a-z]{2,8})\.?\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?"
    r"(?:\s+at\s+(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>[AaPp]\.?[Mm]\.?)?)?",
)

# A bare clock time, no date: "resets 3pm", "resets at 3pm", "try again at 10:07 AM".
# The "at" is optional because Claude omits it.
_CLOCK_RESET = re.compile(
    r"(?:try again at|resets?(?:\s+at)?|available again at)\s+"
    r"(?P<when>(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>[AaPp]\.?[Mm]\.?)?)",
)

_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

_SPAN_SECONDS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}

#: Enough of the provider's own sentence to be recognisable, not the TUI around it.
_QUOTE_LIMIT = 300


@dataclass(frozen=True)
class UsageLimit:
    """A recognised provider limit, and when it lifts if the provider said so."""

    provider: str
    """One of codex/claude/cursor, or "" when the text names no provider."""
    reset_at: datetime | None
    """Absolute reset instant (UTC-aware) when one could be resolved."""
    reset_text: str
    """The provider's own wording for the reset, e.g. "Aug 12th, 2026 10:07 AM"."""
    quote: str
    """The matched sentence, trimmed for display."""
    scope: str = ""
    """Which window was exhausted, in the provider's words ("weekly limit").

    Claude names it in prose and Cursor in an error code; the distinction is
    actionable either way. A session limit is an hour of waiting, an Opus limit
    leaves Sonnet available, a weekly limit means neither, and a usage-pricing
    limit is not a window at all.
    """

    @property
    def provider_label(self) -> str:
        return _PROVIDER_LABELS.get(self.provider, _PROVIDER_LABELS[""])

    @property
    def window_label(self) -> str:
        """What to call the exhausted window in a sentence."""
        return self.scope or "usage limit"

    @property
    def clears_on_reset(self) -> bool:
        """Whether waiting fixes this, or only a payment / plan change does.

        Cursor's spend-cap and Pro-only codes never lift on their own, so telling
        an operator to wait for a window would leave the ticket parked forever.
        """
        return self.scope not in _CURSOR_NO_RESET_SCOPES


def _to_24h(hour: int, ampm: str | None) -> int:
    if not ampm:
        return hour
    meridiem = ampm.replace(".", "").lower()
    if meridiem == "pm" and hour < 12:
        return hour + 12
    if meridiem == "am" and hour == 12:
        return 0
    return hour


def _parse_span(span: str) -> timedelta | None:
    seconds = sum(
        int(value) * _SPAN_SECONDS[unit.lower()] for value, unit in _SPAN_PART.findall(span)
    )
    return timedelta(seconds=seconds) if seconds else None


def _sentence_around(text: str, index: int) -> str:
    """The provider's sentence at ``index``, so the quote is not a TUI dump."""
    start = max(text.rfind("\n", 0, index), text.rfind(". ", 0, index) + 1, 0)
    end = len(text)
    for terminator in (". ", "\n"):
        found = text.find(terminator, index)
        if found != -1:
            end = min(end, found + len(terminator))
    quote = text[start:end].strip().strip('"').strip()
    if len(quote) > _QUOTE_LIMIT:
        quote = quote[: _QUOTE_LIMIT - 1].rstrip() + "…"
    return quote


def _detect_provider(text_lower: str) -> str:
    for provider, markers in _PROVIDER_MARKERS:
        if any(marker in text_lower for marker in markers):
            return provider
    return ""


def _epoch_reset(text: str, now: datetime) -> tuple[datetime | None, str] | None:
    match = _EPOCH_RESET.search(text)
    if not match:
        return None
    value = int(match.group("epoch"))
    # Claude has emitted both seconds and milliseconds here.
    if value > 10_000_000_000:
        value //= 1000
    moment = datetime.fromtimestamp(value, tz=timezone.utc)
    return moment, moment.strftime("%b %d, %Y %H:%M UTC")


def _absolute_reset(text: str, now: datetime) -> tuple[datetime | None, str] | None:
    match = _ABSOLUTE_RESET.search(text)
    month = _MONTHS.get(match.group("month")[:3].lower()) if match else None
    if not match or not month:
        return None
    try:
        moment = datetime(
            int(match.group("year")),
            month,
            int(match.group("day")),
            _to_24h(int(match.group("hour")), match.group("ampm")),
            int(match.group("minute")),
        )
    except ValueError:
        return None
    # No zone in the provider's text — keep it naive-as-local in the wording and
    # do not invent an offset.
    return None, moment.strftime("%b %d, %Y %I:%M %p").replace(" 0", " ")


def _date_reset(text: str, now: datetime) -> tuple[datetime | None, str] | None:
    """A month/day reset with no year, which is how Claude writes a weekly one."""
    match = _DATE_RESET.search(text)
    if not match or match.group("month")[:3].lower() not in _MONTHS:
        return None
    when = f"{match.group('month')} {match.group('day')}"
    if match.group("hour"):
        minute = f":{match.group('minute')}" if match.group("minute") else ""
        when = f"{when} at {match.group('hour')}{minute}{match.group('ampm') or ''}"
    # Deliberately no instant: without a year or a zone, resolving one would be a
    # guess, and a wrong instant is worse than none for a window an operator waits on.
    return None, when


def _retry_after_reset(text: str, now: datetime) -> tuple[datetime | None, str] | None:
    """A machine-readable wait, which is the only reset Cursor ever states."""
    ms_match = _RETRY_AFTER_MS.search(text)
    seconds = int(ms_match.group("ms")) / 1000 if ms_match else None
    if seconds is None:
        s_match = _RETRY_AFTER_S.search(text)
        seconds = int(s_match.group("seconds")) if s_match else None
    if not seconds:
        return None
    span = timedelta(seconds=seconds)
    minutes = round(span.total_seconds() / 60)
    wording = (
        f"in {minutes} minute{'s' if minutes != 1 else ''}" if minutes else "in under a minute"
    )
    return now + span, wording


def _relative_reset(text: str, now: datetime) -> tuple[datetime | None, str] | None:
    match = _RELATIVE_RESET.search(text)
    span = _parse_span(match.group("span")) if match else None
    if not match or not span:
        return None
    return now + span, f"in {match.group('span').strip()}"


def _clock_reset(text: str, now: datetime) -> tuple[datetime | None, str] | None:
    match = _CLOCK_RESET.search(text)
    if not match:
        return None
    hour = _to_24h(int(match.group("hour")), match.group("ampm"))
    minute = int(match.group("minute") or 0)
    if not (0 <= hour < 24 and 0 <= minute < 60):
        return None
    # Echo the provider's own spacing ("3pm", not "3 pm").
    return None, " ".join(match.group("when").split())


#: Most specific wording first — a message carrying a full date should not be
#: read as the bare clock time inside it.
_RESET_READERS = (
    _epoch_reset,
    _absolute_reset,
    _date_reset,
    _retry_after_reset,
    _relative_reset,
    _clock_reset,
)


def _resolve_reset(text: str, *, now: datetime) -> tuple[datetime | None, str]:
    """Best available reset instant, plus the wording to show the operator."""
    for reader in _RESET_READERS:
        resolved = reader(text, now)
        if resolved:
            return resolved
    return None, ""


def detect_usage_limit(*texts: str, now: datetime | None = None) -> UsageLimit | None:
    """Return the limit described by ``texts``, or None when none is described.

    Several texts because a CLI splits the evidence: the message lands on stdout
    or stderr depending on the adapter and on whether it exited non-zero.
    """
    for text in texts:
        if not text:
            continue
        limit = _first_refusal(text, now=now or datetime.now(timezone.utc))
        if limit:
            return limit
    return None


def _limit_indexes(text_lower: str) -> list[int]:
    """Every limit hit — prose phrase or Cursor code — earliest first."""
    hits = {text_lower.find(p) for p in _LIMIT_PHRASES}
    hits |= {text_lower.find(code) for code, _ in _CURSOR_CODES}
    return sorted(hits - {-1})


def _cursor_scope(sentence: str) -> str:
    """The window a Cursor error code names, if the sentence carries one.

    Read from the whole sentence rather than the matched offset: Cursor prints
    the server's prose and its code on one line, and the code is the half that
    means something precise ("usage limit hit [ERROR_FREE_USER_USAGE_LIMIT]").
    Ordered longest-first in ``_CURSOR_CODES`` so ``rate_limited_changeable``
    is not read as ``rate_limited``.
    """
    lowered = sentence.lower()
    for code, scope in _CURSOR_CODES:
        if code in lowered:
            return scope
    return ""


def _claude_scope(sentence: str) -> str:
    match = _CLAUDE_SCOPE.search(sentence)
    if not match:
        return ""
    # Keep the provider's capitalisation — "Opus limit" is a model name, not a
    # sentence start.
    scope = " ".join(match.group("scope").split())
    # "You've hit your limit" names no window; leave it to the generic wording.
    return "" if scope.lower() == "limit" else scope


def _first_refusal(text: str, *, now: datetime) -> UsageLimit | None:
    """The first limit phrase in ``text`` that is an actual refusal.

    Warnings are skipped rather than ending the scan: a long run's log can carry
    "You're close to your weekly limit" an hour before the refusal that killed it,
    and stopping at the first hit would report the warning and miss the stop.
    """
    lower = text.lower()
    for index in _limit_indexes(lower):
        sentence = _sentence_around(text, index)
        sentence_lower = sentence.lower()
        if any(marker in sentence_lower for marker in _NON_REFUSAL_MARKERS):
            continue
        code_scope = _cursor_scope(sentence)
        # Prefer the refusal's own sentence so an unrelated "resets" elsewhere in
        # the log cannot be attached to it; fall back to the whole text for CLIs
        # that print the window on the next line.
        reset_at, reset_text = _resolve_reset(sentence, now=now)
        if not reset_text:
            reset_at, reset_text = _resolve_reset(text, now=now)
        provider = _detect_provider(lower)
        return UsageLimit(
            # A Cursor error code identifies the provider on its own — its
            # transport can emit one with no other Cursor word in the line.
            provider=provider or ("cursor" if code_scope else ""),
            reset_at=reset_at,
            reset_text=reset_text,
            quote=sentence,
            scope=code_scope or _claude_scope(sentence),
        )
    return None


def format_usage_limit_hint(limit: UsageLimit) -> str:
    """The operator-facing explanation: who refused, until when, what to do."""
    when = f" It resets {limit.reset_text}." if limit.reset_text else ""
    if limit.reset_text and limit.reset_text[0].isdigit():
        when = f" It resets at {limit.reset_text}."

    if limit.clears_on_reset:
        why = (
            "This is a plan limit, not a failure of the work — re-running before "
            "the window resets will fail the same way."
        )
        first_step = "1. Wait for the reset and re-run the stage, or"
    else:
        when = ""
        why = (
            "This is a spend/plan cap, not a failure of the work — and unlike a "
            "rate limit it does not lift on its own, so waiting will not clear it."
        )
        first_step = "1. Raise the cap (or enable usage-based pricing) for this account, or"

    lines = [
        f"{limit.provider_label} refused this turn: the account's "
        f"{limit.window_label} is reached.{when}",
        "",
        why,
        "",
        "Fix:",
        first_step,
    ]
    link = _PROVIDER_LINKS.get(limit.provider)
    if link:
        lines.append(f"2. Raise the limit / buy credits at {link}, or")
    else:
        lines.append("2. Raise the limit or buy credits with the provider, or")
    lines.append("3. Switch this chat/workspace runtime to another adapter")
    if limit.quote:
        lines.extend(["", f"Provider said: {limit.quote}"])
    return "\n".join(lines)


def usage_limit_blocking_issue(limit: UsageLimit) -> str:
    """One-line form for ``ticket.blocking_issues`` — the workflow pane truncates."""
    window = limit.window_label
    headline = f"{window[0].upper()}{window[1:]} reached on {limit.provider_label}"
    if not limit.clears_on_reset:
        return (
            f"{headline}. The stage did not fail on its merits, and this cap does "
            "not lift on its own — raise it (or enable usage-based pricing), or "
            "switch adapter, before re-running."
        )
    when = f" — resets {limit.reset_text}" if limit.reset_text else ""
    if limit.reset_text and limit.reset_text[0].isdigit():
        when = f" — resets at {limit.reset_text}"
    return (
        f"{headline}{when}. "
        "The stage did not fail on its merits; re-run it after the window resets, "
        "raise the plan limit, or switch adapter."
    )
