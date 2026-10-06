import { fireEvent, screen } from "@testing-library/react";
// Rendered through the shared helper because these surfaces now carry
// `AddToTabMenu`, which lists the operator's tabs and therefore needs a
// QueryClient. Aliased to `render` so every existing call site reads the
// same; the provider is the only thing that changed.
import { renderWithRouter as render } from "../../test/renderWithRouter";

import { stubScrolling } from "../../test/scrollStubs";
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

  it("carries the tab strip — and only the strip — to a new tab past its edge", () => {
    // The strip shows 0..300 sideways; a newly active tab lands at 320..420.
    const scrolling = stubScrolling(".terminal-tabs { overflow-x: auto; }", (element) => {
      if (element.matches(".terminal-tabs")) return { left: 0, right: 300 };
      if (element.matches(".terminal-tab.is-active")) return { left: 320, right: 420 };
      return null;
    });
    try {
      render(<TerminalWorkspace workspaceSlug="loregarden" visible onEmpty={jest.fn()} />);
      scrolling.scrollTo.mockClear();

      fireEvent.click(screen.getByRole("button", { name: "New terminal" }));

      expect(scrolling.scrollTo.mock.instances).toEqual([
        screen.getByRole("tablist", { name: "Terminal tabs" }),
      ]);
      expect(scrolling.scrollTo).toHaveBeenCalledWith({ left: 120, behavior: "auto" });
      expect(scrolling.scrollIntoView).not.toHaveBeenCalled();
    } finally {
      scrolling.restore();
    }
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
