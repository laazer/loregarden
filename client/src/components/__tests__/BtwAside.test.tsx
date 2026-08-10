import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactElement } from "react";

import * as apiClient from "../../api/client";
import { useActiveChatSession } from "../../hooks/useActiveChatSession";
import { useTerminalTarget } from "../../hooks/useTerminalTarget";
import { useUiStore } from "../../state/uiStore";
import { AppActionBar } from "../AppActionBar";
import { BtwPrimitive } from "../chat/primitives/BtwPrimitive";
import type { BtwPart } from "../chat/primitives/types";

jest.mock("../../hooks/useActiveChatSession");
jest.mock("../../hooks/useTerminalTarget");
jest.mock("../../api/client", () => jest.requireActual("../../test/apiClientMock"));

const mockResolver = useActiveChatSession as jest.MockedFunction<typeof useActiveChatSession>;
const mockTerminal = useTerminalTarget as jest.MockedFunction<typeof useTerminalTarget>;
const mockApi = apiClient.api as unknown as {
  ticket: jest.Mock;
  ticketAsides: jest.Mock;
  askAside: jest.Mock;
  escalateAside: jest.Mock;
};

function renderWithClient(ui: ReactElement) {
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
    archive: null,
    composedOnScreen: false,
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

function exchange(overrides = {}) {
  return {
    id: "bx1",
    ticket_id: "t1",
    question: "why the subprocess path?",
    answer: "The log shows it shelling out to git.",
    status: "answered" as const,
    error: "",
    escalated: false,
    escalation_refusal: "",
    observed_run_id: "run-1",
    observed_agent_id: "planner",
    observed_stage_key: "plan",
    observed_run_active: true,
    created_at: "2026-08-03T10:00:00",
    answered_at: "2026-08-03T10:00:20",
    ...overrides,
  };
}

function part(overrides: Partial<BtwPart> = {}): BtwPart {
  return {
    primitive: "btw",
    exchange_id: "bx1",
    ticket_id: "t1",
    question: "why the subprocess path?",
    answer: "The log shows it shelling out to git.",
    observed_run_id: "run-1",
    observed_agent_id: "planner",
    observed_stage_key: "plan",
    escalated: false,
    ...overrides,
  };
}

beforeEach(() => {
  jest.clearAllMocks();
  useUiStore.setState({ copilotOpen: false, terminalOpen: false, utilityDockEdge: "bottom" });
  mockTerminal.mockReturnValue({ workspaceSlug: "loregarden", agent: "implementer" });
  mockTicket("idle");
  mockApi.ticketAsides.mockResolvedValue({ exchanges: [] });
});

/** `activity` is the axis that says whether anything is executing on the ticket
 *  — the signal `find_active_run` refuses ordinary chat turns on. */
function mockTicket(activity: "running" | "awaiting" | "queued" | "idle") {
  mockApi.ticket.mockResolvedValue({
    id: "t1",
    external_id: "LG-1",
    activity,
    artifacts: { logs: [{ time: "10:00:00", tag: "ERR", text: "pytest exited 1" }], live: null },
  });
}

describe("the composer while a run is working", () => {
  it("routes to the aside channel while a stage run works", async () => {
    // The case the channel exists for. `session.isBusy` is false here — it is
    // derived from triage turns only — so keying on it would have missed this.
    const bound = session({ isBusy: false });
    mockResolver.mockReturnValue(bind({ session: bound, label: "Ticket triage" }));
    mockTicket("running");
    mockApi.askAside.mockResolvedValue(exchange({ status: "pending", answer: "" }));

    renderWithClient(<AppActionBar />);
    const input = await screen.findByLabelText(/ask an aside/i);
    fireEvent.change(input, { target: { value: "what are you doing?" } });
    fireEvent.click(screen.getByRole("button", { name: /ask aside/i }));

    await waitFor(() => expect(mockApi.askAside).toHaveBeenCalledWith("t1", "what are you doing?"));
    // The chat is left alone — sending there is what the server refuses.
    expect(bound.send).not.toHaveBeenCalled();
  });

  it("routes to the aside channel while a run waits on an approval", async () => {
    mockResolver.mockReturnValue(bind({ session: session(), label: "Ticket triage" }));
    mockTicket("awaiting");

    renderWithClient(<AppActionBar />);

    expect(await screen.findByLabelText(/ask an aside/i)).toBeTruthy();
  });

  it("covers the gap between a chat turn being sent and activity catching up", async () => {
    // `isBusy` flips on the POST; `activity` waits for the next poll. A message
    // sent into that gap would otherwise reach the chat and come back a 409.
    mockResolver.mockReturnValue(
      bind({ session: session({ isBusy: true }), label: "Ticket triage" }),
    );
    mockTicket("idle");

    renderWithClient(<AppActionBar />);

    expect(await screen.findByLabelText(/ask an aside/i)).toBeTruthy();
  });

  it("sends through the chat as usual when nothing is running", async () => {
    const bound = session();
    mockResolver.mockReturnValue(bind({ session: bound, label: "Ticket triage" }));
    mockTicket("idle");

    renderWithClient(<AppActionBar />);
    const input = await screen.findByLabelText("Message this conversation");
    fireEvent.change(input, { target: { value: "what is blocking this?" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(bound.send).toHaveBeenCalledWith("what is blocking this?", {
      autoApprove: false,
      skill: "",
    });
    expect(mockApi.askAside).not.toHaveBeenCalled();
  });

  it("leaves a queued ticket on the ordinary chat, which still accepts it", async () => {
    // `find_active_run` counts RUNNING and AWAITING_PERMISSION only, so a queued
    // ticket takes a normal message — diverting it would be a downgrade.
    const bound = session();
    mockResolver.mockReturnValue(bind({ session: bound, label: "Ticket triage" }));
    mockTicket("queued");

    renderWithClient(<AppActionBar />);
    const input = await screen.findByLabelText("Message this conversation");
    fireEvent.change(input, { target: { value: "why is this queued?" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(bound.send).toHaveBeenCalled();
    expect(mockApi.askAside).not.toHaveBeenCalled();
  });

  it("drops the log-excerpt toggle, which the observer is handed server-side", async () => {
    mockResolver.mockReturnValue(bind({ session: session(), label: "Ticket triage" }));
    mockTicket("running");

    renderWithClient(<AppActionBar />);

    await screen.findByLabelText(/ask an aside/i);
    expect(screen.queryByRole("button", { name: "Run logs" })).toBeNull();
    // Nor auto-approve: an aside runs read-only, so there is nothing to approve.
    expect(screen.queryByRole("button", { name: "Auto-approve" })).toBeNull();
  });
});

describe("the aside card", () => {
  it("attributes the answer to the observer, not to the agent it is about", async () => {
    mockApi.ticketAsides.mockResolvedValue({ exchanges: [exchange()] });

    renderWithClient(<BtwPrimitive part={part()} />);

    expect(await screen.findByText(/read from planner · plan's log by baxter/i)).toBeTruthy();
    expect(screen.getByText(/not answered by that agent/i)).toBeTruthy();
  });

  it("offers the escalation only when the run can actually take it", async () => {
    mockApi.ticketAsides.mockResolvedValue({
      exchanges: [exchange({ escalation_refusal: "" })],
    });

    renderWithClient(<BtwPrimitive part={part()} />);

    const button = await screen.findByRole("button", { name: /ask the running agent/i });
    // The cost is stated on the control itself, not left to be discovered.
    expect(button.getAttribute("title")).toMatch(/can change what it does next/i);
  });

  it("states the reason instead of offering a button that would be refused", async () => {
    mockApi.ticketAsides.mockResolvedValue({
      exchanges: [
        exchange({ escalation_refusal: "The backend_implementer agent runs on cursor…" }),
      ],
    });

    renderWithClient(<BtwPrimitive part={part()} />);

    expect(await screen.findByText(/runs on cursor/i)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /ask the running agent/i })).toBeNull();
  });

  it("escalates through the ticket's own endpoint", async () => {
    mockApi.ticketAsides.mockResolvedValue({ exchanges: [exchange()] });
    mockApi.escalateAside.mockResolvedValue(exchange({ escalated: true }));

    renderWithClient(<BtwPrimitive part={part()} />);
    fireEvent.click(await screen.findByRole("button", { name: /ask the running agent/i }));

    await waitFor(() => expect(mockApi.escalateAside).toHaveBeenCalledWith("t1", "bx1"));
  });

  it("says where the escalated answer went once it has been asked", async () => {
    mockApi.ticketAsides.mockResolvedValue({ exchanges: [exchange({ escalated: true })] });

    renderWithClient(<BtwPrimitive part={part({ escalated: true })} />);

    expect(await screen.findByText(/its reply is in that run's log/i)).toBeTruthy();
  });

  it("renders a preview from the part alone, with no lookup and no escalation", async () => {
    // The gallery's exchange id refers to nothing: a fetch would hang the card
    // on "checking…" and a button would post to an aside that does not exist.
    renderWithClient(<BtwPrimitive part={part({ interactive: false })} />);

    expect(await screen.findByText(/preview — this card is not bound/i)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /ask the running agent/i })).toBeNull();
    expect(mockApi.ticketAsides).not.toHaveBeenCalled();
    // The answer still renders — a preview that showed nothing would be useless.
    expect(screen.getByText(/shelling out to git/i)).toBeTruthy();
  });

  it("shows a pending aside as still being read rather than as an empty answer", async () => {
    mockApi.ticketAsides.mockResolvedValue({
      exchanges: [exchange({ status: "pending", answer: "" })],
    });

    renderWithClient(<BtwPrimitive part={part({ answer: "" })} />);

    expect(await screen.findByText(/reading the run's log/i)).toBeTruthy();
  });
});
