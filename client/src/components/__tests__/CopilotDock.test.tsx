import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { api } from "../../api/client";
import { CopilotDock } from "../CopilotDock";
import { useActiveChatSession, type ChatAgents } from "../../hooks/useActiveChatSession";
import { useTerminalTarget } from "../../hooks/useTerminalTarget";
import { findUsabilityProblems } from "../../lib/usabilityCheck";
import { useUiStore } from "../../state/uiStore";
import { installPointerEvents } from "../../test/pointerEvents";

jest.mock("../../hooks/useActiveChatSession");
jest.mock("../../hooks/useTerminalTarget");
// The workspace opens real websockets and paints through canvases; neither
// exists here, and these tests cover the dock lifecycle around it.
jest.mock("../TerminalWorkspace", () => ({
  TerminalWorkspace: ({
    workspaceSlug,
    visible,
  }: {
    workspaceSlug: string;
    visible: boolean;
  }) => (
    <div data-testid="terminal-panel" data-visible={visible}>
      {workspaceSlug}
    </div>
  ),
}));
jest.mock("../../api/client", () => {
  const actual = jest.requireActual("../../api/client");
  return { ...actual, api: { ...actual.api, baxterChatSessions: jest.fn() } };
});
jest.mock("../../hooks/useApprovalResolution", () => ({
  useApprovalResolution: () => ({
    mutate: jest.fn(),
    isPending: false,
    isError: false,
    error: null,
    variables: undefined,
  }),
}));

const mockResolver = useActiveChatSession as jest.MockedFunction<typeof useActiveChatSession>;
const mockTerminal = useTerminalTarget as jest.MockedFunction<typeof useTerminalTarget>;

const mockedApi = api as jest.Mocked<typeof api>;

function bind(overrides: Partial<ReturnType<typeof useActiveChatSession>>) {
  return {
    session: null,
    label: "",
    ticketId: "t1",
    pendingApprovals: [],
    archive: null,
    agents: null,
    assistantLabel: "Baxter",
    maximisePath: null,
    composerBlocked: null,
    ...overrides,
    // As the real hook does: the openers follow the bound conversation's kind.
    promptKind: overrides.promptKind ?? overrides.session?.kind ?? "",
  } as ReturnType<typeof useActiveChatSession>;
}

function renderDock(ui: ReactElement = <CopilotDock />) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>, {
    wrapper: MemoryRouter,
  });
}

function session(overrides = {}) {
  return {
    kind: "ticket-triage" as const,
    id: "t1",
    messages: [],
    isBusy: false,
    activeTurnId: null,
    canAct: true,
    isLoading: false,
    error: null,
    loadError: false,
    send: jest.fn().mockResolvedValue({}),
    stop: jest.fn().mockResolvedValue({}),
    isStopping: false,
    ...overrides,
  };
}

beforeEach(() => {
  jest.clearAllMocks();
  useUiStore.setState({
    copilotOpen: false,
    copilotHeight: 340,
    copilotHistoryOpen: false,
    terminalOpen: false,
  });
  mockTerminal.mockReturnValue({ workspaceSlug: "loregarden", agent: "implementer" });
});

it("draws nothing while collapsed — the action bar is the collapsed state", () => {
  // Two bars stacked was the pre-v6 shape. The dock is now only the panels.
  mockResolver.mockReturnValue(bind({ session: session(), label: "Ticket triage" }));

  const { container } = render(<CopilotDock />, { wrapper: MemoryRouter });

  expect(container).toBeEmptyDOMElement();
});

it("tells the layout whether a panel is showing, so a right dock can collapse", () => {
  // Docked right, the layout reserves a column only while this is true; a
  // closed omnibar used to leave that column standing empty.
  mockResolver.mockReturnValue(bind({ session: session(), label: "Ticket triage" }));
  useUiStore.setState({ copilotOpen: true, dockPanelsVisible: false });

  const { rerender } = render(<CopilotDock />, { wrapper: MemoryRouter });
  expect(useUiStore.getState().dockPanelsVisible).toBe(true);

  act(() => useUiStore.setState({ copilotOpen: false }));
  rerender(<CopilotDock />);
  expect(useUiStore.getState().dockPanelsVisible).toBe(false);
});

