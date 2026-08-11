import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { AppActionBar } from "../AppActionBar";
import * as apiClient from "../../api/client";
import * as composerApiModule from "../../api/composerApi";
import { useActiveChatSession } from "../../hooks/useActiveChatSession";
import { useTerminalTarget } from "../../hooks/useTerminalTarget";
import { useComposerQueueStore } from "../../state/composerQueueStore";
import { useUiStore } from "../../state/uiStore";

jest.mock("../../hooks/useActiveChatSession");
jest.mock("../../hooks/useTerminalTarget");
jest.mock("../../api/client", () => jest.requireActual("../../test/apiClientMock"));
jest.mock("../../api/composerApi", () =>
  jest.requireActual("../../test/composerApiMock"),
);

const mockResolver = useActiveChatSession as jest.MockedFunction<typeof useActiveChatSession>;
const mockTerminal = useTerminalTarget as jest.MockedFunction<typeof useTerminalTarget>;
const mockApi = apiClient.api as unknown as Record<string, jest.Mock>;
const mockComposerApi = composerApiModule.composerApi as unknown as Record<string, jest.Mock>;

const PLACEHOLDER = "Message about this ticket…";
/** A branch conversation, so a busy turn does not divert the composer to `btw`. */
const BRANCH_PLACEHOLDER = "Message about this branch…";

/** The post-it strip, so its Send is not confused with the bar's own. */
function notesStrip() {
  return within(screen.getByRole("group", { name: "Notes and queued messages" }));
}

function renderBar() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <AppActionBar />
    </QueryClientProvider>,
  );
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

function type(value: string, placeholder = PLACEHOLDER) {
  const input = screen.getByPlaceholderText(placeholder);
  fireEvent.change(input, { target: { value } });
  return input;
}

beforeEach(() => {
  jest.clearAllMocks();
  useUiStore.setState({ copilotOpen: false, terminalOpen: false, utilityDockEdge: "bottom" });
  useComposerQueueStore.setState({ queues: {} });
  mockTerminal.mockReturnValue({ workspaceSlug: "loregarden", agent: "" });
  mockApi.ticket.mockResolvedValue({ id: "t1", artifacts: { logs: [], live: null } });
  mockApi.runtimeOptions.mockResolvedValue({});
  mockApi.skills.mockResolvedValue([]);
  mockComposerApi.editorSearch.mockResolvedValue([]);
  mockComposerApi.notes.mockResolvedValue([]);
});

describe("the / menu", () => {
  it("opens on a leading slash and lists the built-in commands", async () => {
    mockResolver.mockReturnValue(bind({ session: session(), label: "Ticket triage" }));
    renderBar();
    type("/");

    expect(await screen.findByRole("option", { name: /queue/ })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /note/ })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /help/ })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /clear/ })).toBeInTheDocument();
  });

  it("stays shut for a slash inside the message", () => {
    mockResolver.mockReturnValue(bind({ session: session(), label: "Ticket triage" }));
    renderBar();
    type("look at src/lib");

    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("completes the highlighted command on Enter instead of sending it", () => {
    const bound = session();
    mockResolver.mockReturnValue(bind({ session: bound, label: "Ticket triage" }));
    renderBar();
    const input = type("/qu");
    fireEvent.keyDown(input, { key: "Enter" });

    expect(bound.send).not.toHaveBeenCalled();
    expect(input).toHaveValue("/queue ");
  });

  it("leaves /note out where no workspace is named — it would have nowhere to live", async () => {
    mockTerminal.mockReturnValue({ workspaceSlug: "", agent: "" });
    mockResolver.mockReturnValue(bind({ session: session(), label: "Ticket triage" }));
    renderBar();
    type("/");

    expect(await screen.findByRole("option", { name: /queue/ })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /note/ })).not.toBeInTheDocument();
  });

  it("leaves /queue out where there is no conversation to queue into", async () => {
    mockResolver.mockReturnValue(bind({ session: null, label: "" }));
    renderBar();
    fireEvent.change(screen.getByPlaceholderText("Open a ticket or a branch to chat about it"), {
      target: { value: "/" },
    });

    expect(await screen.findByRole("option", { name: /note/ })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /queue/ })).not.toBeInTheDocument();
  });

  it("leaves skills out where the turn cannot carry one", async () => {
    // A ticket-triage turn has no skill field, so offering skills would be
    // offering something that quietly does nothing.
    mockApi.skills.mockResolvedValue(["review"]);
    mockResolver.mockReturnValue(bind({ session: session(), label: "Ticket triage" }));
    renderBar();
    type("/");

    await screen.findByRole("option", { name: /queue/ });
    expect(screen.queryByRole("option", { name: /review/ })).not.toBeInTheDocument();
    expect(mockApi.skills).not.toHaveBeenCalled();
  });
});

describe("the @ picker", () => {
  it("searches the screen's workspace and inserts the path it is given", async () => {
    mockComposerApi.editorSearch.mockResolvedValue([
      { name: "AppActionBar.tsx", repo_path: "client/src/AppActionBar.tsx", kind: "file" },
    ]);
    mockResolver.mockReturnValue(bind({ session: session(), label: "Ticket triage" }));
    renderBar();
    const input = type("look at @appact");

    await waitFor(() =>
      expect(mockComposerApi.editorSearch).toHaveBeenCalledWith("loregarden", "appact"),
    );
    fireEvent.keyDown(await screen.findByPlaceholderText(PLACEHOLDER), { key: "Enter" });

    expect(input).toHaveValue("look at @client/src/AppActionBar.tsx ");
  });

  it("keeps a directory reference open so the next keystroke narrows inside it", async () => {
    mockComposerApi.editorSearch.mockResolvedValue([
      { name: "components", repo_path: "client/src/components", kind: "directory" },
    ]);
    mockResolver.mockReturnValue(bind({ session: session(), label: "Ticket triage" }));
    renderBar();
    const input = type("@comp");

    await screen.findByRole("option", { name: /components/ });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(input).toHaveValue("@client/src/components/");
  });
});

