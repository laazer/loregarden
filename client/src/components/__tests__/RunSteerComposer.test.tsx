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
  mockApi.runMessages.mockResolvedValue({ messages: [], refusal: "" });
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
  mockApi.runMessages.mockResolvedValue({ messages: [], refusal: "" });

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
