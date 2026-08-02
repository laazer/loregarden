import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactElement } from "react";

import { AppActionBar } from "../AppActionBar";
import * as apiClient from "../../api/client";
import { useActiveChatSession } from "../../hooks/useActiveChatSession";
import { useTerminalTarget } from "../../hooks/useTerminalTarget";
import { useUiStore } from "../../state/uiStore";

jest.mock("../../hooks/useActiveChatSession");
jest.mock("../../hooks/useTerminalTarget");
jest.mock("../../api/client", () =>
  jest.requireActual("../../test/apiClientMock"),
);

const mockResolver = useActiveChatSession as jest.MockedFunction<
  typeof useActiveChatSession
>;
const mockTerminal = useTerminalTarget as jest.MockedFunction<
  typeof useTerminalTarget
>;
const mockApi = apiClient.api as unknown as { ticket: jest.Mock };

function renderBar(ui: ReactElement = <AppActionBar />) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

function bind(overrides: Partial<ReturnType<typeof useActiveChatSession>>) {
  return {
    session: null,
    label: "",
    ticketId: "t1",
    pendingApprovals: [],
    branch: null,
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
  useUiStore.setState({
    copilotOpen: false,
    terminalOpen: false,
    utilityDockEdge: "bottom",
  });
  mockTerminal.mockReturnValue({
    workspaceSlug: "loregarden",
    agent: "implementer",
  });
  mockApi.ticket.mockResolvedValue({
    id: "t1",
    external_id: "LG-1",
    artifacts: {
      logs: [{ time: "10:00:00", tag: "ERR", text: "pytest exited 1" }],
      live: null,
    },
  });
});

it("sends through the bound session, not its own transport", () => {
  const bound = session();
  mockResolver.mockReturnValue(
    bind({ session: bound, label: "Ticket triage" }),
  );

  renderBar();
  const input = screen.getByPlaceholderText("Message about this ticket…");
  fireEvent.change(input, { target: { value: "why did verify reject?" } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));

  expect(bound.send).toHaveBeenCalledWith("why did verify reject?", {
    autoApprove: false,
  });
});

it("opens the thread on send so the reply is not answered behind a collapsed dock", () => {
  mockResolver.mockReturnValue(
    bind({ session: session(), label: "Ticket triage" }),
  );

  renderBar();
  fireEvent.change(screen.getByPlaceholderText("Message about this ticket…"), {
    target: { value: "status?" },
  });
  fireEvent.keyDown(screen.getByPlaceholderText("Message about this ticket…"), {
    key: "Enter",
  });

  expect(useUiStore.getState().copilotOpen).toBe(true);
});

