import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { RunLogModal } from "../RunLogModal";
import * as apiClient from "../../api/client";

jest.mock("../../api/client", () => jest.requireActual("../../test/apiClientMock"));

const api = apiClient.api as unknown as { runLog: jest.Mock };

function makeLog(overrides: Record<string, unknown> = {}) {
  return {
    id: "run-1",
    run_code: "run_abc123",
    agent_id: "static_qa",
    skill_name: "run_tests",
    stage_key: "testing",
    status: "succeeded",
    command: "claude -p 'run the tests'",
    started_at: null,
    finished_at: null,
    lines: [
      { time: "20:57:14", tag: "RUN", text: "static_qa invoked" },
      { time: "20:57:20", tag: "OUT", text: "3 passed" },
    ],
    live: null,
    stderr: "",
    ...overrides,
  };
}

function renderModal(runId: string | null, onClose = jest.fn()) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return {
    onClose,
    ...render(
      <QueryClientProvider client={queryClient}>
        <RunLogModal runId={runId} onClose={onClose} />
      </QueryClientProvider>,
    ),
  };
}

beforeEach(() => {
  jest.clearAllMocks();
  api.runLog.mockResolvedValue(makeLog());
});

it("renders nothing and fetches nothing when no run is selected", () => {
  renderModal(null);
  expect(screen.queryByTestId("modal-content")).not.toBeInTheDocument();
  expect(api.runLog).not.toHaveBeenCalled();
});

it("shows the selected run's log lines", async () => {
  renderModal("run-1");

  expect(await screen.findByText("static_qa invoked")).toBeInTheDocument();
  expect(screen.getByText("3 passed")).toBeInTheDocument();
  expect(screen.getByText("run_abc123")).toBeInTheDocument();
  expect(api.runLog).toHaveBeenCalledWith("run-1");
});

it("tells the user when a run has no recorded log", async () => {
  api.runLog.mockResolvedValue(makeLog({ lines: [], live: null }));
  renderModal("run-1");

  expect(await screen.findByText(/no log recorded for this run/i)).toBeInTheDocument();
});

it("closes on Escape and on overlay click", async () => {
  const { onClose } = renderModal("run-1");
  await screen.findByText("3 passed");

  fireEvent.keyDown(document, { key: "Escape" });
  expect(onClose).toHaveBeenCalledTimes(1);

  fireEvent.click(screen.getByTestId("modal-backdrop"));
  expect(onClose).toHaveBeenCalledTimes(2);
});

it("surfaces a fetch failure instead of rendering an empty log", async () => {
  api.runLog.mockRejectedValue(new Error("boom"));
  renderModal("run-1");

  await waitFor(() => expect(screen.getByText(/could not load/i)).toBeInTheDocument());
  expect(screen.queryByText(/no log recorded/i)).not.toBeInTheDocument();
});

// --- lg-durable-remote-336: a run that outlived its server -------------------
//
// The modal answers one question for the operator: is this run still alive,
// where is its process, and did I lose any of its output when the server
// restarted? The action it leads to is copying the `tmux attach` command, or
// stopping the run.
//
// Volume, measured read-only from the live database on 2026-10-08: 2,285
// agent_runs, 939 with log lines, 1,667 lines in the largest single run, 29
// runs over 500 lines, 836 runs with a pid identity — so 1,449 will report no
// transport at all.

function detachedLog(overrides: Record<string, unknown> = {}) {
  return makeLog({
    status: "running",
    transport: "tmux",
    attach_command: "tmux attach -t lg-run_abc123-0f8c1a2b",
    ...overrides,
  });
}

it("names the transport the run is detached on in its subtitle", async () => {
  // AC27. This is how AC18's "visible in the run record" is satisfied.
  api.runLog.mockResolvedValue(detachedLog());
  renderModal("run-1");

  const subtitle = await screen.findByText(/static_qa · testing · running · tmux/);
  expect(subtitle).toBeInTheDocument();
});

it("renders an em dash, never a guessed default, when no transport was recorded", async () => {
  // AC28. 1,449 existing runs have no process identity and never will;
  // rendering those as "file" asserts something the row does not say. Same
  // fail-closed direction as `run_reattach.liveness`.
  api.runLog.mockResolvedValue(makeLog({ transport: "", attach_command: "" }));
  renderModal("run-1");

  const subtitle = await screen.findByText(/static_qa · testing · succeeded · —/);
  expect(subtitle).toBeInTheDocument();
  expect(screen.queryByText(/· file/)).not.toBeInTheDocument();
});

it("offers the attach command as a copy control", async () => {
  // AC27/AC35. `CopyValueButton` already owns the copied-for-1.5s state and
  // routes a clipboard failure through describeError → pushToast, so there is
  // no new copy chrome here.
  api.runLog.mockResolvedValue(detachedLog());
  renderModal("run-1");

  const copy = await screen.findByRole("button", { name: "Copy tmux attach command" });
  expect(copy).toBeEnabled();
});

