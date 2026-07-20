import { fireEvent, render, screen } from "@testing-library/react";

import { CopilotDock } from "../CopilotDock";
import { useActiveChatSession } from "../../hooks/useActiveChatSession";
import { useUiStore } from "../../state/uiStore";

jest.mock("../../hooks/useActiveChatSession");

const mockResolver = useActiveChatSession as jest.MockedFunction<typeof useActiveChatSession>;

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
  mockResolver.mockReturnValue({ session: null, label: "" });

  render(<CopilotDock />);
  expect(screen.getByText(/open a ticket or a branch/i)).toBeInTheDocument();
});

it("names the bound conversation while collapsed", () => {
  mockResolver.mockReturnValue({ session: session(), label: "Ticket triage" });

  render(<CopilotDock />);
  expect(screen.getByText("Ticket triage")).toBeInTheDocument();
  // Collapsed: the bar only. No composer until it is opened.
  expect(screen.queryByPlaceholderText(/message about this ticket/i)).not.toBeInTheDocument();
});

it("opens and closes from the bar", () => {
  mockResolver.mockReturnValue({ session: session(), label: "Ticket triage" });

  render(<CopilotDock />);
  fireEvent.click(screen.getByRole("button", { name: /expand copilot/i }));
  expect(screen.getByPlaceholderText(/message about this ticket/i)).toBeInTheDocument();
  expect(useUiStore.getState().copilotOpen).toBe(true);

  fireEvent.click(screen.getByRole("button", { name: /collapse copilot/i }));
  expect(useUiStore.getState().copilotOpen).toBe(false);
});

it("sends through the bound session, not its own transport", () => {
  const bound = session();
  mockResolver.mockReturnValue({ session: bound, label: "Ticket triage" });
  useUiStore.setState({ copilotOpen: true });

  render(<CopilotDock />);
  const input = screen.getByPlaceholderText(/message about this ticket/i);
  fireEvent.change(input, { target: { value: "why did verify reject?" } });
  fireEvent.click(screen.getByRole("button", { name: /^send$/i }));

  expect(bound.send).toHaveBeenCalledWith("why did verify reject?", { autoApprove: false });
});

it("shows a send failure without claiming the chat is gone", () => {
  mockResolver.mockReturnValue({
    session: session({ error: "Failed to send message" }),
    label: "Ticket triage",
  });
  useUiStore.setState({ copilotOpen: true });

  render(<CopilotDock />);
  expect(screen.getByText("Failed to send message")).toBeInTheDocument();
  expect(screen.queryByText(/conversation unavailable/i)).not.toBeInTheDocument();
});

it("distinguishes an unavailable conversation from a failed send", () => {
  // The distinction U2a's descriptor grew a field for.
  mockResolver.mockReturnValue({
    session: session({ loadError: true }),
    label: "Ticket triage",
  });

  render(<CopilotDock />);
  expect(screen.getByText(/conversation unavailable/i)).toBeInTheDocument();
});
