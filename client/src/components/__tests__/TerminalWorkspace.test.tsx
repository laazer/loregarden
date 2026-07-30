import { fireEvent, render, screen } from "@testing-library/react";

import { TerminalWorkspace } from "../TerminalWorkspace";

jest.mock("../TerminalPanel", () => ({
  TerminalPanel: ({ workspaceSlug }: { workspaceSlug: string }) => (
    <div data-testid="terminal-session">{workspaceSlug}</div>
  ),
}));

describe("TerminalWorkspace", () => {
  it("starts with one real terminal tab and no decorative window controls", () => {
    render(<TerminalWorkspace workspaceSlug="loregarden" visible onEmpty={jest.fn()} />);

    expect(screen.getByRole("tab", { name: /Terminal \d+/ })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByTestId("terminal-session")).toHaveTextContent("loregarden");
    expect(document.querySelector(".terminal-lights")).not.toBeInTheDocument();
  });

  it("opens independent terminals in new tabs without unmounting the first", () => {
    render(<TerminalWorkspace workspaceSlug="loregarden" visible onEmpty={jest.fn()} />);
    const firstTab = screen.getByRole("tab", { name: /Terminal \d+/ });

    fireEvent.click(screen.getByRole("button", { name: "New terminal" }));

    expect(screen.getAllByRole("tab")).toHaveLength(2);
    expect(screen.getAllByTestId("terminal-session")).toHaveLength(2);
    expect(firstTab).toHaveAttribute("aria-selected", "false");

    fireEvent.click(firstTab);

    expect(firstTab).toHaveAttribute("aria-selected", "true");
    expect(screen.getAllByTestId("terminal-session")).toHaveLength(2);
  });

  it("splits the active tab into independent shell panes", () => {
    render(<TerminalWorkspace workspaceSlug="loregarden" visible onEmpty={jest.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Split terminal" }));

    expect(screen.getAllByTestId("terminal-session")).toHaveLength(2);
    expect(screen.getByLabelText("2 panes")).toBeInTheDocument();
  });

  it("closes only the selected split pane", () => {
    render(<TerminalWorkspace workspaceSlug="loregarden" visible onEmpty={jest.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Split terminal" }));

    const closePaneButtons = screen.getAllByRole("button", { name: /Close pane/ });
    fireEvent.click(closePaneButtons[1]);

    expect(screen.getAllByTestId("terminal-session")).toHaveLength(1);
    expect(screen.queryByLabelText("2 panes")).not.toBeInTheDocument();
  });

  it("closes the dock when its final terminal is explicitly closed", () => {
    const onEmpty = jest.fn();
    render(<TerminalWorkspace workspaceSlug="loregarden" visible onEmpty={onEmpty} />);

    fireEvent.click(screen.getByRole("button", { name: /Close Terminal/ }));

    expect(onEmpty).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("terminal-session")).not.toBeInTheDocument();
  });

  it("starts a new shell when an empty workspace is shown again", () => {
    const onEmpty = jest.fn();
    const { rerender } = render(
      <TerminalWorkspace workspaceSlug="loregarden" visible onEmpty={onEmpty} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Close Terminal/ }));

    rerender(<TerminalWorkspace workspaceSlug="loregarden" visible={false} onEmpty={onEmpty} />);
    expect(screen.queryByTestId("terminal-session")).not.toBeInTheDocument();

    rerender(<TerminalWorkspace workspaceSlug="loregarden" visible onEmpty={onEmpty} />);
    expect(screen.getByTestId("terminal-session")).toBeInTheDocument();
  });
});
