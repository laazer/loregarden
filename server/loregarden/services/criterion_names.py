"""Which of a ticket's acceptance criteria a reviewer meant by the name it wrote.

The contract asks for `unmet_criteria` word for word, and reviewers write them
the way a person cites a long criterion: its label and heading, with the reason
it failed after it. lg-code-knowledge-1113 stores criteria like
``AC10 — TRUNCATION IS REVERSIBLE, … DIFFERENCE. In `services/…` …`` (several
hundred words each), and its reviewer named
``AC10 — TRUNCATION IS REVERSIBLE, … DIFFERENCE (the uniform field family is
absent on the vault leg, …)``. An exact comparison matched none of the three
named, so the split at the rework cap was refused and the ticket paused for a
person, though the reviewer had named each one plainly.

A name resolves only when exactly one criterion fits a rule. A bare ``AC1`` on a
ticket that also has AC10–AC12 begins all four, so the prefix rule abstains and
the label rule resolves it to AC1; a name that fits several, or none, moves
nothing.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence

#: A trailing parenthetical: the reason a reviewer appended to the name.
_TRAILING_REASON = re.compile(r"\s*\([^()]*\)\s*$")
#: A leading criterion label: ``AC10``, ``R1.3``, ``UI-D7``, ``AC-4``.
_LABEL = re.compile(r"^\s*([A-Za-z]{1,4}-?[A-Za-z]?\d+(?:\.\d+)*[a-z]?)(?=$|[\s:—–.)-])")


def _collapsed(text: str) -> str:
    return " ".join(text.split())


def _label(text: str) -> str | None:
    found = _LABEL.match(text)
    return found.group(1).upper() if found else None


def _only(criteria: Sequence[str], fits: Callable[[str], bool]) -> str | None:
    matches = [criterion for criterion in criteria if fits(criterion)]
    return matches[0] if len(matches) == 1 else None


def resolve_criterion(criteria: Sequence[str], name: str) -> str | None:
    """The one criterion in ``criteria`` that ``name`` refers to, or None.

    In order, the first rule that yields exactly one criterion wins:

    1. the name is the criterion, word for word (whitespace aside);
    2. the name, less a trailing ``(reason)``, is how the criterion begins;
    3. the criterion is how the name begins (the criterion, then decoration);
    4. the name and the criterion carry the same leading label (``AC10``).
    """
    said = _collapsed(name)
    if not said:
        return None
    collapsed = {criterion: _collapsed(criterion) for criterion in criteria}
    exact = _only(criteria, lambda c: collapsed[c] == said)
    if exact is not None:
        return exact
    head = _TRAILING_REASON.sub("", said).rstrip(" .:;,")
    if head:
        begun = _only(criteria, lambda c: collapsed[c].startswith(head))
        if begun is not None:
            return begun
    decorated = _only(criteria, lambda c: bool(collapsed[c]) and said.startswith(collapsed[c]))
    if decorated is not None:
        return decorated
    label = _label(said)
    if label is None:
        return None
    return _only(criteria, lambda c: _label(c) == label)
