import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { useActiveChatSession } from "../useActiveChatSession";
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
    baxterChatSession: jest.fn().mockResolvedValue({ messages: [], run_status: "idle" }),
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
    chatWorkspaceSlug: "loregarden",
  });
});

it("binds to the ticket named in the path", () => {
  const { result } = renderHook(() => useActiveChatSession(), {
    wrapper: wrapperFor("/tickets/abc-123/diff"),
  });

  expect(result.current.session?.kind).toBe("ticket-triage");
  expect(result.current.session?.id).toBe("abc-123");
  expect(result.current.label).toBe("Ticket triage");
});

it("reads the ticket from the path, not from route params", () => {
  // useParams is empty above <Routes>; a params-based resolver would silently
  // bind to nothing here and the dock would never open on a ticket.
  const { result } = renderHook(() => useActiveChatSession(), {
    wrapper: wrapperFor("/tickets/from-path/logs"),
  });

  expect(result.current.session?.id).toBe("from-path");
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
});

it("binds to nothing on Home, whose hero is the way into the same thread", () => {
  const { result } = renderHook(() => useActiveChatSession(), {
    wrapper: wrapperFor("/"),
  });

  expect(result.current.session).toBeNull();
  expect(result.current.composedOnScreen).toBe(true);
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

  expect(result.current.archive?.workspaceSlug).toBe("loregarden");
});

it("keeps the archive off a ticket conversation, which has no other thread", () => {
  const { result } = renderHook(() => useActiveChatSession(), {
    wrapper: wrapperFor("/tickets/abc-123/diff"),
  });

  expect(result.current.archive).toBeNull();
});
