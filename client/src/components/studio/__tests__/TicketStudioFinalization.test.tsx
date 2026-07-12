import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import type { TicketStudioPanelProps } from "../TicketStudioPanel";
import { TicketStudioPanel } from "../TicketStudioPanel";
import type { TicketStudioSession } from "../../../api/types";

/**
 * Integration test suite for post-finalization UX in Studio context.
 *
 * Ticket:   43-post-finalization-ux-and-navigation
 *
 * Tests the full flow: user completes hierarchy editing in Studio → clicks
 * "Create tickets" → POST /api/ticket-studio/sessions/:id/commit succeeds →
 * FinalizationConfirmation displays with counts → user can navigate to the
 * created hierarchy.
 *
 * Acceptance Criteria:
 *   - AC1: Success confirmation displayed after finalization
 *   - AC2: Clear indication of what was created (milestone/feature/capability/task counts)
 *   - AC3: Navigation to created hierarchy available
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
      commitTicketStudioSession: jest.fn(),
    },
  };
});

const { api } = require("../../../api/client");

const mockNavigate = jest.fn();
jest.mock("react-router-dom", () => ({
  ...jest.requireActual("react-router-dom"),
  useNavigate: () => mockNavigate,
}));

const SESSION_ID = "session-fin-1";

function buildSession(overrides: Partial<TicketStudioSession> = {}): TicketStudioSession {
  return {
    id: SESSION_ID,
    workspace_slug: "loregarden",
    title: "Login Feature",
    brief: "User authentication system",
    parent_ticket_id: null,
    parent_ticket_title: "",
    status: "draft",
    summary: "Scoped into a feature with tasks.",
    clarifying_questions: [],
    clarifying_answers: [],
    clarifying_resolved: true,
    draft: [
      {
        ref: "f1",
        work_item_type: "feature",
        parent_ref: null,
        title: "Email Login Flow",
        description: "Email-based authentication",
        acceptance_criteria: ["Accept email/password"],
        priority: 1,
        suggested_agent: "",
        selected: true,
      },
      {
        ref: "t1",
        work_item_type: "task",
        parent_ref: "f1",
        title: "Create LoginForm component",
        description: "",
        acceptance_criteria: [],
        priority: 2,
        suggested_agent: "",
        selected: true,
      },
    ],
    messages: [],
    runtime: {
      cli_adapter: "default",
      claude_model: "",
      cursor_model: "",
      lmstudio_base_url: "",
      lmstudio_model: "",
    },
    is_preview: false,
    imported_tickets: [],
    created_at: "2026-07-01T00:00:00Z",
    updated_at: "2026-07-01T00:00:00Z",
    ...overrides,
  };
}

const FINALIZE_SUCCESS_RESPONSE = {
  session_id: SESSION_ID,
  created_ticket_ids: ["uuid-m1", "uuid-f1", "uuid-t1"],
  created_count: 3,
  breakdown: { milestone: 1, feature: 1, task: 1 },
  root_ticket_id: "uuid-m1",
};

function renderStudioPanel(overrides: Partial<TicketStudioPanelProps> = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });

  const props: TicketStudioPanelProps = {
    workspaceSlug: "loregarden",
    onClose: jest.fn(),
    ...overrides,
  };

  const utils = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[`/studio/tickets/${SESSION_ID}`]}>
        <TicketStudioPanel {...props} />
      </MemoryRouter>
    </QueryClientProvider>,
  );

  return { ...utils, props };
}

beforeEach(() => {
  jest.clearAllMocks();
  mockNavigate.mockClear();
  api.ticketStudioSessions.mockResolvedValue([buildSession()]);
  api.ticketStudioSession.mockResolvedValue(buildSession());
  api.studioAgents.mockResolvedValue([]);
});

async function getCommitButton() {
  return waitFor(() => screen.getByRole("button", { name: /create.*ticket/i }));
}

describe("Group I — Integration: Finalize Flow", () => {
  it("I1: user can click the commit button to start finalization", async () => {
    const user = userEvent.setup();
    api.commitTicketStudioSession.mockResolvedValueOnce(FINALIZE_SUCCESS_RESPONSE);
    renderStudioPanel();

    const commitButton = await getCommitButton();
    await user.click(commitButton);

    await waitFor(() => {
      expect(api.commitTicketStudioSession).toHaveBeenCalledWith(SESSION_ID);
    });
  });

  it("I2: shows loading state while finalization is in progress", async () => {
    const user = userEvent.setup();
    let resolveCommit: (value: typeof FINALIZE_SUCCESS_RESPONSE) => void;
    api.commitTicketStudioSession.mockImplementationOnce(
      () => new Promise((resolve) => { resolveCommit = resolve; }),
    );
    renderStudioPanel();

    const commitButton = await getCommitButton();
    await user.click(commitButton);

    expect(screen.getByRole("status", { hidden: true })).toBeInTheDocument();

    resolveCommit!(FINALIZE_SUCCESS_RESPONSE);
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /hierarchy.*created/i })).toBeInTheDocument();
    });
  });

  it("I3: commit button is removed once finalization succeeds (no double submission)", async () => {
    const user = userEvent.setup();
    api.commitTicketStudioSession.mockResolvedValueOnce(FINALIZE_SUCCESS_RESPONSE);
    renderStudioPanel();

    const commitButton = await getCommitButton();
    await user.click(commitButton);

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /hierarchy.*created/i })).toBeInTheDocument();
    });

    expect(screen.queryByRole("button", { name: /create.*ticket/i })).not.toBeInTheDocument();
    expect(api.commitTicketStudioSession).toHaveBeenCalledTimes(1);
  });
});

describe("Group II — Success Display & Navigation", () => {
  it("II1: displays success confirmation after finalization succeeds", async () => {
    const user = userEvent.setup();
    api.commitTicketStudioSession.mockResolvedValueOnce(FINALIZE_SUCCESS_RESPONSE);
    renderStudioPanel();

    const commitButton = await getCommitButton();
    await user.click(commitButton);

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /hierarchy.*created/i })).toBeInTheDocument();
    });
  });

  it("II2: displays total count of items created", async () => {
    const user = userEvent.setup();
    api.commitTicketStudioSession.mockResolvedValueOnce(FINALIZE_SUCCESS_RESPONSE);
    renderStudioPanel();

    const commitButton = await getCommitButton();
    await user.click(commitButton);

    await waitFor(() => {
      expect(screen.getByText("3")).toBeInTheDocument();
    });
  });

  it("II3: provides button to navigate to created hierarchy", async () => {
    const user = userEvent.setup();
    api.commitTicketStudioSession.mockResolvedValueOnce(FINALIZE_SUCCESS_RESPONSE);
    renderStudioPanel();

    const commitButton = await getCommitButton();
    await user.click(commitButton);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /view.*hierarchy/i })).toBeInTheDocument();
    });
  });

  it("II4: navigate button navigates using the root ticket ID from the response", async () => {
    const user = userEvent.setup();
    api.commitTicketStudioSession.mockResolvedValueOnce(FINALIZE_SUCCESS_RESPONSE);
    renderStudioPanel();

    const commitButton = await getCommitButton();
    await user.click(commitButton);

    const navButton = await waitFor(() => screen.getByRole("button", { name: /view.*hierarchy/i }));
    await user.click(navButton);

    expect(mockNavigate).toHaveBeenCalledWith(expect.stringContaining("uuid-m1"));
  });

  it("II5: user can close the confirmation", async () => {
    const user = userEvent.setup();
    api.commitTicketStudioSession.mockResolvedValueOnce(FINALIZE_SUCCESS_RESPONSE);
    renderStudioPanel();

    const commitButton = await getCommitButton();
    await user.click(commitButton);

    const closeButton = await waitFor(() => screen.getByRole("button", { name: /close/i }));
    await user.click(closeButton);

    await waitFor(() => {
      expect(screen.queryByRole("heading", { name: /hierarchy.*created/i })).not.toBeInTheDocument();
    });
  });
});

describe("Group III — Error Handling", () => {
  it("III1: displays error message when finalization fails", async () => {
    const user = userEvent.setup();
    api.commitTicketStudioSession.mockRejectedValueOnce(
      new Error("Duplicate external_id: 'fin-test-m1' already exists"),
    );
    renderStudioPanel();

    const commitButton = await getCommitButton();
    await user.click(commitButton);

    await waitFor(() => {
      expect(screen.getByText(/duplicate/i)).toBeInTheDocument();
    });
  });

  it("III2: does not show success confirmation when finalization fails", async () => {
    const user = userEvent.setup();
    api.commitTicketStudioSession.mockRejectedValueOnce(new Error("Validation failed"));
    renderStudioPanel();

    const commitButton = await getCommitButton();
    await user.click(commitButton);

    await waitFor(() => {
      expect(screen.queryByRole("heading", { name: /hierarchy.*created/i })).not.toBeInTheDocument();
    });
  });

  it("III3: allows user to retry finalization after error via close then commit again", async () => {
    const user = userEvent.setup();
    api.commitTicketStudioSession.mockRejectedValueOnce(new Error("Network error"));
    renderStudioPanel();

    let commitButton = await getCommitButton();
    await user.click(commitButton);

    await waitFor(() => {
      expect(screen.getByText(/network/i)).toBeInTheDocument();
    });

    const closeButton = screen.getByRole("button", { name: /close/i });
    await user.click(closeButton);

    api.commitTicketStudioSession.mockResolvedValueOnce(FINALIZE_SUCCESS_RESPONSE);
    commitButton = await getCommitButton();
    await user.click(commitButton);

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /hierarchy.*created/i })).toBeInTheDocument();
    });
  });
});

describe("Group IV — Edge Cases & State Management", () => {
  it("IV1: passes the workspace-scoped session ID to the commit API", async () => {
    const user = userEvent.setup();
    api.commitTicketStudioSession.mockResolvedValueOnce(FINALIZE_SUCCESS_RESPONSE);
    renderStudioPanel({ workspaceSlug: "custom-workspace" });

    const commitButton = await getCommitButton();
    await user.click(commitButton);

    await waitFor(() => {
      expect(api.commitTicketStudioSession).toHaveBeenCalledWith(SESSION_ID);
    });
  });

  it("IV2: handles a response with an empty breakdown gracefully", async () => {
    const user = userEvent.setup();
    api.commitTicketStudioSession.mockResolvedValueOnce({
      session_id: SESSION_ID,
      created_ticket_ids: ["uuid-1"],
      created_count: 1,
      breakdown: {},
      root_ticket_id: "uuid-1",
    });
    renderStudioPanel();

    const commitButton = await getCommitButton();
    await user.click(commitButton);

    await waitFor(() => {
      expect(screen.getByText("1")).toBeInTheDocument();
    });
  });

  it("IV3: handles a large hierarchy finalization (120 items)", async () => {
    const user = userEvent.setup();
    api.commitTicketStudioSession.mockResolvedValueOnce({
      session_id: SESSION_ID,
      created_ticket_ids: Array.from({ length: 120 }, (_, i) => `id-${i}`),
      created_count: 120,
      breakdown: { milestone: 10, feature: 40, capability: 35, task: 35 },
      root_ticket_id: "id-0",
    });
    renderStudioPanel();

    const commitButton = await getCommitButton();
    await user.click(commitButton);

    await waitFor(() => {
      expect(screen.getByText("120")).toBeInTheDocument();
    });
  });
});