describe("/help and /clear", () => {
  it("lists available commands without sending a turn", async () => {
    const bound = session();
    mockResolver.mockReturnValue(bind({ session: bound, label: "Ticket triage" }));
    renderBar();
    // A trailing space closes the `/` menu so Enter submits rather than completes.
    fireEvent.keyDown(type("/help "), { key: "Enter" });

    expect(bound.send).not.toHaveBeenCalled();
    expect(await screen.findByRole("region", { name: "Composer commands" })).toBeInTheDocument();
    expect(screen.getByText("/queue")).toBeInTheDocument();
  });

  it("empties the draft", () => {
    mockResolver.mockReturnValue(bind({ session: session(), label: "Ticket triage" }));
    renderBar();
    const input = type("/clear leftover");
    fireEvent.keyDown(input, { key: "Enter" });
    expect(input).toHaveValue("");
  });
});

describe("/queue", () => {
  it("holds the message instead of sending it, and shows it as held", () => {
    const bound = session({ kind: "branch-triage", isBusy: true });
    mockResolver.mockReturnValue(bind({ session: bound, label: "Branch", ticketId: null }));
    renderBar();
    const input = type("/queue run the tests", BRANCH_PLACEHOLDER);
    fireEvent.keyDown(input, { key: "Enter" });

    expect(bound.send).not.toHaveBeenCalled();
    expect(screen.getByText("run the tests")).toBeInTheDocument();
  });

  it("sends the held message once the conversation goes idle", async () => {
    const busy = session({ kind: "branch-triage", isBusy: true });
    mockResolver.mockReturnValue(bind({ session: busy, label: "Branch", ticketId: null }));
    const { rerender } = renderBar();
    fireEvent.keyDown(type("/q run the tests", BRANCH_PLACEHOLDER), { key: "Enter" });
    expect(busy.send).not.toHaveBeenCalled();

    const idle = session({ kind: "branch-triage", isBusy: false, send: busy.send });
    mockResolver.mockReturnValue(bind({ session: idle, label: "Branch", ticketId: null }));
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    rerender(
      <QueryClientProvider client={qc}>
        <AppActionBar />
      </QueryClientProvider>,
    );

    await waitFor(() =>
      expect(idle.send).toHaveBeenCalledWith("run the tests", {
        autoApprove: false,
        skill: "",
      }),
    );
  });

  it("drops a held message when it is cancelled", () => {
    mockResolver.mockReturnValue(
      bind({ session: session({ kind: "branch-triage", isBusy: true }), label: "Branch", ticketId: null }),
    );
    renderBar();
    fireEvent.keyDown(type("/q run the tests", BRANCH_PLACEHOLDER), { key: "Enter" });

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByText("run the tests")).not.toBeInTheDocument();
  });
});

describe("/note", () => {
  it("writes the post-it to the workspace rather than sending it", async () => {
    mockComposerApi.createNote.mockResolvedValue({
      id: "n1",
      body: "ask about the lane",
      sent_at: null,
      created_at: "",
      updated_at: "",
    });
    const bound = session();
    mockResolver.mockReturnValue(bind({ session: bound, label: "Ticket triage" }));
    renderBar();
    fireEvent.keyDown(type("/note ask about the lane"), { key: "Enter" });

    // The text goes to the note editor, not to the conversation.
    expect(bound.send).not.toHaveBeenCalled();
    fireEvent.click(notesStrip().getByRole("button", { name: "Keep" }));
    await waitFor(() =>
      expect(mockComposerApi.createNote).toHaveBeenCalledWith("loregarden", "ask about the lane"),
    );
  });

  it("sends a kept note into the conversation and stamps it", async () => {
    mockComposerApi.notes.mockResolvedValue([
      { id: "n1", body: "check the estimates", sent_at: null, created_at: "", updated_at: "" },
    ]);
    mockComposerApi.updateNote.mockResolvedValue({});
    const bound = session();
    mockResolver.mockReturnValue(bind({ session: bound, label: "Ticket triage" }));
    renderBar();

    await screen.findByText("check the estimates");
    fireEvent.click(notesStrip().getByRole("button", { name: "Send" }));

    expect(bound.send).toHaveBeenCalledWith("check the estimates", {
      autoApprove: false,
      skill: "",
    });
    await waitFor(() =>
      expect(mockComposerApi.updateNote).toHaveBeenCalledWith("loregarden", "n1", {
        mark_sent: true,
      }),
    );
  });

  it("offers a new chat only where there is an archive to start one in", async () => {
    mockComposerApi.notes.mockResolvedValue([
      { id: "n1", body: "check the estimates", sent_at: null, created_at: "", updated_at: "" },
    ]);
    mockResolver.mockReturnValue(bind({ session: session(), label: "Ticket triage" }));
    renderBar();

    await screen.findByText("check the estimates");
    expect(
      notesStrip().queryByRole("button", { name: "Send in new chat" }),
    ).not.toBeInTheDocument();
  });
});
