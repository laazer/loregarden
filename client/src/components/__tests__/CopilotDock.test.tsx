import { fireEvent, render, screen } from "@testing-library/react";

import { CopilotDock } from "../CopilotDock";
import { useActiveChatSession } from "../../hooks/useActiveChatSession";
import { useTerminalTarget } from "../../hooks/useTerminalTarget";
import { useUiStore } from "../../state/uiStore";

jest.mock("../../hooks/useActiveChatSession");
jest.mock("../../hooks/useTerminalTarget");
// The panel opens a real websocket and paints through a canvas; neither exists
// here, and what these tests are about is the dock's layout around it.
jest.mock("../TerminalPanel", () => ({
  TerminalPanel: ({ workspaceSlug }: { workspaceSlug: string }) => (
    <div data-testid="terminal-panel">{workspaceSlug}</div>
  ),
}));
jest.mock("../../hooks/useApprovalResolution", () => ({
  useApprovalResolution: () => ({
    mutate: jest.fn(),
    isPending: false,
    isError: false,
    error: null,
    variables: undefined,
  }),
}));

const mockResolver = useActiveChatSession as jest.MockedFunction<typeof useActiveChatSession>;
const mockTerminal = useTerminalTarget as jest.MockedFunction<typeof useTerminalTarget>;

function bind(overrides: Partial<ReturnType<typeof useActiveChatSession>>) {
  return {
    session: null,
    label: "",
    ticketId: "t1",
    pendingApprovals: [],
    ...overrides,
  } as ReturnType<typeof useActiveChatSession>;
}

function session(overrides = {}) {
  return {
    kind: "ticket-triage" as const,
    id: "t1",
    messages: [],
    isBusy: false,
    isLoading: false,
    error: null,
    loadError: false,
    send: jest.fn().mockResolvedValue({}),
    ...overrides,
  };
}

beforeEach(() => {
  jest.clearAllMocks();
  useUiStore.setState({ copilotOpen: false, copilotHeight: 340, terminalOpen: false });
  mockTerminal.mockReturnValue({ workspaceSlug: "loregarden", agent: "implementer" });
});

it("says what to do when no screen owns a conversation", () => {
  mockResolver.mockReturnValue(bind({ session: null, label: "" }));

  render(<CopilotDock />);
  expect(screen.getByText(/open a ticket or a branch/i)).toBeInTheDocument();
});

it("names the bound conversation while collapsed", () => {
  mockResolver.mockReturnValue(bind({ session: session(), label: "Ticket triage" }));

  render(<CopilotDock />);
  expect(screen.getByText("Ticket triage")).toBeInTheDocument();
  // Collapsed: the bar only. No composer until it is opened.
  expect(screen.queryByPlaceholderText(/message about this ticket/i)).not.toBeInTheDocument();
});

it("opens and closes from the bar", () => {
  mockResolver.mockReturnValue(bind({ session: session(), label: "Ticket triage" }));

  render(<CopilotDock />);
  fireEvent.click(screen.getByRole("button", { name: /expand copilot/i }));
  expect(screen.getByPlaceholderText(/message about this ticket/i)).toBeInTheDocument();
  expect(useUiStore.getState().copilotOpen).toBe(true);

  fireEvent.click(screen.getByRole("button", { name: /collapse copilot/i }));
  expect(useUiStore.getState().copilotOpen).toBe(false);
});

it("sends through the bound session, not its own transport", () => {
  const bound = session();
  mockResolver.mockReturnValue(bind({ session: bound, label: "Ticket triage" }));
  useUiStore.setState({ copilotOpen: true });

  render(<CopilotDock />);
  const input = screen.getByPlaceholderText(/message about this ticket/i);
  fireEvent.change(input, { target: { value: "why did verify reject?" } });
  fireEvent.click(screen.getByRole("button", { name: /^send$/i }));

  expect(bound.send).toHaveBeenCalledWith("why did verify reject?", { autoApprove: false });
});

it("shows a send failure without claiming the chat is gone", () => {
  mockResolver.mockReturnValue(bind({
    session: session({ error: "Failed to send message" }),
    label: "Ticket triage",
  }));
  useUiStore.setState({ copilotOpen: true });

  render(<CopilotDock />);
  expect(screen.getByText("Failed to send message")).toBeInTheDocument();
  expect(screen.queryByText(/conversation unavailable/i)).not.toBeInTheDocument();
});

