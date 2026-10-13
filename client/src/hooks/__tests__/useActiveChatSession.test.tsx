import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { api } from "../../api/client";
import { useActiveChatSession } from "../useActiveChatSession";
import { INITIATIVE_PLANNING, INITIATIVE_TRIAGE } from "../../lib/dockChatPrompts";
import { useInitiativeAgentStore } from "../../state/initiativeAgentStore";
import { useTicketRefStore } from "../../state/ticketRefStore";
import { useUiStore } from "../../state/uiStore";

jest.mock("../../api/client", () => ({
  // Named export, not just `api`: the Baxter session narrows load failures with
  // `instanceof ApiError`, which throws on an undefined right-hand side.
  ApiError: class ApiError extends Error {},
  api: {
    triage: jest.fn().mockResolvedValue({ messages: [], run_status: "idle" }),
    approvals: jest.fn().mockResolvedValue([]),
    sendTriageMessage: jest.fn().mockResolvedValue({}),
    workspaces: jest.fn().mockResolvedValue([{ slug: "loregarden", name: "Loregarden" }]),
    baxterChatSession: jest.fn().mockResolvedValue({
      messages: [],
      runtime: {
        cli_adapter: "default",
        claude_model: "",
        cursor_model: "",
        codex_model: "",
        lmstudio_base_url: "",
        lmstudio_model: "",
        claude_effort: "",
        cursor_effort: "",
        lmstudio_effort: "",
      },
      run_status: "idle",
    }),
    plannerChat: jest.fn().mockResolvedValue({
      initiative_id: "init-tinkercg-build-1",
      messages: [],
      active_turn_id: null,
      runtime: {
        cli_adapter: "claude",
        claude_model: "opus",
        cursor_model: "",
        codex_model: "",
        lmstudio_base_url: "",
        lmstudio_model: "",
        claude_effort: "",
        cursor_effort: "",
        lmstudio_effort: "",
      },
      workspace_slug: "tinkercg",
    }),
    initiativePlan: jest.fn().mockResolvedValue({ milestones: [{ id: "m1" }] }),
  },
}));
jest.mock("../../lib/branchTriageApi", () => ({
  fetchBranchChat: jest.fn().mockResolvedValue({ messages: [], run_status: "idle" }),
  sendBranchChatMessage: jest.fn().mockResolvedValue({}),
}));

function wrapperFor(path: string) {
  return function Wrapper({ children }: { children: ReactNode }) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return (
      <QueryClientProvider client={client}>
        {/* Mirrors the real tree: the dock mounts *above* <Routes>, so no
            route has matched where the resolver runs. */}
        <MemoryRouter initialEntries={[path]}>
          {children}
          <Routes>
            <Route path="/tickets/:ticketId/:tab" element={<div />} />
            <Route path="*" element={<div />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );
  };
}

beforeEach(() => {
  useUiStore.setState({
    branchTriageWorkspaceSlug: "",
    branchTriageBranch: "",
    chatScope: "loregarden",
  });
});

it("binds to the ticket named in the path", () => {
  const { result } = renderHook(() => useActiveChatSession(), {
    wrapper: wrapperFor("/tickets/41aac2d7-26a6-4f0b-988a-fc220d8dfa6c/diff"),
  });

  expect(result.current.session?.kind).toBe("ticket-triage");
  expect(result.current.session?.id).toBe("41aac2d7-26a6-4f0b-988a-fc220d8dfa6c");
  expect(result.current.label).toBe("Ticket triage");
});

it("reads the ticket from the path, not from route params", () => {
  // useParams is empty above <Routes>; a params-based resolver would silently
  // bind to nothing here and the dock would never open on a ticket.
  const { result } = renderHook(() => useActiveChatSession(), {
    wrapper: wrapperFor("/tickets/9f3d1c02-7b64-4a51-9d8e-2c5b7a10ef33/logs"),
  });

  expect(result.current.session?.id).toBe("9f3d1c02-7b64-4a51-9d8e-2c5b7a10ef33");
});

it("binds to nothing while a shareable id is still being resolved", () => {
  // A ticket route also accepts `lor-mcp-gateway-142`, which TicketRouteResolver
  // swaps for the UUID. Binding the dock to the ref in between would have it
  // fetch triage and asides under an id no endpoint accepts.
  const { result } = renderHook(() => useActiveChatSession(), {
    wrapper: wrapperFor("/tickets/lor-mcp-gateway-142/diff"),
  });

  expect(result.current.session?.kind).not.toBe("ticket-triage");
});

