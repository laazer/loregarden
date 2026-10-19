"""Terminal output made readable where it is not shown in a terminal.

Agents run tools that colour their output (lefthook draws a 24-bit gradient
banner, jest and jscpd colour their reports), and that output reaches the run
history, the workflow pane and the context fed back to agents as raw escape
sequences, which read as `[38;2;5;5;5m─` noise instead of text.
"""

from __future__ import annotations

import re

#: A whole escape sequence: CSI (colour, cursor, erase, private modes such as
#: `ESC[?25l`), OSC (titles, `ESC]8;;url` hyperlinks) ended by BEL or ST, and
#: the two-byte Fe escapes less `[` and `]`, which open the other two.
_SEQUENCE = (
    r"\x1b\[[0-?]*[ -/]*[@-~]"
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
    r"|\x1b[@-Z\\^_]"
)
_SEQUENCE_RE = re.compile(_SEQUENCE)
#: Those, plus a sequence cut off by the end of the text, which a stream split
#: mid-code leaves behind and which never means anything shown.
_ANSI_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*)?$|" + _SEQUENCE)


def strip_ansi(text: str) -> str:
    """Drop ANSI escape sequences, keeping the text they decorated."""
    return _ANSI_RE.sub("", text)


def safe_cut(text: str, limit: int) -> int:
    """The cut at or before `limit` that splits no escape sequence.

    A code cut in half stores `ESC[3` at the end of one chunk and `8;2;5;5;5m─`
    at the start of the next, where nothing can recognise it any more. When the
    last escape before `limit` does not end before it, the cut moves in front
    of that escape; never to 0, so a caller slicing in a loop always advances.
    """
    escape = text.rfind("\x1b", 0, limit)
    if escape <= 0:
        return limit
    whole = _SEQUENCE_RE.match(text, escape)
    if whole is not None and whole.end() <= limit:
        return limit
    return escape
