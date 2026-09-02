/**
 * A conversation as a pane.
 *
 * The point of this primitive is that two of them are two *different*
 * conversations — the thing the store-backed session id made impossible — so
 * the assertions are about which thread each pane asks for and sends into, not
 * about the markup of a bubble, which `StudioChatMessages` already owns.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { api } from "../../../api/client";
import { ContainerPrimitiveHost } from "../primitives/registry";

jest.mock("../../../api/client", () => require("../../../test/apiClientMock"));

const mockApi = api as jest.Mocked<typeof api>;

function snapshot(id: string, text: string) {
  return {
    id,
    title: `Thread ${id}`,
    messages: [{ id: `m-${id}`, role: "assistant", content: text }],
    run_status: "idle",
    pending_approvals: [],
    active_turn_id: null,
  };
}

beforeEach(() => {
  jest.clearAllMocks();
  mockApi.baxterChatSession.mockImplementation(
    async (_slug: string, id: string) => snapshot(id, `hello from ${id}`) as never,
  );
  mockApi.sendBaxterChatMessage.mockResolvedValue(snapshot("s-1", "hi") as never);
});

function renderPane(settings: Record<string, unknown>, containerId = "c1") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ContainerPrimitiveHost
        containerId={containerId}
        settings={{ primitive_id: "chat_session", ...settings }}
      />
    </QueryClientProvider>,
  );
}

describe("before it is configured", () => {
  it("says so, and asks for no thread", async () => {
    renderPane({ workspace_slug: "loregarden", session_id: "" });
    expect(await screen.findByText(/no conversation yet/i)).toBeInTheDocument();
    expect(mockApi.baxterChatSession).not.toHaveBeenCalled();
  });

  it("needs the workspace too, not just the thread", async () => {
    // The snapshot is fetched by the pair; an id without the workspace that
    // owns it is a request that cannot be formed.
    renderPane({ workspace_slug: "", session_id: "s-1" });
    expect(await screen.findByText(/no conversation yet/i)).toBeInTheDocument();
    expect(mockApi.baxterChatSession).not.toHaveBeenCalled();
  });
});

describe("a configured pane", () => {
  it("shows the thread its settings name", async () => {
    renderPane({ workspace_slug: "loregarden", session_id: "s-1" });

    expect(await screen.findByText("hello from s-1")).toBeInTheDocument();
    expect(mockApi.baxterChatSession).toHaveBeenCalledWith("loregarden", "s-1");
  });

  it("sends into that thread and clears the box", async () => {
    const user = userEvent.setup();
    renderPane({ workspace_slug: "loregarden", session_id: "s-1" });
    await screen.findByText("hello from s-1");

    const box = screen.getByPlaceholderText("Reply to Baxter…");
    await user.type(box, "ship it");
    await user.keyboard("{Enter}");

    await waitFor(() =>
      expect(mockApi.sendBaxterChatMessage).toHaveBeenCalledWith(
        "loregarden",
        "s-1",
        "ship it",
        "",
      ),
    );
    expect(box).toHaveValue("");
  });

  it("refuses to send nothing", async () => {
    // The guarantee is the pane's; the enforcement is the composer's `canSend`,
    // which is why the pane holds no second copy of the rule.
    const user = userEvent.setup();
    renderPane({ workspace_slug: "loregarden", session_id: "s-1" });
    await screen.findByText("hello from s-1");

    await user.type(screen.getByPlaceholderText("Reply to Baxter…"), "   ");
    await user.keyboard("{Enter}");

    expect(mockApi.sendBaxterChatMessage).not.toHaveBeenCalled();
  });
});

describe("two panes are two conversations", () => {
  it("each holds the thread its own settings name", async () => {
    // The whole reason this primitive could not exist before: the session id
    // lived in one app-wide store, so a second pane would have shown — and
    // switched — the first one's thread.
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <ContainerPrimitiveHost
          containerId="a"
          settings={{ primitive_id: "chat_session", workspace_slug: "loregarden", session_id: "s-1" }}
        />
        <ContainerPrimitiveHost
          containerId="b"
          settings={{ primitive_id: "chat_session", workspace_slug: "loregarden", session_id: "s-2" }}
        />
      </QueryClientProvider>,
    );

    expect(await screen.findByText("hello from s-1")).toBeInTheDocument();
    expect(await screen.findByText("hello from s-2")).toBeInTheDocument();
  });
});