it("binds to the branch conversation on the branch-triage screen", () => {
  useUiStore.setState({ branchTriageWorkspaceSlug: "loregarden", branchTriageBranch: "feat/x" });

  const { result } = renderHook(() => useActiveChatSession(), {
    wrapper: wrapperFor("/branch-triage"),
  });

  expect(result.current.session?.kind).toBe("branch-triage");
  expect(result.current.label).toBe("Branch · feat/x");
});

it("binds to nothing when the branch screen has no branch selected", () => {
  useUiStore.setState({ branchTriageWorkspaceSlug: "loregarden", branchTriageBranch: "" });

  const { result } = renderHook(() => useActiveChatSession(), {
    wrapper: wrapperFor("/branch-triage"),
  });

  expect(result.current.session).toBeNull();
});

it("falls back to the workspace Baxter thread on a screen that owns no conversation", () => {
  const { result } = renderHook(() => useActiveChatSession(), {
    wrapper: wrapperFor("/studio/agents"),
  });

  expect(result.current.session?.kind).toBe("baxter-home");
  expect(result.current.label).toBe("Baxter · loregarden");
});

it("falls back to Baxter on the queue screen too", () => {
  const { result } = renderHook(() => useActiveChatSession(), {
    wrapper: wrapperFor("/queue"),
  });

  expect(result.current.session?.kind).toBe("baxter-home");
});

it("binds to nothing on the Baxter chat screen, which composes for itself", () => {
  const { result } = renderHook(() => useActiveChatSession(), {
    wrapper: wrapperFor("/chat"),
  });

  expect(result.current.session).toBeNull();
  expect(result.current.composedOnScreen).toBe(true);
  // The archive still rides along: the bar draws the model picker there.
  expect(result.current.archive?.scope).toBe("loregarden");
});

it("binds to nothing on Home, whose hero is the way into the same thread", () => {
  const { result } = renderHook(() => useActiveChatSession(), {
    wrapper: wrapperFor("/"),
  });

  expect(result.current.session).toBeNull();
  expect(result.current.composedOnScreen).toBe(true);
});

it("binds to nothing on an initiative plan page, whose planner chat composes for itself", () => {
  const { result } = renderHook(() => useActiveChatSession(), {
    wrapper: wrapperFor("/initiatives/init-tinkercg-build-1"),
  });

  expect(result.current.session).toBeNull();
  expect(result.current.composedOnScreen).toBe(true);
  // No Baxter archive: the conversation on that page is the planner's.
  expect(result.current.archive).toBeNull();
});

it("binds the planner's model, not Baxter's, on an initiative plan page", async () => {
  const uuid = "6402cb6c-1d41-459f-af5e-8ad1c4dab5fb";
  useTicketRefStore.setState({ uuidByRef: { "init-tinkercg-build-1": uuid }, refByUuid: {} });
  try {
    const { result } = renderHook(() => useActiveChatSession(), {
      wrapper: wrapperFor("/initiatives/init-tinkercg-build-1"),
    });

    await waitFor(() => expect(result.current.model?.runtime.claude_model).toBe("opus"));
    expect(result.current.model?.scopeLabel).toBe("Initiative planner");
    // Options come from the workspace the planner runs in, not the chat workspace.
    expect(result.current.model?.workspaceSlug).toBe("tinkercg");
    // By the UUID the page uses: the planner endpoint 404s on the shareable ref.
    expect(api.plannerChat).toHaveBeenCalledWith(uuid);
    // The bar suggests planning questions, sendable once the plan has a milestone.
    expect(result.current.screenPrompts?.kind).toBe(INITIATIVE_PLANNING);
    await waitFor(() => expect(result.current.screenPrompts?.unavailable).toBe(false));
    expect(api.plannerChat).not.toHaveBeenCalledWith("init-tinkercg-build-1");
  } finally {
    useTicketRefStore.setState({ uuidByRef: {}, refByUuid: {} });
  }
});

