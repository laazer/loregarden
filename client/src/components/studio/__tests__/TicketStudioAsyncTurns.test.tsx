import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import type { TicketStudioSession } from "../../../api/types";
import { TRIAGE_AGENT_NAME } from "../../../lib/triageAgent";
import type { TicketStudioPanelProps } from "../TicketStudioPanel";
import { TicketStudioPanel } from "../TicketStudioPanel";

/**
 * Scoper turns run in the background, so the panel must take "is it working?"
 * from the server rather than from whichever mutation happens to be in flight
 * in this tab. Otherwise a reload mid-scope shows an idle panel that silently
 * changes under the operator minutes later.
 */

jest.mock("../../../api/client", () => {
  const originalClient = jest.requireActual("../../../api/client");
  return {
    ...originalClient,
    api: {
      ...originalClient.api,
      ticketStudioSessions: jest.fn(),
      ticketStudioSession: jest.fn(),
      studioAgents: jest.fn(),
      generateTicketStudioScope: jest.fn(),
    },
  };
});

const { api } = require("../../../api/client");

jest.mock("react-router-dom", () => ({
  ...jest.requireActual("react-router-dom"),
  useNavigate: () => jest.fn(),
}));

const SESSION_ID = "session-async-1";

function buildSession(overrides: Partial<TicketStudioSession> = {}): TicketStudioSession {
  return {
    id: SESSION_ID,
    workspace_slug: "loregarden",
    title: "Async scoping",
    brief: "A brief",
    parent_ticket_id: null,
    parent_ticket_title: "",
    status: "draft",
    run_status: "idle",
    active_turn_id: null,
    summary: "",
    clarifying_questions: [],
    clarifying_answers: [],
    clarifying_resolved: true,
    draft: [],
    messages: [
      {
        id: "m1",
        role: "user",
        content: "Generate the full ticket breakdown for this feature.",
        created_at: "2026-07-30T09:00:00Z",
      },
    ],
    runtime: {
      cli_adapter: "default",
      claude_model: "",
      cursor_model: "",
      lmstudio_base_url: "",
      lmstudio_model: "",
    },
    is_preview: false,
    imported_tickets: [],
    created_at: "2026-07-30T09:00:00Z",
    updated_at: "2026-07-30T09:00:00Z",
    ...overrides,
  };
}

function renderPanel(overrides: Partial<TicketStudioPanelProps> = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const props: TicketStudioPanelProps = {
    workspaceSlug: "loregarden",
    onClose: jest.fn(),
    ...overrides,
  };
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[`/studio/tickets/${SESSION_ID}`]}>
        <TicketStudioPanel {...props} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  api.studioAgents.mockResolvedValue([]);
});

describe("Ticket Studio async scoper turns", () => {
  it("shows the scoper working from the server's run_status alone", async () => {
    // No mutation was started in this tab — this is the reload case.
    const running = buildSession({ run_status: "running", active_turn_id: "turn-1" });
    api.ticketStudioSessions.mockResolvedValue([running]);
    api.ticketStudioSession.mockResolvedValue(running);

    renderPanel();

    expect(
      await screen.findByText(`${TRIAGE_AGENT_NAME} is thinking…`),
    ).toBeInTheDocument();
  });

  it("does not show it working once the turn has settled", async () => {
    const idle = buildSession({
      messages: [
        ...buildSession().messages,
        {
          id: "m2",
          role: "assistant",
          content: "Here is the breakdown.",
          display_content: "Here is the breakdown.",
          created_at: "2026-07-30T09:00:30Z",
        },
      ],
    });
    api.ticketStudioSessions.mockResolvedValue([idle]);
    api.ticketStudioSession.mockResolvedValue(idle);

    renderPanel();

    expect(await screen.findByText("Here is the breakdown.")).toBeInTheDocument();
    expect(screen.queryByText(`${TRIAGE_AGENT_NAME} is thinking…`)).not.toBeInTheDocument();
  });

  it("blocks a second turn while one is in flight", async () => {
    const running = buildSession({ run_status: "running", active_turn_id: "turn-1" });
    api.ticketStudioSessions.mockResolvedValue([running]);
    api.ticketStudioSession.mockResolvedValue(running);

    renderPanel();

    // The server rejects a concurrent turn with a 409; the panel should not
    // offer one in the first place.
    const generate = await screen.findByRole("button", { name: /generate ticket/i });
    await waitFor(() => expect(generate).toBeDisabled());
    expect(api.generateTicketStudioScope).not.toHaveBeenCalled();
  });
});