it("shows the bound conversation's turns once opened", () => {
  mockResolver.mockReturnValue(bind({ session: session(), label: "Ticket triage" }));
  useUiStore.setState({ copilotOpen: true });

  render(<CopilotDock />, { wrapper: MemoryRouter });

  expect(screen.getByText("No messages yet.")).toBeInTheDocument();
});

it("renders pending approvals as an in-thread agent ask, not dock chrome", () => {
  mockResolver.mockReturnValue(
    bind({
      session: session({
        messages: [{ id: "m1", role: "user", content: "Ship queue history" }],
        isBusy: true,
      }),
      label: "Ticket triage",
      pendingApprovals: [
        {
          id: "a1",
          title: "Which shape?",
          level: "low",
          workspace_slug: "loregarden",
          stage_key: "home-chat",
          stage_name: "Home chat",
          impact: "",
          ticket_id: "",
          ticket_external_id: "",
          kind: "cli_question",
          status: "pending",
          run_id: "r1",
          tool_name: "AskUserQuestion",
          tool_input_json: "{}",
          cli_adapter: "claude",
          questions: [
            {
              question: "Which shape?",
              header: "Shape",
              options: [{ label: "Cards" }, { label: "Table" }],
            },
          ],
        },
      ],
    }),
  );
  useUiStore.setState({ copilotOpen: true });

  render(<CopilotDock />, { wrapper: MemoryRouter });

  const ask = screen.getByLabelText("Agent is asking");
  expect(ask).toBeInTheDocument();
  expect(screen.queryByText("Needs attention")).not.toBeInTheDocument();
  expect(document.querySelector(".lg-chat-messages")).toContainElement(ask);
  expect(document.querySelector(".pending-approvals--ask")).toBeTruthy();
  expect(screen.queryByText("Baxter is looking…")).not.toBeInTheDocument();
  expect(screen.queryByText("Assistant is thinking…")).not.toBeInTheDocument();
});

it("stays empty when no screen owns a conversation and no shell was asked for", () => {
  mockResolver.mockReturnValue(bind({ session: null, label: "" }));
  useUiStore.setState({ copilotOpen: true });

  const { container } = render(<CopilotDock />, { wrapper: MemoryRouter });

  expect(container).toBeEmptyDOMElement();
});

it("sends an opener through the bound session rather than filling a composer", () => {
  // The composer moved to the action bar, so an opener that only set draft text
  // would land in a box the operator is not looking at.
  const bound = session();
  mockResolver.mockReturnValue(bind({ session: bound, label: "Ticket triage" }));
  useUiStore.setState({ copilotOpen: true });

  render(<CopilotDock />, { wrapper: MemoryRouter });
  fireEvent.click(screen.getByRole("button", { name: "What is blocking this ticket?" }));

  expect(bound.send).toHaveBeenCalledWith("What is blocking this ticket?", {
    autoApprove: false,
  });
});

it("keeps the openers beside the turns, not between them and the composer", () => {
  mockResolver.mockReturnValue(bind({ session: session(), label: "Ticket triage" }));
  useUiStore.setState({ copilotOpen: true });

  const { container } = render(<CopilotDock />, { wrapper: MemoryRouter });

  const rail = container.querySelector(".copilot-dock-rail");
  expect(rail).not.toBeNull();
  expect(rail).toContainElement(
    screen.getByRole("button", { name: "What is blocking this ticket?" }),
  );
});

it("offers no openers once the thread has turns", () => {
  mockResolver.mockReturnValue(
    bind({
      session: session({ messages: [{ role: "user", content: "hi" }] }),
      label: "Ticket triage",
    }),
  );
  useUiStore.setState({ copilotOpen: true });

  render(<CopilotDock />, { wrapper: MemoryRouter });

  expect(screen.queryByText("Try asking")).not.toBeInTheDocument();
});