it("offers no copy control when there is nothing to attach to", async () => {
  // A FILE-transport run, or a settled one: the server sends "".
  api.runLog.mockResolvedValue(detachedLog({ transport: "file", attach_command: "" }));
  renderModal("run-1");

  await screen.findByText(/static_qa · testing · running · file/);
  expect(screen.queryByRole("button", { name: /tmux attach command/i })).not.toBeInTheDocument();
});

it("copies the server-composed command rather than one built in the client", async () => {
  // AC27. The client cannot know the session name: it is derived from the run
  // id as well as the run code, and the `lg-` prefix lives once, on the server.
  const writeText = jest.fn().mockResolvedValue(undefined);
  Object.defineProperty(window.navigator, "clipboard", {
    value: { writeText },
    configurable: true,
  });
  api.runLog.mockResolvedValue(detachedLog());
  renderModal("run-1");

  fireEvent.click(await screen.findByRole("button", { name: "Copy tmux attach command" }));

  await waitFor(() =>
    expect(writeText).toHaveBeenCalledWith("tmux attach -t lg-run_abc123-0f8c1a2b"),
  );
});

it("keeps the last-known feed on screen when a refetch fails", async () => {
  // AC30, the defect. react-query keeps `data` across a failed refetch, so the
  // pane has lines; blanking them says the run is gone at exactly the moment
  // this ticket's point is that it is not.
  api.runLog.mockResolvedValueOnce(detachedLog());
  renderModal("run-1");
  await screen.findByText("3 passed");

  api.runLog.mockRejectedValue(new Error("Failed to fetch"));
  // The 2s poll re-runs the query; the rendered lines must survive it.
  await waitFor(() => expect(api.runLog).toHaveBeenCalledTimes(2), { timeout: 4000 });

  expect(screen.getByText("3 passed")).toBeInTheDocument();
  expect(await screen.findByText(/reconnecting to the control plane/i)).toBeInTheDocument();
  expect(screen.queryByText(/could not load this run.s log/i)).not.toBeInTheDocument();
});

it("still reports an error when there is nothing on screen to keep", async () => {
  // The control for the test above: the error state is not simply removed.
  api.runLog.mockRejectedValue(new Error("Failed to fetch"));
  renderModal("run-1");

  expect(await screen.findByText(/could not load this run.s log/i)).toBeInTheDocument();
  expect(screen.queryByText(/reconnecting to the control plane/i)).not.toBeInTheDocument();
});

it("tells the operator a detached run has simply not spoken yet", async () => {
  // AC32. Otherwise a live detached run reads as a dead one.
  api.runLog.mockResolvedValue(detachedLog({ lines: [], live: null }));
  renderModal("run-1");

  expect(
    await screen.findByText("No output yet — the agent is running detached on tmux."),
  ).toBeInTheDocument();
});

it("keeps the finished-run wording for a settled run with no log", async () => {
  api.runLog.mockResolvedValue(makeLog({ lines: [], live: null, transport: "" }));
  renderModal("run-1");

  expect(await screen.findByText(/no log recorded for this run/i)).toBeInTheDocument();
});

it("shows the restart marker as one ordinary SYS line in the feed", async () => {
  // AC29. Not a banner: a 6,451-minute run can survive two restarts and a
  // banner can only describe one.
  api.runLog.mockResolvedValue(
    detachedLog({
      lines: [
        { time: "20:57:14", tag: "RUN", text: "static_qa invoked" },
        { time: "21:04:02", tag: "SYS", text: "reattached · control-plane restart, resumed at byte 8192" },
        { time: "21:04:03", tag: "OUT", text: "still working" },
      ],
    }),
  );
  renderModal("run-1");

  expect(
    await screen.findByText(/reattached · control-plane restart, resumed at byte 8192/),
  ).toBeInTheDocument();
  expect(screen.getByText("still working")).toBeInTheDocument();
});

it("reaches the copy control from the modal title by keyboard alone", async () => {
  // AC35. It sits inside `useDialogFocusTrap`'s order; nothing focusable is
  // added outside the trap, and `useDialogDismiss` still owns Escape.
  api.runLog.mockResolvedValue(detachedLog());
  renderModal("run-1");

  const copy = await screen.findByRole("button", { name: "Copy tmux attach command" });
  const panel = screen.getByTestId("modal-content");
  expect(panel.contains(copy)).toBe(true);
  expect(copy.tabIndex).toBeGreaterThanOrEqual(0);
});

it("renders the largest realistic run without recreating unchanged rows", async () => {
  // AC24. 1,667 lines is the largest single run in the live database, polled
  // every 2s. The row memo is on time+tag+text, so an identical poll must not
  // re-parse the history.
  const lines = Array.from({ length: 1667 }, (_, index) => ({
    time: "20:57:14",
    tag: index % 7 === 0 ? "TOOL" : "OUT",
    text: `line ${index}`,
  }));
  api.runLog.mockResolvedValue(detachedLog({ lines }));
  renderModal("run-1");

  expect(await screen.findByText("line 1666")).toBeInTheDocument();
  const firstRow = screen.getByText("line 0");
  await waitFor(() => expect(api.runLog).toHaveBeenCalledTimes(2), { timeout: 4000 });
  expect(screen.getByText("line 0")).toBe(firstRow);
});