it("holds the planner's suggestions while the initiative has no milestone to schedule", async () => {
  const uuid = "6402cb6c-1d41-459f-af5e-8ad1c4dab5fb";
  useTicketRefStore.setState({ uuidByRef: { "init-tinkercg-build-1": uuid }, refByUuid: {} });
  (api.initiativePlan as jest.Mock).mockResolvedValueOnce({ milestones: [] });
  try {
    const { result } = renderHook(() => useActiveChatSession(), {
      wrapper: wrapperFor("/initiatives/init-tinkercg-build-1"),
    });

    await waitFor(() => expect(api.initiativePlan).toHaveBeenCalledWith(uuid));
    await waitFor(() => expect(result.current.model?.runtime.claude_model).toBe("opus"));
    expect(result.current.screenPrompts?.unavailable).toBe(true);
  } finally {
    useTicketRefStore.setState({ uuidByRef: {}, refByUuid: {} });
  }
});

it("binds Baxter's model on an initiative plan page when the panel shows Baxter", async () => {
  const uuid = "6402cb6c-1d41-459f-af5e-8ad1c4dab5fb";
  useTicketRefStore.setState({ uuidByRef: { "init-tinkercg-build-1": uuid }, refByUuid: {} });
  useInitiativeAgentStore.setState({ agent: "baxter", baxterSessionByInitiative: { [uuid]: "bx1" } });
  try {
    const { result } = renderHook(() => useActiveChatSession(), {
      wrapper: wrapperFor("/initiatives/init-tinkercg-build-1"),
    });

    expect(result.current.composedOnScreen).toBe(true);
    expect(result.current.session).toBeNull();
    expect(result.current.model?.scopeLabel).toBe("Baxter · initiative");
    expect(result.current.screenPrompts?.kind).toBe(INITIATIVE_TRIAGE);
    // The thread the panel shows: filed under the UUID, read in the initiative's scope.
    await waitFor(() => expect(api.baxterChatSession).toHaveBeenCalledWith(`@initiative:${uuid}`, "bx1"));
  } finally {
    useInitiativeAgentStore.setState({ agent: "planner", baxterSessionByInitiative: {} });
    useTicketRefStore.setState({ uuidByRef: {}, refByUuid: {} });
  }
});

it("asks for no planner conversation until the initiative's shareable id resolves", () => {
  useTicketRefStore.setState({ uuidByRef: {}, refByUuid: {} });
  (api.plannerChat as jest.Mock).mockClear();
  const { result } = renderHook(() => useActiveChatSession(), {
    wrapper: wrapperFor("/initiatives/init-pre-tinkercg-3"),
  });

  // Still the planner's page: Baxter's composer stays off it.
  expect(result.current.composedOnScreen).toBe(true);
  // Nothing to send to until the id resolves.
  expect(result.current.screenPrompts?.unavailable).toBe(true);
  expect(api.plannerChat).not.toHaveBeenCalled();
});

it("asks for no planner conversation off an initiative page", () => {
  (api.plannerChat as jest.Mock).mockClear();
  const { result } = renderHook(() => useActiveChatSession(), {
    wrapper: wrapperFor("/queue"),
  });

  expect(result.current.model?.scopeLabel).toBe("Baxter");
  expect(api.plannerChat).not.toHaveBeenCalled();
});

it("keeps Baxter's composer on the initiative suggestions page, which has no planner", () => {
  const { result } = renderHook(() => useActiveChatSession(), {
    wrapper: wrapperFor("/initiatives/suggest"),
  });

  expect(result.current.composedOnScreen).toBe(false);
  expect(result.current.session?.kind).toBe("baxter-home");
});

it("distinguishes a screen composing for itself from one with no conversation", () => {
  // Both bind nothing; only one should lose the composer. Branch triage with no
  // branch picked still wants the bar to say what to open.
  useUiStore.setState({ branchTriageWorkspaceSlug: "loregarden", branchTriageBranch: "" });

  const { result } = renderHook(() => useActiveChatSession(), {
    wrapper: wrapperFor("/branch-triage"),
  });

  expect(result.current.composedOnScreen).toBe(false);
});

it("offers the archive on a screen bound to the Baxter thread", () => {
  const { result } = renderHook(() => useActiveChatSession(), {
    wrapper: wrapperFor("/queue"),
  });

  expect(result.current.archive?.scope).toBe("loregarden");
});

it("keeps the archive off a ticket conversation, which has no other thread", () => {
  const { result } = renderHook(() => useActiveChatSession(), {
    wrapper: wrapperFor("/tickets/41aac2d7-26a6-4f0b-988a-fc220d8dfa6c/diff"),
  });

  expect(result.current.archive).toBeNull();
});
