import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";

import { useBranchChatSession } from "../useBranchChatSession";
import { fetchBranchChat, stopBranchChatTurn } from "../../lib/branchTriageApi";

jest.mock("../../lib/branchTriageApi", () => ({
  fetchBranchChat: jest.fn(),
  sendBranchChatMessage: jest.fn(),
  stopBranchChatTurn: jest.fn(),
}));

const fetchMock = fetchBranchChat as jest.MockedFunction<typeof fetchBranchChat>;
const stopMock = stopBranchChatTurn as jest.MockedFunction<typeof stopBranchChatTurn>;

function snapshot(run_status: "idle" | "running") {
  return {
    workspace_id: "ws",
    branch: "feature/hung",
    linked_ticket_id: null,
    linked_ticket_external_id: null,
    messages: [],
    runtime: {
      cli_adapter: "default",
      claude_model: "",
      cursor_model: "",
      codex_model: "",
      lmstudio_base_url: "",
      lmstudio_model: "",
    },
    run_status,
    active_turn_id: run_status === "running" ? "turn-1" : null,
  };
}

function Wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  jest.clearAllMocks();
});

test("a busy branch chat offers a stop, and stopping clears the busy flag", async () => {
  fetchMock.mockResolvedValue(snapshot("running"));
  stopMock.mockResolvedValue(snapshot("idle"));

  const { result } = renderHook(() => useBranchChatSession("loregarden", "feature/hung"), {
    wrapper: Wrapper,
  });

  await waitFor(() => expect(result.current.isBusy).toBe(true));
  // The dock renders its stop control only for a session that supplies one, so
  // the busy state is only escapable if this is defined.
  expect(result.current.stop).toBeDefined();

  // The snapshot the poll would return next is the settled one.
  fetchMock.mockResolvedValue(snapshot("idle"));
  await act(async () => {
    await result.current.stop!();
  });

  expect(stopMock).toHaveBeenCalledWith("loregarden", "feature/hung");
  await waitFor(() => expect(result.current.isBusy).toBe(false));
});
