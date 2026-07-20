import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import type { TicketStudioPanelProps } from "../TicketStudioPanel";
import { TicketStudioPanel } from "../TicketStudioPanel";
import type { TicketStudioSession } from "../../../api/types";

/**
 * ADVERSARIAL INTEGRATION TEST SUITE: Ticket Studio Finalization
 *
 * Ticket:   43-post-finalization-ux-and-navigation
 *
 * Exercises the real commit → FinalizationConfirmation wiring in
 * TicketStudioPanel against malformed/adversarial API responses and
 * network failure scenarios.
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

const SESSION_ID = "session-fin-adv-1";

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
        description: "",
        acceptance_criteria: [],
        priority: 1,
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

const BASE_SUCCESS_RESPONSE = {
  session_id: SESSION_ID,
  created_ticket_ids: ["m-1", "f-1"],
  created_count: 2,
  breakdown: { milestone: 1, feature: 1 },
  root_ticket_id: "m-1",
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

describe("ADVA-INT-1: API Response Mutations", () => {
  it("ADVA-INT-1.1: handles a response with an empty created_ticket_ids array", async () => {
    const user = userEvent.setup();
    api.commitTicketStudioSession.mockResolvedValueOnce({
      session_id: SESSION_ID,
      created_ticket_ids: [],
      created_count: 0,
      breakdown: {},
      root_ticket_id: null,
    });
    renderStudioPanel();

    await user.click(await getCommitButton());

    await waitFor(() => {
      expect(screen.getByText("0")).toBeInTheDocument();
    });
  });

  it("ADVA-INT-1.2: handles a response with an empty breakdown object", async () => {
    const user = userEvent.setup();
    api.commitTicketStudioSession.mockResolvedValueOnce({
      session_id: SESSION_ID,
      created_ticket_ids: ["m-1"],
      created_count: 1,
      breakdown: {},
      root_ticket_id: "m-1",
    });
    renderStudioPanel();

    await user.click(await getCommitButton());

    await waitFor(() => {
      expect(screen.getByText("1")).toBeInTheDocument();
    });
  });

  it("ADVA-INT-1.3: handles the API returning extra, unexpected fields", async () => {
    const user = userEvent.setup();
    api.commitTicketStudioSession.mockResolvedValueOnce({
      ...BASE_SUCCESS_RESPONSE,
      secret_field: "should-be-ignored",
      malicious_command: "DELETE * FROM users",
    });
    renderStudioPanel();

    await user.click(await getCommitButton());

    await waitFor(() => {
      expect(screen.getByText("2")).toBeInTheDocument();
      expect(screen.queryByText(/secret_field|malicious/i)).not.toBeInTheDocument();
    });
  });

  it("ADVA-INT-1.4: handles an extremely large created_ticket_ids array (10k items)", async () => {
    const user = userEvent.setup();
    api.commitTicketStudioSession.mockResolvedValueOnce({
      session_id: SESSION_ID,
      created_ticket_ids: Array.from({ length: 10000 }, (_, i) => `id-${i}`),
      created_count: 10000,
      breakdown: { milestone: 2500, feature: 2500, capability: 2500, task: 2500 },
      root_ticket_id: "id-0",
    });
    renderStudioPanel();

    await user.click(await getCommitButton());

    await waitFor(
      () => {
        expect(screen.getByText("10000")).toBeInTheDocument();
      },
      { timeout: 5000 },
    );
  });
});

describe("ADVA-INT-2: Network Failure Scenarios", () => {
  it("ADVA-INT-2.1: handles a never-resolving commit request (disables the button)", async () => {
    const user = userEvent.setup();
    api.commitTicketStudioSession.mockImplementationOnce(() => new Promise(() => {}));
    renderStudioPanel();

    const commitButton = await getCommitButton();
    await user.click(commitButton);

    await waitFor(() => {
      expect(screen.getByRole("status", { hidden: true })).toBeInTheDocument();
    });
  });

  it("ADVA-INT-2.2: handles a connection-refused network error", async () => {
    const user = userEvent.setup();
    api.commitTicketStudioSession.mockRejectedValueOnce(new Error("Network error: ECONNREFUSED"));
    renderStudioPanel();

    await user.click(await getCommitButton());

    await waitFor(() => {
      expect(screen.getByText(/network/i)).toBeInTheDocument();
    });
  });

  it("ADVA-INT-2.3: handles a 500 server error with a message", async () => {
    const user = userEvent.setup();
    api.commitTicketStudioSession.mockRejectedValueOnce(new Error("Server error: 500 Internal Server Error"));
    renderStudioPanel();

    await user.click(await getCommitButton());

    await waitFor(() => {
      expect(screen.getByText(/500|server/i)).toBeInTheDocument();
    });
  });

  it("ADVA-INT-2.4: handles a 400 bad request error", async () => {
    const user = userEvent.setup();
    api.commitTicketStudioSession.mockRejectedValueOnce(new Error("Bad request: Invalid hierarchy structure"));
    renderStudioPanel();

    await user.click(await getCommitButton());

    await waitFor(() => {
      expect(screen.getByText(/bad request|invalid/i)).toBeInTheDocument();
    });
  });

  it("ADVA-INT-2.5: repeated failures each surface their own error message", async () => {
    const user = userEvent.setup();
    api.commitTicketStudioSession.mockRejectedValueOnce(new Error("First failure"));
    renderStudioPanel();

    await user.click(await getCommitButton());
    await waitFor(() => {
      expect(screen.getByText(/first failure/i)).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: /close/i }));

    api.commitTicketStudioSession.mockRejectedValueOnce(new Error("Second failure"));
    await user.click(await getCommitButton());
    await waitFor(() => {
      expect(screen.getByText(/second failure/i)).toBeInTheDocument();
    });
  });
});

describe("ADVA-INT-3: State Management Race Conditions", () => {
  it("ADVA-INT-3.1: a second click while a request is pending does not fire a second call", async () => {
    const user = userEvent.setup();
    let resolveFirst: ((value: typeof BASE_SUCCESS_RESPONSE) => void) | undefined;
    api.commitTicketStudioSession.mockImplementationOnce(
      () => new Promise((resolve) => { resolveFirst = resolve; }),
    );
    renderStudioPanel();

    const commitButton = await getCommitButton();
    await user.click(commitButton);

    // The commit control is replaced by the (non-clickable) loading state,
    // so there is no button left to double-click.
    expect(screen.queryByRole("button", { name: /create.*ticket/i })).not.toBeInTheDocument();

    resolveFirst!(BASE_SUCCESS_RESPONSE);
    await waitFor(() => {
      expect(api.commitTicketStudioSession).toHaveBeenCalledTimes(1);
    });
  });

  it("ADVA-INT-3.2: error state is cleared once a retry succeeds", async () => {
    const user = userEvent.setup();
    api.commitTicketStudioSession.mockRejectedValueOnce(new Error("Error 1"));
    renderStudioPanel();

    await user.click(await getCommitButton());
    await waitFor(() => {
      expect(screen.getByText(/error 1/i)).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: /close/i }));
    api.commitTicketStudioSession.mockResolvedValueOnce(BASE_SUCCESS_RESPONSE);
    await user.click(await getCommitButton());

    await waitFor(() => {
      expect(screen.queryByText(/error 1/i)).not.toBeInTheDocument();
      expect(screen.getByRole("heading", { name: /hierarchy.*created/i })).toBeInTheDocument();
    });
  });

  it("ADVA-INT-3.3: unmounting mid-request does not throw", async () => {
    const user = userEvent.setup();
    let resolveFirst: ((value: typeof BASE_SUCCESS_RESPONSE) => void) | undefined;
    api.commitTicketStudioSession.mockImplementationOnce(
      () => new Promise((resolve) => { resolveFirst = resolve; }),
    );
    const { unmount } = renderStudioPanel();

    const commitButton = await getCommitButton();
    await user.click(commitButton);
    unmount();
    resolveFirst!(BASE_SUCCESS_RESPONSE);
  });

  it("ADVA-INT-3.4: rapid navigation clicks after success all register", async () => {
    const user = userEvent.setup();
    api.commitTicketStudioSession.mockResolvedValueOnce(BASE_SUCCESS_RESPONSE);
    renderStudioPanel();

    await user.click(await getCommitButton());
    const navButton = await waitFor(() => screen.getByRole("button", { name: /view.*hierarchy/i }));

    await user.click(navButton);
    await user.click(navButton);
    await user.click(navButton);

    expect(mockNavigate).toHaveBeenCalledTimes(3);
  });
});

describe("ADVA-INT-4: Error Message Handling", () => {
  it("ADVA-INT-4.1: renders an HTML-like error message as inert text (no XSS)", async () => {
    const user = userEvent.setup();
    api.commitTicketStudioSession.mockRejectedValueOnce(new Error("<img src=x onerror='alert(1)'>"));
    renderStudioPanel();

    await user.click(await getCommitButton());

    await waitFor(() => {
      expect(screen.getByText(/img/i, { exact: false })).toBeInTheDocument();
    });
    expect(document.querySelector("img[onerror]")).toBeNull();
  });

  it("ADVA-INT-4.2: renders a very long error message without crashing", async () => {
    const user = userEvent.setup();
    api.commitTicketStudioSession.mockRejectedValueOnce(new Error("E".repeat(5000)));
    renderStudioPanel();

    await user.click(await getCommitButton());

    await waitFor(() => {
      expect(screen.getByText(/E{50,}/)).toBeInTheDocument();
    });
  });
});

describe("ADVA-INT-5: Navigation Integration", () => {
  it("ADVA-INT-5.1: navigates using the root_ticket_id from the response, not created_ticket_ids[0]", async () => {
    const user = userEvent.setup();
    api.commitTicketStudioSession.mockResolvedValueOnce({
      session_id: SESSION_ID,
      created_ticket_ids: ["first-id", "second-id"],
      created_count: 2,
      breakdown: { feature: 1, task: 1 },
      root_ticket_id: "different-root-id",
    });
    renderStudioPanel();

    await user.click(await getCommitButton());
    const navButton = await waitFor(() => screen.getByRole("button", { name: /view.*hierarchy/i }));
    await user.click(navButton);

    expect(mockNavigate).toHaveBeenCalledWith(expect.stringContaining("different-root-id"));
  });

  it("ADVA-INT-5.2: navigation button is disabled when root_ticket_id is null", async () => {
    const user = userEvent.setup();
    api.commitTicketStudioSession.mockResolvedValueOnce({
      session_id: SESSION_ID,
      created_ticket_ids: ["a-1"],
      created_count: 1,
      breakdown: { milestone: 1 },
      root_ticket_id: null,
    });
    renderStudioPanel();

    await user.click(await getCommitButton());

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /view.*hierarchy/i })).toBeDisabled();
    });
  });
});
