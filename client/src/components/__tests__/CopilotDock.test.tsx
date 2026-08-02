import { act, fireEvent, render, screen } from "@testing-library/react";

import { CopilotDock } from "../CopilotDock";
import { useActiveChatSession } from "../../hooks/useActiveChatSession";
import { useTerminalTarget } from "../../hooks/useTerminalTarget";
import { useUiStore } from "../../state/uiStore";

jest.mock("../../hooks/useActiveChatSession");
jest.mock("../../hooks/useTerminalTarget");
// The workspace opens real websockets and paints through canvases; neither
// exists here, and these tests cover the dock lifecycle around it.
jest.mock("../TerminalWorkspace", () => ({
  TerminalWorkspace: ({
    workspaceSlug,
    visible,
  }: {
    workspaceSlug: string;
    visible: boolean;
  }) => (
    <div data-testid="terminal-panel" data-visible={visible}>
      {workspaceSlug}
    </div>
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
    activeTurnId: null,
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

it("draws nothing while collapsed — the action bar is the collapsed state", () => {
  // Two bars stacked was the pre-v6 shape. The dock is now only the panels.
  mockResolver.mockReturnValue(bind({ session: session(), label: "Ticket triage" }));

  const { container } = render(<CopilotDock />);

  expect(container).toBeEmptyDOMElement();
});

it("shows the bound conversation's turns once opened", () => {
  mockResolver.mockReturnValue(bind({ session: session(), label: "Ticket triage" }));
  useUiStore.setState({ copilotOpen: true });

  render(<CopilotDock />);

  expect(screen.getByText("No messages yet.")).toBeInTheDocument();
});

it("stays empty when no screen owns a conversation and no shell was asked for", () => {
  mockResolver.mockReturnValue(bind({ session: null, label: "" }));
  useUiStore.setState({ copilotOpen: true });

  const { container } = render(<CopilotDock />);

  expect(container).toBeEmptyDOMElement();
});

it("sends an opener through the bound session rather than filling a composer", () => {
  // The composer moved to the action bar, so an opener that only set draft text
  // would land in a box the operator is not looking at.
  const bound = session();
  mockResolver.mockReturnValue(bind({ session: bound, label: "Ticket triage" }));
  useUiStore.setState({ copilotOpen: true });

  render(<CopilotDock />);
  fireEvent.click(screen.getByRole("button", { name: "What is blocking this ticket?" }));

  expect(bound.send).toHaveBeenCalledWith("What is blocking this ticket?", {
    autoApprove: false,
  });
});

it("keeps the openers beside the turns, not between them and the composer", () => {
  mockResolver.mockReturnValue(bind({ session: session(), label: "Ticket triage" }));
  useUiStore.setState({ copilotOpen: true });

  const { container } = render(<CopilotDock />);

  const rail = container.querySelector(".copilot-dock-rail");
  expect(rail).not.toBeNull();
  expect(rail).toContainElement(
    screen.getByRole("button", { name: "What is blocking this ticket?" }),
  );
});

it("offers no openers once the thread has turns", () => {
  mockResolver.mockReturnValue(
    bind({
      session: session({ messages: [{ role: "user", content: "hi" }] }),
      label: "Ticket triage",
    }),
  );
  useUiStore.setState({ copilotOpen: true });

  render(<CopilotDock />);

  expect(screen.queryByText("Try asking")).not.toBeInTheDocument();
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
  });

  it("opens a shell in the workspace the screen is showing", () => {
    // The control lives in the action bar now; the dock follows the store.
    openDock();
    useUiStore.setState({ copilotOpen: true, terminalOpen: true });

    render(<CopilotDock />);

    expect(screen.getByTestId("terminal-panel")).toHaveTextContent("loregarden");
  });

  it("keeps the chat when the terminal is open", () => {
    // Side by side, not instead of — the dock is still the way into the chat.
    openDock();
    useUiStore.setState({ copilotOpen: true, terminalOpen: true });

    render(<CopilotDock />);

    expect(screen.getByTestId("terminal-panel")).toBeInTheDocument();
    expect(screen.getByText("No messages yet.")).toBeInTheDocument();
  });

  it("will not open a shell with nowhere to run it", () => {
    // A shell needs a cwd more than the header needs a label, and "all
    // workspaces" names no directory.
    openDock();
    mockTerminal.mockReturnValue({ workspaceSlug: "", agent: "" });
    useUiStore.setState({ copilotOpen: true, terminalOpen: true });

    render(<CopilotDock />);

    expect(screen.queryByTestId("terminal-panel")).not.toBeInTheDocument();
  });

  it("keeps the shell when the chat collapses", () => {
    // Collapsing the conversation says nothing about wanting the shell gone.
    // Reaping it here is what made "just a terminal" impossible.
    openDock();
    useUiStore.setState({ copilotOpen: true, terminalOpen: true });
    const { rerender } = render(<CopilotDock />);
    expect(screen.getByTestId("terminal-panel")).toBeInTheDocument();

    act(() => useUiStore.setState({ copilotOpen: false }));
    rerender(<CopilotDock />);

    expect(screen.getByTestId("terminal-panel")).toBeInTheDocument();
    expect(screen.queryByText("No messages yet.")).not.toBeInTheDocument();
  });

  it("keeps the shell when the terminal itself is closed", () => {
    // Closing the omnibar is hide, not kill: cwd, jobs, and scrollback have to
    // survive a toggle. The panel stays mounted (kept) until the screen names
    // a different workspace.
    openDock();
    useUiStore.setState({ copilotOpen: true, terminalOpen: true });
    const { rerender, container } = render(<CopilotDock />);
    expect(screen.getByTestId("terminal-panel")).toBeInTheDocument();

    act(() => useUiStore.setState({ terminalOpen: false }));
    rerender(<CopilotDock />);

    expect(screen.getByTestId("terminal-panel")).toBeInTheDocument();
    expect(container.querySelector(".copilot-dock-terminal.is-kept")).not.toBeNull();
  });

  it("reaps the kept shell when the screen names a different workspace", () => {
    openDock();
    useUiStore.setState({ copilotOpen: false, terminalOpen: true });
    const { rerender } = render(<CopilotDock />);
    expect(screen.getByTestId("terminal-panel")).toHaveTextContent("loregarden");

    act(() => {
      useUiStore.setState({ terminalOpen: false });
      mockTerminal.mockReturnValue({ workspaceSlug: "other-repo", agent: "" });
    });
    rerender(<CopilotDock />);

    expect(screen.queryByTestId("terminal-panel")).not.toBeInTheDocument();
  });

  it("hosts a shell on a screen with no conversation", () => {
    // A shell is scoped to the workspace, not to a chat. Returning early when
    // no session exists is what made the action bar's button do nothing on the
    // console screen.
    mockResolver.mockReturnValue(bind({ session: null, label: "" }));
    useUiStore.setState({ copilotOpen: false, terminalOpen: true });

    render(<CopilotDock />);

    expect(screen.getByTestId("terminal-panel")).toHaveTextContent("loregarden");
  });
});
