import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { ApiError, api, type BaxterChatSnapshot } from "../../api/client";
import { HOME_BAXTER_PROMPT_KEY } from "../../lib/homeBaxter";
import { useUiStore } from "../../state/uiStore";
import { BaxterChatPage } from "../BaxterChatPage";

jest.mock("../../api/client", () => {
  const actual = jest.requireActual("../../api/client");
  return {
    ...actual,
    api: {
      ...actual.api,
      approvals: jest.fn(),
      ticket: jest.fn(),
      ticketTree: jest.fn(),
      tickets: jest.fn(),
      workspaces: jest.fn(),
      baxterChatSessions: jest.fn(),
      baxterChatSession: jest.fn(),
      createBaxterChatSession: jest.fn(),
      renameBaxterChatSession: jest.fn(),
      deleteBaxterChatSession: jest.fn(),
      sendBaxterChatMessage: jest.fn(),
    },
  };
});

const mockedApi = api as jest.Mocked<typeof api>;

const APPROVAL_FIXTURE = {
  id: "a1",
  title: "Merge retro-tokens",
  level: "ask",
  workspace_slug: "loregarden",
  stage_key: "gate",
  stage_name: "Gate",
  impact: "",
  ticket_id: "t1",
  ticket_external_id: "retro-1",
  kind: "workflow_gate",
  status: "pending",
  run_id: "r1",
  tool_name: "",
  tool_input_json: "{}",
  cli_adapter: "claude",
};

/**
 * A stand-in for the server's thread store.
 *
 * The reply now lands on a persisted thread rather than in the send's response,
 * so a test that only stubs the POST would prove nothing about what the page
 * displays. This keeps the same rule the server has: the POST records the turn,
 * a read returns it.
 */
function fakeChatServer(reply = "Model says ship Home polish.") {
  const threads = new Map<string, BaxterChatSnapshot>();
  let nextId = 1;

  const snapshot = (id: string): BaxterChatSnapshot => {
    const found = threads.get(id);
    if (!found) throw new ApiError(404, "Chat session not found");
    return found;
  };

  mockedApi.createBaxterChatSession.mockImplementation(async (_slug, title = "") => {
    const created: BaxterChatSnapshot = {
      id: `s${nextId++}`,
      workspace_id: "w1",
      title: title || "New chat",
      messages: [],
      run_status: "idle",
      active_turn_id: null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };
    threads.set(created.id, created);
    return created;
  });
  mockedApi.baxterChatSession.mockImplementation(async (_slug, id) => snapshot(id));
  mockedApi.sendBaxterChatMessage.mockImplementation(async (_slug, id, content) => {
    const thread = snapshot(id);
    const updated: BaxterChatSnapshot = {
      ...thread,
      title: thread.messages.length ? thread.title : content,
      messages: [
        ...thread.messages,
        { id: `m${nextId++}`, role: "user", content, created_at: new Date().toISOString() },
        { id: `m${nextId++}`, role: "assistant", content: reply, created_at: new Date().toISOString() },
      ],
    };
    threads.set(id, updated);
    return updated;
  });
  mockedApi.deleteBaxterChatSession.mockImplementation(async (_slug, id) => {
    threads.delete(id);
    return { deleted: id };
  });
  mockedApi.baxterChatSessions.mockImplementation(async () =>
    [...threads.values()].map((thread) => ({
      id: thread.id,
      title: thread.title,
      message_count: thread.messages.length,
      preview: thread.messages[thread.messages.length - 1]?.content ?? "",
      created_at: thread.created_at,
      updated_at: thread.updated_at,
    })),
  );
  return threads;
}

