/**
 * Ticket Studio's commit against the acceptance-criteria cap (lg-milestone-that-2250).
 *
 * Studio is the one criteria-writing surface that commits a whole draft at once,
 * so an over-cap draft fails the batch server-side with nothing on screen saying
 * which row was the problem. The row already carries an "N AC" label; the mark
 * goes there, and the commit button is disabled with a title naming that draft —
 * matching the `draftDirty` precedent, which disables the same button for the
 * same kind of reason.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import type { TicketStudioPanelProps } from "../TicketStudioPanel";
import { TicketStudioPanel } from "../TicketStudioPanel";
import type { TicketStudioSession } from "../../../api/types";
import { MAX_ACCEPTANCE_CRITERIA } from "../../../lib/criterionDrafts";

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

jest.mock("react-router-dom", () => ({
  ...jest.requireActual("react-router-dom"),
  useNavigate: () => jest.fn(),
}));

const SESSION_ID = "session-cap-1";
const OVER_CAP_TITLE = "Email Login Flow";

function criteria(n: number): string[] {
  return Array.from({ length: n }, (_, i) => `criterion ${i}`);
}

function buildSession(featureCriteria: string[]): TicketStudioSession {
  return {
    id: SESSION_ID,
    workspace_slug: "loregarden",
    title: "Login Feature",
    brief: "User authentication system",
    parent_ticket_id: null,
    parent_ticket_title: "",
    status: "draft",
    run_status: "idle",
    active_turn_id: null,
    summary: "Scoped into a feature with tasks.",
    clarifying_questions: [],
    clarifying_answers: [],
    clarifying_resolved: true,
    draft: [
      {
        ref: "f1",
        work_item_type: "feature",
        parent_ref: null,
        title: OVER_CAP_TITLE,
        description: "Email-based authentication",
        acceptance_criteria: featureCriteria,
        priority: 1,
        selected: true,
      },
      {
        ref: "t1",
        work_item_type: "task",
        parent_ref: "f1",
        title: "Create LoginForm component",
        description: "",
        acceptance_criteria: criteria(2),
        priority: 2,
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
    reference_repos: [],
    survey: [],
    created_at: "2026-07-01T00:00:00Z",
    updated_at: "2026-07-01T00:00:00Z",
  };
}

function renderStudioPanel(featureCriteria: string[]) {
  api.ticketStudioSessions.mockResolvedValue([buildSession(featureCriteria)]);
  api.ticketStudioSession.mockResolvedValue(buildSession(featureCriteria));
  api.studioAgents.mockResolvedValue([]);
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const props: TicketStudioPanelProps = { workspaceSlug: "loregarden", onClose: jest.fn() };
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[`/studio/tickets/${SESSION_ID}`]}>
        <TicketStudioPanel {...props} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const commitButton = () =>
  waitFor(() => screen.getByRole("button", { name: /create.*ticket/i }), { timeout: 10_000 });

beforeEach(() => {
  jest.clearAllMocks();
});

it("commit is disabled and names the over-cap draft", async () => {
  renderStudioPanel(criteria(MAX_ACCEPTANCE_CRITERIA + 1));
  const button = await commitButton();

  expect(button).toBeDisabled();
  const title = button.getAttribute("title") ?? "";
  expect(title).toContain(OVER_CAP_TITLE);
  expect(title).toContain(String(MAX_ACCEPTANCE_CRITERIA));

  // The row's existing "N AC" label is where the mark goes; no new affordance.
  // The card, not `closest("li, div")`: the title is itself a <div>, so that
  // selector resolves to the title element and can only ever see the title.
  const row = await waitFor(() => screen.getByText(OVER_CAP_TITLE));
  expect(row.closest(".ticket-draft-card")).toHaveTextContent(
    new RegExp(`${MAX_ACCEPTANCE_CRITERIA + 1} AC`),
  );
  expect(api.commitTicketStudioSession).not.toHaveBeenCalled();
});

it("commit is enabled when every draft is at or under the cap", async () => {
  renderStudioPanel(criteria(MAX_ACCEPTANCE_CRITERIA));
  const button = await commitButton();

  expect(button).toBeEnabled();
  expect(button).not.toHaveAttribute("title");
});
