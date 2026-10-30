/** BaxterChatPage while a turn waits on background work it registered. */
import { fireEvent, screen, within } from "@testing-library/react";

import { DEFAULT_RUNTIME } from "../../lib/runtimeSettings";
import { useUiStore } from "../../state/uiStore";
import {
  NO_WORKBENCH,
  fakeChatServer,
  mockedApi,
  renderChat,
  resetBaxterChatPage,
} from "./baxterChatPageHarness";

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
      initiatives: jest.fn(),
      runtimeOptions: jest.fn(),
      baxterChatSessions: jest.fn(),
      baxterChatSession: jest.fn(),
      createBaxterChatSession: jest.fn(),
      renameBaxterChatSession: jest.fn(),
      deleteBaxterChatSession: jest.fn(),
      setBaxterChatRuntime: jest.fn(),
      sendBaxterChatMessage: jest.fn(),
      stopBaxterChatTurn: jest.fn(),
      uploadBaxterChatAttachment: jest.fn(),
      resolveApproval: jest.fn(),
    },
  };
});

describe("BaxterChatPage — a waiting turn", () => {
  beforeEach(() => {
    resetBaxterChatPage();
  });

  it("shows a trailing wait status instead of thinking", async () => {
    const threads = fakeChatServer();
    threads.set("s-wait", {
      id: "s-wait",
      workspace_id: "w1",
      scope: "workspace",
      initiative_id: null,
      host_workspace_slug: "loregarden",
      ...NO_WORKBENCH,
      title: "Push",
      messages: [
        {
          id: "m1",
          role: "user",
          content: "push it",
          created_at: "2026-10-27T21:00:00Z",
        },
        {
          id: "m2",
          role: "assistant",
          content: "Pushing in the background.",
          created_at: "2026-10-27T21:00:01Z",
        },
      ],
      runtime: DEFAULT_RUNTIME,
      run_status: "waiting",
      active_turn_id: null,
      wait: {
        command: "git push origin HEAD",
        log_path: "/tmp/lg-wait/push.log",
        registered_at: "2026-10-27T21:00:00Z",
        deadline: "2026-10-27T23:00:00Z",
      },
      created_at: "2026-10-27T21:00:00Z",
      updated_at: "2026-10-27T21:00:01Z",
    });
    useUiStore.setState({ baxterChatSessionId: "s-wait" });

    renderChat();

    expect(await screen.findByText("Pushing in the background.")).toBeInTheDocument();
    expect(screen.queryByText("Baxter is looking…")).not.toBeInTheDocument();
    expect(document.querySelector(".lg-chat-loading-walker")).toBeNull();
    expect(document.querySelector(".baxter-avatar--walking")).toBeNull();
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("git push origin HEAD");
    expect(status).toHaveTextContent("/tmp/lg-wait/push.log");
    expect(status).toHaveTextContent(/left/);
    expect(screen.getByRole("button", { name: "Stop" })).toBeEnabled();
    expect(screen.getByPlaceholderText("Reply to Baxter…")).toBeEnabled();
    const suggested = screen.queryByRole("group", { name: "Suggested prompts" });
    if (suggested) {
      for (const button of within(suggested).getAllByRole("button")) {
        expect(button).toBeDisabled();
      }
    }
  });

  it("does not cancel a wait on Escape", async () => {
    const threads = fakeChatServer();
    threads.set("s-wait-esc", {
      id: "s-wait-esc",
      workspace_id: "w1",
      scope: "workspace",
      initiative_id: null,
      host_workspace_slug: "loregarden",
      ...NO_WORKBENCH,
      title: "Push",
      messages: [
        { id: "m1", role: "user", content: "push it", created_at: "2026-10-27T21:00:00Z" },
      ],
      runtime: DEFAULT_RUNTIME,
      run_status: "waiting",
      active_turn_id: null,
      wait: {
        command: "git push origin HEAD",
        log_path: "/tmp/lg-wait/push.log",
        registered_at: "2026-10-27T21:00:00Z",
        deadline: "2026-10-27T23:00:00Z",
      },
      created_at: "2026-10-27T21:00:00Z",
      updated_at: "2026-10-27T21:00:01Z",
    });
    useUiStore.setState({ baxterChatSessionId: "s-wait-esc" });

    renderChat();
    expect(await screen.findByRole("button", { name: "Stop" })).toBeEnabled();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(mockedApi.stopBaxterChatTurn).not.toHaveBeenCalled();
  });

  it("labels a wait-end report as Loregarden, not Baxter", async () => {
    const threads = fakeChatServer();
    threads.set("s-stop-msg", {
      id: "s-stop-msg",
      workspace_id: "w1",
      scope: "workspace",
      initiative_id: null,
      host_workspace_slug: "loregarden",
      ...NO_WORKBENCH,
      title: "Stopped",
      messages: [
        { id: "m1", role: "user", content: "push it", created_at: "2026-10-27T21:00:00Z" },
        {
          id: "m2",
          role: "system",
          content:
            "Stopped waiting for git push origin HEAD at your request. The process was not killed — ask again, or watch /tmp/lg-wait/push.log.",
          created_at: "2026-10-27T21:01:00Z",
        },
      ],
      runtime: DEFAULT_RUNTIME,
      run_status: "idle",
      active_turn_id: null,
      created_at: "2026-10-27T21:00:00Z",
      updated_at: "2026-10-27T21:01:00Z",
    });
    useUiStore.setState({ baxterChatSessionId: "s-stop-msg" });

    renderChat();

    expect(await screen.findByText(/Stopped waiting for git push origin HEAD/)).toBeInTheDocument();
    expect(screen.getByText("Loregarden")).toBeInTheDocument();
  });
});
