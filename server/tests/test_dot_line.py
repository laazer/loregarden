"""Unit tests for the mid-dot / tag log-line builders."""

from loregarden.dot_line import (
    CMD,
    OUT,
    RUN,
    SYS,
    TOOL,
    Dot,
    LogLine,
    clip,
    kv,
    kv_space,
    mid_dot,
    shell,
)


def test_dot_joins_with_mid_dot():
    assert str(Dot("codex thread") / "abc") == "codex thread · abc"
    assert str(Dot("session init") / "haiku") == "session init · haiku"


def test_dot_skips_empty_parts():
    assert str(Dot("$ pytest") / "") == "$ pytest"
    assert str(Dot("$ pytest") / None) == "$ pytest"
    assert str(Dot("a") / "" / "b") == "a · b"


def test_dot_chains_like_pathlib():
    assert str(Dot("one") / "two" / "three") == "one · two · three"


def test_mid_dot_helper():
    assert mid_dot("a", "b", "") == "a · b"
    assert mid_dot(Dot("a") / "b", "c") == "a · b · c"


def test_dot_rtruediv():
    assert str("prefix" / Dot("tail")) == "prefix · tail"


def test_tag_slash_builds_log_line():
    assert SYS / "codex turn started" == ("SYS", "codex turn started")
    assert SYS / "codex thread" / "abc" == ("SYS", "codex thread · abc")
    assert TOOL / shell("pytest") / "ok" == ("TOOL", "$ pytest · ok")
    assert TOOL / shell("pytest") / "" == ("TOOL", "$ pytest")
    assert OUT / "hello" == ("OUT", "hello")


def test_tag_call_form():
    assert SYS("codex turn done") == ("SYS", "codex turn done")
    assert RUN("agent invoked") / kv("skill", "implement") == (
        "RUN",
        "agent invoked · skill=implement",
    )


def test_tag_maybe_drops_blank():
    assert OUT.maybe("  hi  ") == ("OUT", "hi")
    assert OUT.maybe("   ") is None
    assert OUT.maybe(None) is None


def test_shell_and_clip():
    assert shell("pytest -q") == "$ pytest -q"
    assert shell("") == "$ command"
    assert shell("x" * 200, width=10) == "$ " + ("x" * 10)
    assert clip("abcdef", 3) == "abc"
    assert clip("", 3, fallback="command") == "command"


def test_kv_helpers():
    assert kv("skill", "implement") == "skill=implement"
    assert kv("skill", "") == "skill=—"
    assert kv_space(in_=12, out=3) == "in=12 out=3"
    assert SYS / "codex turn done" / kv_space(in_=1, out=2) == (
        "SYS",
        "codex turn done · in=1 out=2",
    )


def test_with_body_appends_clipped_stdout():
    line = (TOOL / shell("pytest") / "ok").with_body("pass\n")
    assert line == ("TOOL", "$ pytest · ok\npass")
    assert (TOOL / shell("pytest")).with_body("") == ("TOOL", "$ pytest")
    assert (TOOL / shell("x")).with_body("y" * 50, width=5).text.endswith("\nyyyyy")


def test_log_line_unpacks_like_a_tuple():
    tag, text = SYS / "session init" / "haiku"
    assert tag == "SYS"
    assert text == "session init · haiku"
    assert len(SYS / "x") == 2
    assert (SYS / "x")[0] == "SYS"


def test_log_line_equality_and_hash():
    a = SYS / "codex thread" / "abc"
    b = LogLine("SYS", "codex thread", "abc")
    assert a == b
    assert a == ("SYS", "codex thread · abc")
    assert {a, b} == {a}


def test_cmd_tag():
    assert CMD / "pytest -q" == ("CMD", "pytest -q")
