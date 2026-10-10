import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { api } from "../../../api/client";
import type { RunLog } from "../../../api/types";
import { useUiStore } from "../../../state/uiStore";
import { LaneLogView } from "../LaneLogView";

// Requirement 3 (AC-3.2) — LaneLogView is the leaf that actually calls
// GET /runs/{run_id}/log and renders one lane's own lines/live. Tab
// derivation and selection live in LogsPanel.test.tsx; this file covers the
// view's own render states and the auto-follow toggle it owns.

jest.mock("../../../api/client");

const mockApi = api as jest.Mocked<typeof api>;

function runLog(overrides: Partial<RunLog> = {}): RunLog {
  return {
    id: "r1",
    run_code: "run_a",
    agent_id: "backend_implementer",
    skill_name: "",
    stage_key: "implement",
    status: "running",
    command: "",
    started_at: null,
    finished_at: null,
    lines: [],
    live: null,
    stderr: "",
    transport: "",
    attach_command: "",
    ...overrides,
  };
}

function renderLane(runId = "r1") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LaneLogView runId={runId} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  useUiStore.setState({ autoFollowByRunId: {} });
});

it("renders each rendered line for the run", async () => {
  mockApi.runLog.mockResolvedValue(
    runLog({
      lines: [
        { time: "10:00:00", tag: "RUN", text: "backend_implementer invoked" },
        { time: "10:00:05", tag: "OUT", text: "3 passed" },
      ],
    }),
  );

  renderLane();

  expect(await screen.findByText("backend_implementer invoked")).toBeInTheDocument();
  expect(screen.getByText("3 passed")).toBeInTheDocument();
});

it("renders the live line when the run is still in progress", async () => {
  mockApi.runLog.mockResolvedValue(runLog({ live: "still working" }));

  renderLane();

  expect(await screen.findByText("still working")).toBeInTheDocument();
});

it("shows a loading state before the run log resolves", async () => {
  mockApi.runLog.mockReturnValue(new Promise(() => {}));

  renderLane();

  expect(await screen.findByText(/loading log/i)).toBeInTheDocument();
});

it("shows an error state rather than crashing when the run log fails to load", async () => {
  mockApi.runLog.mockRejectedValue(new Error("network down"));

  renderLane();

  expect(await screen.findByText(/could not load this run.s log/i)).toBeInTheDocument();
});

it("shows an empty state when the run has no lines and no live text yet", async () => {
  mockApi.runLog.mockResolvedValue(runLog());

  renderLane();

  expect(await screen.findByText(/no log recorded for this run/i)).toBeInTheDocument();
});

it("defaults the auto-follow checkbox to checked for a lane with no stored preference", async () => {
  mockApi.runLog.mockResolvedValue(runLog({ lines: [{ time: "10:00:00", tag: "OUT", text: "x" }] }));

  renderLane("r1");
  await screen.findByText("x");

  expect(screen.getByLabelText(/auto-follow/i)).toBeChecked();
});

it("reflects an existing stored auto-follow=false preference for this run id", async () => {
  useUiStore.setState({ autoFollowByRunId: { r1: false } });
  mockApi.runLog.mockResolvedValue(runLog({ lines: [{ time: "10:00:00", tag: "OUT", text: "x" }] }));

  renderLane("r1");
  await screen.findByText("x");

  expect(screen.getByLabelText(/auto-follow/i)).not.toBeChecked();
});

it("toggling the auto-follow checkbox persists the choice to the ui store, keyed by run id", async () => {
  mockApi.runLog.mockResolvedValue(runLog({ lines: [{ time: "10:00:00", tag: "OUT", text: "x" }] }));

  renderLane("r1");
  await screen.findByText("x");

  const checkbox = screen.getByLabelText(/auto-follow/i);
  expect(checkbox).toBeChecked();

  fireEvent.click(checkbox);

  expect(checkbox).not.toBeChecked();
  expect(useUiStore.getState().autoFollowByRunId.r1).toBe(false);
});

// --- lg-durable-remote-336: the lane log across a restart boundary -----------
//
// The lane log answers "what is the running agent doing right now?" and leads
// to selecting a lane and then steering or stopping it. At most 3 concurrent
// lanes (the review stage's three reviewers), each at the modal's per-run line
// volume.
//
// Its five states are INHERITED from the run log modal: both render the same
// `{lines, live, transport}` payload, and a divergence is how one pane keeps
// the old behaviour. That is why the decision lives in `lib/logFeedState` and
// both panes import it — fixing one and not the other is the defect.

it("keeps the last-known feed when a refetch fails", async () => {
  // AC30. react-query keeps `data` across a failed refetch; blanking the pane
  // reports the run as gone during the 10-30s its server is restarting.
  mockApi.runLog.mockResolvedValueOnce(
    runLog({ lines: [{ time: "10:00:00", tag: "OUT", text: "mid-stage output" }] }),
  );
  renderLane();
  await screen.findByText("mid-stage output");

  mockApi.runLog.mockRejectedValue(new Error("Failed to fetch"));
  await waitFor(() => expect(mockApi.runLog).toHaveBeenCalledTimes(2), { timeout: 4000 });

  expect(screen.getByText("mid-stage output")).toBeInTheDocument();
  expect(await screen.findByText(/reconnecting to the control plane/i)).toBeInTheDocument();
  expect(screen.queryByText(/could not load this run.s log/i)).not.toBeInTheDocument();
});

it("still reports an error when the lane has nothing on screen", async () => {
  // The control: the error state is not simply deleted from this pane either.
  mockApi.runLog.mockRejectedValue(new Error("Failed to fetch"));
  renderLane();

  expect(await screen.findByText(/could not load this run.s log/i)).toBeInTheDocument();
});

it("tells the operator a detached lane has not spoken yet", async () => {
  // AC32, in the same words the modal uses — they share one module.
  mockApi.runLog.mockResolvedValue(runLog({ status: "running", transport: "file", lines: [] }));
  renderLane();

  expect(
    await screen.findByText("No output yet — the agent is running detached on file."),
  ).toBeInTheDocument();
});

it("keeps the finished-run wording when a settled lane recorded nothing", async () => {
  mockApi.runLog.mockResolvedValue(runLog({ status: "succeeded", transport: "", lines: [] }));
  renderLane();

  expect(await screen.findByText(/no log recorded for this run/i)).toBeInTheDocument();
});

it("shows the restart marker inline in the lane's own feed", async () => {
  // AC29/AC25. A lane that survives a restart keeps streaming into the same
  // feed, with one SYS line recording where it resumed.
  mockApi.runLog.mockResolvedValue(
    runLog({
      status: "running",
      transport: "tmux",
      lines: [
        { time: "10:00:00", tag: "OUT", text: "before the restart" },
        { time: "10:04:00", tag: "SYS", text: "reattached · control-plane restart, resumed at byte 4096" },
        { time: "10:04:01", tag: "OUT", text: "after the restart" },
      ],
    }),
  );
  renderLane();

  expect(await screen.findByText(/resumed at byte 4096/)).toBeInTheDocument();
  expect(screen.getByText("after the restart")).toBeInTheDocument();
});
