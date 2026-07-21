import { fireEvent, render, screen } from "@testing-library/react";

import { CopilotDock } from "../CopilotDock";
import { useActiveChatSession } from "../../hooks/useActiveChatSession";
import { useUiStore } from "../../state/uiStore";

jest.mock("../../hooks/useActiveChatSession");
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
  useUiStore.setState({ copilotOpen: false, copilotHeight: 340 });
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
