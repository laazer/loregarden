"""What may be put in front of a person at a human gate, and what is an agent's to check.

The twelve gates waiting on 2026-11-25 asked a person 51 things. About forty
were facts ("bank= steps 0.50, then 1.50", "HP falls about 1 to 2 points a
second"): a test reads those exactly, and a person watching a screen cannot
read them at all. The rest were what the gate is for: does it feel like a
payoff, can you tell the two colours apart at a glance.

So each thing a person is asked names the judgment it needs
(:class:`PersonJudgment`) and why no agent can make it, and its wording is
refused where it states a fact a tool can measure. The wording check is a
floor. It catches the shapes that are never a judgment; whether the stated
reason is true is for whoever reads it, which is why the page shows it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from loregarden.models.domain import PersonJudgment

#: Short enough to allow "Whether it is fun", long enough to refuse "n/a".
MIN_REASON_CHARS = 15


@dataclass(frozen=True)
class _Shape:
    pattern: re.Pattern[str]
    problem: str


def _shape(pattern: str, problem: str) -> _Shape:
    return _Shape(re.compile(pattern, re.IGNORECASE), problem)


_MEASURED = "a measurement: a test reads it exactly and a person cannot"
_READOUT = "a readout: a test or a capture can compare the text"
_CODE = "named in code: a person at a running build cannot see it"
_TOOLING = "a command, log or test result: run it and record what it said"

_NUMBER_WORD = r"(?:a|one|two|three|four|five|half an?)"
_UNIT = (
    r"(?:ms|milliseconds?|s|secs?|seconds?|frames?|fps|px|pixels?|%|percent|"
    r"hp|points?|units?|m/s|dps|degrees?)"
)

#: Shapes that state a fact. Each matched against the sentence a person is
#: asked to confirm, never against the keys and places that lead them to it.
_FACT_SHAPES = (
    _shape(rf"\b\d+(?:\.\d+)?\s*{_UNIT}(?![\w/])", _MEASURED),
    _shape(r"\b\d+\.\d+\b", _MEASURED),
    _shape(
        rf"\b(?:about |roughly )?(?:once|twice|{_NUMBER_WORD}|\d+) (?:times )?(?:a|per|each) second\b",
        _MEASURED,
    ),
    _shape(rf"\b{_NUMBER_WORD} seconds?\b", _MEASURED),
    _shape(r"\bfrom \d+ to \d+\b", _MEASURED),
    _shape(r"\b(?:drops?|falls?|rises?|goes|jumps?)(?: back)? to (?:about )?\d+", _MEASURED),
    _shape(r"\bstays? at \d+|\bunchanged\b", _MEASURED),
    _shape(
        r"""\b(?:says?|reads?|labell?ed|prints?|shows?)\b[^.;]{0,30}["“'‘][^"”'’]+["”'’]""",
        _READOUT,
    ),
    _shape(r"\b\w+=(?=\s|\S)", _READOUT),
    _shape(r"`[^`]+`", _CODE),
    _shape(r"\bres://", _CODE),
    _shape(r"\b[a-z0-9]+(?:_[a-z0-9]+)+\b", _CODE),
    _shape(r"\b\w+\.(?:tscn|gd|py|tsx?|jsx?|json|ya?ml|sql|md)\b", _CODE),
    _shape(r"\bno (?:console |engine |script )?(?:errors?|warnings?)\b", _TOOLING),
    _shape(r"\b(?:console|stack trace|log file|exit code|status code|exits? 0)\b", _TOOLING),
    _shape(r"\bHTTP \d{3}\b", _TOOLING),
    _shape(
        r"\b(?:tests?|suite|lint|build|ci)\b[^.;]{0,20}\b(?:pass(?:es)?|green|succeeds?)\b",
        _TOOLING,
    ),
)

#: Reasons that describe missing agent work, not a person's judgment.
_EXCUSES = (
    re.compile(r"\b(?:no|not|without) (?:an? |any )?(?:\w+ )?tests?\b", re.IGNORECASE),
    re.compile(
        r"\b(?:untested|not (?:yet )?(?:tested|covered|automated|verified))\b", re.IGNORECASE
    ),
    re.compile(
        r"\b(?:could ?n[o']t|can ?n[o']t|can't|unable to|did ?n[o']t|failed to)"
        r" (?:run|launch|start|capture|open|boot|connect)\b",
        re.IGNORECASE,
    ),
)


def fact_problem(text: str) -> str:
    """Why ``text`` is a fact an agent checks rather than a person's judgment, or ""."""
    for shape in _FACT_SHAPES:
        match = shape.pattern.search(text)
        if match:
            return f'"{match.group(0).strip()}" is {shape.problem}'
    return ""


def reason_problem(reason: str) -> str:
    """Why ``reason`` does not explain needing a person, or ""."""
    stripped = reason.strip()
    if len(stripped) < MIN_REASON_CHARS:
        return "say in a sentence why no test or capture can answer it"
    for excuse in _EXCUSES:
        match = excuse.search(stripped)
        if match:
            return (
                f'"{match.group(0)}" is work an agent still owes, not a reason to ask a person: '
                "write the test, or block on what stopped you"
            )
    return ""


__all__ = ["MIN_REASON_CHARS", "PersonJudgment", "fact_problem", "reason_problem"]