function renderChat() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/chat"]}>
        <Routes>
          <Route path="/chat" element={<BaxterChatPage />} />
          <Route path="/console" element={<div>Console</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/**
 * Chat is confined to one workspace, so the composer stays locked until that
 * workspace is known — typing before then is dropped, not queued.
 */
async function renderChatReady() {
  const result = renderChat();
  await waitFor(() =>
    expect(screen.getByPlaceholderText("What should we ship today?")).toBeEnabled(),
  );
  return result;
}

describe("BaxterChatPage", () => {
  beforeEach(() => {
    sessionStorage.clear();
    // Call history leaks between tests otherwise, so per-workspace assertions
    // would see requests another test made.
    jest.clearAllMocks();
    mockedApi.approvals.mockResolvedValue([]);
    mockedApi.ticket.mockRejectedValue(new ApiError(404, "Example ticket not found"));
    mockedApi.ticketTree.mockResolvedValue([]);
    mockedApi.tickets.mockResolvedValue([]);
    mockedApi.workspaces.mockResolvedValue([
      { id: "w1", slug: "loregarden", name: "Loregarden", repo_path: "/tmp" } as never,
      { id: "w2", slug: "blobert", name: "Blobert", repo_path: "/tmp/b" } as never,
    ]);
    fakeChatServer();
    // Both the slug and the bound thread are persisted, so leaving them set
    // would leak across tests.
    useUiStore.setState({
      baxterHistoryOpen: false,
      chatWorkspaceSlug: "",
      workspace: "all",
      baxterChatSessionId: "",
    });
  });

  it("shows a welcoming empty shell with the large Ask Baxter composer", async () => {
    renderChat();
    expect(screen.getByRole("heading", { name: /Good (morning|afternoon|evening)/i })).toBeInTheDocument();
    expect(screen.getByLabelText("Ask Baxter")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("What should we ship today?")).toBeInTheDocument();
    expect(screen.getByText("What should I look at first?")).toBeInTheDocument();
    await waitFor(() => expect(mockedApi.approvals).toHaveBeenCalled());
  });

  it("creates a thread on the first send and shows the persisted reply", async () => {
    await renderChatReady();
    const input = screen.getByPlaceholderText("What should we ship today?");
    fireEvent.change(input, { target: { value: "Hello Baxter" } });
    fireEvent.click(screen.getByRole("button", { name: /Ask Baxter/i }));

    await waitFor(() => {
      expect(mockedApi.createBaxterChatSession).toHaveBeenCalledWith("loregarden");
      expect(mockedApi.sendBaxterChatMessage).toHaveBeenCalledWith(
        "loregarden",
        "s1",
        "Hello Baxter",
      );
    });
    await waitFor(() => {
      expect(screen.getByText("Model says ship Home polish.")).toBeInTheDocument();
    });
    // The thread is bound, so a remount resumes it instead of starting over.
    expect(useUiStore.getState().baxterChatSessionId).toBe("s1");
  });

  it("restores the bound thread from the server on mount", async () => {
    const threads = fakeChatServer();
    threads.set("s9", {
      id: "s9",
      workspace_id: "w1",
      title: "Yesterday's triage",
      messages: [
        { id: "m1", role: "user", content: "Where did we stop?", created_at: "2026-07-29T09:00:00Z" },
        { id: "m2", role: "assistant", content: "On the retro-tokens gate.", created_at: "2026-07-29T09:00:01Z" },
      ],
      run_status: "idle",
      active_turn_id: null,
      created_at: "2026-07-29T09:00:00Z",
      updated_at: "2026-07-29T09:00:01Z",
    });
    useUiStore.setState({ baxterChatSessionId: "s9" });

    renderChat();

    expect(await screen.findByText("Where did we stop?")).toBeInTheDocument();
    expect(screen.getByText("On the retro-tokens gate.")).toBeInTheDocument();
  });

  it("falls back to a fresh chat when the bound thread is gone", async () => {
    useUiStore.setState({ baxterChatSessionId: "deleted-thread" });
    renderChat();

    await waitFor(() => expect(useUiStore.getState().baxterChatSessionId).toBe(""));
    expect(screen.getByPlaceholderText("What should we ship today?")).toBeInTheDocument();
  });

  it("locks the composer until it knows which workspace answers", async () => {
    let resolveWorkspaces: (value: unknown) => void = () => undefined;
    mockedApi.workspaces.mockImplementation(
      () => new Promise((resolve) => { resolveWorkspaces = resolve; }) as never,
    );

    renderChat();
    const input = screen.getByPlaceholderText("What should we ship today?");
    expect(input).toBeDisabled();

    fireEvent.change(input, { target: { value: "Hello Baxter" } });
    fireEvent.click(screen.getByRole("button", { name: /Ask Baxter/i }));
    expect(mockedApi.sendBaxterChatMessage).not.toHaveBeenCalled();

    await act(async () => {
      resolveWorkspaces([
        { id: "w1", slug: "loregarden", name: "Loregarden", repo_path: "/tmp" },
      ]);
    });
    await waitFor(() => expect(input).toBeEnabled());
  });

  it("sends to the picked chat workspace, not the Console filter", async () => {
    useUiStore.setState({ chatWorkspaceSlug: "blobert", workspace: "loregarden" });
    await renderChatReady();

    fireEvent.change(screen.getByPlaceholderText("What should we ship today?"), {
      target: { value: "What is next here?" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Ask Baxter/i }));

    await waitFor(() => {
      expect(mockedApi.sendBaxterChatMessage).toHaveBeenCalledWith(
        "blobert",
        "s1",
        "What is next here?",
      );
    });
  });

  it("scopes its ticket queries to one workspace instead of every workspace", async () => {
    useUiStore.setState({ chatWorkspaceSlug: "blobert" });
    await renderChatReady();

    await waitFor(() => expect(mockedApi.tickets).toHaveBeenCalled());
    for (const call of mockedApi.tickets.mock.calls) {
      expect(call[0]).toMatchObject({ workspace: "blobert" });
    }
  });

  it("drops approvals belonging to another workspace", async () => {
    useUiStore.setState({ chatWorkspaceSlug: "loregarden" });
    mockedApi.approvals.mockResolvedValue([
      { ...APPROVAL_FIXTURE, id: "a1", title: "Mine", workspace_slug: "loregarden" },
      { ...APPROVAL_FIXTURE, id: "a2", title: "Theirs", workspace_slug: "blobert" },
    ] as never);
    fakeChatServer("One thing waits on you.");

    await renderChatReady();
    // Only the in-workspace approval counts toward the greeting summary.
    await screen.findByText(/1 approval waiting/);

    fireEvent.change(screen.getByPlaceholderText("What should we ship today?"), {
      target: { value: "What waits on me?" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Ask Baxter/i }));

    await waitFor(() => expect(screen.getByText("Mine")).toBeInTheDocument());
    expect(screen.queryByText("Theirs")).not.toBeInTheDocument();
  });

  it("bootstraps a handoff prompt from Home into the thread", async () => {
    sessionStorage.setItem(HOME_BAXTER_PROMPT_KEY, "What should I look at first this morning?");
    mockedApi.approvals.mockResolvedValue([APPROVAL_FIXTURE] as never);
    fakeChatServer("Start with the Merge retro-tokens approval.");

    renderChat();

    await waitFor(() => {
      expect(screen.getByText("What should I look at first this morning?")).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(screen.getByText("Start with the Merge retro-tokens approval.")).toBeInTheDocument();
      expect(screen.getByText("Merge retro-tokens")).toBeInTheDocument();
    });
    expect(sessionStorage.getItem(HOME_BAXTER_PROMPT_KEY)).toBeNull();
  });

  it("renders assistant replies as markdown", async () => {
    fakeChatServer("**Ship Home polish** with `cursor` next.");
    await renderChatReady();
    const input = screen.getByPlaceholderText("What should we ship today?");
    fireEvent.change(input, { target: { value: "What next?" } });
    fireEvent.click(screen.getByRole("button", { name: /Ask Baxter/i }));

    await waitFor(() => {
      expect(screen.getByText("Ship Home polish").tagName).toBe("STRONG");
      expect(screen.getByText("cursor").tagName).toBe("CODE");
    });
  });

  it("shows an animated loading card while waiting on Baxter", async () => {
    const threads = fakeChatServer();
    let resolveReply: (value: BaxterChatSnapshot) => void = () => undefined;
    mockedApi.sendBaxterChatMessage.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveReply = resolve;
        }),
    );
    await renderChatReady();
    const input = screen.getByPlaceholderText("What should we ship today?");
    fireEvent.change(input, { target: { value: "Hello" } });
    fireEvent.click(screen.getByRole("button", { name: /Ask Baxter/i }));

    await waitFor(() => {
      expect(screen.getByText("Baxter is looking…")).toBeInTheDocument();
      expect(screen.getByText(/workspace model/i)).toBeInTheDocument();
      expect(document.querySelector(".lg-chat-loading-walker")).toBeTruthy();
      expect(document.querySelector(".baxter-avatar--full.baxter-avatar--typing")).toBeTruthy();
    });
    const settled: BaxterChatSnapshot = {
      id: "s1",
      workspace_id: "w1",
      title: "Hello",
      messages: [
        { id: "m1", role: "user", content: "Hello", created_at: "2026-07-30T09:00:00Z" },
        { id: "m2", role: "assistant", content: "On it.", created_at: "2026-07-30T09:00:01Z" },
      ],
      run_status: "idle",
      active_turn_id: null,
      created_at: "2026-07-30T09:00:00Z",
      updated_at: "2026-07-30T09:00:01Z",
    };
    // Settle it on the server too — the page re-reads the thread after a send.
    threads.set("s1", settled);
    await act(async () => {
      resolveReply(settled);
    });
    await waitFor(() => expect(screen.getByText("On it.")).toBeInTheDocument());
  });

  it("keeps the composer busy while the server reports a turn in flight", async () => {
    const threads = fakeChatServer();
    threads.set("s7", {
      id: "s7",
      workspace_id: "w1",
      title: "In flight",
      messages: [
        { id: "m1", role: "user", content: "Still working?", created_at: "2026-07-30T09:00:00Z" },
      ],
      run_status: "running",
      active_turn_id: "m2",
      created_at: "2026-07-30T09:00:00Z",
      updated_at: "2026-07-30T09:00:00Z",
    });
    useUiStore.setState({ baxterChatSessionId: "s7" });

    renderChat();

    // Busy is server-derived, so a reload mid-turn still shows the agent working.
    expect(await screen.findByText("Baxter is looking…")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByPlaceholderText("Reply to Baxter…")).toBeDisabled(),
    );
  });

  it("surfaces API failures in the thread", async () => {
    mockedApi.sendBaxterChatMessage.mockRejectedValue(
      new ApiError(502, "Baxter unavailable: boom"),
    );
    await renderChatReady();
    const input = screen.getByPlaceholderText("What should we ship today?");
    fireEvent.change(input, { target: { value: "Hello" } });
    fireEvent.click(screen.getByRole("button", { name: /Ask Baxter/i }));

    await waitFor(() => {
      expect(screen.getByText("Baxter unavailable: boom")).toBeInTheDocument();
    });
  });

  it("starts a new chat when the global topbar reset fires", async () => {
    await renderChatReady();
    const input = screen.getByPlaceholderText("What should we ship today?");
    fireEvent.change(input, { target: { value: "Hello" } });
    fireEvent.click(screen.getByRole("button", { name: /Ask Baxter/i }));

    await waitFor(() => expect(screen.getByText("Hello")).toBeInTheDocument());
    await waitFor(() => expect(screen.queryByText("Baxter is looking…")).not.toBeInTheDocument());

    act(() => useUiStore.getState().requestBaxterChatReset());
    await waitFor(() => {
      expect(screen.getByPlaceholderText("What should we ship today?")).toBeInTheDocument();
    });
    expect(screen.queryByText("Hello")).not.toBeInTheDocument();
    // The thread is not deleted, only unbound — it is still in the archive.
    expect(mockedApi.deleteBaxterChatSession).not.toHaveBeenCalled();
  });

  it("lists saved conversations in the archive and reopens one", async () => {
    await renderChatReady();
    fireEvent.change(screen.getByPlaceholderText("What should we ship today?"), {
      target: { value: "Triage the stuck tickets" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Ask Baxter/i }));
    await waitFor(() =>
      expect(screen.getByText("Model says ship Home polish.")).toBeInTheDocument(),
    );

    act(() => useUiStore.getState().requestBaxterChatReset());
    act(() => useUiStore.getState().setBaxterHistoryOpen(true));

    // The row also holds a delete control naming the same thread, so anchor the match.
    const entry = await screen.findByRole("button", { name: /^Triage the stuck tickets/ });
    fireEvent.click(entry);

    await waitFor(() => {
      expect(screen.getByText("Model says ship Home polish.")).toBeInTheDocument();
    });
    expect(useUiStore.getState().baxterChatSessionId).toBe("s1");
  });

  it("deletes a conversation from the archive", async () => {
    await renderChatReady();
    fireEvent.change(screen.getByPlaceholderText("What should we ship today?"), {
      target: { value: "Delete me" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Ask Baxter/i }));
    await waitFor(() => expect(mockedApi.sendBaxterChatMessage).toHaveBeenCalled());

    act(() => useUiStore.getState().setBaxterHistoryOpen(true));
    fireEvent.click(await screen.findByRole("button", { name: /Delete Delete me/i }));

    await waitFor(() =>
      expect(mockedApi.deleteBaxterChatSession).toHaveBeenCalledWith("loregarden", "s1"),
    );
    await waitFor(() => expect(useUiStore.getState().baxterChatSessionId).toBe(""));
  });

  it("opens the fallback history and loads the primitive gallery", async () => {
    renderChat();

    act(() => useUiStore.getState().setBaxterHistoryOpen(true));
    expect(await screen.findByRole("complementary", { name: "Chat history" })).toBeInTheDocument();
    expect(screen.getByText("UI Primitive gallery")).toBeInTheDocument();
    expect(screen.getByText("Filterable kanban")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /UI Primitive gallery/i }));

    await waitFor(() => {
      expect(screen.getByText("Show me examples of every chat UI primitive.")).toBeInTheDocument();
    });
    expect(screen.getByText("Thinking")).toBeInTheDocument();
    expect(screen.getAllByText("client · npm test").length).toBeGreaterThan(0);
    expect(screen.getByText("Workspace schedule")).toBeInTheDocument();
    expect(screen.queryByRole("complementary", { name: "Chat history" })).not.toBeInTheDocument();
    // The gallery is a rendering reference, not a conversation — nothing saved.
    expect(mockedApi.createBaxterChatSession).not.toHaveBeenCalled();
  });

  it("brackets each gallery card with the ask that produced it", async () => {
    const { container } = renderChat();

    act(() => useUiStore.getState().setBaxterHistoryOpen(true));
    await screen.findByRole("complementary", { name: "Chat history" });
    fireEvent.click(screen.getByRole("button", { name: /UI Primitive gallery/i }));

    await waitFor(() => {
      expect(screen.getByText("What is scheduled today?")).toBeInTheDocument();
    });
    // Every card is a reply to a visible ask, so no card can read as unattributed.
    const asks = container.querySelectorAll(".lg-chat-turn--user");
    const replies = container.querySelectorAll(".lg-chat-turn--assistant");
    expect(asks.length).toBeGreaterThan(1);
    expect(replies.length).toBe(asks.length);
  });

  it("pairs the thread's fade clearance with the dock that draws the ramp", async () => {
    const { container } = renderChat();

    act(() => useUiStore.getState().setBaxterHistoryOpen(true));
    await screen.findByRole("complementary", { name: "Chat history" });
    fireEvent.click(screen.getByRole("button", { name: /UI Primitive gallery/i }));

    await waitFor(() => {
      expect(container.querySelector(".baxter-chat-dock")).not.toBeNull();
    });
    // The ramp is drawn above the dock and needs matching clearance inside the
    // scrollport; one without the other either veils the last card or does nothing.
    expect(container.querySelector(".baxter-chat-thread--faded")).not.toBeNull();
    expect(container.querySelector(".baxter-chat-dock--fade")).not.toBeNull();
  });
});
