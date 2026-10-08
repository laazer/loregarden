import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { api } from "../../api/client";
import { RunSteerComposer } from "../RunSteerComposer";

jest.mock("../../api/client");

const mockApi = api as jest.Mocked<typeof api>;

function renderComposer(isActive = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <RunSteerComposer runId="run-1" isActive={isActive} />
    </QueryClientProvider>,
  );
}

function message(overrides = {}) {
  return {
    id: "m1",
    run_id: "run-1",
    content: "use the existing helper",
    created_at: "2026-07-20T10:00:00",
    delivered_at: null,
    ...overrides,
  };
}

beforeEach(() => {
  jest.clearAllMocks();
});

it("sends a message to a steerable run", async () => {
  mockApi.runMessages.mockResolvedValue({ messages: [], refusal: "", cancel_requested_at: null });
  mockApi.sendRunMessage.mockResolvedValue(message());

  renderComposer();
  const input = await screen.findByLabelText(/message to this run/i);
  fireEvent.change(input, { target: { value: "prefer the existing seam" } });
  fireEvent.click(screen.getByRole("button", { name: /send/i }));

  await waitFor(() =>
    expect(mockApi.sendRunMessage).toHaveBeenCalledWith("run-1", "prefer the existing seam"),
  );
});

it("will not send an empty message", async () => {
  mockApi.runMessages.mockResolvedValue({ messages: [], refusal: "", cancel_requested_at: null });

  renderComposer();
  await screen.findByLabelText(/message to this run/i);
  expect(screen.getByRole("button", { name: /send/i })).toBeDisabled();
});

it("explains why a run cannot be steered instead of taking input", async () => {
  // cursor-agent has no --input-format, so there is no channel to write into a
  // run it is executing. Accepting a message here would be a lie.
  mockApi.runMessages.mockResolvedValue({
    messages: [],
    refusal: "The backend_implementer agent runs on the cursor adapter, which cannot receive input once started.",
    cancel_requested_at: null,
  });

  renderComposer();
  expect(await screen.findByText(/cannot receive input once started/i)).toBeInTheDocument();
  expect(screen.queryByLabelText(/message to this run/i)).not.toBeInTheDocument();
});

it("distinguishes a queued message from a delivered one", async () => {
  // A steer the agent never received is worse than none, because the operator
  // believes the run was corrected.
  mockApi.runMessages.mockResolvedValue({
    messages: [
      message({ id: "m1", content: "first", delivered_at: "2026-07-20T10:00:05" }),
      message({ id: "m2", content: "second", delivered_at: null }),
    ],
    refusal: "",
  });

  renderComposer();
  expect(await screen.findByText("first")).toBeInTheDocument();
  expect(screen.getByText(/· delivered/)).toBeInTheDocument();
  expect(screen.getByText(/· queued/)).toBeInTheDocument();
});

it("stays out of the way on a finished run with no history", async () => {
  mockApi.runMessages.mockResolvedValue({
    messages: [],
    refusal: "Run is succeeded, so there is nothing to steer.",
    cancel_requested_at: null,
  });

  const { container } = renderComposer(false);
  await waitFor(() => expect(mockApi.runMessages).toHaveBeenCalled());
  await waitFor(() => expect(container).toBeEmptyDOMElement());
});

it("keeps showing what was sent after the run finishes", async () => {
  mockApi.runMessages.mockResolvedValue({
    messages: [message({ content: "check the migration", delivered_at: "2026-07-20T10:00:05" })],
    refusal: "Run is succeeded, so there is nothing to steer.",
  });

  renderComposer(false);
  expect(await screen.findByText("check the migration")).toBeInTheDocument();
  expect(screen.queryByLabelText(/message to this run/i)).not.toBeInTheDocument();
});

it("stops a run after asking once", async () => {
  mockApi.runMessages.mockResolvedValue({ messages: [], refusal: "", cancel_requested_at: null });
  mockApi.cancelRun.mockResolvedValue({
    id: "run-1",
    status: "running",
    cancel_requested_at: "2026-08-14T12:00:00",
    refusal: "Cancel already requested.",
  });

  renderComposer();

  // One click arms it, it does not fire. The button sits beside a text input
  // and ending a turn cannot be undone.
  fireEvent.click(await screen.findByRole("button", { name: /stop this run/i }));
  expect(mockApi.cancelRun).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole("button", { name: /confirm stop/i }));
  await waitFor(() => expect(mockApi.cancelRun).toHaveBeenCalledWith("run-1"));
});

it("lets an operator back out of a stop", async () => {
  mockApi.runMessages.mockResolvedValue({ messages: [], refusal: "", cancel_requested_at: null });

  renderComposer();
  fireEvent.click(await screen.findByRole("button", { name: /stop this run/i }));
  fireEvent.click(screen.getByRole("button", { name: /keep going/i }));

  expect(screen.getByRole("button", { name: /stop this run/i })).toBeInTheDocument();
  expect(mockApi.cancelRun).not.toHaveBeenCalled();
});

