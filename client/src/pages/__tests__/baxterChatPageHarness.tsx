/**
 * What BaxterChatPage's test files share: the thread store stand-in, the
 * renderers and the per-test reset. Each test file still declares its own
 * `jest.mock("../../api/client")`, because a mock is hoisted per file.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { ApiError, api, type BaxterChatSnapshot } from "../../api/client";
import { DEFAULT_RUNTIME } from "../../lib/runtimeSettings";
import { useUiStore } from "../../state/uiStore";
import { BaxterChatPage } from "../BaxterChatPage";

/** A thread on one workspace with no focus beyond it, no loop and no branch. */
export const NO_WORKBENCH = {
  focus: {
    all_workspaces: false,
    workspaces: [{ id: "ws-1", slug: "loregarden", fixed: true }],
    initiatives: [],
  },
  feature: null,
  chat_branch: null,
};

export const mockedApi = api as jest.Mocked<typeof api>;

/**
 * A stand-in for the server's thread store.
 *
 * The reply now lands on a persisted thread rather than in the send's response,
 * so a test that only stubs the POST would prove nothing about what the page
 * displays. This keeps the same rule the server has: the POST records the turn,
 * a read returns it.
 */
export function fakeChatServer(reply = "Model says ship Home polish.") {
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
      scope: "workspace",
      initiative_id: null,
      host_workspace_slug: "loregarden",
      ...NO_WORKBENCH,
      title: title || "New chat",
      messages: [],
      pending_approvals: [],
      runtime: DEFAULT_RUNTIME,
      run_status: "idle",
      active_turn_id: null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };
    threads.set(created.id, created);
    return created;
  });
  mockedApi.baxterChatSession.mockImplementation(async (_slug, id) => snapshot(id));
  mockedApi.setBaxterChatRuntime.mockImplementation(async (_slug, id, runtime) => {
    const thread = snapshot(id);
    threads.set(id, { ...thread, runtime });
    return runtime;
  });
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
  mockedApi.stopBaxterChatTurn.mockImplementation(async (_slug, id) => {
    const thread = snapshot(id);
    const updated: BaxterChatSnapshot = {
      ...thread,
      run_status: "idle",
      active_turn_id: null,
      messages: [
        ...thread.messages,
        {
          id: `stop-${nextId++}`,
          role: "assistant",
          content: "Baxter stopped this turn at your request.",
          created_at: new Date().toISOString(),
        },
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

export function renderChat() {
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
export async function renderChatReady() {
  const result = renderChat();
  await waitFor(() =>
    expect(screen.getByPlaceholderText("What should we ship today?")).toBeEnabled(),
  );
  return result;
}

/** The page's own way back to a hidden workbench; the topbar toggle is AppLayoutChrome's. */
export async function showWorkbenchFromComposer() {
  const composer = document.querySelector<HTMLElement>(".lg-chat-composer")!;
  fireEvent.click(within(composer).getByRole("button", { name: "Chat actions" }));
  fireEvent.click(await screen.findByRole("menuitem", { name: "Show workbench" }));
}

/** The defaults every BaxterChatPage test starts from; call it in `beforeEach`. */
export function resetBaxterChatPage() {
  sessionStorage.clear();
  // The workbench remembers being hidden; one test must not hide it for the next.
  localStorage.clear();
  // Call history leaks between tests otherwise, so per-workspace assertions
  // would see requests another test made.
  jest.clearAllMocks();
  mockedApi.approvals.mockResolvedValue([]);
  mockedApi.ticket.mockRejectedValue(new ApiError(404, "Example ticket not found"));
  mockedApi.ticketTree.mockResolvedValue([]);
  mockedApi.tickets.mockResolvedValue([]);
  mockedApi.runtimeOptions.mockResolvedValue({
    cli_adapters: [
      { id: "default", label: "Workspace default" },
      { id: "claude", label: "Claude Code" },
      { id: "cursor", label: "Cursor" },
    ],
    claude_models: [{ id: "", label: "Default (Claude profile)" }],
    cursor_models: [
      { id: "", label: "Default (Cursor profile)" },
      { id: "gpt-5", label: "GPT-5" },
    ],
    codex_models: [{ id: "", label: "Default (Codex profile)" }],
    lmstudio_models: [{ id: "", label: "Auto" }],
    claude_efforts: [],
    cursor_efforts: [],
    lmstudio_efforts: [],
    cursor_effort_models: [],
    effective: {
      cli_adapter: "claude",
      cli_adapter_source: "workspace",
      model: "",
      model_source: "cli-default",
      effort: "",
      effort_source: "cli-default",
      supports_model: true,
      supports_effort: false,
    },
  });
  mockedApi.workspaces.mockResolvedValue([
    { id: "w1", slug: "loregarden", name: "Loregarden", repo_path: "/tmp" } as never,
    { id: "w2", slug: "blobert", name: "Blobert", repo_path: "/tmp/b" } as never,
  ]);
  mockedApi.initiatives.mockResolvedValue([
    {
      id: "i1",
      external_id: "INI-1",
      title: "Cross-repo launch",
      state: "in_progress",
    } as never,
  ]);
  fakeChatServer();
  // Both the slug and the bound thread are persisted, so leaving them set
  // would leak across tests.
  useUiStore.setState({
    baxterHistoryOpen: false,
    baxterWorkbenchOpen: null,
    chatScope: "",
    workspace: "all",
    baxterChatSessionId: "",
  });
}