it("distinguishes an unavailable conversation from a failed send", () => {
  // The distinction U2a's descriptor grew a field for.
  mockResolver.mockReturnValue(bind({
    session: session({ loadError: true }),
    label: "Ticket triage",
  }));

  render(<CopilotDock />);
  expect(screen.getByText(/conversation unavailable/i)).toBeInTheDocument();
});

it("surfaces a decision waiting on the operator while collapsed", () => {
  // An agent question becomes an approval, never a chat message. A dock that
  // showed only messages would sit on "working…" with nothing to answer.
  mockResolver.mockReturnValue(
    bind({
      session: session(),
      label: "Ticket triage",
      pendingApprovals: [{ id: "a1", title: "Which shape?", kind: "cli_question" }] as never,
    }),
  );

  render(<CopilotDock />);
  expect(screen.getByText(/1 waiting on you/i)).toBeInTheDocument();
});

it("prefers the waiting decision over the busy indicator", () => {
  // Both are true while an agent waits on an answer; only one is actionable.
  mockResolver.mockReturnValue(
    bind({
      session: session({ isBusy: true }),
      label: "Ticket triage",
      pendingApprovals: [{ id: "a1", title: "Which shape?", kind: "cli_question" }] as never,
    }),
  );

  render(<CopilotDock />);
  expect(screen.getByText(/waiting on you/i)).toBeInTheDocument();
  expect(screen.queryByText(/working…/)).not.toBeInTheDocument();
});

describe("the terminal pane", () => {
  const openDock = () =>
    mockResolver.mockReturnValue(bind({ session: session(), label: "Ticket triage" }));

  it("spawns no shell until someone asks for one", () => {
    // Mounting the panel starts a real login shell. Opening the chat is not
    // asking for one, and a process nobody requested is the wrong default.
    openDock();
    useUiStore.setState({ copilotOpen: true, terminalOpen: false });

    render(<CopilotDock />);

    expect(screen.queryByTestId("terminal-panel")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Terminal" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("opens a shell in the workspace the screen is showing", () => {
    openDock();
    useUiStore.setState({ copilotOpen: true, terminalOpen: false });
    render(<CopilotDock />);

    fireEvent.click(screen.getByRole("button", { name: "Terminal" }));

    expect(screen.getByTestId("terminal-panel")).toHaveTextContent("loregarden");
  });

  it("keeps the chat when the terminal is open", () => {
    // Side by side, not instead of — the dock is still the way into the chat.
    openDock();
    useUiStore.setState({ copilotOpen: true, terminalOpen: true });

    render(<CopilotDock />);

    expect(screen.getByTestId("terminal-panel")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Message about this ticket…")).toBeInTheDocument();
  });

  it("will not open a shell with nowhere to run it", () => {
    // A shell needs a cwd more than the header needs a label, and "all
    // workspaces" names no directory.
    openDock();
    mockTerminal.mockReturnValue({ workspaceSlug: "", agent: "" });
    useUiStore.setState({ copilotOpen: true, terminalOpen: true });

    render(<CopilotDock />);

    expect(screen.queryByTestId("terminal-panel")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Terminal" })).toBeDisabled();
  });

  it("reaps the shell when the dock collapses", () => {
    // The panel unmounts, which closes the socket and reaps the shell. Leaving
    // it mounted behind a collapsed dock would keep a login shell per session.
    openDock();
    useUiStore.setState({ copilotOpen: true, terminalOpen: true });
    render(<CopilotDock />);
    expect(screen.getByTestId("terminal-panel")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Collapse copilot" }));

    expect(screen.queryByTestId("terminal-panel")).not.toBeInTheDocument();
  });

  it("remembers the terminal was open across a remount", () => {
    openDock();
    useUiStore.setState({ copilotOpen: true, terminalOpen: false });
    const { unmount } = render(<CopilotDock />);
    fireEvent.click(screen.getByRole("button", { name: "Terminal" }));
    unmount();

    render(<CopilotDock />);

    expect(screen.getByTestId("terminal-panel")).toBeInTheDocument();
  });
});
