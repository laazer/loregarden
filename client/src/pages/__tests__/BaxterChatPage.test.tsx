import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { ApiError, api } from "../../api/client";
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
      sendBaxterChatMessage: jest.fn(),
    },
  };
});

const mockedApi = api as jest.Mocked<typeof api>;

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

describe("BaxterChatPage", () => {
  beforeEach(() => {
    sessionStorage.clear();
    mockedApi.approvals.mockResolvedValue([]);
    mockedApi.ticket.mockRejectedValue(new ApiError(404, "Example ticket not found"));
    mockedApi.ticketTree.mockResolvedValue([]);
    mockedApi.tickets.mockResolvedValue([]);
    mockedApi.workspaces.mockResolvedValue([
      { id: "w1", slug: "loregarden", name: "Loregarden", repo_path: "/tmp" } as never,
    ]);
    mockedApi.sendBaxterChatMessage.mockResolvedValue({ reply: "Model says ship Home polish." });
    useUiStore.setState({ baxterHistoryOpen: false });
  });

  it("shows a welcoming empty shell with the large Ask Baxter composer", async () => {
    renderChat();
    expect(screen.getByRole("heading", { name: /Good (morning|afternoon|evening)/i })).toBeInTheDocument();
    expect(screen.getByLabelText("Ask Baxter")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("What should we ship today?")).toBeInTheDocument();
    expect(screen.getByText("What should I look at first?")).toBeInTheDocument();
    await waitFor(() => expect(mockedApi.approvals).toHaveBeenCalled());
  });

  it("sends the prompt to the workspace Baxter chat API", async () => {
    renderChat();
    const input = screen.getByPlaceholderText("What should we ship today?");
    fireEvent.change(input, { target: { value: "Hello Baxter" } });
    fireEvent.click(screen.getByRole("button", { name: /Ask Baxter/i }));

    await waitFor(() => {
      expect(mockedApi.sendBaxterChatMessage).toHaveBeenCalledWith("loregarden", {
        content: "Hello Baxter",
        history: [],
      });
    });
    await waitFor(() => {
      expect(screen.getByText("Model says ship Home polish.")).toBeInTheDocument();
    });
  });

  it("bootstraps a handoff prompt from Home into the thread", async () => {
    sessionStorage.setItem(HOME_BAXTER_PROMPT_KEY, "What should I look at first this morning?");
    mockedApi.approvals.mockResolvedValue([
      {
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
      },
    ]);
    mockedApi.sendBaxterChatMessage.mockResolvedValue({
      reply: "Start with the Merge retro-tokens approval.",
    });

    renderChat();

    await waitFor(() => {
      expect(screen.getByText("What should I look at first this morning?")).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(mockedApi.sendBaxterChatMessage).toHaveBeenCalled();
      expect(screen.getByText("Start with the Merge retro-tokens approval.")).toBeInTheDocument();
      expect(screen.getByText("Merge retro-tokens")).toBeInTheDocument();
    });
    expect(sessionStorage.getItem(HOME_BAXTER_PROMPT_KEY)).toBeNull();
  });

  it("renders assistant replies as markdown", async () => {
    mockedApi.sendBaxterChatMessage.mockResolvedValue({
      reply: "**Ship Home polish** with `cursor` next.",
    });
    renderChat();
    const input = screen.getByPlaceholderText("What should we ship today?");
    fireEvent.change(input, { target: { value: "What next?" } });
    fireEvent.click(screen.getByRole("button", { name: /Ask Baxter/i }));

    await waitFor(() => {
      expect(screen.getByText("Ship Home polish").tagName).toBe("STRONG");
      expect(screen.getByText("cursor").tagName).toBe("CODE");
    });
  });

  it("shows an animated loading card while waiting on Baxter", async () => {
    let resolveReply: (value: { reply: string }) => void = () => undefined;
    mockedApi.sendBaxterChatMessage.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveReply = resolve;
        }),
    );
    renderChat();
    const input = screen.getByPlaceholderText("What should we ship today?");
    fireEvent.change(input, { target: { value: "Hello" } });
    fireEvent.click(screen.getByRole("button", { name: /Ask Baxter/i }));

    await waitFor(() => {
      expect(screen.getByText("Baxter is looking…")).toBeInTheDocument();
      expect(screen.getByText(/workspace model/i)).toBeInTheDocument();
      expect(document.querySelector(".lg-chat-loading-walker")).toBeTruthy();
      expect(document.querySelector(".baxter-avatar--full.baxter-avatar--typing")).toBeTruthy();
    });
    resolveReply({ reply: "On it." });
    await waitFor(() => expect(screen.getByText("On it.")).toBeInTheDocument());
  });

  it("surfaces API failures in the thread", async () => {
    mockedApi.sendBaxterChatMessage.mockRejectedValue(new ApiError(502, "Baxter unavailable: boom"));
    renderChat();
    const input = screen.getByPlaceholderText("What should we ship today?");
    fireEvent.change(input, { target: { value: "Hello" } });
    fireEvent.click(screen.getByRole("button", { name: /Ask Baxter/i }));

    await waitFor(() => {
      expect(screen.getByText("Baxter unavailable: boom")).toBeInTheDocument();
    });
  });

  it("starts a new chat when the global topbar reset fires", async () => {
    renderChat();
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
