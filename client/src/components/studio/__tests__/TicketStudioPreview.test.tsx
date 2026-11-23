import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useNavigate } from "react-router-dom";

import type {
  ImportedTicket,
  TicketStudioDraftItem,
  TicketStudioSession,
} from "../../../api/types";
import { TicketStudioPanel } from "../TicketStudioPanel";

/**
 * A smart import opens a preview session: the imported tickets are listed, and
 * nothing is created until the operator confirms in a dialog.
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

const SESSION_ID = "session-preview-1";
const OTHER_ID = "session-preview-2";

function imported(n: number, overrides: Partial<ImportedTicket> = {}): ImportedTicket {
  return {
    external_id: `imp-${n}`,
    title: `Imported ticket ${n}`,
    work_item_type: "task",
    priority: 2,
    ...overrides,
  };
}

function draftItem(n: number, selected = true): TicketStudioDraftItem {
  return {
    ref: `d${n}`,
    work_item_type: "feature",
    parent_ref: null,
    title: `Draft ${n}`,
    description: "",
    acceptance_criteria: [],
    priority: 2,
    selected,
  };
}

function buildSession(overrides: Partial<TicketStudioSession> = {}): TicketStudioSession {
  return {
    id: SESSION_ID,
    workspace_slug: "loregarden",
    title: "Smart import",
    brief: "",
    parent_ticket_id: null,
    parent_ticket_title: "",
    status: "draft",
    run_status: "idle",
    active_turn_id: null,
    summary: "",
    clarifying_questions: [],
    clarifying_answers: [],
    clarifying_resolved: true,
    draft: [draftItem(1), draftItem(2)],
    messages: [],
    runtime: {
      cli_adapter: "default",
      claude_model: "",
      cursor_model: "",
      lmstudio_base_url: "",
      lmstudio_model: "",
    },
    is_preview: true,
    imported_tickets: [imported(1), imported(2)],
    reference_repos: [],
    survey: [],
    created_at: "2026-07-01T00:00:00Z",
    updated_at: "2026-07-01T00:00:00Z",
    ...overrides,
  };
}

let goTo: (path: string) => void = () => {};
function NavigationProbe() {
  goTo = useNavigate();
  return null;
}

function renderPanel(...sessions: TicketStudioSession[]) {
  api.ticketStudioSessions.mockResolvedValue(sessions);
  api.ticketStudioSession.mockImplementation(async (id: string) =>
    sessions.find((session) => session.id === id),
  );
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[`/studio/tickets/${sessions[0].id}`]}>
        <NavigationProbe />
        <TicketStudioPanel workspaceSlug="loregarden" />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const openButton = () => screen.findByRole("button", { name: /confirm to finalize/i });
const dialog = () => screen.findByRole("dialog", { name: /finalize imported tickets/i });
const noDialog = () => expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

beforeEach(() => {
  jest.clearAllMocks();
  api.studioAgents.mockResolvedValue([]);
});

describe("what a preview session shows", () => {
  it("lists every imported ticket with its type and priority", async () => {
    renderPanel(
      buildSession({
        imported_tickets: [
          imported(1, { work_item_type: "milestone", priority: 1 }),
          imported(2, { title: "<img src=x onerror=alert(1)>" }),
        ],
      }),
    );

    const items = await screen.findAllByTestId("imported-ticket-item");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("Imported ticket 1MilestoneP1");
    // Imported text is file content, so markup in it stays text.
    expect(items[1]).toHaveTextContent("<img src=x onerror=alert(1)>TaskP2");
    expect(items[1].querySelector("img")).toBeNull();
    expect(screen.getByTestId("preview-state-indicator")).toHaveTextContent("Preview");
  });

  it("shows neither the badge nor the list for an ordinary session", async () => {
    renderPanel(buildSession({ is_preview: false, imported_tickets: [] }));

    expect(await screen.findByRole("button", { name: /create 2 tickets/i })).toBeEnabled();
    expect(screen.queryByTestId("preview-state-indicator")).not.toBeInTheDocument();
    expect(screen.queryByTestId("imported-ticket-item")).not.toBeInTheDocument();
  });

  it("commits an ordinary session without a confirmation", async () => {
    const user = userEvent.setup();
    api.commitTicketStudioSession.mockReturnValue(new Promise(() => {}));
    renderPanel(buildSession({ is_preview: false, imported_tickets: [] }));

    await user.click(await screen.findByRole("button", { name: /create 2 tickets/i }));

    expect(api.commitTicketStudioSession).toHaveBeenCalledWith(SESSION_ID);
    noDialog();
  });

  it("cannot be finalized with nothing selected", async () => {
    renderPanel(buildSession({ draft: [draftItem(1, false)] }));
    expect(await openButton()).toBeDisabled();
  });

  it("offers no finalize button once committed", async () => {
    renderPanel(buildSession({ status: "committed" }));
    await screen.findByText("Committed to workspace");
    expect(screen.queryByRole("button", { name: /finalize/i })).not.toBeInTheDocument();
  });
});

describe("the confirmation", () => {
  it("creates nothing until it is confirmed, then commits this session once", async () => {
    const user = userEvent.setup();
    api.commitTicketStudioSession.mockReturnValue(new Promise(() => {}));
    renderPanel(buildSession());

    await user.click(await openButton());
    const box = await dialog();
    expect(api.commitTicketStudioSession).not.toHaveBeenCalled();

    await user.click(within(box).getByRole("button", { name: "Create 2 tickets" }));
    expect(api.commitTicketStudioSession.mock.calls).toEqual([[SESSION_ID]]);
    await waitFor(noDialog);
    expect(screen.getByText(/finalizing hierarchy/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /confirm to finalize/i })).not.toBeInTheDocument();
  });

  it("counts the selected drafts, which are what the commit creates", async () => {
    const user = userEvent.setup();
    renderPanel(
      buildSession({
        draft: [draftItem(1), draftItem(2, false)],
        imported_tickets: [1, 2, 3, 4, 5].map((n) => imported(n)),
      }),
    );

    await user.click(await openButton());
    const box = await dialog();
    expect(box).toHaveTextContent("create 1 work item in");
    expect(within(box).getByRole("button", { name: "Create 1 ticket" })).toBeEnabled();
    expect(box).toHaveTextContent("5 imported tickets");
    expect(within(box).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "Imported ticket 1",
      "Imported ticket 2",
      "Imported ticket 3",
      "and 2 more",
    ]);
  });

  it.each([
    ["Cancel", async (user: ReturnType<typeof userEvent.setup>, box: HTMLElement) =>
      user.click(within(box).getByRole("button", { name: "Cancel" }))],
    ["Escape", async (user: ReturnType<typeof userEvent.setup>) => user.keyboard("{Escape}")],
  ])("is dismissed by %s, creating nothing, and can be opened again", async (_name, dismiss) => {
    const user = userEvent.setup();
    renderPanel(buildSession());

    await user.click(await openButton());
    await dismiss(user, await dialog());

    await waitFor(noDialog);
    expect(api.commitTicketStudioSession).not.toHaveBeenCalled();
    await user.click(await openButton());
    expect(await dialog()).toBeInTheDocument();
  });

  it("takes focus when it opens and hands it back when it closes", async () => {
    const user = userEvent.setup();
    renderPanel(buildSession());

    const opener = await openButton();
    opener.focus();
    await user.keyboard("{Enter}");
    const box = await dialog();
    expect(box).toContainElement(document.activeElement as HTMLElement);

    await user.keyboard("{Escape}");
    await waitFor(() => expect(opener).toHaveFocus());
  });

  it("gets out of the way of a failed commit, which can be retried", async () => {
    const user = userEvent.setup();
    api.commitTicketStudioSession.mockRejectedValueOnce(new Error("parent_ref not found"));
    renderPanel(buildSession());

    await user.click(await openButton());
    await user.click(within(await dialog()).getByRole("button", { name: /^create/i }));

    expect(await screen.findByText("parent_ref not found")).toBeInTheDocument();
    await waitFor(noDialog);
    await user.click(await openButton());
    expect(await dialog()).toBeInTheDocument();
  });
});

describe("switching sessions", () => {
  it("does not show one session's commit result on another", async () => {
    const user = userEvent.setup();
    api.commitTicketStudioSession.mockRejectedValueOnce(new Error("parent_ref not found"));
    renderPanel(buildSession(), buildSession({ id: OTHER_ID, title: "Another import" }));

    await user.click(await openButton());
    await user.click(within(await dialog()).getByRole("button", { name: /^create/i }));
    await screen.findByText("parent_ref not found");

    act(() => goTo(`/studio/tickets/${OTHER_ID}`));
    expect(await screen.findByRole("heading", { name: "Another import" })).toBeInTheDocument();
    expect(screen.queryByText("parent_ref not found")).not.toBeInTheDocument();
  });

  it("offers another session its own finalize button after one was committed", async () => {
    const user = userEvent.setup();
    api.commitTicketStudioSession.mockResolvedValueOnce({
      session_id: SESSION_ID,
      created_ticket_ids: ["uuid-1"],
      created_count: 1,
      breakdown: { feature: 1 },
      root_ticket_id: "uuid-1",
    });
    renderPanel(buildSession(), buildSession({ id: OTHER_ID, title: "Another import" }));

    await user.click(await openButton());
    await user.click(within(await dialog()).getByRole("button", { name: /^create/i }));
    await screen.findByRole("heading", { name: /hierarchy created/i });

    act(() => goTo(`/studio/tickets/${OTHER_ID}`));
    await screen.findByRole("heading", { name: "Another import" });
    expect(screen.queryByRole("heading", { name: /hierarchy created/i })).not.toBeInTheDocument();
    expect(await openButton()).toBeEnabled();
  });
});
