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
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

#: Phrases that only appear when a plan/quota window is exhausted.
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
)

#: Provider fingerprints, checked in order — the first hit names the provider.
_PROVIDER_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("codex", ("chatgpt.com", "codex", "openai")),
    ("claude", ("claude", "anthropic")),
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

# The Claude CLI reports its reset as a unix timestamp: "…usage limit reached|1786530420"
_EPOCH_RESET = re.compile(r"usage limit reached\s*\|\s*(?P<epoch>\d{9,13})", re.IGNORECASE)

# A bare clock time, no date: "resets at 3pm", "try again at 10:07 AM"
_CLOCK_RESET = re.compile(
    r"(?:try again at|resets? at|available again at)\s+"
    r"(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>[AaPp]\.?[Mm]\.?)?",
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

    @property
    def provider_label(self) -> str:
        return _PROVIDER_LABELS.get(self.provider, _PROVIDER_LABELS[""])


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
    suffix = f":{minute:02d}" if match.group("minute") else ""
    return None, f"{match.group('hour')}{suffix} {match.group('ampm') or ''}".strip()


#: Most specific wording first — a message carrying a full date should not be
#: read as the bare clock time inside it.
_RESET_READERS = (_epoch_reset, _absolute_reset, _relative_reset, _clock_reset)


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
        lower = text.lower()
        index = -1
        for phrase in _LIMIT_PHRASES:
            found = lower.find(phrase)
            if found != -1:
                index = found
                break
        if index == -1:
            continue
        reset_at, reset_text = _resolve_reset(text, now=now or datetime.now(timezone.utc))
        return UsageLimit(
            provider=_detect_provider(lower),
            reset_at=reset_at,
            reset_text=reset_text,
            quote=_sentence_around(text, index),
        )
    return None


def format_usage_limit_hint(limit: UsageLimit) -> str:
    """The operator-facing explanation: who refused, until when, what to do."""
    when = f" It resets {limit.reset_text}." if limit.reset_text else ""
    if limit.reset_text and limit.reset_text[0].isdigit():
        when = f" It resets at {limit.reset_text}."

    lines = [
        f"{limit.provider_label} refused this turn: the account's usage limit is reached.{when}",
        "",
        "This is a plan limit, not a failure of the work — re-running before the "
        "window resets will fail the same way.",
        "",
        "Fix:",
        "1. Wait for the reset and re-run the stage, or",
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
    when = f" — resets {limit.reset_text}" if limit.reset_text else ""
    if limit.reset_text and limit.reset_text[0].isdigit():
        when = f" — resets at {limit.reset_text}"
    return (
        f"Usage limit reached on {limit.provider_label}{when}. "
        "The stage did not fail on its merits; re-run it after the window resets, "
        "raise the plan limit, or switch adapter."
    )