describe("the head", () => {
  const archive = () => ({
    scope: "loregarden",
    sessionId: "s1",
    openSession: jest.fn(),
    startNewChat: jest.fn(),
    sendInNewChat: jest.fn().mockResolvedValue(undefined),
    forkSession: jest.fn().mockResolvedValue(undefined),
    forkFromMessage: jest.fn().mockResolvedValue(undefined),
  });

  function agents(overrides: Partial<ChatAgents> = {}): ChatAgents {
    return {
      options: [
        { id: "planner", label: "Plan", description: "Dates and pace" },
        { id: "baxter", label: "Triage", description: "The tickets underneath" },
      ],
      active: "planner",
      select: jest.fn(),
      ...overrides,
    };
  }

  beforeEach(() => {
    useUiStore.setState({ copilotOpen: true });
  });

  it("is Baxter on a ticket too, with the run it is reading as the context", () => {
    mockResolver.mockReturnValue(
      bind({ session: session(), label: "lg-external-harness-1413 · test-design" }),
    );

    renderDock();

    const region = screen.getByRole("region", { name: "Baxter" });
    expect(region).toHaveTextContent("lg-external-harness-1413 · test-design");
    // A ticket's thread keeps no others and has no page of its own.
    expect(screen.queryByRole("button", { name: "New chat" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "History" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Open full page" })).not.toBeInTheDocument();
    expect(screen.queryByRole("radiogroup")).not.toBeInTheDocument();
  });

  it("says Baxter is looking while a turn runs, never a nameless assistant", () => {
    mockResolver.mockReturnValue(bind({ session: session({ isBusy: true }), label: "x" }));

    renderDock();

    expect(screen.getByText("Baxter is looking…")).toBeInTheDocument();
    expect(screen.queryByText(/Assistant is thinking/)).not.toBeInTheDocument();
  });

  it("starts a fresh thread and gives the archive's place back to it", () => {
    const bound = archive();
    mockResolver.mockReturnValue(
      bind({ session: session({ kind: "baxter-home" }), label: "loregarden", archive: bound }),
    );
    useUiStore.setState({ copilotHistoryOpen: true });

    renderDock();
    fireEvent.click(screen.getByRole("button", { name: "New chat" }));

    expect(bound.startNewChat).toHaveBeenCalled();
    expect(useUiStore.getState().copilotHistoryOpen).toBe(false);
    expect(useUiStore.getState().copilotOpen).toBe(true);
  });

  it("opens and closes the archive without collapsing the thread", () => {
    mockResolver.mockReturnValue(
      bind({ session: session({ kind: "baxter-home" }), label: "loregarden", archive: archive() }),
    );

    renderDock();
    const history = screen.getByRole("button", { name: "History" });
    expect(history).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(history);
    expect(useUiStore.getState().copilotHistoryOpen).toBe(true);
    expect(history).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(history);
    expect(useUiStore.getState().copilotHistoryOpen).toBe(false);
    expect(useUiStore.getState().copilotOpen).toBe(true);
  });

  it("opens the workspace thread full-page on the chat screen", () => {
    mockResolver.mockReturnValue(
      bind({ session: session({ kind: "baxter-home" }), label: "loregarden", maximisePath: "/chat" }),
    );
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/queue"]}>
          <CopilotDock />
          <Routes>
            <Route path="/chat" element={<p>chat page</p>} />
            <Route path="*" element={null} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Open full page" }));

    expect(screen.getByText("chat page")).toBeInTheDocument();
  });

  it("closes to the bar", () => {
    mockResolver.mockReturnValue(bind({ session: session(), label: "x" }));

    renderDock();
    fireEvent.click(screen.getByRole("button", { name: "Close Baxter" }));

    expect(useUiStore.getState().copilotOpen).toBe(false);
  });

  describe("Baxter's modes", () => {
    function bindPlanner(overrides: Partial<ReturnType<typeof useActiveChatSession>> = {}) {
      return bind({
        session: session({ kind: "initiative-planner", id: "init1" }),
        label: "init-tinkercg-build-1",
        ticketId: null,
        agents: agents(),
        ...overrides,
      });
    }

    it("shows the bound mode as checked", () => {
      mockResolver.mockReturnValue(bindPlanner());
      renderDock();

      expect(screen.getByRole("radiogroup", { name: "Baxter mode" })).toBeInTheDocument();
      expect(screen.getByRole("radio", { name: "Plan" })).toHaveAttribute("aria-checked", "true");
      expect(screen.getByRole("radio", { name: "Triage" })).toHaveAttribute("aria-checked", "false");
    });

    it("switches on a click, and by arrow keys as a radio group does", () => {
      const select = jest.fn();
      mockResolver.mockReturnValue(bindPlanner({ agents: agents({ select }) }));
      renderDock();

      fireEvent.click(screen.getByRole("radio", { name: "Triage" }));
      expect(select).toHaveBeenLastCalledWith("baxter");

      const plan = screen.getByRole("radio", { name: "Plan" });
      expect(plan).toHaveAttribute("tabindex", "0");
      expect(screen.getByRole("radio", { name: "Triage" })).toHaveAttribute("tabindex", "-1");
      select.mockClear();
      fireEvent.keyDown(plan, { key: "ArrowLeft" });
      expect(select).toHaveBeenLastCalledWith("baxter");
    });

    it("holds the switch while a turn runs, so the reply lands where it was asked", () => {
      const select = jest.fn();
      mockResolver.mockReturnValue(
        bindPlanner({
          session: session({ kind: "initiative-planner", id: "init1", isBusy: true }),
          agents: agents({ select }),
        }),
      );
      renderDock();

      expect(screen.getByRole("radio", { name: "Triage" })).toBeDisabled();
      fireEvent.keyDown(screen.getByRole("radio", { name: "Plan" }), { key: "ArrowRight" });
      expect(select).not.toHaveBeenCalled();
    });

    it("keeps the switch usable while plan mode cannot take a message", () => {
      mockResolver.mockReturnValue(bindPlanner({ composerBlocked: "Attach a milestone first" }));
      renderDock();

      expect(screen.getByRole("radio", { name: "Triage" })).toBeEnabled();
    });
  });
});

describe("the chat archive", () => {
  const ARCHIVE_ENTRY = {
    id: "s2",
    title: "Queue triage",
    message_count: 4,
    preview: "Two lanes are idle.",
    created_at: "2026-08-04T10:00:00Z",
    updated_at: "2026-08-04T10:00:00Z",
  };

  const bindBaxter = (overrides = {}) =>
    bind({
      session: session({ kind: "baxter-home" }),
      label: "Baxter · loregarden",
      ticketId: null,
      archive: {
        scope: "loregarden",
        sessionId: "s1",
        openSession: jest.fn(),
        startNewChat: jest.fn(),
        sendInNewChat: jest.fn().mockResolvedValue(undefined),
        forkSession: jest.fn().mockResolvedValue(undefined),
        forkFromMessage: jest.fn().mockResolvedValue(undefined),
        ...overrides,
      },
    });

  beforeEach(() => {
    mockedApi.baxterChatSessions.mockResolvedValue([ARCHIVE_ENTRY]);
  });

  it("takes the openers' place in the rail rather than covering the turns", async () => {
    mockResolver.mockReturnValue(bindBaxter());
    useUiStore.setState({ copilotOpen: true, copilotHistoryOpen: true });

    const { container } = renderDock();

    const rail = container.querySelector(".copilot-dock-rail");
    expect(rail).not.toBeNull();
    expect(rail).toContainElement(await screen.findByText("Queue triage"));
    expect(screen.queryByText("Try asking")).not.toBeInTheDocument();
  });

  it("opens the picked conversation and gives the rail back", async () => {
    const openSession = jest.fn();
    mockResolver.mockReturnValue(bindBaxter({ openSession }));
    useUiStore.setState({ copilotOpen: true, copilotHistoryOpen: true });

    renderDock();
    fireEvent.click(await screen.findByText("Queue triage"));

    expect(openSession).toHaveBeenCalledWith("s2");
    expect(useUiStore.getState().copilotHistoryOpen).toBe(false);
  });

  it("leaves the openers up while nobody asked for the archive", () => {
    mockResolver.mockReturnValue(bindBaxter());
    useUiStore.setState({ copilotOpen: true, copilotHistoryOpen: false });

    renderDock();

    expect(screen.getByText("Try asking")).toBeInTheDocument();
    expect(mockedApi.baxterChatSessions).not.toHaveBeenCalled();
  });

  it("shows the archive over a thread that already has turns", async () => {
    // The openers are withheld once a conversation is under way; the archive is
    // not — switching threads is exactly what you do mid-conversation.
    mockResolver.mockReturnValue(
      bind({
        session: session({ kind: "baxter-home", messages: [{ role: "user", content: "hi" }] }),
        label: "Baxter · loregarden",
        ticketId: null,
        archive: {
          scope: "loregarden",
          sessionId: "s1",
          openSession: jest.fn(),
          startNewChat: jest.fn(),
          sendInNewChat: jest.fn().mockResolvedValue(undefined),
          forkSession: jest.fn().mockResolvedValue(undefined),
          forkFromMessage: jest.fn().mockResolvedValue(undefined),
        },
      }),
    );
    useUiStore.setState({ copilotOpen: true, copilotHistoryOpen: true });

    renderDock();

    expect(await screen.findByText("Queue triage")).toBeInTheDocument();
  });

  it("says so when there is nothing archived yet", async () => {
    mockedApi.baxterChatSessions.mockResolvedValue([]);
    mockResolver.mockReturnValue(bindBaxter());
    useUiStore.setState({ copilotOpen: true, copilotHistoryOpen: true });

    renderDock();

    await waitFor(() =>
      expect(screen.getByText(/No conversations yet/i)).toBeInTheDocument(),
    );
  });

  it("keeps the openers on a conversation that has no archive", () => {
    // Ticket triage: the history flag may still be set from a Baxter thread,
    // and must not blank the rail here.
    mockResolver.mockReturnValue(bind({ session: session(), label: "Ticket triage" }));
    useUiStore.setState({ copilotOpen: true, copilotHistoryOpen: true });

    renderDock();

    expect(screen.getByText("Try asking")).toBeInTheDocument();
  });
});

describe("the terminal pane", () => {
  const openDock = () =>
    mockResolver.mockReturnValue(bind({ session: session(), label: "Ticket triage" }));

  it("spawns no shell until someone asks for one", () => {
    // Mounting the panel starts a real login shell. Opening the chat is not
    // asking for one, and a process nobody requested is the wrong default.
    openDock();
    useUiStore.setState({ copilotOpen: true, terminalOpen: false });

    render(<CopilotDock />, { wrapper: MemoryRouter });

    expect(screen.queryByTestId("terminal-panel")).not.toBeInTheDocument();
  });

  it("opens a shell in the workspace the screen is showing", () => {
    // The control lives in the action bar now; the dock follows the store.
    openDock();
    useUiStore.setState({ copilotOpen: true, terminalOpen: true });

    render(<CopilotDock />, { wrapper: MemoryRouter });

    expect(screen.getByTestId("terminal-panel")).toHaveTextContent("loregarden");
  });

  it("keeps the chat when the terminal is open", () => {
    // Side by side, not instead of — the dock is still the way into the chat.
    openDock();
    useUiStore.setState({ copilotOpen: true, terminalOpen: true });

    render(<CopilotDock />, { wrapper: MemoryRouter });

    expect(screen.getByTestId("terminal-panel")).toBeInTheDocument();
    expect(screen.getByText("No messages yet.")).toBeInTheDocument();
  });

  it("will not open a shell with nowhere to run it", () => {
    // A shell needs a cwd more than the header needs a label, and "all
    // workspaces" names no directory.
    openDock();
    mockTerminal.mockReturnValue({ workspaceSlug: "", agent: "" });
    useUiStore.setState({ copilotOpen: true, terminalOpen: true });

    render(<CopilotDock />, { wrapper: MemoryRouter });

    expect(screen.queryByTestId("terminal-panel")).not.toBeInTheDocument();
  });

  it("keeps the shell when the chat collapses", () => {
    // Collapsing the conversation says nothing about wanting the shell gone.
    // Reaping it here is what made "just a terminal" impossible.
    openDock();
    useUiStore.setState({ copilotOpen: true, terminalOpen: true });
    const { rerender } = render(<CopilotDock />, { wrapper: MemoryRouter });
    expect(screen.getByTestId("terminal-panel")).toBeInTheDocument();

    act(() => useUiStore.setState({ copilotOpen: false }));
    rerender(<CopilotDock />);

    expect(screen.getByTestId("terminal-panel")).toBeInTheDocument();
    expect(screen.queryByText("No messages yet.")).not.toBeInTheDocument();
  });

  it("keeps the shell when the terminal itself is closed", () => {
    // Closing the omnibar is hide, not kill: cwd, jobs, and scrollback have to
    // survive a toggle. The panel stays mounted (kept) until the screen names
    // a different workspace.
    openDock();
    useUiStore.setState({ copilotOpen: true, terminalOpen: true });
    const { rerender, container } = render(<CopilotDock />, { wrapper: MemoryRouter });
    expect(screen.getByTestId("terminal-panel")).toBeInTheDocument();

    act(() => useUiStore.setState({ terminalOpen: false }));
    rerender(<CopilotDock />);

    expect(screen.getByTestId("terminal-panel")).toBeInTheDocument();
    expect(container.querySelector(".copilot-dock-terminal.is-kept")).not.toBeNull();
  });

  it("reaps the kept shell when the screen names a different workspace", () => {
    openDock();
    useUiStore.setState({ copilotOpen: false, terminalOpen: true });
    const { rerender } = render(<CopilotDock />, { wrapper: MemoryRouter });
    expect(screen.getByTestId("terminal-panel")).toHaveTextContent("loregarden");

    act(() => {
      useUiStore.setState({ terminalOpen: false });
      mockTerminal.mockReturnValue({ workspaceSlug: "other-repo", agent: "" });
    });
    rerender(<CopilotDock />);

    expect(screen.queryByTestId("terminal-panel")).not.toBeInTheDocument();
  });

  it("hosts a shell on a screen with no conversation", () => {
    // A shell is scoped to the workspace, not to a chat. Returning early when
    // no session exists is what made the action bar's button do nothing on the
    // console screen.
    mockResolver.mockReturnValue(bind({ session: null, label: "" }));
    useUiStore.setState({ copilotOpen: false, terminalOpen: true });

    render(<CopilotDock />, { wrapper: MemoryRouter });

    expect(screen.getByTestId("terminal-panel")).toHaveTextContent("loregarden");
  });
});

describe("resizing the dock", () => {
  installPointerEvents();

  // `copilotHeight`/`copilotWidth` were clamped, persisted and documented as
  // holding "a restored or dragged" size, but nothing outside the store's own
  // test ever called their setters — so the dock was pinned to its default and
  // a long answer had to be read through a 340px slot.
  function openDock() {
    mockResolver.mockReturnValue(bind({ session: session(), label: "Ticket triage" }));
    useUiStore.setState({ copilotOpen: true });
    // A tall window, so the 44vh cap is only in play in the test that asserts
    // it. jsdom's default 768px would cap at 338 and quietly swallow the rest.
    window.innerHeight = 2000;
  }

  function drag(handle: Element, from: number, to: number, axis: "y" | "x" = "y") {
    const at = (value: number) => (axis === "y" ? { clientY: value } : { clientX: value });
    fireEvent.pointerDown(handle, { button: 0, pointerId: 1, ...at(from) });
    fireEvent.pointerMove(handle, { pointerId: 1, ...at(to) });
    fireEvent.pointerUp(handle, { pointerId: 1 });
  }

  it("grows the dock when dragged away from the edge it sits on", () => {
    openDock();
    renderDock();

    // Bottom dock: upwards is towards the page, so it adds.
    drag(screen.getByRole("separator"), 500, 420);

    expect(useUiStore.getState().copilotHeight).toBe(420);
  });

  it("shrinks it when dragged back", () => {
    openDock();
    renderDock();

    drag(screen.getByRole("separator"), 500, 560);

    expect(useUiStore.getState().copilotHeight).toBe(280);
  });

  it("stops where the dock stops rather than running into a dead range", () => {
    // The dock is capped at 44vh, so on a short window most of the store's
    // 720px range renders identically. Without this the grip would keep
    // reporting progress the dock cannot show.
    openDock();
    window.innerHeight = 800; // 44vh = 352
    renderDock();

    drag(screen.getByRole("separator"), 500, 0);

    expect(useUiStore.getState().copilotHeight).toBe(352);
  });

  it("holds the store's own floor", () => {
    openDock();
    renderDock();

    drag(screen.getByRole("separator"), 500, 900);

    expect(useUiStore.getState().copilotHeight).toBe(180);
  });

  it("nudges with the arrow keys, so it is not a mouse-only control", () => {
    openDock();
    renderDock();
    const handle = screen.getByRole("separator");

    fireEvent.keyDown(handle, { key: "ArrowUp" });
    expect(useUiStore.getState().copilotHeight).toBe(364);

    fireEvent.keyDown(handle, { key: "ArrowDown" });
    expect(useUiStore.getState().copilotHeight).toBe(340);
  });

  it("resets to the default on a double-click", () => {
    openDock();
    useUiStore.setState({ copilotHeight: 600 });
    renderDock();

    fireEvent.doubleClick(screen.getByRole("separator"));

    expect(useUiStore.getState().copilotHeight).toBe(340);
  });

  it("resizes the width when the dock is on the right edge", () => {
    openDock();
    useUiStore.setState({ utilityDockEdge: "right", copilotWidth: 380 });
    renderDock();

    const handle = screen.getByRole("separator");
    expect(handle.getAttribute("aria-orientation")).toBe("vertical");
    drag(handle, 900, 820, "x");

    expect(useUiStore.getState().copilotWidth).toBe(460);
  });

  it("offers no grip while collapsed, when there is no panel to resize", () => {
    mockResolver.mockReturnValue(bind({ session: session(), label: "Ticket triage" }));

    renderDock();

    expect(screen.queryByRole("separator")).toBeNull();
  });
});

describe("a chat wait", () => {
  const wait = {
    command: "git push origin HEAD",
    log_path: "/tmp/lg-wait/push.log",
    registered_at: "2026-10-27T21:00:00Z",
    deadline: "2026-10-27T23:00:00Z",
  };

  beforeEach(() => {
    useUiStore.setState({ copilotOpen: true });
    jest.useFakeTimers();
    jest.setSystemTime(new Date("2026-10-27T21:30:00Z"));
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  it("is a trailing status row, not thinking", () => {
    mockResolver.mockReturnValue(
      bind({
        session: session({
          kind: "ticket-triage",
          isBusy: true,
          activeTurnId: null,
          runStatus: "waiting",
          wait,
          messages: [
            {
              id: "m1",
              role: "assistant",
              content: "Pushing now.",
              created_at: "2026-10-27T21:00:00Z",
            },
          ],
        }),
        label: "Ticket triage",
      }),
    );

    renderDock();

    expect(screen.queryByText("Baxter is looking…")).not.toBeInTheDocument();
    expect(document.querySelector(".lg-chat-loading-walker")).toBeNull();
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("git push origin HEAD");
    expect(status).toHaveTextContent("/tmp/lg-wait/push.log");
    expect(status).toHaveTextContent("1h 30m left");
    expect(screen.getByTitle("git push origin HEAD")).toBeInTheDocument();
    const thread = document.querySelector(".copilot-dock-messages, .lg-chat-messages");
    expect(thread).toContainElement(status);
  });

  it("is a polite live region with no skeleton, spinner, or progress bar", () => {
    mockResolver.mockReturnValue(
      bind({
        session: session({
          kind: "ticket-triage",
          isBusy: true,
          activeTurnId: null,
          runStatus: "waiting",
          wait,
        }),
        label: "Ticket triage",
      }),
    );

    renderDock();

    const status = screen.getByRole("status");
    expect(status).toHaveAttribute("aria-live", "polite");
    expect(screen.queryByRole("progressbar")).toBeNull();
    expect(document.querySelector("[class*='skeleton']")).toBeNull();
    expect(document.querySelector(".lg-chat-loading-walker")).toBeNull();
    expect(status.innerHTML).toMatch(/--mono/);
    expect(findUsabilityProblems(status).map((problem) => problem.kind)).not.toContain(
      "tabular-list",
    );
  });

  it("keeps a multiline command to one line and puts the full command in title", () => {
    const command = "git push origin HEAD &&\npytest -q &&\necho done";
    mockResolver.mockReturnValue(
      bind({
        session: session({
          kind: "ticket-triage",
          isBusy: true,
          activeTurnId: null,
          runStatus: "waiting",
          wait: { ...wait, command },
        }),
        label: "Ticket triage",
      }),
    );

    renderDock();

    // getByTitle exact-matches a whitespace-collapsed title, so a command with
    // newlines has to be read off the attribute the spec names.
    const titled = screen.getByRole("status").querySelector("[title]");
    expect(titled).not.toBeNull();
    expect(titled?.textContent ?? "").not.toMatch(/\n/);
    expect(titled).toHaveAttribute("title", command);
  });
});

describe("the Chat and Artifacts tabs", () => {
  const withCards = [
    { id: "u1", role: "user", content: "Plan it" },
    {
      id: "a1",
      role: "assistant",
      content: "Here is the plan.",
      parts: [
        { primitive: "text" as const, content: "Here is the plan." },
        {
          primitive: "todo_list" as const,
          owner: "agent" as const,
          title: "Ship the guide",
          items: [{ id: "s1", text: "Write the tabs", status: "pending" as const }],
        },
      ],
    },
  ];

  it("collects what the chat made under Artifacts, drawn in full, and leads back to its turn", async () => {
    mockResolver.mockReturnValue(bind({ session: session({ messages: withCards }), label: "Ticket triage" }));
    useUiStore.setState({ copilotOpen: true });
    const scrollIntoView = jest.fn();
    Element.prototype.scrollIntoView = scrollIntoView;

    renderDock();

    const chatTab = screen.getByRole("tab", { name: "Chat" });
    const artifactsTab = screen.getByRole("tab", { name: "Artifacts 1" });
    expect(chatTab).toHaveAttribute("aria-selected", "true");

    fireEvent.click(artifactsTab);
    expect(artifactsTab).toHaveAttribute("aria-selected", "true");
    const list = screen.getByRole("list", { name: "1 artifacts in this chat" });
    expect(list).toHaveTextContent("Plan");
    expect(list).toHaveTextContent("Write the tabs");
    // The thread is hidden, not unmounted, so it keeps its place.
    expect(screen.getByText("Here is the plan.")).not.toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: /Show Plan Ship the guide in chat/ }));
    expect(chatTab).toHaveAttribute("aria-selected", "true");
    await waitFor(() => expect(scrollIntoView).toHaveBeenCalled());
  });

  it("says what will collect there before Baxter has made anything", () => {
    mockResolver.mockReturnValue(bind({ session: session(), label: "Ticket triage" }));
    useUiStore.setState({ copilotOpen: true });

    renderDock();
    fireEvent.click(screen.getByRole("tab", { name: "Artifacts" }));

    expect(screen.getByText(/Nothing made yet/)).toBeInTheDocument();
  });

  it("moves between the tabs with the arrow keys", () => {
    mockResolver.mockReturnValue(bind({ session: session(), label: "Ticket triage" }));
    useUiStore.setState({ copilotOpen: true });

    renderDock();
    fireEvent.keyDown(screen.getByRole("tab", { name: "Chat" }), { key: "ArrowRight" });

    expect(screen.getByRole("tab", { name: "Artifacts" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Artifacts" })).toHaveFocus();
  });
});
