/**
 * The terminal card's output pane, and its keyboard path.
 *
 * lg-milestone-that-2215: `.lg-primitive-terminal-body` is a bounded
 * `overflow: auto` <pre>. The card's own controls — the expand toggle and the
 * overflow menu — are in the header ABOVE it, so once a command printed more
 * than the box holds, nothing could scroll it without a mouse.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { TerminalPrimitive } from "../TerminalPrimitive";
import type { TerminalLine, TerminalPart } from "../types";

function part(lines: TerminalLine[], overrides: Partial<TerminalPart> = {}): TerminalPart {
  return { primitive: "terminal", title: "pytest", cwd: "/repo", lines, ...overrides };
}

const REGION = "Command output";

describe("the terminal card's output pane", () => {
  it("is a tab stop carrying the shared ring, reached by Tab — AC3", async () => {
    render(
      <TerminalPrimitive
        part={part([
          { kind: "command", text: "pytest -q" },
          { kind: "stdout", text: "12 passed" },
          { kind: "status", text: "exit 0" },
        ])}
      />,
    );

    const pane = screen.getByRole("group", { name: REGION });
    expect(pane).toHaveAttribute("tabindex", "0");
    expect(pane).toHaveClass("scroll-focus-ring");

    // The card's overflow menu PRECEDES the pane in DOM order and `Open IDE`
    // in the footer FOLLOWS it, so both steps exist here and both are named.
    pane.focus();
    await userEvent.tab({ shift: true });
    expect(screen.getByRole("button", { name: "More for pytest" })).toHaveFocus();
    pane.focus();
    await userEvent.tab();
    expect(screen.getByRole("button", { name: "Open IDE" })).toHaveFocus();
  });

  it("puts a line in its place for a command that printed nothing — AC8", () => {
    // Only a status line, so there is nothing to read: the box would announce
    // "Command output" over an empty <pre>, which AC8 forbids.
    render(<TerminalPrimitive part={part([{ kind: "status", text: "exit 0" }])} />);

    expect(screen.queryByRole("group", { name: REGION })).toBeNull();
    expect(screen.getByText("This command produced no output.")).toBeInTheDocument();
  });
});
