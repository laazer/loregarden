/**
 * Agent actions that act on running work and runtimes: steering and stopping
 * a run, the queue, starting/stopping the open ticket, and runtime changes.
 * Each runs the call its control makes, reaches the instance it names when
 * several are on screen, relays failure rather than reporting success, and
 * never disturbs what the operator is typing.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { uiActionRegistry } from "../../lib/agentActions/registry";
import { AgentActionHost } from "../AgentActionHost";
import { QueueAdvancedControls } from "../QueueAdvancedControls";
import { RunSteerComposer } from "../RunSteerComposer";
import { TicketAgentActions } from "../TicketAgentActions";

const api = {
  ticket: jest.fn(),
  runMessages: jest.fn(),
  sendRunMessage: jest.fn(),
  cancelRun: jest.fn(),
  queueRunAction: jest.fn(),
  startRun: jest.fn(),
  orchestrate: jest.fn(),
  stopTicket: jest.fn(),
  setTicketRuntime: jest.fn(),
  workspaceRuntime: jest.fn(),
  setWorkspaceRuntime: jest.fn(),
};

jest.mock("../../api/client", () => ({
  API_BASE: "http://127.0.0.1:8000",
  api: new Proxy({}, { get: (_t, name: string) => (...args: unknown[]) => api[name as keyof typeof api](...args) }),
}));

const RUNTIME = {
  cli_adapter: "claude",
  claude_model: "sonnet",
  cursor_model: "",
  lmstudio_base_url: "",
  lmstudio_model: "",
};
const OPEN = {
  id: "11111111-1111-1111-1111-111111111111",
  external_id: "lg-open-1",
  stages: [{ key: "implement" }, { key: "review" }],
  orchestration_runtime: RUNTIME,
};

function wrap(children: ReactNode, path = "/") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>{children}</MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeAll(() => {
  (globalThis as { WebSocket?: unknown }).WebSocket = class {
    readyState = 0;
    close() {}
  };
});

beforeEach(() => {
  for (const fn of Object.values(api)) fn.mockReset();
  api.ticket.mockResolvedValue(OPEN);
  api.runMessages.mockResolvedValue({ messages: [], refusal: "" });
  api.sendRunMessage.mockResolvedValue({ id: "m1" });
  api.cancelRun.mockResolvedValue({});
  api.queueRunAction.mockResolvedValue({});
  api.startRun.mockResolvedValue({ ...OPEN, admission: null });
  api.stopTicket.mockResolvedValue(OPEN);
  api.setTicketRuntime.mockImplementation(async (_id: string, body: object) => body);
  api.workspaceRuntime.mockResolvedValue(RUNTIME);
  api.setWorkspaceRuntime.mockImplementation(async (_slug: string, body: object) => body);
});

afterEach(() => expect(uiActionRegistry.available()).toEqual([]));

describe("run.send_message / run.cancel", () => {
  const two = () =>
    wrap(
      <>
        <RunSteerComposer runId="r1" isActive />
        <RunSteerComposer runId="r2" isActive />
      </>,
    );

  it("reaches the composer for the run it names, marked as from an agent", async () => {
    const view = two();
    await act(() => uiActionRegistry.run("run.send_message", { run_id: "r1", message: "use the seam" }));
    expect(api.sendRunMessage).toHaveBeenCalledWith("r1", "[from an agent] use the seam");
    await act(() => uiActionRegistry.run("run.cancel", { run_id: "r2" }));
    expect(api.cancelRun).toHaveBeenCalledWith("r2");
    view.unmount();
  });

  it("refuses a run no composer on screen holds, and a run that is not running", async () => {
    const view = two();
    await expect(uiActionRegistry.run("run.cancel", { run_id: "r9" })).rejects.toThrow(
      "no steering composer for run r9 is on screen",
    );
    view.unmount();
    const idle = wrap(<RunSteerComposer runId="r1" isActive={false} />);
    await expect(uiActionRegistry.run("run.send_message", { run_id: "r1", message: "x" })).rejects.toThrow(
      "run r1 is not running",
    );
    expect(api.sendRunMessage).not.toHaveBeenCalled();
    expect(api.cancelRun).not.toHaveBeenCalled();
    idle.unmount();
  });

  it("does not clear the operator's draft when an agent's steer goes out", async () => {
    const view = wrap(<RunSteerComposer runId="r1" isActive />);
    const input = await screen.findByLabelText("Message to this run");
    fireEvent.change(input, { target: { value: "half-typed" } });
    await act(() => uiActionRegistry.run("run.send_message", { run_id: "r1", message: "steer" }));
    expect(input).toHaveValue("half-typed");
    view.unmount();
  });
});

describe("queue.promote / queue.cancel", () => {
  const queued = [{ run_id: "q1", ticket_id: "t", stage_key: "implement" }] as never;

  it("promotes and cancels a queued run through the row's call", async () => {
    const view = wrap(<QueueAdvancedControls activeRuns={[]} queuedRuns={queued} />);
    await act(() => uiActionRegistry.run("queue.promote", { run_id: "q1" }));
    expect(api.queueRunAction).toHaveBeenCalledWith("q1", "promote");
    await act(() => uiActionRegistry.run("queue.cancel", { run_id: "q1" }));
    expect(api.queueRunAction).toHaveBeenLastCalledWith("q1", "cancel");
    view.unmount();
  });

  it("refuses a run not in the queue, and relays a failed call instead of reporting success", async () => {
    const view = wrap(<QueueAdvancedControls activeRuns={[]} queuedRuns={queued} />);
    await expect(uiActionRegistry.run("queue.cancel", { run_id: "q9" })).rejects.toThrow("run q9 is not in the queue");
    api.queueRunAction.mockRejectedValue(new Error("run already admitted"));
    await expect(uiActionRegistry.run("queue.promote", { run_id: "q1" })).rejects.toThrow("run already admitted");
    view.unmount();
  });
});

describe("ticket.start_stage / ticket.stop / ticket.set_runtime", () => {
  const openTicket = () =>
    wrap(
      <Routes>
        <Route path="/tickets/:ticketId/:tab" element={<TicketAgentActions />} />
      </Routes>,
      `/tickets/${OPEN.id}/diff`,
    );

  it("starts a stage the ticket has, and stops the ticket", async () => {
    const view = openTicket();
    await waitFor(() => expect(api.ticket).toHaveBeenCalled());
    await waitFor(async () =>
      expect(await uiActionRegistry.run("ticket.start_stage", { ticket_id: "lg-open-1", stage_key: "review" })).toEqual({
        ticket_id: OPEN.id,
        stage_key: "review",
        admission: null,
      }),
    );
    expect(api.startRun).toHaveBeenCalledWith(OPEN.id, { stage_key: "review" });
    await expect(
      uiActionRegistry.run("ticket.start_stage", { ticket_id: OPEN.id, stage_key: "deploy" }),
    ).rejects.toThrow("lg-open-1 has no stage deploy");
    expect(api.startRun).toHaveBeenCalledTimes(1);

    await act(() => uiActionRegistry.run("ticket.stop", { ticket_id: OPEN.id }));
    expect(api.stopTicket).toHaveBeenCalledWith(OPEN.id);
    view.unmount();
  });

  it("changes only the runtime fields named, keeping the rest", async () => {
    const view = openTicket();
    await waitFor(() => expect(api.ticket).toHaveBeenCalled());
    await waitFor(() =>
      expect(uiActionRegistry.run("ticket.set_runtime", { ticket_id: OPEN.id, claude_model: "opus" })).resolves.toBeTruthy(),
    );
    expect(api.setTicketRuntime).toHaveBeenCalledWith(OPEN.id, { ...RUNTIME, claude_model: "opus" });
    view.unmount();
  });
});

describe("workspace.set_runtime", () => {
  it("merges the change onto the workspace's saved runtime, from any page", async () => {
    const view = wrap(<AgentActionHost />);
    await act(() => uiActionRegistry.run("workspace.set_runtime", { workspace_slug: "alpha", cli_adapter: "cursor" }));
    expect(api.workspaceRuntime).toHaveBeenCalledWith("alpha");
    expect(api.setWorkspaceRuntime).toHaveBeenCalledWith("alpha", { ...RUNTIME, cli_adapter: "cursor" });
    await expect(uiActionRegistry.run("workspace.set_runtime", { workspace_slug: "alpha" })).rejects.toThrow(
      "no runtime fields to change",
    );
    view.unmount();
  });
});
