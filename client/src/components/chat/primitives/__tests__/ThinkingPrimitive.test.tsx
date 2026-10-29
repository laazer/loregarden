import { fireEvent, render, screen } from "@testing-library/react";

import { ThinkingPrimitive } from "../ThinkingPrimitive";

const trace = ["Checking the runner.", "· Read · server/runner.py", "· Read · server/cli.py", "· Bash · git status"].join("\n");

describe("ThinkingPrimitive", () => {
  it("folds a reply's reasoning to one line: what it did and how many tool calls", () => {
    render(<ThinkingPrimitive part={{ primitive: "thinking", content: trace }} />);

    const head = screen.getByRole("button", { name: /Read runner\.py and cli\.py/ });
    expect(head).toHaveTextContent("· 3 tool calls");
    expect(head).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText(/Checking the runner/)).not.toBeInTheDocument();
  });

  it("opens to the full trace and folds again", () => {
    render(<ThinkingPrimitive part={{ primitive: "thinking", content: trace }} />);
    const head = screen.getByRole("button", { name: /tool calls/ });

    fireEvent.click(head);
    expect(head).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText(/Checking the runner/)).toBeInTheDocument();
    fireEvent.click(head);
    expect(screen.queryByText(/Checking the runner/)).not.toBeInTheDocument();
  });

  it("starts open when the part asks to, and says a tool-free turn thought it through", () => {
    render(<ThinkingPrimitive part={{ primitive: "thinking", content: "Just reasoning.", collapsed: false }} />);
    expect(screen.getByRole("button", { name: /Thought it through/ })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Just reasoning.")).toBeInTheDocument();
  });
});