it("can stop a run that cannot be steered", async () => {
  // A cursor-adapter run takes no input, and must not therefore be unstoppable.
  mockApi.runMessages.mockResolvedValue({
    messages: [],
    refusal: "The reviewer agent runs on the cursor adapter, which cannot receive input once started.",
    cancel_requested_at: null,
  });

  renderComposer();

  expect(await screen.findByRole("button", { name: /stop this run/i })).toBeInTheDocument();
});

it("offers no stop for a run that is already finished", async () => {
  mockApi.runMessages.mockResolvedValue({
    messages: [message({ delivered_at: "x" })],
    refusal: "",
    cancel_requested_at: null,
  });

  renderComposer(false);

  await screen.findByText(/use the existing helper/i);
  expect(screen.queryByRole("button", { name: /stop this run/i })).not.toBeInTheDocument();
});

// --- lg-durable-remote-336: stopping a run the server no longer owns ---------
//
// The control answers "can I still correct this run, or do I have to kill it?".
// Stopping is rare and deliberate — 11 runs cancelled in the whole history
// against 238 failed — so the two-press confirm stays.
//
// What changes is the window after the press. `isPending` resets as soon as the
// POST returns, which would put "Stop this run" back on screen while a stop is
// still working its way to a detached process group. A second press then
// signals a process group the server no longer owns. So the label is LATCHED
// off `cancel_requested_at`, newly returned by GET /api/runs/{id}/messages.

function messagesPayload(overrides: Record<string, unknown> = {}) {
  return { messages: [], refusal: "", cancel_requested_at: null, ...overrides };
}

it("reads Stopping… once a stop has been requested, and stays disabled", async () => {
  // AC33. Latched off the server's record, not the mutation's local state.
  mockApi.runMessages.mockResolvedValue(
    messagesPayload({ cancel_requested_at: "2026-10-08T12:00:00+00:00" }),
  );

  renderComposer();

  const stopping = await screen.findByRole("button", { name: /stopping…/i });
  expect(stopping).toBeDisabled();
  expect(screen.queryByRole("button", { name: /^stop this run$/i })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /confirm stop/i })).not.toBeInTheDocument();
});

it("does not revert to Stop this run when the cancel POST returns", async () => {
  // AC33, the defect. The mutation settles long before the process does.
  mockApi.runMessages
    .mockResolvedValueOnce(messagesPayload())
    .mockResolvedValue(messagesPayload({ cancel_requested_at: "2026-10-08T12:00:00+00:00" }));
  mockApi.cancelRun.mockResolvedValue({
    id: "run-1",
    status: "running",
    refusal: "",
    cancel_requested_at: "2026-10-08T12:00:00+00:00",
  });

  renderComposer();
  fireEvent.click(await screen.findByRole("button", { name: /^stop this run$/i }));
  fireEvent.click(screen.getByRole("button", { name: /confirm stop/i }));

  await waitFor(() => expect(mockApi.cancelRun).toHaveBeenCalledWith("run-1"));
  expect(await screen.findByRole("button", { name: /stopping…/i })).toBeDisabled();
  expect(screen.queryByRole("button", { name: /^stop this run$/i })).not.toBeInTheDocument();
});

it("disables the steer input and Send for the same window", async () => {
  // A message queued behind a stop is either dropped unread or is the last
  // thing an agent is told before being killed. Neither is what was asked for.
  mockApi.runMessages.mockResolvedValue(
    messagesPayload({ cancel_requested_at: "2026-10-08T12:00:00+00:00" }),
  );

  renderComposer();
  await screen.findByRole("button", { name: /stopping…/i });

  const input = screen.queryByLabelText(/message to this run/i);
  if (input) expect(input).toBeDisabled();
  const send = screen.queryByRole("button", { name: /^send$/i });
  if (send) expect(send).toBeDisabled();
});

it("takes the control away entirely once the run is no longer active", async () => {
  // AC33's last label: `Stopping…` then the control is gone.
  mockApi.runMessages.mockResolvedValue(
    messagesPayload({
      refusal: "Run is cancelled, so there is nothing to steer.",
      cancel_requested_at: "2026-10-08T12:00:00+00:00",
    }),
  );

  renderComposer(false);

  await waitFor(() => expect(mockApi.runMessages).toHaveBeenCalled());
  expect(screen.queryByRole("button", { name: /stopping…/i })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /^stop this run$/i })).not.toBeInTheDocument();
});

it("explains in words that a detached run has no stdin to write into", async () => {
  // AC34. The server composes the sentence; the composer renders it rather
  // than offering an input that goes nowhere. `print_mode` never drains
  // RunMessage — only the permission bridge does — so this closes a void the
  // detached path would otherwise ship.
  mockApi.runMessages.mockResolvedValue(
    messagesPayload({
      refusal:
        "This run is detached on tmux, which has no stdin to write into, so it cannot be steered.",
    }),
  );

  renderComposer();

  expect(await screen.findByText(/no stdin to write into/i)).toBeInTheDocument();
  expect(screen.queryByLabelText(/message to this run/i)).not.toBeInTheDocument();
  // Still stoppable: a run that cannot take a message must not be unstoppable.
  expect(screen.getByRole("button", { name: /^stop this run$/i })).toBeInTheDocument();
});