it("carries the auto-approve choice into the turn it was set for", () => {
  const bound = session();
  mockResolver.mockReturnValue(
    bind({ session: bound, label: "Ticket triage" }),
  );

  renderBar();
  fireEvent.click(screen.getByRole("button", { name: "Auto-approve" }));
  fireEvent.change(screen.getByPlaceholderText("Message about this ticket…"), {
    target: { value: "run the tests" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));

  expect(bound.send).toHaveBeenCalledWith("run the tests", {
    autoApprove: true,
  });
});

it("sends a quick opener while collapsed", () => {
  const bound = session();
  mockResolver.mockReturnValue(
    bind({ session: bound, label: "Ticket triage" }),
  );

  renderBar();
  fireEvent.click(
    screen.getByRole("button", { name: "What is blocking this ticket?" }),
  );

  expect(bound.send).toHaveBeenCalledWith("What is blocking this ticket?", {
    autoApprove: false,
  });
});

it("offers shipping the work when the conversation sits on its own branch", () => {
  const bound = session();
  mockResolver.mockReturnValue(
    bind({ session: bound, label: "Ticket triage", branch: "feat/thing" }),
  );

  renderBar();
  fireEvent.click(screen.getByRole("button", { name: "Commit, push, and open a PR" }));

  expect(bound.send).toHaveBeenCalledWith("Commit, push, and open a PR", {
    autoApprove: false,
  });
});

it("withholds shipping on the default branch — there is nothing to open a PR against", () => {
  mockResolver.mockReturnValue(
    bind({ session: session({ kind: "branch-triage" }), label: "Branch · main", branch: "main" }),
  );

  renderBar();

  expect(
    screen.queryByRole("button", { name: "Commit, push, and open a PR" }),
  ).not.toBeInTheDocument();
});

it("drops the openers once the thread is open, leaving the composer the width", () => {
  mockResolver.mockReturnValue(
    bind({ session: session(), label: "Ticket triage" }),
  );
  useUiStore.setState({ copilotOpen: true });

  renderBar();

  expect(
    screen.queryByRole("button", { name: "What is blocking this ticket?" }),
  ).not.toBeInTheDocument();
});

it("names the bound conversation while collapsed", () => {
  mockResolver.mockReturnValue(
    bind({ session: session(), label: "Ticket triage" }),
  );

  renderBar();

  expect(screen.getByText(/On Ticket triage/)).toBeInTheDocument();
});

it("expands and collapses the thread from Baxter", () => {
  mockResolver.mockReturnValue(
    bind({ session: session(), label: "Ticket triage" }),
  );

  renderBar();
  fireEvent.click(screen.getByRole("button", { name: "Expand Baxter" }));
  expect(useUiStore.getState().copilotOpen).toBe(true);

  fireEvent.click(screen.getByRole("button", { name: "Collapse Baxter" }));
  expect(useUiStore.getState().copilotOpen).toBe(false);
});

it("surfaces a decision waiting on the operator", () => {
  // An agent question becomes an approval, never a chat message. A bar that
  // showed only messages would sit on "working…" with nothing to answer.
  mockResolver.mockReturnValue(
    bind({
      session: session(),
      label: "Ticket triage",
      pendingApprovals: [
        { id: "a1", title: "Which shape?", kind: "cli_question" },
      ] as never,
    }),
  );

  renderBar();

  expect(screen.getByText(/1 waiting on you/i)).toBeInTheDocument();
});

it("prefers the waiting decision over the busy indicator", () => {
  // Both are true while an agent waits on an answer; only one is actionable.
  mockResolver.mockReturnValue(
    bind({
      session: session({ isBusy: true }),
      label: "Ticket triage",
      pendingApprovals: [
        { id: "a1", title: "Which shape?", kind: "cli_question" },
      ] as never,
    }),
  );

  renderBar();

  expect(screen.getByText(/waiting on you/i)).toBeInTheDocument();
  expect(screen.queryByText(/working…/)).not.toBeInTheDocument();
});

it("shows a send failure without claiming the chat is gone", () => {
  mockResolver.mockReturnValue(
    bind({
      session: session({ error: "Failed to send message" }),
      label: "Ticket triage",
    }),
  );

  renderBar();

  expect(screen.getByText("Failed to send message")).toBeInTheDocument();
  expect(
    screen.queryByText(/conversation unavailable/i),
  ).not.toBeInTheDocument();
});

it("distinguishes an unavailable conversation from a failed send", () => {
  mockResolver.mockReturnValue(
    bind({ session: session({ loadError: true }), label: "Ticket triage" }),
  );

  renderBar();

  expect(screen.getByText(/conversation unavailable/i)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
});

it("says what to do when no screen owns a conversation", () => {
  mockResolver.mockReturnValue(bind({ session: null, label: "" }));

  renderBar();

  const input = screen.getByPlaceholderText(/open a ticket or a branch/i);
  expect(input).toBeDisabled();
  expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
  expect(
    screen.queryByRole("button", { name: "Auto-approve" }),
  ).not.toBeInTheDocument();
});

describe("the run-logs toggle", () => {
  it("sends the log tail with the question when asked to", async () => {
    // The excerpt the removed logs composer used to prepend — the question is
    // about output the agent cannot otherwise see.
    const bound = session();
    mockResolver.mockReturnValue(
      bind({ session: bound, label: "Ticket triage" }),
    );

    renderBar();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Run logs" })).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Run logs" }));
    fireEvent.change(
      screen.getByPlaceholderText("Message about this ticket…"),
      {
        target: { value: "why did this fail?" },
      },
    );
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(bound.send).toHaveBeenCalledWith(
      "Question about the run logs below:\n\n```\n10:00:00 ERR pytest exited 1\n```\n\nwhy did this fail?",
      { autoApprove: false },
    );
  });

  it("leaves the question alone while the toggle is off", () => {
    const bound = session();
    mockResolver.mockReturnValue(
      bind({ session: bound, label: "Ticket triage" }),
    );

    renderBar();
    fireEvent.change(
      screen.getByPlaceholderText("Message about this ticket…"),
      {
        target: { value: "why did this fail?" },
      },
    );
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(bound.send).toHaveBeenCalledWith("why did this fail?", {
      autoApprove: false,
    });
  });

  it("cannot be armed on a ticket with no run output", async () => {
    mockApi.ticket.mockResolvedValue({
      id: "t1",
      external_id: "LG-1",
      artifacts: { logs: [], live: null },
    });
    mockResolver.mockReturnValue(
      bind({ session: session(), label: "Ticket triage" }),
    );

    renderBar();

    await waitFor(() => expect(mockApi.ticket).toHaveBeenCalled());
    expect(screen.getByRole("button", { name: "Run logs" })).toBeDisabled();
  });

  it("stays off a branch conversation, which has no run log", () => {
    mockResolver.mockReturnValue(
      bind({
        session: session({ kind: "branch-triage" }),
        label: "Branch · feat/x",
        ticketId: null,
      }),
    );

    renderBar();

    expect(
      screen.queryByRole("button", { name: "Run logs" }),
    ).not.toBeInTheDocument();
  });
});

describe("the shell switch", () => {
  it("toggles the terminal for the workspace on screen", () => {
    mockResolver.mockReturnValue(bind({ session: null, label: "" }));

    renderBar();
    fireEvent.click(screen.getByRole("button", { name: "Terminal" }));

    expect(useUiStore.getState().terminalOpen).toBe(true);
  });

  it("will not offer a shell with nowhere to run it", () => {
    mockResolver.mockReturnValue(bind({ session: null, label: "" }));
    mockTerminal.mockReturnValue({ workspaceSlug: "", agent: "" });

    renderBar();

    expect(screen.getByRole("button", { name: "Terminal" })).toBeDisabled();
  });
});

it("snaps the dock to the other edge", () => {
  mockResolver.mockReturnValue(bind({ session: null, label: "" }));

  renderBar();
  fireEvent.click(
    screen.getByRole("button", { name: "Dock utility panel to the right" }),
  );

  expect(useUiStore.getState().utilityDockEdge).toBe("right");
});
