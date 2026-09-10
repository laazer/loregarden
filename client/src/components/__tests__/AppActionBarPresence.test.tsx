/**
 * The status light must say which of three things is true, and the wiring from
 * the queue subscription to the dot is the part worth testing — a mocked
 * `useAgentPresence` would pass while the provider fed it nothing.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";

import { AppActionBar } from "../AppActionBar";
import { useActiveChatSession } from "../../hooks/useActiveChatSession";
import { useParallelExecutionWS } from "../../hooks/useParallelExecutionWS";
import type { ParallelExecutionWSStatus } from "../../hooks/useParallelExecutionWS";
import { useTerminalTarget } from "../../hooks/useTerminalTarget";
import { DEFAULT_PARALLEL_STATS } from "../../lib/queueSocket";
import { QueueStatusProvider } from "../../state/QueueStatusContext";
import { useUiStore } from "../../state/uiStore";

jest.mock("../../hooks/useActiveChatSession");
jest.mock("../../hooks/useTerminalTarget");
jest.mock("../../hooks/useParallelExecutionWS");
jest.mock("../../api/client", () => jest.requireActual("../../test/apiClientMock"));

const mockResolver = useActiveChatSession as jest.MockedFunction<typeof useActiveChatSession>;
const mockTerminal = useTerminalTarget as jest.MockedFunction<typeof useTerminalTarget>;
const mockQueue = useParallelExecutionWS as jest.MockedFunction<typeof useParallelExecutionWS>;

function queueStatus(overrides: Partial<ParallelExecutionWSStatus> = {}): ParallelExecutionWSStatus {
  return {
    activeRuns: [],
    queuedRuns: [],
    lanes: [],
    stats: DEFAULT_PARALLEL_STATS,
    estimatedClearSeconds: null,
    estimatedWaitSeconds: null,
    loading: false,
    error: null,
    connectionState: "open",
    isWebSocket: true,
    ...overrides,
  };
}

function activeRun(id: string) {
  return { run_id: id } as ParallelExecutionWSStatus["activeRuns"][number];
}

function renderBar({ withProvider = true }: { withProvider?: boolean } = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const bar = withProvider ? (
    <QueueStatusProvider>
      <AppActionBar />
    </QueueStatusProvider>
  ) : (
    <AppActionBar />
  );
  return render(<QueryClientProvider client={qc}>{bar}</QueryClientProvider>);
}

/** The dot itself is aria-hidden; its labelled wrapper is what carries state. */
function light() {
  const wrapper = document.querySelector(".app-action-bar-live");
  if (!wrapper) throw new Error("status light not rendered");
  const dot = wrapper.querySelector(".app-action-bar-live-dot");
  if (!dot) throw new Error("status dot not rendered");
  return { wrapper, dot };
}

beforeEach(() => {
  jest.clearAllMocks();
  useUiStore.setState({
    copilotOpen: false,
    copilotHistoryOpen: false,
    terminalOpen: false,
    utilityDockEdge: "bottom",
  });
  mockTerminal.mockReturnValue({ workspaceSlug: "loregarden", agent: "implementer" });
  mockResolver.mockReturnValue({
    session: null,
    label: "",
    ticketId: null,
    pendingApprovals: [],
    branch: null,
    archive: null,
    composedOnScreen: false,
  } as unknown as ReturnType<typeof useActiveChatSession>);
  mockQueue.mockReturnValue(queueStatus());
});

describe("action bar status light", () => {
  it("is lit and counts the runs while agents are active", () => {
    mockQueue.mockReturnValue(
      queueStatus({ activeRuns: [activeRun("r1"), activeRun("r2")] }),
    );
    renderBar();

    const { wrapper, dot } = light();
    expect(dot).toHaveClass("is-active");
    expect(dot).not.toHaveClass("is-idle");
    expect(dot).not.toHaveClass("is-unknown");
    expect(wrapper).toHaveAttribute("aria-label", "2 agents running");
    expect(wrapper).toHaveAttribute("title", "2 agents running");
    expect(screen.getByLabelText("2 agents running")).toBe(wrapper);
  });

  it("goes dark, not green, when nothing is running", () => {
    renderBar();

    const { wrapper, dot } = light();
    expect(dot).toHaveClass("is-idle");
    expect(dot).not.toHaveClass("is-active");
    expect(wrapper).toHaveAttribute("aria-label", "no agents running");
  });

  it("reports the backend being unreachable instead of claiming agents are online", () => {
    mockQueue.mockReturnValue(
      queueStatus({
        activeRuns: [activeRun("r1")],
        error: "Failed to fetch status",
        isWebSocket: false,
        connectionState: "closed",
      }),
    );
    renderBar();

    const { wrapper, dot } = light();
    expect(dot).toHaveClass("is-unknown");
    expect(dot).not.toHaveClass("is-active");
    // Stale runs from before the failure must not relabel this "1 agent running".
    expect(wrapper).toHaveAttribute(
      "aria-label",
      "backend unreachable — Failed to fetch status",
    );
  });

  it("says it is still checking before the first answer lands", () => {
    mockQueue.mockReturnValue(queueStatus({ loading: true }));
    renderBar();

    const { wrapper, dot } = light();
    expect(dot).toHaveClass("is-unknown");
    expect(wrapper).toHaveAttribute("aria-label", "checking agent status…");
  });

  it("is unknown, not idle, with no queue subscription in scope", () => {
    renderBar({ withProvider: false });

    const { wrapper, dot } = light();
    expect(dot).toHaveClass("is-unknown");
    expect(wrapper).toHaveAttribute("aria-label", "agent status unavailable");
  });
});
