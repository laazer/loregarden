"""Terminal escape codes come out of text shown outside a terminal."""

from __future__ import annotations

import pytest
from loregarden.core.terminal_text import safe_cut, strip_ansi

#: The head of lefthook's banner as a run log stored it: a 24-bit gradient,
#: one colour code per box-drawing character, then a bold hook name.
LEFTHOOK_BANNER = (
    "\x1b[38;2;0;0;0m╭\x1b[38;2;5;5;5m─\x1b[38;2;11;11;11m─\x1b[38;2;42;42;42m╮\x1b[m\n"
    "\x1b[38;2;0;0;0m│\x1b[m 🥊 lefthook  v2.1.10   hook:  \x1b[1mpre-commit\x1b[m "
    "\x1b[38;2;52;52;52m│\x1b[m"
)


def test_a_lefthook_banner_reads_as_its_box():
    assert strip_ansi(LEFTHOOK_BANNER) == ("╭──╮\n│ 🥊 lefthook  v2.1.10   hook:  pre-commit │")


@pytest.mark.parametrize(
    ("raw", "readable"),
    [
        ("\x1b[1m\x1b[32mclient/src/a.tsx\x1b[39m\x1b[22m [71:52]", "client/src/a.tsx [71:52]"),
        ("\x1b[?25lworking\x1b[?25h", "working"),  # cursor hide/show
        ("\x1b[2K\x1b[1Gdone", "done"),  # erase line, column
        ("\x1b]8;;https://x.test\x07link\x1b]8;;\x07", "link"),  # OSC hyperlink, BEL
        ("\x1b]0;title\x1b\\text", "text"),  # OSC title, ST
        ("plain text, [38;2;0m not an escape", "plain text, [38;2;0m not an escape"),
    ],
)
def test_escape_sequences_go_and_the_text_stays(raw, readable):
    assert strip_ansi(raw) == readable


@pytest.mark.parametrize("tail", ["\x1b", "\x1b[", "\x1b[3", "\x1b[38;2;5"])
def test_a_sequence_cut_off_by_the_end_of_the_text_goes_whole(tail):
    """Stored lines were cut mid-code; `ESC[3` at a line's end is noise."""
    assert strip_ansi(f"ok {tail}") == "ok "


CODE = "\x1b[38;2;5;5;5m"


def test_a_cut_inside_a_code_moves_in_front_of_it():
    text = "ab" + CODE + "cd"
    assert safe_cut(text, 5) == 2


def test_a_cut_after_a_whole_code_stays_where_it_was():
    text = "ab" + CODE + "cd"
    assert safe_cut(text, len("ab" + CODE) + 1) == len("ab" + CODE) + 1


def test_a_cut_with_no_code_before_it_stays_where_it_was():
    assert safe_cut("abcdef", 3) == 3


def test_a_cut_never_moves_to_the_start_so_a_slicing_loop_advances():
    assert safe_cut(CODE + "cd", 4) == 4
