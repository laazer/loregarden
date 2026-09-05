"""The tool-output body policy: which commands' stdout is worth storing."""

from loregarden.dot_line import TOOL, shell
from loregarden.services.tool_body import (
    CommandOutcome,
    ToolBodyPolicy,
    body_policy,
    command_outcome,
    output_shape,
    with_tool_output,
)


def test_successful_read_only_command_is_shaped():
    assert (
        body_policy("sed -n '1,320p' foo.ts", outcome=CommandOutcome.SUCCEEDED)
        is ToolBodyPolicy.SHAPE
    )
    assert (
        body_policy("rg -n 'pattern' src", outcome=CommandOutcome.SUCCEEDED) is ToolBodyPolicy.SHAPE
    )
    assert body_policy("ls -la", outcome=CommandOutcome.SUCCEEDED) is ToolBodyPolicy.SHAPE


def test_shell_wrapper_is_not_the_command():
    """`/bin/zsh -lc '<command>'` — reading the wrapper classifies everything alike."""
    assert (
        body_policy("/bin/zsh -lc \"sed -n '1,105p' a.tsx\"", outcome=CommandOutcome.SUCCEEDED)
        is ToolBodyPolicy.SHAPE
    )
    assert (
        body_policy("/bin/zsh -lc 'npx jest src/'", outcome=CommandOutcome.SUCCEEDED)
        is ToolBodyPolicy.KEEP
    )


def test_output_that_is_the_finding_is_kept():
    assert body_policy("pytest -q", outcome=CommandOutcome.SUCCEEDED) is ToolBodyPolicy.KEEP
    assert body_policy("npx tsc --noEmit", outcome=CommandOutcome.SUCCEEDED) is ToolBodyPolicy.KEEP
    assert body_policy("git diff --stat", outcome=CommandOutcome.SUCCEEDED) is ToolBodyPolicy.KEEP


def test_unrecognised_command_keeps_its_body():
    """The default is to store. A command we cannot classify is not noise by fiat."""
    assert (
        body_policy("./scripts/whatever.sh", outcome=CommandOutcome.SUCCEEDED)
        is ToolBodyPolicy.KEEP
    )


def test_a_failing_read_only_command_keeps_its_body():
    """`rg: no such file` is the only record of why the command failed."""
    assert body_policy("rg -n 'x' missing.py", outcome=CommandOutcome.FAILED) is ToolBodyPolicy.KEEP


def test_every_segment_of_a_pipeline_must_be_read_only():
    assert (
        body_policy("cat a.txt && wc -l a.txt", outcome=CommandOutcome.SUCCEEDED)
        is ToolBodyPolicy.SHAPE
    )
    assert (
        body_policy("cat a.txt && pytest -q", outcome=CommandOutcome.SUCCEEDED)
        is ToolBodyPolicy.KEEP
    )
    assert (
        body_policy("rg -l foo | xargs sed -i s/a/b/", outcome=CommandOutcome.SUCCEEDED)
        is ToolBodyPolicy.KEEP
    )


def test_a_redirect_is_not_a_read():
    assert body_policy("cat a.txt > b.txt", outcome=CommandOutcome.SUCCEEDED) is ToolBodyPolicy.KEEP


def test_command_outcome_reads_a_foreign_status_into_our_vocabulary():
    assert command_outcome("completed") is CommandOutcome.SUCCEEDED
    assert command_outcome("0") is CommandOutcome.SUCCEEDED
    assert command_outcome("failed") is CommandOutcome.FAILED
    # An adapter that reported nothing has not reported success.
    assert command_outcome("") is CommandOutcome.UNKNOWN
    assert body_policy("ls", outcome=CommandOutcome.UNKNOWN) is ToolBodyPolicy.KEEP


def test_output_shape_reports_size_not_contents():
    assert output_shape("a\nb\nc") == "3 lines, 5 B"
    assert output_shape("x" * 2048) == "1 lines, 2.0 KB"


def test_with_tool_output_shapes_a_read_and_keeps_a_test():
    read = with_tool_output(
        TOOL / shell("sed -n '1,3p' a.ts") / "completed",
        command="sed -n '1,3p' a.ts",
        output="line one\nline two\nline three",
        outcome=CommandOutcome.SUCCEEDED,
    )
    assert read == ("TOOL", "$ sed -n '1,3p' a.ts · completed · 3 lines, 28 B")

    tested = with_tool_output(
        TOOL / shell("pytest -q") / "failed",
        command="pytest -q",
        output="FAILED tests/test_a.py::test_b",
        outcome=CommandOutcome.FAILED,
    )
    assert tested == ("TOOL", "$ pytest -q · failed\nFAILED tests/test_a.py::test_b")


def test_empty_output_leaves_the_line_alone():
    line = TOOL / shell("ls") / "completed"
    kw = {"command": "ls", "outcome": CommandOutcome.SUCCEEDED}
    assert with_tool_output(line, output="", **kw) == line
    assert with_tool_output(line, output=None, **kw) == line
