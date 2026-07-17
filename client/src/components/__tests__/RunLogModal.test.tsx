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
